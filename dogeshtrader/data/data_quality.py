"""Light data-quality checks for candle datasets.

Catches:
* duplicate timestamps
* timestamp regressions (out-of-order rows)
* gaps larger than the expected step
* OHLC self-inconsistency (high < low etc.)
* obvious zeros / negatives in price

The goal is to fail fast in CI / before launching a paper run, not to be a
full data validator.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Iterable

from dogeshtrader.core.clock import session_date
from dogeshtrader.core.models import Candle


_EXPECTED_STEPS = {
    "minute": timedelta(minutes=1),
    "3minute": timedelta(minutes=3),
    "5minute": timedelta(minutes=5),
    "10minute": timedelta(minutes=10),
    "15minute": timedelta(minutes=15),
    "30minute": timedelta(minutes=30),
    "60minute": timedelta(minutes=60),
    "day": timedelta(days=1),
}


@dataclass
class DataQualityReport:
    interval: str
    rows: int = 0
    duplicates: int = 0
    out_of_order: int = 0
    gaps: list[tuple[datetime, datetime]] = field(default_factory=list)
    bad_ohlc: int = 0
    bad_prices: int = 0

    @property
    def ok(self) -> bool:
        return self.duplicates == 0 and self.out_of_order == 0 and self.bad_ohlc == 0 and self.bad_prices == 0


def check_candles(candles: Iterable[Candle], *, interval: str) -> DataQualityReport:
    report = DataQualityReport(interval=interval)
    expected_step = _EXPECTED_STEPS.get(interval)
    prev: Candle | None = None
    seen: set[datetime] = set()
    for c in candles:
        report.rows += 1
        if c.timestamp in seen:
            report.duplicates += 1
        seen.add(c.timestamp)

        if prev is not None and c.timestamp < prev.timestamp:
            report.out_of_order += 1
        if prev is not None and expected_step is not None:
            delta = c.timestamp - prev.timestamp
            # Allow up to 2x the step before flagging a gap, this avoids
            # noise around session gaps and lunch breaks.
            if delta > expected_step * 2:
                report.gaps.append((prev.timestamp, c.timestamp))

        if c.high < c.low or c.high < c.open or c.high < c.close or c.low > c.open or c.low > c.close:
            report.bad_ohlc += 1
        if min(c.open, c.high, c.low, c.close) <= 0:
            report.bad_prices += 1

        prev = c
    return report


def candle_block_reason(
    prev: Candle | None,
    candle: Candle,
    *,
    interval: str,
    multiple: float,
) -> str | None:
    """Reason to block a new entry on this candle, or None when the bar is usable.

    Overnight jumps are normal. A gap counts only inside the same IST session
    and only when it is longer than ``multiple`` times the expected bar step.
    """
    if min(candle.open, candle.high, candle.low, candle.close) <= 0:
        return "non_positive_price"
    if (
        candle.high < candle.low
        or candle.high < candle.open
        or candle.high < candle.close
        or candle.low > candle.open
        or candle.low > candle.close
    ):
        return "bad_ohlc"
    if prev is None or multiple <= 0:
        return None
    if session_date(prev.timestamp) != session_date(candle.timestamp):
        return None
    if candle.timestamp <= prev.timestamp:
        return "timestamp_not_advancing"
    step = _EXPECTED_STEPS.get(interval)
    if step is None:
        return None
    if candle.timestamp - prev.timestamp > step * multiple:
        return "intraday_gap"
    return None


__all__ = ["DataQualityReport", "candle_block_reason", "check_candles"]
