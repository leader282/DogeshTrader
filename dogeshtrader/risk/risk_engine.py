"""Risk engine - turns OrderIntents into OrderRequests (or rejects them).

Pre-trade checks (in this order):
1. Strategy id must be present.
2. Circuit breakers must not be tripped.
3. Instrument allowlist/blocklist.
4. Quantity / notional limits.
5. Lot-size alignment.
6. Tick-size alignment (LIMIT / SL prices).
7. MARKET-protection requirement.
8. Market hours (configurable).
9. Order rate budget (per-minute).
10. Idempotency (delegated to OrderRouter; the engine generates the key here).
11. Max open orders.

Sizing is conservative: the engine will *reduce* quantity to fit the
per-order notional cap but will never increase it.
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Iterable
from datetime import datetime

from dogeshtrader.config import RiskConfig
from dogeshtrader.constants import (
    DEFAULT_ORDER_TAG_PREFIX,
    KITE_TAG_MAX_LEN,
    ORDER_INTERNAL_PREFIX,
)
from dogeshtrader.core.clock import is_market_open, minutes_until_close
from dogeshtrader.core.enums import OrderStatus, OrderType, Side
from dogeshtrader.core.errors import RiskRejected
from dogeshtrader.core.interfaces import IRiskEngine
from dogeshtrader.core.models import (
    Fill,
    OrderIntent,
    OrderRequest,
    OrderUpdate,
    StrategyContext,
)
from dogeshtrader.execution.rate_limiter import TokenBucket
from dogeshtrader.execution.tags import compose_tag
from dogeshtrader.risk.circuit_breakers import CircuitBreaker
from dogeshtrader.risk.rules import (
    align_to_lot_size,
    align_to_tick_size,
    instrument_allowed,
    is_lot_aligned,
    is_tick_aligned,
    notional,
    safe_market_protection_bounds,
)
from dogeshtrader.risk.sizing import clamp_quantity_by_notional


class RiskEngine(IRiskEngine):
    """Pre-trade validator + post-trade circuit-breaker driver.

    The engine is mode-aware: in BACKTEST / PAPER it skips the session-hours
    check (since timestamps come from historical data, not wall-clock).
    """

    def __init__(
        self,
        config: RiskConfig,
        *,
        tag_prefix: str = DEFAULT_ORDER_TAG_PREFIX,
        enforce_market_hours: bool = True,
    ) -> None:
        self.config = config
        self.tag_prefix = tag_prefix
        self.enforce_market_hours = enforce_market_hours
        self.breaker = CircuitBreaker(
            max_daily_loss=config.max_daily_loss,
            max_consecutive_rejections=config.max_consecutive_rejections,
            data_feed_grace_seconds=config.data_feed_disconnect_grace_seconds,
        )
        # Per-minute order budget.
        self._per_minute_bucket = TokenBucket(
            rate_per_sec=max(config.max_orders_per_minute, 1) / 60.0,
            burst=max(config.max_orders_per_minute, 1),
        )
        self._open_orders: int = 0
        self._daily_realised: float = 0.0

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def check(self, intent: OrderIntent, ctx: StrategyContext) -> OrderRequest:
        """Validate an intent and return a broker-ready :class:`OrderRequest`.

        Raises :class:`RiskRejected` on failure.
        """
        self._require_strategy_id(intent)
        self._check_breakers()

        instrument = intent.instrument

        if not instrument_allowed(
            instrument,
            allowed=self.config.allowed_instruments or None,
            blocked=self.config.blocked_instruments or None,
        ):
            self._reject("instrument_not_allowed", f"{instrument.key} is filtered")

        if intent.quantity > self.config.max_order_quantity > 0:
            self._reject(
                "max_order_quantity",
                f"qty {intent.quantity} > limit {self.config.max_order_quantity}",
            )

        if not is_lot_aligned(intent.quantity, instrument):
            # Auto-floor instead of rejecting; safer behaviour for retail.
            aligned = align_to_lot_size(intent.quantity, instrument)
            if aligned <= 0:
                self._reject(
                    "lot_size_alignment",
                    f"qty {intent.quantity} cannot satisfy lot size {instrument.lot_size}",
                )
            intent = intent.model_copy(update={"quantity": aligned})

        ref_price = self._reference_price(intent, ctx)

        if (
            intent.order_type == OrderType.LIMIT
            and intent.price is not None
            and not is_tick_aligned(intent.price, instrument)
        ):
            intent = intent.model_copy(
                update={"price": align_to_tick_size(intent.price, instrument)}
            )
        if (
            intent.order_type in (OrderType.SL, OrderType.SL_M)
            and intent.trigger_price is not None
            and not is_tick_aligned(intent.trigger_price, instrument)
        ):
            intent = intent.model_copy(
                update={"trigger_price": align_to_tick_size(intent.trigger_price, instrument)}
            )

        order_notional = notional(ref_price, intent.quantity)
        if self.config.max_order_notional > 0 and order_notional > self.config.max_order_notional:
            clamped = clamp_quantity_by_notional(
                intent.quantity,
                ref_price,
                max_notional=self.config.max_order_notional,
                instrument=instrument,
            )
            if clamped <= 0:
                self._reject(
                    "max_order_notional",
                    f"notional {order_notional:.2f} > limit {self.config.max_order_notional:.2f}"
                    f" and cannot be sized down",
                )
            intent = intent.model_copy(update={"quantity": clamped})

        if self.config.max_position_notional > 0:
            self._check_position_notional(intent, ctx, ref_price)

        if intent.order_type == OrderType.MARKET and self.config.require_market_protection:
            if intent.market_protection_pct is None:
                self._reject(
                    "market_protection_required",
                    "MARKET orders require market_protection_pct in this config",
                )
            elif ref_price is not None:
                lo, hi = safe_market_protection_bounds(
                    ref_price,
                    intent.market_protection_pct or self.config.default_market_protection_pct,
                )
                # store bounds in metadata for the broker adapter.
                intent.metadata.setdefault("market_protection_bounds", {"low": lo, "high": hi})

        if self.enforce_market_hours and ctx.mode in ("PAPER", "LIVE"):
            self._check_session_window(ctx.timestamp)

        if not self._per_minute_bucket.try_acquire():
            self._reject("order_rate_per_minute", "exceeded per-minute order budget")

        if self.config.max_open_orders > 0 and self._open_orders >= self.config.max_open_orders:
            self._reject(
                "max_open_orders",
                f"already have {self._open_orders} open orders",
            )

        intent_id = f"{ORDER_INTERNAL_PREFIX}{uuid.uuid4().hex[:12]}"
        idem_key = intent.idempotency_key or _make_idempotency_key(intent, ctx, intent_id)
        tag = compose_tag(self.tag_prefix, intent.strategy_id, intent_id)
        if len(tag) > KITE_TAG_MAX_LEN:
            tag = tag[:KITE_TAG_MAX_LEN]

        request = OrderRequest(
            intent_id=intent_id,
            strategy_id=intent.strategy_id,
            instrument=instrument,
            side=intent.side,
            quantity=intent.quantity,
            order_type=intent.order_type,
            product=intent.product,
            variety=intent.variety,
            price=intent.price,
            trigger_price=intent.trigger_price,
            validity=intent.validity,
            disclosed_quantity=intent.disclosed_quantity,
            iceberg_legs=intent.iceberg_legs,
            iceberg_quantity=intent.iceberg_quantity,
            tag=tag,
            idempotency_key=idem_key,
            metadata={
                **intent.metadata,
                "reason": intent.reason,
                "ref_price": ref_price,
            },
        )
        return request

    def filter_intents(
        self, intents: Iterable[OrderIntent], ctx: StrategyContext
    ) -> tuple[list[OrderRequest], list[tuple[OrderIntent, RiskRejected]]]:
        """Convenience: validate many intents, separate accepted from rejected."""
        accepted: list[OrderRequest] = []
        rejected: list[tuple[OrderIntent, RiskRejected]] = []
        for i in intents:
            try:
                accepted.append(self.check(i, ctx))
            except RiskRejected as e:
                rejected.append((i, e))
        return accepted, rejected

    def on_fill(self, fill: Fill) -> None:
        self._daily_realised -= fill.fees
        self.breaker.on_pnl_update(self._daily_realised)

    def update_realised_pnl(self, realised_pnl: float) -> None:
        """For engines that compute P&L externally and want to feed it in."""
        self._daily_realised = realised_pnl
        self.breaker.on_pnl_update(realised_pnl)

    def on_rejection(self, reason: str) -> None:
        self.breaker.on_rejection(reason)

    def on_order_update(self, update: OrderUpdate) -> None:
        if update.status in (OrderStatus.PENDING, OrderStatus.SUBMITTED, OrderStatus.OPEN):
            self._open_orders = max(self._open_orders, 0) + 1
        elif update.status in (
            OrderStatus.COMPLETE,
            OrderStatus.CANCELLED,
            OrderStatus.REJECTED,
        ):
            self._open_orders = max(self._open_orders - 1, 0)
        if update.status == OrderStatus.REJECTED:
            self.breaker.on_rejection(update.status_message or "rejected")
        elif update.status in (OrderStatus.COMPLETE, OrderStatus.PARTIAL):
            self.breaker.on_acceptance()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _require_strategy_id(self, intent: OrderIntent) -> None:
        if not intent.strategy_id:
            self._reject("missing_strategy_id", "intent has empty strategy_id")

    def _check_breakers(self) -> None:
        if self.breaker.is_tripped:
            self._reject(
                "circuit_breaker",
                "; ".join(self.breaker.tripped_reasons()) or "tripped",
            )

    def _reference_price(self, intent: OrderIntent, ctx: StrategyContext) -> float | None:
        if intent.price is not None and intent.order_type in (
            OrderType.LIMIT,
            OrderType.SL,
        ):
            return intent.price
        if intent.trigger_price is not None and intent.order_type in (
            OrderType.SL,
            OrderType.SL_M,
        ):
            return intent.trigger_price
        return ctx.last_price(intent.instrument)

    def _check_position_notional(
        self, intent: OrderIntent, ctx: StrategyContext, ref_price: float | None
    ) -> None:
        if ref_price is None:
            return
        # Approximate new position notional.
        pos = ctx.position(intent.instrument)
        delta = intent.quantity if intent.side == Side.BUY else -intent.quantity
        future_qty = pos.quantity + delta
        future_notional = abs(future_qty) * ref_price

        # Plus current notional across other instruments.
        portfolio_notional = sum(
            abs(p.quantity) * (ctx.latest_prices.get(p.instrument.key) or p.last_price or 0)
            for p in ctx.positions.values()
            if p.instrument.key != intent.instrument.key
        )
        if portfolio_notional + future_notional > self.config.max_position_notional:
            self._reject(
                "max_position_notional",
                f"position notional {portfolio_notional + future_notional:.2f} "
                f"> limit {self.config.max_position_notional:.2f}",
            )

    def _check_session_window(self, now: datetime) -> None:
        # No-new-orders window before close.
        cutoff_minutes = self.config.no_new_orders_in_last_minutes
        if not is_market_open(now, self.config.session_start, self.config.session_end):
            self._reject("market_hours", f"market closed at {now.isoformat()}")
        if cutoff_minutes > 0:
            if minutes_until_close(now, self.config.session_end) <= cutoff_minutes:
                self._reject(
                    "no_new_orders_window",
                    f"within last {cutoff_minutes} minutes of close",
                )

    def _reject(self, rule: str, detail: str) -> None:
        self.breaker.on_rejection(detail)
        raise RiskRejected(rule, detail)


def _make_idempotency_key(intent: OrderIntent, ctx: StrategyContext, intent_id: str) -> str:
    payload = "|".join(
        [
            intent.strategy_id,
            intent.instrument.key,
            intent.side.value,
            str(intent.quantity),
            intent.order_type.value,
            str(intent.price or ""),
            str(intent.trigger_price or ""),
            (intent.signal_ts or ctx.timestamp).isoformat(),
            intent_id,
        ]
    )
    return hashlib.sha256(payload.encode()).hexdigest()[:32]


__all__ = ["RiskEngine"]
