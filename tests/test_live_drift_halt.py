"""LIVE mode halts when the broker holds a position the engine did not open."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from dogeshtrader.config import AppConfig, InstrumentRef, RiskConfig, StrategyConfig
from dogeshtrader.core.enums import Exchange, Mode
from dogeshtrader.core.models import (
    Candle,
    Instrument,
    OrderIntent,
    Position,
    StrategyContext,
    Tick,
)
from dogeshtrader.risk.kill_switch import KillSwitch, kill_switch_path
from dogeshtrader.simulation.live_paper_engine import LivePaperEngine
from dogeshtrader.storage import Database, run_migrations
from dogeshtrader.strategy.base import Strategy

INFY = Instrument(exchange=Exchange.NSE, tradingsymbol="INFY", instrument_token=408065)


class _Noop(Strategy):
    def on_candle(self, ctx: StrategyContext, candle: Candle) -> list[OrderIntent]:
        return []


class _BrokerWithPositions:
    mode = "LIVE"

    def __init__(self, positions: dict[str, Position]) -> None:
        self._positions = positions

    def on_order_update(self, callback) -> None:
        pass

    def positions(self) -> dict[str, Position]:
        return self._positions

    def place_order(self, request):  # pragma: no cover - must never be called
        raise AssertionError("no orders expected")


def _engine(tmp_path: Path, broker: _BrokerWithPositions) -> LivePaperEngine:
    db = Database(":memory:", in_memory=True)
    run_migrations(db)
    return LivePaperEngine(
        strategy=_Noop({}),
        instruments=[INFY],
        strategy_cfg=StrategyConfig(
            strategy="noop",
            strategy_id="drift",
            instruments=[InstrumentRef(exchange="NSE", tradingsymbol="INFY")],
            interval="tick",
        ),
        app_cfg=AppConfig(data_dir=tmp_path, db_path=tmp_path / "x.sqlite"),
        risk_cfg=RiskConfig(),
        db=db,
        mode=Mode.LIVE,
        broker=broker,  # type: ignore[arg-type]
    )


def _tick() -> Tick:
    return Tick(timestamp=datetime(2024, 1, 1, 10, 0), instrument_token=408065, ltp=1500.0)


def test_unexpected_broker_position_engages_kill_switch(tmp_path: Path) -> None:
    broker = _BrokerWithPositions({INFY.key: Position(instrument=INFY, quantity=7)})
    engine = _engine(tmp_path, broker)
    engine.push_tick(_tick())
    engine.run(max_events=1)

    kill = KillSwitch(kill_switch_path(tmp_path))
    assert kill.is_engaged()
    assert "NSE:INFY local=0 broker=7" in kill.reason()
    assert engine.risk.breaker.states["unexpected_position"].tripped


def test_matching_broker_positions_leave_trading_enabled(tmp_path: Path) -> None:
    engine = _engine(tmp_path, _BrokerWithPositions({}))
    engine.push_tick(_tick())
    engine.run(max_events=1)

    assert not KillSwitch(kill_switch_path(tmp_path)).is_engaged()
