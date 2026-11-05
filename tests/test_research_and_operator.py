"""Out-of-sample gate and position halt."""

from __future__ import annotations

from datetime import date, datetime

from dogeshtrader.backtest.metrics import Trade
from dogeshtrader.core.models import Instrument, Position
from dogeshtrader.execution.reconciliation import position_drifts
from dogeshtrader.research import evaluate_windows


def test_positive_out_of_sample_is_promoted() -> None:
    equity = [
        (datetime(2024, 1, 2, 10, 0), 100_000),
        (datetime(2024, 3, 28, 15, 0), 101_000),
        (datetime(2024, 4, 2, 10, 0), 101_000),
        (datetime(2024, 6, 28, 15, 0), 102_000),
    ]
    trades = [
        Trade(
            instrument_key="NSE:INFY",
            side="LONG",
            entry_time=datetime(2024, 2, 1, 10, 0),
            exit_time=datetime(2024, 2, 1, 11, 0),
            entry_price=100,
            exit_price=110,
            quantity=10,
            pnl=80,
            fees=20,
        ),
        Trade(
            instrument_key="NSE:INFY",
            side="LONG",
            entry_time=datetime(2024, 5, 2, 10, 0),
            exit_time=datetime(2024, 5, 2, 12, 0),
            entry_price=100,
            exit_price=110,
            quantity=10,
            pnl=70,
            fees=30,
        ),
    ]
    report = evaluate_windows(
        equity_curve=equity,
        trades=trades,
        split=date(2024, 4, 1),
        costs_note="costs included",
    )
    assert report.promoted
    assert report.out_of_sample.expectancy == 70
    assert report.out_of_sample.total_fees == 30
    assert report.in_sample.trade_count == 1


def test_negative_out_of_sample_is_not_promoted() -> None:
    equity = [
        (datetime(2024, 4, 2, 10, 0), 100_000),
        (datetime(2024, 5, 2, 15, 0), 99_000),
    ]
    trades = [
        Trade(
            instrument_key="NSE:INFY",
            side="LONG",
            entry_time=datetime(2024, 4, 3, 10, 0),
            exit_time=datetime(2024, 4, 3, 11, 0),
            entry_price=100,
            exit_price=90,
            quantity=10,
            pnl=-120,
            fees=20,
        )
    ]
    report = evaluate_windows(equity_curve=equity, trades=trades, split=date(2024, 4, 1))
    assert not report.promoted
    assert any("expectancy" in reason for reason in report.reasons)


def test_position_drift_flags_a_broker_quantity_the_run_does_not_have() -> None:
    local = {"NSE:INFY": 0, "NSE:TCS": 2}
    broker = {"NSE:INFY": 5, "NSE:TCS": 2, "NSE:RELIANCE": 9}
    drifts = position_drifts(local, broker, universe=set(local))
    assert len(drifts) == 1
    assert drifts[0].instrument_key == "NSE:INFY"
    assert drifts[0].broker_quantity == 5

    infy = Instrument.equity("NSE", "INFY")
    local_positions = {"NSE:INFY": Position(instrument=infy, quantity=1)}
    broker_positions = {"NSE:INFY": Position(instrument=infy, quantity=1)}
    assert position_drifts(local_positions, broker_positions, universe={"NSE:INFY"}) == []
