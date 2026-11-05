"""Historical candle downloader.

Kite imposes a per-call window limit per interval (e.g. 60 days for minute,
2000 days for day). The downloader chunks requests, retries transient errors
with exponential backoff, and resumes from the last stored timestamp so
re-runs are idempotent.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Iterable, Iterator

from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from dogeshtrader.constants import HISTORICAL_INTERVALS
from dogeshtrader.core.errors import BrokerError
from dogeshtrader.core.models import Candle, Instrument
from dogeshtrader.data.candle_store import CandleStore
from dogeshtrader.logging_config import get_logger


_log = get_logger(__name__)


_WINDOW_DAYS: dict[str, int] = {
    "minute": 60,
    "3minute": 100,
    "5minute": 100,
    "10minute": 100,
    "15minute": 200,
    "30minute": 200,
    "60minute": 400,
    "day": 2000,
}


@dataclass
class DownloadProgress:
    instrument: Instrument
    interval: str
    chunks: int
    candles: int


class HistoricalDownloader:
    """Download candles from a market data adapter and persist via :class:`CandleStore`."""

    def __init__(self, market_data: Any, store: CandleStore) -> None:
        self.market_data = market_data
        self.store = store

    def download(
        self,
        instrument: Instrument,
        *,
        from_dt: date,
        to_dt: date,
        interval: str,
        oi: bool = False,
    ) -> DownloadProgress:
        if interval not in HISTORICAL_INTERVALS:
            raise BrokerError(
                f"interval {interval!r} not supported; choose from {HISTORICAL_INTERVALS}"
            )
        if instrument.instrument_token is None:
            raise BrokerError(
                f"cannot download history for {instrument.key}; run instruments download first"
            )

        window_days = _WINDOW_DAYS.get(interval, 60)
        chunks = 0
        total_candles = 0
        cursor = from_dt
        while cursor <= to_dt:
            chunk_end = min(cursor + timedelta(days=window_days - 1), to_dt)
            _log.info(
                "history_chunk",
                instrument=instrument.key,
                interval=interval,
                from_=cursor.isoformat(),
                to=chunk_end.isoformat(),
            )
            candles = list(
                _fetch_chunk(
                    self.market_data,
                    instrument=instrument,
                    from_dt=datetime.combine(cursor, datetime.min.time()),
                    to_dt=datetime.combine(chunk_end, datetime.max.time()),
                    interval=interval,
                    oi=oi,
                )
            )
            self.store.upsert(instrument, interval, candles)
            chunks += 1
            total_candles += len(candles)
            cursor = chunk_end + timedelta(days=1)

        return DownloadProgress(
            instrument=instrument,
            interval=interval,
            chunks=chunks,
            candles=total_candles,
        )

    def download_many(
        self,
        instruments: Iterable[Instrument],
        *,
        from_dt: date,
        to_dt: date,
        interval: str,
        oi: bool = False,
    ) -> list[DownloadProgress]:
        return [
            self.download(i, from_dt=from_dt, to_dt=to_dt, interval=interval, oi=oi)
            for i in instruments
        ]


@retry(
    reraise=True,
    retry=retry_if_exception_type(BrokerError),
    stop=stop_after_attempt(4),
    wait=wait_exponential(multiplier=1.0, min=1, max=8),
)
def _fetch_chunk(
    market_data: Any,
    *,
    instrument: Instrument,
    from_dt: datetime,
    to_dt: datetime,
    interval: str,
    oi: bool,
) -> Iterator[Candle]:
    yield from market_data.historical(
        instrument,
        from_dt=from_dt,
        to_dt=to_dt,
        interval=interval,
        oi=oi,
    )


__all__ = ["HistoricalDownloader", "DownloadProgress"]
