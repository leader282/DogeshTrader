"""Deterministic intraday setup math.

The strategy calls these helpers. They do not place orders and they do not
read a broker. A setup either names a side plus a structural stop, or it
explains which filter declined the bar.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, time

from dogeshtrader.core.clock import IST
from dogeshtrader.core.enums import Side
from dogeshtrader.core.models import Candle


@dataclass(frozen=True)
class SetupView:
    side: Side | None
    reason: str
    stop_price: float | None = None
    evidence: dict[str, float | str | None] = field(default_factory=dict)


def local_time(ts: datetime) -> datetime:
    if ts.tzinfo is None:
        return ts.replace(tzinfo=IST)
    return ts.astimezone(IST)


def minute_of_day(ts: datetime) -> int:
    local = local_time(ts)
    return local.hour * 60 + local.minute


def add_minutes(t: time, minutes: int) -> time:
    total = t.hour * 60 + t.minute + minutes
    return time((total // 60) % 24, total % 60)


def session_vwap(candles: list[Candle]) -> float | None:
    """Volume-weighted typical price. Zero-volume bars are ignored."""
    notional = 0.0
    volume = 0
    for candle in candles:
        if candle.volume <= 0:
            continue
        typical = (candle.high + candle.low + candle.close) / 3.0
        notional += typical * candle.volume
        volume += candle.volume
    if volume <= 0:
        return None
    return notional / volume


def opening_range(
    candles: list[Candle],
    *,
    session_start: time,
    range_minutes: int,
) -> tuple[float, float] | None:
    """High and low of the opening window, once that window has closed."""
    if not candles:
        return None
    end = add_minutes(session_start, range_minutes)
    latest = local_time(candles[-1].timestamp).time()
    if latest <= end:
        return None
    window = [c for c in candles if session_start <= local_time(c.timestamp).time() <= end]
    if not window:
        return None
    high = max(c.high for c in window)
    low = min(c.low for c in window)
    if high <= low or low <= 0:
        return None
    return high, low


def relative_volume(current: int, history: list[int]) -> float | None:
    if current < 0 or not history:
        return None
    avg = sum(history) / len(history)
    if avg <= 0:
        return None
    return current / avg


def evaluate_breakout(
    *,
    close: float,
    high: float,
    low: float,
    volume: int,
    range_high: float,
    range_low: float,
    vwap: float | None,
    rvol: float | None,
    min_rvol: float,
    min_bar_value: float,
    index_close: float | None,
    index_vwap: float | None,
    require_index: bool,
    allow_short: bool,
    spread_pct: float | None,
    max_spread_pct: float,
    max_bar_range_pct: float,
) -> SetupView:
    """Decide whether this bar is an opening-range breakout worth taking."""
    bar_value = close * max(volume, 0)
    bar_range_pct = ((high - low) / close * 100.0) if close > 0 else 0.0
    evidence: dict[str, float | str | None] = {
        "close": close,
        "vwap": vwap,
        "range_high": range_high,
        "range_low": range_low,
        "rvol": rvol,
        "bar_value": bar_value,
        "bar_range_pct": bar_range_pct,
        "index_close": index_close,
        "index_vwap": index_vwap,
        "spread_pct": spread_pct,
    }

    def decline(reason: str) -> SetupView:
        return SetupView(side=None, reason=reason, evidence=evidence)

    if vwap is None:
        return decline("vwap_unavailable")
    if rvol is None:
        return decline("rvol_unavailable")
    if rvol < min_rvol:
        return decline("rvol_below_min")
    if bar_value < min_bar_value:
        return decline("bar_value_below_min")
    if max_bar_range_pct > 0 and bar_range_pct > max_bar_range_pct:
        return decline("bar_range_too_wide")
    if max_spread_pct > 0 and spread_pct is not None and spread_pct > max_spread_pct:
        return decline("spread_too_wide")

    long_break = close > range_high and close > vwap
    short_break = allow_short and close < range_low and close < vwap
    if not long_break and not short_break:
        return decline("inside_range")

    if require_index:
        if index_close is None or index_vwap is None:
            return decline("index_unavailable")
        if long_break and index_close <= index_vwap:
            return decline("index_not_confirming")
        if short_break and index_close >= index_vwap:
            return decline("index_not_confirming")

    if long_break:
        return SetupView(
            side=Side.BUY,
            reason="orb_vwap_long",
            stop_price=range_low,
            evidence=evidence,
        )
    return SetupView(
        side=Side.SELL,
        reason="orb_vwap_short",
        stop_price=range_high,
        evidence=evidence,
    )


__all__ = [
    "SetupView",
    "add_minutes",
    "evaluate_breakout",
    "local_time",
    "minute_of_day",
    "opening_range",
    "relative_volume",
    "session_vwap",
]
