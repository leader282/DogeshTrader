"""The public intraday setup math. The strategy that uses it stays private."""

from __future__ import annotations

from datetime import UTC, datetime, time

from dogeshtrader.core.clock import IST
from dogeshtrader.core.enums import Side
from dogeshtrader.core.models import Candle
from dogeshtrader.strategy.signals import (
    add_minutes,
    evaluate_breakout,
    local_time,
    minute_of_day,
    opening_range,
    relative_volume,
    session_vwap,
)


def _bar(hour: int, minute: int, *, high: float, low: float, close: float, volume: int) -> Candle:
    return Candle(
        timestamp=datetime(2024, 1, 2, hour, minute, tzinfo=IST),
        open=close,
        high=high,
        low=low,
        close=close,
        volume=volume,
    )


def _view(**overrides: object):
    payload: dict[str, object] = dict(
        close=105.0,
        high=106.0,
        low=104.0,
        volume=1_000,
        range_high=100.0,
        range_low=90.0,
        vwap=99.0,
        rvol=2.0,
        min_rvol=1.5,
        min_bar_value=1_000.0,
        index_close=101.0,
        index_vwap=100.0,
        require_index=True,
        allow_short=False,
        spread_pct=0.05,
        max_spread_pct=0.20,
        max_bar_range_pct=0.0,
    )
    payload.update(overrides)
    return evaluate_breakout(**payload)  # type: ignore[arg-type]


def test_clock_helpers_accept_naive_and_aware_timestamps() -> None:
    naive = datetime(2024, 1, 2, 9, 20)
    aware = datetime(2024, 1, 2, 3, 50, tzinfo=UTC)  # 09:20 IST
    assert local_time(naive).tzinfo is IST
    assert local_time(aware).hour == 9
    assert minute_of_day(naive) == 9 * 60 + 20
    assert add_minutes(time(23, 50), 20) == time(0, 10)


def test_session_vwap_ignores_zero_volume_and_needs_some_volume() -> None:
    priced = _bar(9, 15, high=103, low=100, close=101, volume=10)
    dead = _bar(9, 20, high=104, low=101, close=102, volume=0)
    typical = (103 + 100 + 101) / 3
    assert session_vwap([priced, dead]) == typical
    assert session_vwap([dead]) is None


def test_opening_range_waits_for_a_closed_window() -> None:
    start = time(9, 15)
    inside = _bar(9, 20, high=110, low=100, close=105, volume=1)
    after = _bar(9, 35, high=112, low=108, close=111, volume=1)
    assert opening_range([], session_start=start, range_minutes=15) is None
    assert opening_range([inside], session_start=start, range_minutes=15) is None
    # The only bar is after the window, so there is nothing to measure.
    assert opening_range([after], session_start=start, range_minutes=15) is None
    assert opening_range([inside, after], session_start=start, range_minutes=15) == (110, 100)
    flat = _bar(9, 20, high=100, low=100, close=100, volume=1)
    assert opening_range([flat, after], session_start=start, range_minutes=15) is None
    non_positive = _bar(9, 20, high=1, low=0, close=1, volume=1)
    assert opening_range([non_positive, after], session_start=start, range_minutes=15) is None


def test_relative_volume_needs_a_positive_history() -> None:
    assert relative_volume(-1, [10]) is None
    assert relative_volume(10, []) is None
    assert relative_volume(10, [0, 0]) is None
    assert relative_volume(30, [10, 20]) == 2.0


def test_breakout_declines_each_filter_in_order() -> None:
    assert _view(vwap=None).reason == "vwap_unavailable"
    assert _view(rvol=None).reason == "rvol_unavailable"
    assert _view(rvol=1.0).reason == "rvol_below_min"
    assert _view(volume=1, min_bar_value=10_000).reason == "bar_value_below_min"
    assert _view(max_bar_range_pct=0.5).reason == "bar_range_too_wide"
    assert _view(spread_pct=1.0).reason == "spread_too_wide"
    # A missing quote is not a wide spread.
    assert _view(spread_pct=None, close=99).reason == "inside_range"
    # A zero close still reports a range of zero rather than dividing by it.
    zero_close = _view(close=0, high=1, low=0, volume=1, min_bar_value=0, vwap=1, rvol=2)
    assert zero_close.reason == "inside_range"
    assert zero_close.evidence["bar_range_pct"] == 0.0


def test_index_gate_confirms_or_declines_both_sides() -> None:
    missing = _view(index_close=None)
    assert missing.reason == "index_unavailable"
    against = _view(index_close=99, index_vwap=100)
    assert against.reason == "index_not_confirming"

    long = _view()
    assert long.side is Side.BUY
    assert long.reason == "orb_vwap_long"
    assert long.stop_price == 90.0

    short_against = _view(
        allow_short=True, close=80, high=82, low=79, index_close=101, index_vwap=100
    )
    assert short_against.reason == "index_not_confirming"
    short = _view(allow_short=True, close=80, high=82, low=79, require_index=False)
    assert short.side is Side.SELL
    assert short.reason == "orb_vwap_short"
    assert short.stop_price == 100.0
