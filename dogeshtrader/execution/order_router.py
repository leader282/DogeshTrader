"""Order router.

Connects three components:
* a :class:`RiskEngine` that validates and shapes intents into requests
* a :class:`Broker` that executes (paper/dry-run/kite)
* a :class:`RateLimiter` that throttles outbound order calls

The router is mode-aware - in DRY_RUN it forces every intent through the
dry-run broker even if a kite client is also configured.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from dogeshtrader.core.enums import Mode
from dogeshtrader.core.errors import OrderRejected, RiskRejected
from dogeshtrader.core.interfaces import IBroker
from dogeshtrader.core.models import (
    OrderIntent,
    OrderRequest,
    OrderUpdate,
    StrategyContext,
)
from dogeshtrader.execution.idempotency import IdempotencyGuard
from dogeshtrader.execution.rate_limiter import RateLimiter
from dogeshtrader.logging_config import get_logger

if TYPE_CHECKING:
    from dogeshtrader.risk.risk_engine import RiskEngine

_log = get_logger(__name__)


@dataclass
class RouterResult:
    """Summary of one routing pass."""

    accepted: list[OrderUpdate] = field(default_factory=list)
    risk_rejected: list[tuple[OrderIntent, str]] = field(default_factory=list)
    broker_rejected: list[tuple[OrderRequest, str]] = field(default_factory=list)
    duplicates: list[OrderRequest] = field(default_factory=list)
    requests: list[OrderRequest] = field(default_factory=list)


class OrderRouter:
    """Single point that decides what to *do* with strategy intents."""

    def __init__(
        self,
        mode: Mode,
        broker: IBroker,
        risk: RiskEngine,
        *,
        idempotency: IdempotencyGuard | None = None,
        rate_limiter: RateLimiter | None = None,
        live_trading_enabled: bool = False,
        live_confirmation: bool = False,
    ) -> None:
        self.mode = mode
        self.broker = broker
        self.risk = risk
        self.idempotency = idempotency
        self.rate_limiter = rate_limiter
        self.live_trading_enabled = live_trading_enabled
        self.live_confirmation = live_confirmation
        self._guardrails_checked = False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def route(self, intents: Iterable[OrderIntent], ctx: StrategyContext) -> RouterResult:
        result = RouterResult()
        self._verify_live_guardrails()

        for intent in intents:
            try:
                request = self.risk.check(intent, ctx)
            except RiskRejected as e:
                _log.warning(
                    "risk_rejected",
                    rule=e.rule,
                    detail=e.detail,
                    instrument=intent.instrument.key,
                    side=intent.side.value,
                    qty=intent.quantity,
                )
                result.risk_rejected.append((intent, str(e)))
                continue

            result.requests.append(request)

            if self.idempotency:
                if not self.idempotency.claim(request.idempotency_key):
                    _log.warning(
                        "idempotency_duplicate",
                        key=request.idempotency_key,
                        intent_id=request.intent_id,
                    )
                    result.duplicates.append(request)
                    continue

            if self.rate_limiter:
                acquired = self.rate_limiter.acquire(timeout=1.0)
                if not acquired:
                    msg = "rate_limit_exhausted"
                    _log.warning(msg, intent_id=request.intent_id)
                    result.broker_rejected.append((request, msg))
                    continue

            try:
                update = self.broker.place_order(request)
            except OrderRejected as e:
                _log.warning(
                    "broker_rejected",
                    reason=e.reason,
                    intent_id=request.intent_id,
                    instrument=request.instrument.key,
                )
                self.risk.on_rejection(e.reason)
                result.broker_rejected.append((request, e.reason))
                continue
            except Exception as e:
                _log.error(
                    "broker_exception",
                    error=str(e),
                    intent_id=request.intent_id,
                )
                self.risk.on_rejection(str(e))
                result.broker_rejected.append((request, str(e)))
                continue

            if self.idempotency:
                self.idempotency.attach(request.idempotency_key, update.order_id)

            # Ensure downstream persistence/reconciliation can always trace a
            # broker update back to the originating framework intent.
            raw = dict(update.raw or {})
            raw.setdefault("intent_id", request.intent_id)
            raw.setdefault("tag", request.tag)
            update.raw = raw

            self.risk.on_order_update(update)
            result.accepted.append(update)

            _log.info(
                "order_placed",
                order_id=update.order_id,
                status=update.status.value,
                intent_id=request.intent_id,
                instrument=request.instrument.key,
                side=request.side.value,
                qty=request.quantity,
                tag=request.tag,
                mode=self.mode.value,
            )

        return result

    # ------------------------------------------------------------------
    # Guardrails
    # ------------------------------------------------------------------

    def _verify_live_guardrails(self) -> None:
        if self.mode != Mode.LIVE or self._guardrails_checked:
            return
        if not self.live_trading_enabled:
            raise OrderRejected(
                "live_trading_enabled flag is false in config; refusing to route LIVE orders"
            )
        if not self.live_confirmation:
            raise OrderRejected(
                "live mode requires --i-understand-this-places-real-orders CLI flag"
            )
        self._guardrails_checked = True


__all__ = ["OrderRouter", "RouterResult"]
