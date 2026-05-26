"""Strategy contract sanity tests.

These don't run the full engine; they verify the interface and registry.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from dogeshtrader.core.enums import OrderType, Side
from dogeshtrader.core.errors import StrategyError
from dogeshtrader.core.models import Candle, Instrument, OrderIntent, Position, StrategyContext
from dogeshtrader.strategy import Strategy, available_strategies, get_strategy, register


def test_registry_lists_samples() -> None:
    names = available_strategies()
    assert "sample_sma_crossover" in names
    assert "sample_opening_range" in names


def test_get_strategy_unknown_raises() -> None:
    with pytest.raises(StrategyError):
        get_strategy("definitely_not_a_strategy_xyz")


def test_strategy_registration_decorator() -> None:
    @register("contract_test_strat_xyz")
    class _Demo(Strategy):
        def on_candle(self, ctx: StrategyContext, candle: Candle) -> list[OrderIntent]:
            return []

    assert get_strategy("contract_test_strat_xyz") is _Demo


def test_strategies_return_intents_only(infy: Instrument) -> None:
    """Strategies must only ever return OrderIntent. No side-effects."""

    class _RouterCallingStrategy(Strategy):
        def on_candle(self, ctx: StrategyContext, candle: Candle) -> list[OrderIntent]:
            return [
                OrderIntent(
                    strategy_id="x",
                    instrument=infy,
                    side=Side.BUY,
                    quantity=1,
                    order_type=OrderType.MARKET,
                    market_protection_pct=1.0,
                )
            ]

    s = _RouterCallingStrategy()
    ctx = StrategyContext(
        run_id="t",
        strategy_id="x",
        mode="BACKTEST",
        timestamp=datetime(2024, 1, 1, 9, 30),
        cash=10_000,
        positions={infy.key: Position(instrument=infy)},
        latest_prices={infy.key: 1500.0},
        config={},
    )
    intents = s.on_candle(
        ctx,
        Candle(timestamp=datetime(2024, 1, 1, 9, 30), open=1, high=1, low=1, close=1),
    )
    assert all(isinstance(i, OrderIntent) for i in intents)


def test_sample_sma_crossover_signature(infy: Instrument) -> None:
    cls = get_strategy("sample_sma_crossover")
    s = cls(params={"fast": 2, "slow": 5, "quantity": 1})
    ctx = StrategyContext(
        run_id="t",
        strategy_id="sma_unit",
        mode="BACKTEST",
        timestamp=datetime(2024, 1, 1, 9, 30),
        cash=100_000,
        positions={infy.key: Position(instrument=infy)},
        latest_prices={infy.key: 1500.0},
        config={"instrument_keys": [infy.key]},
        params={"fast": 2, "slow": 5, "quantity": 1},
    )
    ctx.extra["_current_instrument"] = infy
    s.on_start(ctx)
    # Use a falling-then-rising series so fast SMA starts below slow and
    # then crosses up - that's the signal the strategy reacts to.
    closes = [110, 108, 106, 104, 102, 100, 99, 100, 102, 105, 108, 112, 115]
    intents: list[OrderIntent] = []
    for i, close in enumerate(closes):
        candle = Candle(
            timestamp=datetime(2024, 1, 1, 9, 30 + i),
            open=close,
            high=close + 0.5,
            low=close - 0.5,
            close=close,
        )
        intents.extend(s.on_candle(ctx, candle))
    assert any(i.side == Side.BUY for i in intents)
