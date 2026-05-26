"""Trading clock abstractions.

Provides:
* a deterministic clock for backtests
* a wall-clock implementation for paper / live
* helpers to test whether the Indian equity market is currently open
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover - py<3.9 only
    ZoneInfo = None  # type: ignore[assignment]


IST = ZoneInfo("Asia/Kolkata") if ZoneInfo else timezone(timedelta(hours=5, minutes=30))

DEFAULT_SESSION_START = time(9, 15)
DEFAULT_SESSION_END = time(15, 30)
PRE_OPEN_START = time(9, 0)


class Clock(ABC):
    """Time source for the framework. All timestamps are timezone aware."""

    @abstractmethod
    def now(self) -> datetime:
        """Current timestamp (timezone aware)."""

    def today(self) -> date:
        return self.now().date()


class SystemClock(Clock):
    """Wall-clock time anchored to the IST trading timezone."""

    def __init__(self, tz: timezone | ZoneInfo | None = None) -> None:
        self._tz = tz or IST

    def now(self) -> datetime:
        return datetime.now(self._tz)


@dataclass
class FrozenClock(Clock):
    """Clock that advances only when ``advance_to`` is called.

    Used by the backtest engine to anchor strategy timestamps to candle
    timestamps and avoid any wall-clock leakage.
    """

    _now: datetime

    def __init__(self, start: datetime) -> None:
        if start.tzinfo is None:
            start = start.replace(tzinfo=IST)
        self._now = start

    def now(self) -> datetime:
        return self._now

    def advance_to(self, ts: datetime) -> None:
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=IST)
        if ts < self._now:
            raise ValueError(f"FrozenClock cannot move backwards: {ts} < {self._now}")
        self._now = ts


def is_market_open(
    ts: datetime,
    session_start: time = DEFAULT_SESSION_START,
    session_end: time = DEFAULT_SESSION_END,
) -> bool:
    """Cheap session-window check for the IST equity market.

    NOTE: this does not consult NSE/BSE holiday calendars. For long-running
    services connect a holiday source via :func:`is_trading_day`.
    """
    local = ts.astimezone(IST) if ts.tzinfo else ts.replace(tzinfo=IST)
    if local.weekday() >= 5:  # Saturday=5, Sunday=6
        return False
    return session_start <= local.time() <= session_end


def is_trading_day(d: date, holidays: set[date] | None = None) -> bool:
    if d.weekday() >= 5:
        return False
    if holidays and d in holidays:
        return False
    return True


def minutes_until_close(
    ts: datetime,
    session_end: time = DEFAULT_SESSION_END,
) -> int:
    """Whole minutes between ``ts`` and the session close. Negative if past close."""
    local = ts.astimezone(IST) if ts.tzinfo else ts.replace(tzinfo=IST)
    close_dt = datetime.combine(local.date(), session_end, tzinfo=IST)
    delta = close_dt - local
    return int(delta.total_seconds() // 60)


__all__ = [
    "DEFAULT_SESSION_END",
    "DEFAULT_SESSION_START",
    "IST",
    "PRE_OPEN_START",
    "Clock",
    "FrozenClock",
    "SystemClock",
    "is_market_open",
    "is_trading_day",
    "minutes_until_close",
]
