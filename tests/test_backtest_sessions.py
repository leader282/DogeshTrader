"""Backtest behaviour at session boundaries and under the kill switch."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

from dogeshtrader.core.enums import OrderType, Product, Side
from dogeshtrader.core.models import Candle, Instrument, OrderIntent, StrategyContext
from dogeshtrader.runtime import StrategyRun, Workspace
from dogeshtrader.strategy.base import Strategy

from .conftest import SESSION_DAYS, WorkspaceFiles, build_workspace


def _load(files: WorkspaceFiles) -> StrategyRun:
    ws = Workspace.open(files.app, risk_config=files.risk)
    return StrategyRun.load(ws, files.strategy)


def test_session_end_exit_fills_same_day_at_last_close(workspace: WorkspaceFiles) -> None:
    result = _load(workspace).run_backtest(workspace.root / "out")

    assert result.trades, "the trending fixture should produce breakouts"
    for trade in result.trades:
        assert trade.exit_time.date() == trade.entry_time.date()
        assert trade.pnl > 0


class _LimitThatNeverFills(Strategy):
    def on_candle(self, ctx: StrategyContext, candle: Candle) -> list[OrderIntent]:
        inst: Instrument = ctx.extra["_current_instrument"]
        if candle.timestamp.hour == 15 and candle.timestamp.minute == 25:
            return [
                OrderIntent(
                    strategy_id=ctx.strategy_id,
                    instrument=inst,
                    side=Side.BUY,
                    quantity=1,
                    order_type=OrderType.LIMIT,
                    price=1.0,
                    product=Product.MIS,
                    reason="deep_limit",
                )
            ]
        return []


def test_day_orders_expire_at_session_end(workspace: WorkspaceFiles) -> None:
    run = _load(workspace)
    run.strategy = _LimitThatNeverFills({})
    result = run.run_backtest(workspace.root / "out")

    assert result.trades == []
    rows = run.workspace.db.fetchall(
        "SELECT COUNT(*) AS n FROM audit_log WHERE event = 'order_expired_at_session_end'"
    )
    assert rows[0]["n"] == len(SESSION_DAYS) - 1


class _RoundTripEachDay(Strategy):
    """Buy at the open, sell with 10 minutes left, every session."""

    def __init__(self, quantity: int) -> None:
        super().__init__({})
        self.quantity = quantity

    def on_candle(self, ctx: StrategyContext, candle: Candle) -> list[OrderIntent]:
        inst: Instrument = ctx.extra["_current_instrument"]
        held = ctx.position(inst).quantity
        clock = (candle.timestamp.hour, candle.timestamp.minute)
        if clock == (9, 15) and held == 0:
            side, quantity = Side.BUY, self.quantity
        elif clock == (15, 20) and held > 0:
            side, quantity = Side.SELL, held
        else:
            return []
        return [
            OrderIntent(
                strategy_id=ctx.strategy_id,
                instrument=inst,
                side=side,
                quantity=quantity,
                order_type=OrderType.MARKET,
                market_protection_pct=1.0,
                product=Product.MIS,
                reason="round_trip",
            )
        ]


def test_exit_closes_the_whole_position_above_the_order_cap(tmp_path: Path) -> None:
    # 90 shares cost 9,000 at the open, under the 9,500 cap. By the close the
    # same shares are worth about 9,640, so a capped exit would strand a few.
    files = build_workspace(tmp_path, risk_overrides={"max_order_notional": 9_500})
    run = _load(files)
    run.strategy = _RoundTripEachDay(quantity=90)
    result = run.run_backtest(tmp_path / "out")

    assert [t.quantity for t in result.trades] == [90] * len(SESSION_DAYS)


def test_order_budget_is_spent_on_bar_time_not_replay_speed(tmp_path: Path) -> None:
    # Two orders a session against a budget of two per minute. The replay runs
    # in well under a second, but each session is hours apart on the market clock.
    files = build_workspace(tmp_path, risk_overrides={"max_orders_per_minute": 2})
    run = _load(files)
    run.strategy = _RoundTripEachDay(quantity=1)
    result = run.run_backtest(tmp_path / "out")

    assert len(result.trades) == len(SESSION_DAYS)
    rejected = run.workspace.db.fetchall(
        "SELECT COUNT(*) AS n FROM audit_log WHERE event = 'rejected' AND run_id = ?",
        (result.run_id,),
    )
    assert rejected[0]["n"] == 0


def test_kill_switch_mid_run_flattens_and_blocks_entries(workspace: WorkspaceFiles) -> None:
    run = _load(workspace)
    kill = run.workspace.kill_switch
    source = run.historical_candles()

    def _engage_after_first_entry() -> Iterator[tuple[Instrument, Candle]]:
        for inst, candle in source:
            if candle.timestamp.date() == SESSION_DAYS[0] and candle.timestamp.hour == 11:
                kill.engage("test halt")
            yield inst, candle

    engine = run.backtest_engine()
    engine.candles_provider = _engage_after_first_entry()
    result = engine.run(output_dir=workspace.root / "out")

    assert len(result.trades) == 1
    only = result.trades[0]
    assert only.exit_time.date() == SESSION_DAYS[0]
    assert only.exit_time.hour == 11
    assert kill.is_engaged()


def test_backtest_publishes_operator_snapshot(workspace: WorkspaceFiles) -> None:
    run = _load(workspace)
    run.run_backtest(workspace.root / "out")
    assert run.workspace.operator_state_path.exists()


def test_backtest_without_candles_returns_empty_result(tmp_path: Path) -> None:
    files = build_workspace(tmp_path, with_candles=False)
    result = _load(files).run_backtest(tmp_path / "out")
    assert result.trades == []
    assert result.equity_curve == []
