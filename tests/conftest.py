"""Pytest helpers shared across the suite."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime

import pytest

from dogeshtrader.config import RiskConfig
from dogeshtrader.core.enums import Exchange
from dogeshtrader.core.models import Candle, Instrument, Position, StrategyContext
from dogeshtrader.storage import Database, run_migrations


@pytest.fixture()
def memory_db() -> Database:
    db = Database(":memory:", in_memory=True)
    run_migrations(db)
    return db


@pytest.fixture()
def infy() -> Instrument:
    return Instrument(
        exchange=Exchange.NSE,
        tradingsymbol="INFY",
        instrument_token=408065,
        tick_size=0.05,
        lot_size=1,
    )


@pytest.fixture()
def risk_config() -> RiskConfig:
    return RiskConfig(
        max_order_quantity=100,
        max_order_notional=100_000,
        max_position_notional=250_000,
        max_daily_loss=5_000,
        max_open_orders=10,
        require_market_protection=True,
        default_market_protection_pct=1.0,
        max_orders_per_minute=600,
    )


def make_context(instrument: Instrument, last_price: float = 1500.0) -> StrategyContext:
    return StrategyContext(
        run_id="test",
        strategy_id="unit_test",
        mode="BACKTEST",
        timestamp=datetime(2024, 1, 2, 10, 0),
        cash=1_000_000,
        positions={instrument.key: Position(instrument=instrument)},
        latest_prices={instrument.key: last_price},
        config={"instrument_keys": [instrument.key]},
        params={},
        storage=None,
    )


def make_candles(
    start: datetime,
    closes: Iterable[float],
    *,
    minute_step: int = 5,
) -> list[Candle]:
    out: list[Candle] = []
    ts = start
    for close in closes:
        out.append(
            Candle(
                timestamp=ts,
                open=close,
                high=close + 0.5,
                low=close - 0.5,
                close=close,
                volume=1000,
            )
        )
        ts = ts.replace(minute=ts.minute + minute_step) if ts.minute + minute_step < 60 else ts
    return out


__all__ = ["make_candles", "make_context"]
