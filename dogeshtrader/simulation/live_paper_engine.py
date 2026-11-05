"""Live-paper engine.

A unified engine that:
* sources ticks (live WebSocket) or candles (replay) from any iterator
* runs the same Strategy interface as backtest / live
* uses a PaperBroker that matches against the latest fed price
* answers the question: "what would my orders have done in the market?"

The engine is intentionally synchronous - data callbacks push events onto an
internal queue and the main loop drains it. Live WebSocket integration is
plugged in by the CLI/Kite adapter, not by this module, so we can unit test
without a Kite session.
"""

from __future__ import annotations

import queue
import threading
import time
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from dogeshtrader.brokers.paper_broker import PaperBroker, PaperConfig
from dogeshtrader.config import AppConfig, RiskConfig, StrategyConfig
from dogeshtrader.constants import RUN_ID_PREFIX
from dogeshtrader.core.enums import Mode
from dogeshtrader.core.interfaces import IBroker
from dogeshtrader.core.models import (
    Candle,
    Instrument,
    OrderIntent,
    OrderRequest,
    OrderUpdate,
    Position,
    StrategyContext,
    Tick,
)
from dogeshtrader.execution.idempotency import IdempotencyGuard
from dogeshtrader.execution.order_router import OrderRouter
from dogeshtrader.execution.rate_limiter import RateLimiter
from dogeshtrader.execution.reconciliation import position_drifts
from dogeshtrader.logging_config import get_logger
from dogeshtrader.operator.state import OperatorBook, RunStatus, publish_run, state_path
from dogeshtrader.risk.flatten import flatten_intents
from dogeshtrader.risk.kill_switch import KillSwitch, kill_switch_path
from dogeshtrader.risk.risk_engine import RiskEngine
from dogeshtrader.simulation.latency import LatencyModel
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
class _Event:
    kind: str  # "tick" | "candle" | "stop"
    payload: Any = None


class LivePaperEngine:
    """Single-threaded live-paper engine.

    Data sources push events with :meth:`push_tick` / :meth:`push_candle`. Run
    the main loop with :meth:`run`.
    """

    def __init__(
        self,
        *,
        strategy: Strategy,
        instruments: list[Instrument],
        strategy_cfg: StrategyConfig,
        app_cfg: AppConfig,
        risk_cfg: RiskConfig,
        mode: Mode = Mode.PAPER,
        db: Database | None = None,
        latency: LatencyModel | None = None,
        broker: IBroker | None = None,
        live_confirmation: bool = False,
    ) -> None:
        self.strategy = strategy
        self.instruments = instruments
        self.strategy_cfg = strategy_cfg
        self.app_cfg = app_cfg
        self.risk_cfg = risk_cfg
        self.mode = mode
        self.latency = latency or LatencyModel()
        self.run_id = f"{RUN_ID_PREFIX}paper_{datetime.now():%Y%m%d_%H%M%S}_{uuid.uuid4().hex[:6]}"
        self._queue: queue.Queue[_Event] = queue.Queue()
        self._stop = threading.Event()

        # Storage
        self.db = db or Database(app_cfg.db_path)
        run_migrations(self.db)
        self.run_repo = RunRepository(self.db)
        self.order_repo = OrderRepository(self.db)
        self.fill_repo = FillRepository(self.db)
        self.idem_repo = IdempotencyRepository(self.db)
        self.audit_repo = AuditRepository(self.db)

        # Broker + risk + router
        self.broker: Any = broker or PaperBroker(
            config=PaperConfig(
                starting_cash=strategy_cfg.initial_cash,
                fill_market_on_next_candle_open=False,
                optimistic_limits=False,
                fee_per_order=app_cfg.costs.brokerage_per_order,
                bps_cost=app_cfg.costs.bps_fallback,
            )
        )
        self.kill_switch = KillSwitch(kill_switch_path(app_cfg.data_dir))
        self.operator = OperatorBook(state_path(app_cfg.data_dir))
        self.risk = RiskEngine(
            risk_cfg,
            tag_prefix=app_cfg.order_tag_prefix,
            enforce_market_hours=mode == Mode.LIVE,
            kill_switch=self.kill_switch,
            market_interval=strategy_cfg.interval,
        )
        self.router = OrderRouter(
            mode=self.mode,
            broker=self.broker,
            risk=self.risk,
            idempotency=IdempotencyGuard(self.idem_repo, self.run_id),
            rate_limiter=RateLimiter(
                rate_per_sec=app_cfg.order_rate_per_second,
                burst=app_cfg.order_rate_burst,
            ),
            live_trading_enabled=app_cfg.live_trading_enabled,
            live_confirmation=live_confirmation,
        )

        self._request_by_intent: dict[str, OrderRequest] = {}
        self.set_broker(self.broker)

        self._instrument_by_token: dict[int, Instrument] = {
            i.instrument_token: i for i in instruments if i.instrument_token
        }
        self._instrument_by_key: dict[str, Instrument] = {i.key: i for i in instruments}
        self._latest_prices: dict[str, float] = {}
        self._positions: dict[str, Position] = {i.key: Position(instrument=i) for i in instruments}
        self._tracked: dict[str, Position] = {i.key: Position(instrument=i) for i in instruments}
        self._ctx: StrategyContext | None = None
        self._events_since_reconcile = 0
        #: Seconds to wait for an event before publishing a heartbeat snapshot.
        self.poll_seconds = 1.0

    def set_broker(self, broker: Any) -> None:
        """Swap broker at runtime and wire callbacks.

        Used by LIVE mode, which reuses this engine but swaps ``PaperBroker``
        for ``KiteBroker``.
        """
        self.broker = broker
        if hasattr(self.broker, "on_order_update"):
            self.broker.on_order_update(self._handle_order_update)
        if hasattr(self.broker, "on_fill"):
            self.broker.on_fill(self._handle_fill)

    # ------------------------------------------------------------------
    # Public data ingestion
    # ------------------------------------------------------------------

    def push_tick(self, tick: Tick) -> None:
        self._queue.put(_Event("tick", tick))

    def push_candle(self, instrument: Instrument, candle: Candle) -> None:
        self._queue.put(_Event("candle", (instrument, candle)))

    def push_order_update(self, update: OrderUpdate) -> None:
        """Inject an order update from an external broker/websocket stream."""
        self._handle_order_update(update)

    def stop(self) -> None:
        self._stop.set()
        self._queue.put(_Event("stop"))

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    def run(self, *, max_events: int | None = None) -> None:
        """Drain the queue until ``stop()`` or ``max_events`` is reached."""
        self.run_repo.create(
            self.run_id,
            mode=self.mode.value,
            strategy=self.strategy_cfg.strategy,
            strategy_id=self.strategy_cfg.strategy_id,
        )
        ctx = StrategyContext(
            run_id=self.run_id,
            strategy_id=self.strategy_cfg.strategy_id,
            mode=self.mode.value,
            timestamp=datetime.now(),
            cash=self.strategy_cfg.initial_cash,
            positions=self._positions,
            latest_prices=self._latest_prices,
            config={
                "instrument_keys": [i.key for i in self.instruments],
                "session_start": self.risk_cfg.session_start,
                "session_end": self.risk_cfg.session_end,
                "interval": self.strategy_cfg.interval,
            },
            params=self.strategy_cfg.params,
            storage=self.db,
        )
        if self.mode == Mode.LIVE:
            self._halt_if_broker_drift()
        self._sync_context_from_broker(ctx, refresh_positions=True)
        self._ctx = ctx
        self.strategy.on_start(ctx)
        processed = 0

        self._publish(ctx, force=True)
        while not self._stop.is_set():
            try:
                event = self._queue.get(timeout=self.poll_seconds)
            except queue.Empty:
                # Heartbeat: a quiet market still refreshes the snapshot, so a
                # stale page means the engine stopped, not that no ticks came.
                self._publish(ctx)
                continue
            if event.kind == "stop":
                break
            try:
                self._dispatch(event, ctx)
            except Exception as e:
                _log.error("paper_loop_error", error=str(e), kind=event.kind)
                self.audit_repo.log(
                    "engine",
                    "paper_loop_error",
                    run_id=self.run_id,
                    payload={"error": str(e), "kind": event.kind},
                )
            processed += 1
            if max_events is not None and processed >= max_events:
                break

        end_intents = self.strategy.on_end(ctx)
        if end_intents:
            self._route(end_intents, ctx)

        self.run_repo.complete(self.run_id)
        self.audit_repo.log("engine", "paper_completed", run_id=self.run_id)
        self._publish(ctx, status="stopped", force=True)

    # ------------------------------------------------------------------
    # Dispatch
    # ------------------------------------------------------------------

    def _dispatch(self, event: _Event, ctx: StrategyContext) -> None:
        if event.kind == "tick":
            self._on_tick(event.payload, ctx)
        elif event.kind == "candle":
            instrument, candle = event.payload
            self._on_candle(instrument, candle, ctx)

    def _on_tick(self, tick: Tick, ctx: StrategyContext) -> None:
        instrument = self._instrument_by_token.get(tick.instrument_token)
        if instrument is None:
            return
        self._latest_prices[instrument.key] = tick.ltp
        ctx.latest_prices = self._latest_prices
        ctx.timestamp = tick.timestamp
        self.risk.breaker.on_tick(tick.timestamp)
        # Apply latency: delay placement decisions by a sampled latency.
        delay_ms = self.latency.sample_placement_ms()
        if delay_ms > 0:
            time.sleep(delay_ms / 1000.0)
        # Feed price into broker so working orders can match (paper mode).
        if hasattr(self.broker, "feed_price"):
            self.broker.feed_price(instrument, tick.ltp, tick.timestamp)
            self._sync_context_from_broker(ctx, refresh_positions=True)
        self._mark_risk(ctx)
        self._maybe_reconcile_positions()
        if self.kill_switch.is_engaged():
            intents = flatten_intents(ctx)
        else:
            intents = self.strategy.on_tick(ctx, tick)
        if intents:
            self._route(intents, ctx)
        self._publish(ctx)

    def _on_candle(self, instrument: Instrument, candle: Candle, ctx: StrategyContext) -> None:
        self._latest_prices[instrument.key] = candle.close
        ctx.latest_prices = self._latest_prices
        ctx.timestamp = candle.timestamp
        ctx.extra["_current_instrument"] = instrument
        if hasattr(self.broker, "feed_candle"):
            self.broker.feed_candle(instrument, candle)
            self._sync_context_from_broker(ctx, refresh_positions=True)
        self.risk.note_candle(instrument.key, candle)
        self._mark_risk(ctx)
        self._maybe_reconcile_positions()
        if self.kill_switch.is_engaged():
            intents = flatten_intents(ctx)
        else:
            intents = self.strategy.on_candle(ctx, candle)
        if intents:
            self._route(intents, ctx)
        self._publish(ctx)

    def _route(self, intents: Iterable[OrderIntent], ctx: StrategyContext) -> None:
        result = self.router.route(intents, ctx)
        for request in result.requests:
            self._request_by_intent[request.intent_id] = request
            self.order_repo.record_intent(
                intent_id=request.intent_id,
                run_id=self.run_id,
                request=request,
                reason=str(request.metadata.get("reason", "")),
            )

        for upd in result.accepted:
            intent_id = (upd.raw or {}).get("intent_id")
            req = self._request_by_intent.get(intent_id) if intent_id else None
            if req is not None:
                self.order_repo.record_order(
                    run_id=self.run_id,
                    broker="paper" if self.mode == Mode.PAPER else "kite",
                    mode=self.mode.value,
                    request=req,
                    update=upd,
                )
            else:
                self.order_repo.update_status(upd)

        for upd in result.accepted:
            self.audit_repo.log(
                "router",
                "paper_accepted",
                run_id=self.run_id,
                payload={"order_id": upd.order_id, "status": upd.status.value},
            )
        for intent, msg in result.risk_rejected:
            self.audit_repo.log(
                "risk",
                "rejected",
                run_id=self.run_id,
                payload={"instrument": intent.instrument.key, "reason": msg},
            )

    # ------------------------------------------------------------------
    # Broker callbacks
    # ------------------------------------------------------------------

    def _handle_order_update(self, update: OrderUpdate) -> None:
        if not self._ctx:
            return
        # Persist update; this also records the order if it doesn't exist yet.
        intent_id = (update.raw or {}).get("intent_id")
        request = None
        if hasattr(self.broker, "request_for_intent"):
            request = self.broker.request_for_intent(intent_id)
        if request is None and intent_id:
            request = self._request_by_intent.get(intent_id)

        if request is None:
            self.order_repo.update_status(update)
        else:
            self._request_by_intent[request.intent_id] = request
            self.order_repo.record_order(
                run_id=self.run_id,
                broker="paper" if self.mode == Mode.PAPER else "kite",
                mode=self.mode.value,
                request=request,
                update=update,
            )
        self.risk.on_order_update(update)
        self._sync_context_from_broker(self._ctx, refresh_positions=True)
        follow_up_intents = self.strategy.on_order_update(self._ctx, update)
        if follow_up_intents:
            self._route(follow_up_intents, self._ctx)

    def _handle_fill(self, fill: Any) -> None:
        self.fill_repo.add(self.run_id, fill)
        self.risk.on_fill(fill)
        tracked = self._tracked.setdefault(
            fill.instrument.key, Position(instrument=fill.instrument)
        )
        tracked.apply_fill(fill)
        if self._ctx is not None:
            self._sync_context_from_broker(self._ctx, refresh_positions=True)

    def _mark_risk(self, ctx: StrategyContext) -> None:
        equity = ctx.cash
        for key, pos in ctx.positions.items():
            price = ctx.latest_prices.get(key) or pos.last_price or 0.0
            equity += pos.quantity * price
        self._last_equity = equity
        self.risk.observe_equity(ctx.timestamp, equity)
        self.risk.update_realised_pnl(sum(pos.realised_pnl for pos in ctx.positions.values()))

    def _publish(
        self,
        ctx: StrategyContext,
        *,
        status: RunStatus = "running",
        force: bool = False,
    ) -> None:
        try:
            publish_run(
                self.operator,
                ctx,
                run_id=self.run_id,
                mode=self.mode.value,
                equity=getattr(self, "_last_equity", ctx.cash),
                risk=self.risk,
                kill_switch=self.kill_switch,
                status=status,
                force=force,
            )
        except Exception as e:
            _log.warning("operator_snapshot_failed", error=str(e))

    def _maybe_reconcile_positions(self) -> None:
        if self.mode != Mode.LIVE:
            return
        self._events_since_reconcile += 1
        if self._events_since_reconcile < 20:
            return
        self._events_since_reconcile = 0
        self._halt_if_broker_drift()

    def _halt_if_broker_drift(self) -> None:
        if not hasattr(self.broker, "positions"):
            return
        try:
            broker_positions = self.broker.positions()
        except Exception as e:
            _log.warning("position_reconcile_failed", error=str(e))
            return
        drifts = position_drifts(
            self._tracked,
            broker_positions,
            universe=set(self._instrument_by_key),
        )
        if not drifts:
            return
        detail = "; ".join(
            f"{item.instrument_key} local={item.local_quantity} broker={item.broker_quantity}"
            for item in drifts
        )
        self.kill_switch.engage(f"unexpected broker position: {detail}")
        self.risk.breaker.trip("unexpected_position", detail)
        self.audit_repo.log(
            "reconcile",
            "unexpected_position",
            run_id=self.run_id,
            payload={"detail": detail},
        )

    def _sync_context_from_broker(self, ctx: StrategyContext, *, refresh_positions: bool) -> None:
        if hasattr(self.broker, "cash"):
            try:
                ctx.cash = float(self.broker.cash)
            except (TypeError, ValueError):
                pass
        if refresh_positions and hasattr(self.broker, "positions"):
            try:
                ctx.positions = self.broker.positions()
            except Exception:
                pass


__all__ = ["LivePaperEngine"]
