"""Backtest engine.

Event-driven candle replay:

    for each (instrument, candle) in chronological order:
        broker.feed_candle(...)           # working orders that should fill, fill
        ctx.timestamp = candle.timestamp  # advance clock
        ctx.latest_prices[key] = close
        intents = strategy.on_candle(ctx, candle)
        router.route(intents, ctx)        # MARKET orders book and fill on the
                                          # NEXT candle to avoid look-ahead.

Trades (round-trips) are derived from the fill stream, not from
``strategy.on_end``. The engine also synthesises an equity curve sampled per
candle for downstream metrics.
"""

from __future__ import annotations

import itertools
import json
import uuid
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from dogeshtrader.backtest.costs import CostModel
from dogeshtrader.backtest.fills import CandleFillModel, FillModel
from dogeshtrader.backtest.metrics import Metrics, Trade, compute_metrics
from dogeshtrader.backtest.report import write_reports
from dogeshtrader.backtest.slippage import SlippageModel
from dogeshtrader.brokers.paper_broker import PaperBroker, PaperConfig
from dogeshtrader.config import AppConfig, RiskConfig, StrategyConfig
from dogeshtrader.constants import RUN_ID_PREFIX
from dogeshtrader.core.clock import FrozenClock, session_date
from dogeshtrader.core.enums import Mode, OrderStatus, OrderType, Side
from dogeshtrader.core.interfaces import IBroker
from dogeshtrader.core.models import (
    Candle,
    Fill,
    Instrument,
    OrderIntent,
    OrderUpdate,
    Position,
    StrategyContext,
)
from dogeshtrader.execution.idempotency import IdempotencyGuard
from dogeshtrader.execution.order_manager import OrderManager
from dogeshtrader.execution.order_router import OrderRouter
from dogeshtrader.logging_config import get_logger
from dogeshtrader.operator.state import OperatorBook, RunStatus, publish_run, state_path
from dogeshtrader.risk.flatten import flatten_intents
from dogeshtrader.risk.kill_switch import KillSwitch, kill_switch_path
from dogeshtrader.risk.risk_engine import RiskEngine
from dogeshtrader.storage import (
    AuditRepository,
    Database,
    FillRepository,
    IdempotencyRepository,
    OrderRepository,
    RunRepository,
    run_migrations,
)
from dogeshtrader.strategy.base import Strategy

_log = get_logger(__name__)


@dataclass
class BacktestResult:
    run_id: str
    metrics: Metrics
    trades: list[Trade]
    equity_curve: list[tuple[datetime, float]]
    fills: list[Fill]
    orders: list[OrderUpdate]
    report_paths: dict[str, Path] = field(default_factory=dict)


class BacktestEngine:
    """Single-pass event-driven backtest engine.

    Parameters
    ----------
    strategy:
        Already-instantiated Strategy.
    candles_provider:
        Callable that yields a chronological stream of ``(Instrument, Candle)``.
    instruments:
        Universe being traded; used for warmup / context bootstrap.
    """

    def __init__(
        self,
        *,
        strategy: Strategy,
        candles_provider: Iterable[tuple[Instrument, Candle]],
        instruments: list[Instrument],
        strategy_cfg: StrategyConfig,
        app_cfg: AppConfig,
        risk_cfg: RiskConfig,
        fill_model: FillModel | None = None,
        slippage: SlippageModel | None = None,
        costs: CostModel | None = None,
        db: Database | None = None,
    ) -> None:
        from dogeshtrader.backtest.costs import build_cost_model
        from dogeshtrader.backtest.slippage import FixedBpsSlippage

        self.strategy = strategy
        self.candles_provider = candles_provider
        self.instruments = instruments
        self.strategy_cfg = strategy_cfg
        self.app_cfg = app_cfg
        self.risk_cfg = risk_cfg
        self.run_id = f"{RUN_ID_PREFIX}{datetime.now():%Y%m%d_%H%M%S}_{uuid.uuid4().hex[:6]}"

        slippage = slippage or FixedBpsSlippage(bps=app_cfg.slippage.bps)
        costs = costs or build_cost_model(
            app_cfg.costs.model,
            bps=app_cfg.costs.bps_fallback,
            fee_per_order=app_cfg.costs.brokerage_per_order,
        )
        self.fill_model = fill_model or CandleFillModel(slippage=slippage, costs=costs)
        self.costs = costs

        self.broker = PaperBroker(
            config=PaperConfig(
                starting_cash=strategy_cfg.initial_cash,
                fill_market_on_next_candle_open=True,
                optimistic_limits=False,
                fee_per_order=0.0,
                bps_cost=0.0,
            )
        )
        # We'll drive fills via fill_model instead of paper broker's matcher.
        # Keep paper broker for position bookkeeping and cash tracking only.

        # Storage
        self.db = db or Database(app_cfg.db_path)
        run_migrations(self.db)
        self.run_repo = RunRepository(self.db)
        self.order_repo = OrderRepository(self.db)
        self.fill_repo = FillRepository(self.db)
        self.idem_repo = IdempotencyRepository(self.db)
        self.audit_repo = AuditRepository(self.db)

        # Risk engine - in backtest mode we disable session-hours enforcement
        # and let strategy-driven flatten logic handle it via timestamps.
        self.kill_switch = KillSwitch(kill_switch_path(app_cfg.data_dir))
        self.operator = OperatorBook(state_path(app_cfg.data_dir))
        self.risk = RiskEngine(
            risk_cfg,
            tag_prefix=app_cfg.order_tag_prefix,
            enforce_market_hours=False,
            kill_switch=self.kill_switch,
            market_interval=strategy_cfg.interval,
        )

        self.order_mgr = OrderManager()
        self.router = OrderRouter(
            mode=Mode.BACKTEST,
            broker=_NoOpRouterBroker(),  # we don't actually call broker.place; engine handles fills
            risk=self.risk,
            idempotency=IdempotencyGuard(self.idem_repo, self.run_id),
        )

        self.clock: FrozenClock | None = None
        self._order_id_seq = itertools.count(1)

    # ------------------------------------------------------------------
    # Run
    # ------------------------------------------------------------------

    def run(self, output_dir: Path | str | None = None) -> BacktestResult:
        cfg_yaml = json.dumps(self.strategy_cfg.model_dump(by_alias=True), default=str)
        self.run_repo.create(
            self.run_id,
            mode=Mode.BACKTEST.value,
            strategy=self.strategy_cfg.strategy,
            strategy_id=self.strategy_cfg.strategy_id,
            config_yaml=cfg_yaml,
        )
        self.audit_repo.log(
            "engine",
            "started",
            run_id=self.run_id,
            payload={"strategy": self.strategy_cfg.strategy},
        )

        positions: dict[str, Position] = {i.key: Position(instrument=i) for i in self.instruments}
        cash = self.strategy_cfg.initial_cash
        equity_curve: list[tuple[datetime, float]] = []
        latest_prices: dict[str, float] = {}
        instrument_by_key = {i.key: i for i in self.instruments}

        ctx = StrategyContext(
            run_id=self.run_id,
            strategy_id=self.strategy_cfg.strategy_id,
            mode="BACKTEST",
            timestamp=datetime.now(),
            cash=cash,
            positions=positions,
            latest_prices=latest_prices,
            config={
                "instrument_keys": [i.key for i in self.instruments],
                "session_start": self.risk_cfg.session_start,
                "session_end": self.risk_cfg.session_end,
                "interval": self.strategy_cfg.interval,
            },
            params=self.strategy_cfg.params,
            storage=self.db,
        )
        self.strategy.on_start(ctx)

        bars_in_market = 0
        total_bars = 0
        all_fills: list[Fill] = []
        # pending intents emitted on bar N fill on bar N+1's data.
        pending_orders: dict[str, list[_PendingOrder]] = defaultdict(list)
        trades: list[Trade] = []
        # Track open lots per instrument for round-trip accounting (FIFO).
        open_lots: dict[str, list[_Lot]] = defaultdict(list)
        first_seen: dict[str, datetime] = {}
        last_candle: dict[str, Candle] = {}

        for instrument, candle in self.candles_provider:
            key = instrument.key
            if key not in instrument_by_key:
                instrument_by_key[key] = instrument
                positions.setdefault(key, Position(instrument=instrument))
            first_seen.setdefault(key, candle.timestamp)
            total_bars += 1
            if self.clock is None:
                self.clock = FrozenClock(candle.timestamp)
            else:
                self.clock.advance_to(candle.timestamp)

            # 1. Fill orders pending from the prior bar. Orders still pending at a
            # session boundary settle against the prior close instead.
            prev = last_candle.get(key)
            last_candle[key] = candle
            fills_now: list[Fill] = []
            if prev is not None and session_date(prev.timestamp) != session_date(candle.timestamp):
                fills_now = self._settle_at_session_close(
                    pending_orders, key, prev, positions, trades, open_lots
                )
            fills_now += self._fill_pending(
                pending_orders[key], candle, positions, trades, open_lots
            )
            for fill in fills_now:
                cash = self._apply_fill_cash(cash, fill)
                all_fills.append(fill)
                self.fill_repo.add(self.run_id, fill)
                self.order_mgr.apply_fill(fill)
                # Order update reflecting the fill.
                upd = OrderUpdate(
                    order_id=fill.order_id,
                    status=OrderStatus.COMPLETE,
                    filled_quantity=fill.quantity,
                    pending_quantity=0,
                    average_price=fill.price,
                    exchange_timestamp=fill.timestamp,
                    raw={"intent_id": (fill.raw or {}).get("intent_id")},
                )
                self.order_mgr.apply_update(upd)
                self.risk.on_order_update(upd)
                # Persist the order row.
                req = next(
                    (p.request for p in pending_orders[key] if p.order_id == fill.order_id),
                    None,
                )
                if req is not None:
                    self.order_repo.record_order(
                        run_id=self.run_id,
                        broker="paper",
                        mode=Mode.BACKTEST.value,
                        request=req,
                        update=upd,
                    )

            # Remove completed orders from the pending list.
            pending_orders[key] = [p for p in pending_orders[key] if not p.filled]

            # 2. Update prices and feed context.
            latest_prices[key] = candle.close
            ctx.latest_prices = latest_prices
            for k, p in positions.items():
                if k in latest_prices:
                    p.mark(latest_prices[k])
            ctx.cash = cash
            ctx.timestamp = candle.timestamp
            ctx.extra["_current_instrument"] = instrument_by_key[key]

            equity_now = _mark_equity(cash, positions, latest_prices)
            self.risk.observe_equity(candle.timestamp, equity_now)
            self.risk.update_realised_pnl(sum(p.realised_pnl for p in positions.values()))
            self.risk.note_candle(key, candle)

            # 3. Strategy decides. A kill switch replaces new entries with a flatten.
            if self.kill_switch.is_engaged():
                intents = flatten_intents(ctx)
            else:
                try:
                    intents = self.strategy.on_candle(ctx, candle)
                except Exception as e:
                    _log.error("strategy_error", error=str(e))
                    self.audit_repo.log(
                        "engine", "strategy_error", run_id=self.run_id, payload={"error": str(e)}
                    )
                    intents = []

            # 4. Risk + idempotency + record orders.
            self._book_intents(intents, ctx, pending_orders)
            self._publish(ctx, equity_now)

            # 5. Equity sample.
            equity = equity_now
            equity_curve.append((candle.timestamp, equity))
            if any(p.quantity != 0 for p in positions.values()):
                bars_in_market += 1

        # End of stream - let strategy emit final intents.
        ctx.timestamp = equity_curve[-1][0] if equity_curve else datetime.now()
        try:
            end_intents = self.strategy.on_end(ctx)
        except Exception as e:
            _log.error("strategy_end_error", error=str(e))
            end_intents = []

        if end_intents:
            # Treat end_intents as MARKET-on-last-close (no future candle).
            for intent in end_intents:
                try:
                    request = self.risk.check(intent, ctx)
                except Exception as e:
                    _log.warning("end_intent_rejected", error=str(e))
                    continue
                close_price = latest_prices.get(request.instrument.key)
                if close_price is None:
                    continue
                fill_price = close_price
                fees = self.costs.estimate(
                    instrument=request.instrument,
                    product=request.product,
                    side=request.side,
                    price=fill_price,
                    quantity=request.quantity,
                )
                end_order_id = f"ord_b_end_{len(all_fills) + 1:04d}"
                upd = OrderUpdate(
                    order_id=end_order_id,
                    status=OrderStatus.COMPLETE,
                    filled_quantity=request.quantity,
                    pending_quantity=0,
                    average_price=fill_price,
                    exchange_timestamp=ctx.timestamp,
                    raw={"intent_id": request.intent_id},
                )
                self.order_repo.record_intent(
                    intent_id=request.intent_id,
                    run_id=self.run_id,
                    request=request,
                    reason=request.metadata.get("reason", "backtest_end"),
                )
                self.order_repo.record_order(
                    run_id=self.run_id,
                    broker="paper",
                    mode=Mode.BACKTEST.value,
                    request=request,
                    update=upd,
                )
                fill = Fill(
                    order_id=end_order_id,
                    trade_id=f"trd_b_end_{len(all_fills) + 1:04d}",
                    instrument=request.instrument,
                    side=request.side,
                    quantity=request.quantity,
                    price=fill_price,
                    timestamp=ctx.timestamp,
                    fees=fees,
                    raw={"intent_id": request.intent_id, "tag": request.tag},
                )
                cash = self._apply_fill_cash(cash, fill)
                self._apply_fill_position(fill, positions, trades, open_lots, ctx.timestamp)
                all_fills.append(fill)
                self.fill_repo.add(self.run_id, fill)

        if equity_curve:
            ts, _ = equity_curve[-1]
            extra_equity = cash + sum(
                positions[k].quantity * latest_prices.get(k, positions[k].avg_price)
                for k in positions
            )
            equity_curve[-1] = (ts, extra_equity)

        final_equity = equity_curve[-1][1] if equity_curve else cash
        ctx.cash = cash
        self._publish(ctx, final_equity, status="stopped", force=True)

        metrics = compute_metrics(
            equity_curve=equity_curve,
            trades=trades,
            bars_in_market=bars_in_market,
            total_bars=total_bars,
        )

        self.run_repo.complete(self.run_id, status="COMPLETED")
        self.audit_repo.log(
            "engine",
            "completed",
            run_id=self.run_id,
            payload={
                "trades": len(trades),
                "fills": len(all_fills),
                "end_equity": metrics.end_equity,
            },
        )

        report_paths: dict[str, Path] = {}
        if output_dir is None:
            output_dir = Path(self.app_cfg.reports_dir) / self.run_id
        report_paths = write_reports(
            output_dir=output_dir,
            metrics=metrics,
            trades=trades,
            equity_curve=equity_curve,
            fills=all_fills,
            orders=self.order_mgr.all_orders(),
            run_id=self.run_id,
            strategy_cfg=self.strategy_cfg,
        )

        return BacktestResult(
            run_id=self.run_id,
            metrics=metrics,
            trades=trades,
            equity_curve=equity_curve,
            fills=all_fills,
            orders=self.order_mgr.all_orders(),
            report_paths=report_paths,
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _publish(
        self,
        ctx: StrategyContext,
        equity: float,
        *,
        status: RunStatus = "running",
        force: bool = False,
    ) -> None:
        try:
            publish_run(
                self.operator,
                ctx,
                run_id=self.run_id,
                mode=Mode.BACKTEST.value,
                equity=equity,
                risk=self.risk,
                kill_switch=self.kill_switch,
                status=status,
                force=force,
            )
        except Exception as e:
            _log.warning("operator_snapshot_failed", error=str(e))

    def _book_intents(
        self,
        intents: list[OrderIntent],
        ctx: StrategyContext,
        pending_orders: dict[str, list[_PendingOrder]],
    ) -> None:
        for intent in intents:
            try:
                request = self.risk.check(intent, ctx)
            except Exception as e:
                self.audit_repo.log(
                    "risk",
                    "rejected",
                    run_id=self.run_id,
                    payload={"reason": str(e), "instrument": intent.instrument.key},
                )
                continue

            if not self.idem_repo.claim(request.idempotency_key, self.run_id):
                self.audit_repo.log(
                    "router",
                    "duplicate_intent",
                    run_id=self.run_id,
                    payload={"key": request.idempotency_key},
                )
                continue
            self.order_repo.record_intent(
                intent_id=request.intent_id,
                run_id=self.run_id,
                request=request,
                reason=intent.reason,
            )
            order_id = f"ord_b{next(self._order_id_seq):08d}"
            self.idem_repo.attach_order(request.idempotency_key, order_id)
            self.order_mgr.register_request(request)
            upd = OrderUpdate(
                order_id=order_id,
                status=OrderStatus.OPEN,
                pending_quantity=request.quantity,
                price=request.price,
                trigger_price=request.trigger_price,
                exchange_timestamp=ctx.timestamp,
                raw={"intent_id": request.intent_id, "tag": request.tag},
            )
            self.order_mgr.apply_update(upd)
            self.order_repo.record_order(
                run_id=self.run_id,
                broker="paper",
                mode=Mode.BACKTEST.value,
                request=request,
                update=upd,
            )
            self.risk.on_order_update(upd)
            pending_orders[request.instrument.key].append(
                _PendingOrder(
                    order_id=order_id,
                    request=request,
                    placed_ts=ctx.timestamp,
                )
            )

    def _settle_at_session_close(
        self,
        pending_orders: dict[str, list[_PendingOrder]],
        key: str,
        last_bar: Candle,
        positions: dict[str, Position],
        trades: list[Trade],
        open_lots: dict[str, list[_Lot]],
    ) -> list[Fill]:
        """MARKET orders fill at the session's last close; day orders expire."""
        market = [p for p in pending_orders[key] if p.request.order_type == OrderType.MARKET]
        expired = [p for p in pending_orders[key] if p not in market and not p.filled]
        for p in expired:
            self.audit_repo.log(
                "engine",
                "order_expired_at_session_end",
                run_id=self.run_id,
                payload={"order_id": p.order_id, "instrument": key},
            )
        pending_orders[key] = market
        close = last_bar.close
        settle_bar = last_bar.model_copy(update={"open": close, "high": close, "low": close})
        return self._fill_pending(market, settle_bar, positions, trades, open_lots)

    def _fill_pending(
        self,
        pending: list[_PendingOrder],
        candle: Candle,
        positions: dict[str, Position],
        trades: list[Trade],
        open_lots: dict[str, list[_Lot]],
    ) -> list[Fill]:
        fills: list[Fill] = []
        for p in pending:
            if p.filled:
                continue
            decision = self.fill_model.evaluate(
                request=p.request,
                candle=candle,
                already_filled=p.filled_qty,
                triggered=p.triggered,
                last_close=candle.close,
            )
            if decision is None:
                continue
            if decision.quantity == 0:
                p.triggered = True
                continue
            p.filled_qty += decision.quantity
            p.triggered = decision.triggered
            fill = Fill(
                order_id=p.order_id,
                trade_id=f"trd_b{p.order_id[-6:]}_{p.fill_seq:02d}",
                instrument=p.request.instrument,
                side=p.request.side,
                quantity=decision.quantity,
                price=decision.fill_price,
                timestamp=candle.timestamp,
                fees=decision.fees,
                raw={"intent_id": p.request.intent_id, "tag": p.request.tag},
            )
            p.fill_seq += 1
            fills.append(fill)
            self._apply_fill_position(fill, positions, trades, open_lots, candle.timestamp)
            if p.filled_qty >= p.request.quantity:
                p.filled = True
        return fills

    def _apply_fill_position(
        self,
        fill: Fill,
        positions: dict[str, Position],
        trades: list[Trade],
        open_lots: dict[str, list[_Lot]],
        ts: datetime,
    ) -> None:
        key = fill.instrument.key
        pos = positions.setdefault(key, Position(instrument=fill.instrument))
        signed = fill.quantity if fill.side == Side.BUY else -fill.quantity
        lots = open_lots[key]
        remaining = abs(signed)
        # Close opposite-sign lots first.
        while (
            remaining > 0
            and lots
            and ((signed > 0 and lots[0].quantity < 0) or (signed < 0 and lots[0].quantity > 0))
        ):
            lot = lots[0]
            close_qty = min(remaining, abs(lot.quantity))
            direction = 1 if lot.quantity > 0 else -1
            pnl = (fill.price - lot.entry_price) * close_qty * direction
            trades.append(
                Trade(
                    instrument_key=key,
                    side="LONG" if direction > 0 else "SHORT",
                    entry_time=lot.entry_time,
                    exit_time=ts,
                    entry_price=lot.entry_price,
                    exit_price=fill.price,
                    quantity=close_qty,
                    pnl=pnl
                    - (fill.fees * (close_qty / max(fill.quantity, 1)))
                    - lot.fees_remaining,
                    fees=fill.fees * (close_qty / max(fill.quantity, 1)) + lot.fees_remaining,
                    bars_held=0,
                    reason_exit=(fill.raw or {}).get("tag", ""),
                )
            )
            lot.quantity += close_qty * direction * -1  # close
            lot.fees_remaining = 0.0
            if lot.quantity == 0:
                lots.pop(0)
            remaining -= close_qty
        # Anything left opens a new lot.
        if remaining > 0:
            lots.append(
                _Lot(
                    quantity=remaining if signed > 0 else -remaining,
                    entry_price=fill.price,
                    entry_time=ts,
                    fees_remaining=fill.fees * (remaining / max(fill.quantity, 1)),
                )
            )
        pos.apply_fill(fill)

    @staticmethod
    def _apply_fill_cash(cash: float, fill: Fill) -> float:
        notional = fill.price * fill.quantity
        if fill.side == Side.BUY:
            cash -= notional
        else:
            cash += notional
        cash -= fill.fees
        return cash


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


@dataclass
class _PendingOrder:
    order_id: str
    request: Any
    placed_ts: datetime
    triggered: bool = False
    filled_qty: int = 0
    filled: bool = False
    fill_seq: int = 0


@dataclass
class _Lot:
    quantity: int  # signed
    entry_price: float
    entry_time: datetime
    fees_remaining: float = 0.0


def _mark_equity(
    cash: float,
    positions: dict[str, Position],
    latest_prices: dict[str, float],
) -> float:
    return cash + sum(
        positions[key].quantity * latest_prices.get(key, positions[key].avg_price)
        for key in positions
    )


class _NoOpRouterBroker(IBroker):
    """A do-nothing broker used by the OrderRouter; the backtest engine handles
    fills via its own pending order book to gain access to next-candle fills.
    """

    mode = "BACKTEST"

    def place_order(self, request: Any) -> OrderUpdate:
        return OrderUpdate(
            order_id="noop",
            status=OrderStatus.OPEN,
            pending_quantity=request.quantity,
            raw={"intent_id": request.intent_id},
        )

    def cancel_order(self, *_: Any, **__: Any) -> OrderUpdate:  # pragma: no cover
        raise NotImplementedError

    def modify_order(self, *_: Any, **__: Any) -> OrderUpdate:  # pragma: no cover
        raise NotImplementedError

    def orders(self) -> list[OrderUpdate]:  # pragma: no cover
        return []

    def order_history(self, *_: Any, **__: Any) -> list[OrderUpdate]:  # pragma: no cover
        return []

    def trades(self, *_: Any, **__: Any) -> list[Fill]:  # pragma: no cover
        return []

    def positions(self) -> dict[str, Position]:  # pragma: no cover
        return {}

    def holdings(self) -> dict[str, Position]:  # pragma: no cover
        return {}

    def quote_ltp(self, instruments: Iterable[Instrument]) -> dict[str, float]:  # pragma: no cover
        return {}


__all__ = ["BacktestEngine", "BacktestResult"]
