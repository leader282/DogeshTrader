"""Percent-of-capital limits, stale data, and the kill switch."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import pytest

from dogeshtrader.config import RiskConfig
from dogeshtrader.core.enums import OrderType, Side
from dogeshtrader.core.errors import RiskRejected
from dogeshtrader.core.models import Candle, Instrument, OrderIntent, Position
from dogeshtrader.risk.kill_switch import KillSwitch
from dogeshtrader.risk.risk_engine import RiskEngine

from .conftest import make_context


def _buy(instrument: Instrument, **overrides) -> OrderIntent:
    payload = dict(
        strategy_id="t",
        instrument=instrument,
        side=Side.BUY,
        quantity=100,
        order_type=OrderType.MARKET,
        market_protection_pct=1.0,
        reason="entry",
        metadata={"stop_price": 90.0},
    )
    payload.update(overrides)
    return OrderIntent(**payload)


def _policy(**overrides) -> RiskConfig:
    base = dict(
        max_order_quantity=100,
        max_order_notional=1_000_000,
        max_position_notional=1_000_000,
        max_daily_loss=1_000_000,
        max_open_orders=10,
        require_market_protection=True,
        max_orders_per_minute=600,
        risk_per_trade_pct=0.25,
        daily_loss_pct=0.75,
        monthly_loss_pct=5.0,
        max_concurrent_positions=2,
        max_trades_per_day=3,
        block_entries_on_bad_data=True,
        stale_candle_multiple=2.0,
    )
    base.update(overrides)
    return RiskConfig(**base)


def test_sizes_entry_to_risk_budget(infy: Instrument) -> None:
    risk = RiskEngine(_policy(), enforce_market_hours=False)
    ctx = make_context(infy, last_price=100.0)
    ctx.cash = 100_000
    request = risk.check(_buy(infy), ctx)
    # 0.25% of 100_000 = ₹250. Stop is ₹10 away, so 25 shares.
    assert request.quantity == 25


def test_exit_is_not_clamped_to_the_order_notional_cap(infy: Instrument) -> None:
    risk = RiskEngine(
        _policy(max_order_notional=5_000, risk_per_trade_pct=0), enforce_market_hours=False
    )
    held = make_context(infy, last_price=1500.0)
    held.positions[infy.key].quantity = 4  # worth 6,000, above the 5,000 cap
    closing = risk.check(_buy(infy, side=Side.SELL, quantity=4, metadata={}), held)
    assert closing.quantity == 4

    # The same cap still shrinks a new entry.
    entry = risk.check(_buy(infy, quantity=50, metadata={}), make_context(infy, last_price=1500.0))
    assert entry.quantity == 3


def test_exit_may_exceed_the_max_order_quantity(infy: Instrument) -> None:
    risk = RiskEngine(
        _policy(max_order_quantity=100, risk_per_trade_pct=0), enforce_market_hours=False
    )
    ctx = make_context(infy)
    ctx.positions[infy.key].quantity = 150
    closing = risk.check(_buy(infy, side=Side.SELL, quantity=150, metadata={}), ctx)
    assert closing.quantity == 150
    with pytest.raises(RiskRejected) as exc:
        risk.check(_buy(infy, quantity=150, metadata={}), ctx)
    assert exc.value.rule == "max_order_quantity"


def _budget_policy(**overrides) -> RiskConfig:
    return _policy(
        max_orders_per_minute=2,
        risk_per_trade_pct=0,
        max_trades_per_day=0,
        max_concurrent_positions=0,
        **overrides,
    )


def test_order_budget_follows_market_time(infy: Instrument) -> None:
    risk = RiskEngine(_budget_policy(), enforce_market_hours=False)
    ctx = make_context(infy)

    def place() -> None:
        risk.check(_buy(infy, quantity=10, metadata={}), ctx)

    place()
    place()
    with pytest.raises(RiskRejected) as exc:
        place()
    assert exc.value.rule == "order_rate_per_minute"
    # A minute of market time later the budget is back, however fast the replay ran.
    ctx.timestamp += timedelta(minutes=1)
    place()


def test_live_order_budget_stays_on_the_wall_clock(infy: Instrument) -> None:
    risk = RiskEngine(_budget_policy(), enforce_market_hours=False)
    ctx = make_context(infy)
    ctx.mode = "LIVE"
    risk.check(_buy(infy, quantity=10, metadata={}), ctx)
    risk.check(_buy(infy, quantity=10, metadata={}), ctx)
    ctx.timestamp += timedelta(days=1)  # an exchange timestamp must not refill a live budget
    with pytest.raises(RiskRejected) as exc:
        risk.check(_buy(infy, quantity=10, metadata={}), ctx)
    assert exc.value.rule == "order_rate_per_minute"


def test_exit_is_not_refused_for_an_empty_order_budget(infy: Instrument) -> None:
    risk = RiskEngine(_budget_policy(), enforce_market_hours=False)
    ctx = make_context(infy)
    ctx.positions[infy.key].quantity = 10
    risk.check(_buy(infy, quantity=10, metadata={}), ctx)
    risk.check(_buy(infy, quantity=10, metadata={}), ctx)  # budget is now empty
    with pytest.raises(RiskRejected):
        risk.check(_buy(infy, quantity=10, metadata={}), ctx)
    closing = risk.check(_buy(infy, side=Side.SELL, quantity=10, metadata={}), ctx)
    assert closing.side == Side.SELL


def test_trade_cap_blocks_the_next_entry(infy: Instrument) -> None:
    risk = RiskEngine(
        _policy(max_trades_per_day=1, risk_per_trade_pct=0), enforce_market_hours=False
    )
    ctx = make_context(infy)
    risk.check(_buy(infy, metadata={}), ctx)
    with pytest.raises(RiskRejected) as exc:
        risk.check(_buy(infy, metadata={}), ctx)
    assert exc.value.rule == "max_trades_per_day"


def test_concurrent_positions_block_a_new_name(infy: Instrument) -> None:
    other = Instrument.equity("NSE", "TCS")
    risk = RiskEngine(
        _policy(max_concurrent_positions=1, risk_per_trade_pct=0), enforce_market_hours=False
    )
    ctx = make_context(infy)
    ctx.positions[infy.key].quantity = 5
    ctx.positions[other.key] = Position(instrument=other)
    with pytest.raises(RiskRejected) as exc:
        risk.check(_buy(other, metadata={}), ctx)
    assert exc.value.rule == "max_concurrent_positions"


def test_monthly_loss_blocks_entries_and_allows_exits(infy: Instrument) -> None:
    risk = RiskEngine(_policy(risk_per_trade_pct=0), enforce_market_hours=False)
    ctx = make_context(infy)
    risk.observe_equity(datetime(2024, 1, 2, 9, 20), 100_000)
    risk.observe_equity(datetime(2024, 1, 16, 10, 0), 94_000)
    assert risk.breaker.states["monthly_loss"].tripped
    with pytest.raises(RiskRejected) as exc:
        risk.check(_buy(infy, metadata={}), ctx)
    assert exc.value.rule == "circuit_breaker"
    ctx.positions[infy.key].quantity = 4
    exit_order = risk.check(
        _buy(infy, side=Side.SELL, quantity=4, metadata={}, reason="flatten"),
        ctx,
    )
    assert exit_order.quantity == 4


def test_stale_candle_blocks_entries_only(infy: Instrument) -> None:
    risk = RiskEngine(
        _policy(risk_per_trade_pct=0), enforce_market_hours=False, market_interval="5minute"
    )
    first = Candle(
        timestamp=datetime(2024, 1, 2, 10, 0), open=100, high=101, low=99, close=100, volume=10
    )
    gapped = Candle(
        timestamp=datetime(2024, 1, 2, 10, 30), open=100, high=101, low=99, close=100, volume=10
    )
    risk.note_candle(infy.key, first)
    risk.note_candle(infy.key, gapped)
    ctx = make_context(infy)
    with pytest.raises(RiskRejected) as exc:
        risk.check(_buy(infy, metadata={}), ctx)
    assert exc.value.rule == "stale_market_data"
    ctx.positions[infy.key].quantity = 2
    exit_order = risk.check(_buy(infy, side=Side.SELL, quantity=2, metadata={}), ctx)
    assert exit_order.side == Side.SELL


def test_kill_switch_blocks_entries(infy: Instrument, tmp_path: Path) -> None:
    switch = KillSwitch(tmp_path / "KILL")
    switch.engage("operator")
    risk = RiskEngine(_policy(risk_per_trade_pct=0), enforce_market_hours=False, kill_switch=switch)
    ctx = make_context(infy)
    with pytest.raises(RiskRejected) as exc:
        risk.check(_buy(infy, metadata={}), ctx)
    assert exc.value.rule == "circuit_breaker"
    ctx.positions[infy.key].quantity = 1
    exit_order = risk.check(_buy(infy, side=Side.SELL, quantity=1, metadata={}), ctx)
    assert exit_order.quantity == 1
    assert exit_order.side == Side.SELL
