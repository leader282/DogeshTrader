"""LivePaperEngine behavior tests."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from dogeshtrader.config import (
    AppConfig,
    CostsConfig,
    InstrumentRef,
    RiskConfig,
    SlippageConfig,
    StrategyConfig,
)
from dogeshtrader.core.enums import Exchange, Mode, OrderType, Product, Side
from dogeshtrader.core.models import Instrument, OrderIntent, StrategyContext, Tick
from dogeshtrader.simulation.live_paper_engine import LivePaperEngine
from dogeshtrader.storage import Database, run_migrations
from dogeshtrader.strategy.base import Strategy


def _test_config(tmp_path: Path) -> tuple[AppConfig, RiskConfig, StrategyConfig, Instrument]:
    inst = Instrument(
        exchange=Exchange.NSE,
        tradingsymbol="INFY",
        instrument_token=408065,
        tick_size=0.05,
        lot_size=1,
    )
    app_cfg = AppConfig(
        data_dir=tmp_path,
        db_path=tmp_path / "paper.sqlite",
        reports_dir=tmp_path / "reports",
        costs=CostsConfig(model="bps_only", brokerage_per_order=0, bps_fallback=0),
        slippage=SlippageConfig(model="fixed_bps", bps=0, ticks=0),
    )
    risk_cfg = RiskConfig(
        max_order_quantity=100,
        max_order_notional=1_000_000,
        max_position_notional=1_000_000,
        max_daily_loss=100_000,
        max_open_orders=100,
        require_market_protection=True,
        default_market_protection_pct=1.0,
        max_orders_per_minute=10_000,
    )
    strategy_cfg = StrategyConfig(
        strategy="test",
        strategy_id="paper_ctx",
        initial_cash=100_000,
        instruments=[InstrumentRef(exchange="NSE", tradingsymbol="INFY")],
        interval="tick",
        params={},
    )
    return app_cfg, risk_cfg, strategy_cfg, inst


class _BuyThenObserveStrategy(Strategy):
    def __init__(self, instrument: Instrument) -> None:
        super().__init__({})
        self.instrument = instrument
        self.seen_qty: list[int] = []

    def on_candle(self, ctx, candle):  # pragma: no cover - tick-only test
        return []

    def on_tick(self, ctx: StrategyContext, tick: Tick):
        self.seen_qty.append(ctx.position(self.instrument).quantity)
        if len(self.seen_qty) == 1:
            return [
                OrderIntent(
                    strategy_id=ctx.strategy_id,
                    instrument=self.instrument,
                    side=Side.BUY,
                    quantity=1,
                    order_type=OrderType.MARKET,
                    product=Product.MIS,
                    market_protection_pct=1.0,
                    reason="unit_buy",
                )
            ]
        return []


class _NoopStrategy(Strategy):
    def on_candle(self, ctx, candle):
        return []


class _LiveBrokerWithoutFeed:
    """Minimal broker used to verify LIVE mode doesn't require feed_price/feed_candle."""

    mode = "LIVE"

    def __init__(self) -> None:
        self._order_update_cbs = []

    def on_order_update(self, callback):
        self._order_update_cbs.append(callback)

    def place_order(self, request):
        raise AssertionError("no orders expected in this test")

    def cancel_order(self, order_id, variety=None):
        raise NotImplementedError

    def modify_order(self, order_id, **kwargs):
        raise NotImplementedError

    def orders(self):
        return []

    def order_history(self, order_id):
        return []

    def trades(self, order_id=None):
        return []

    def positions(self):
        return {}

    def holdings(self):
        return {}

    def quote_ltp(self, instruments):
        return {}


def test_paper_engine_syncs_positions_into_strategy_context(tmp_path: Path) -> None:
    app_cfg, risk_cfg, strategy_cfg, inst = _test_config(tmp_path)
    db = Database(":memory:", in_memory=True)
    run_migrations(db)

    strat = _BuyThenObserveStrategy(inst)
    engine = LivePaperEngine(
        strategy=strat,
        instruments=[inst],
        strategy_cfg=strategy_cfg,
        app_cfg=app_cfg,
        risk_cfg=risk_cfg,
        db=db,
        mode=Mode.PAPER,
    )

    engine.push_tick(
        Tick(timestamp=datetime(2024, 1, 1, 9, 30), instrument_token=408065, ltp=1500.0)
    )
    engine.push_tick(
        Tick(timestamp=datetime(2024, 1, 1, 9, 31), instrument_token=408065, ltp=1501.0)
    )
    engine.run(max_events=2)

    # First tick observes flat position, second tick sees the filled buy.
    assert strat.seen_qty[:2] == [0, 1]


def test_live_mode_tolerates_broker_without_feed_methods(tmp_path: Path) -> None:
    app_cfg, risk_cfg, strategy_cfg, inst = _test_config(tmp_path)
    db = Database(":memory:", in_memory=True)
    run_migrations(db)

    engine = LivePaperEngine(
        strategy=_NoopStrategy({}),
        instruments=[inst],
        strategy_cfg=strategy_cfg,
        app_cfg=app_cfg,
        risk_cfg=risk_cfg,
        db=db,
        mode=Mode.LIVE,
    )
    engine.set_broker(_LiveBrokerWithoutFeed())
    engine.push_tick(
        Tick(timestamp=datetime(2024, 1, 1, 9, 30), instrument_token=408065, ltp=1500.0)
    )
    # Should not raise even though the broker has no feed_price/feed_candle.
    engine.run(max_events=1)
