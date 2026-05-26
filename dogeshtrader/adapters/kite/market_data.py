"""Kite market data adapter.

Wraps:
* GET /quote
* GET /quote/ohlc
* GET /quote/ltp
* GET /instruments/historical (delegated to historical_downloader.py)

Keys for Kite quote endpoints are ``EXCHANGE:TRADINGSYMBOL`` strings; we expose
both the raw and our :class:`Instrument` form for convenience.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from datetime import date, datetime
from typing import Any

from dogeshtrader.core.errors import BrokerError
from dogeshtrader.core.models import Candle, Instrument


class KiteMarketDataAdapter:
    def __init__(self, kite: Any) -> None:
        self.kite = kite

    # ------------------------------------------------------------------
    # Quotes
    # ------------------------------------------------------------------

    def ltp(self, instruments: Iterable[Instrument]) -> dict[str, float]:
        keys = [i.key for i in instruments]
        try:
            raw = self.kite.ltp(keys)
        except Exception as e:
            raise BrokerError(f"ltp failed: {e}") from e
        return {k: float((v or {}).get("last_price") or 0.0) for k, v in (raw or {}).items()}

    def ohlc(self, instruments: Iterable[Instrument]) -> dict[str, dict[str, Any]]:
        keys = [i.key for i in instruments]
        try:
            raw = self.kite.ohlc(keys)
        except Exception as e:
            raise BrokerError(f"ohlc failed: {e}") from e
        return dict(raw or {})

    def quote(self, instruments: Iterable[Instrument]) -> dict[str, dict[str, Any]]:
        keys = [i.key for i in instruments]
        try:
            raw = self.kite.quote(keys)
        except Exception as e:
            raise BrokerError(f"quote failed: {e}") from e
        return dict(raw or {})

    # ------------------------------------------------------------------
    # Historical candles
    # ------------------------------------------------------------------

    def historical(
        self,
        instrument: Instrument,
        *,
        from_dt: date | datetime,
        to_dt: date | datetime,
        interval: str,
        continuous: bool = False,
        oi: bool = False,
    ) -> Iterator[Candle]:
        if instrument.instrument_token is None:
            raise BrokerError(
                f"historical requires instrument_token; resolve {instrument.key} first"
            )
        if isinstance(from_dt, date) and not isinstance(from_dt, datetime):
            from_dt = datetime.combine(from_dt, datetime.min.time())
        if isinstance(to_dt, date) and not isinstance(to_dt, datetime):
            to_dt = datetime.combine(to_dt, datetime.max.time())
        try:
            raw = self.kite.historical_data(
                instrument_token=instrument.instrument_token,
                from_date=from_dt,
                to_date=to_dt,
                interval=interval,
                continuous=continuous,
                oi=oi,
            )
        except Exception as e:
            raise BrokerError(f"historical_data failed: {e}") from e

        for row in raw or []:
            ts = row.get("date") or row.get("timestamp")
            if isinstance(ts, str):
                ts = datetime.fromisoformat(ts)
            if ts is None:
                continue
            yield Candle(
                timestamp=ts,
                open=float(row.get("open") or 0.0),
                high=float(row.get("high") or 0.0),
                low=float(row.get("low") or 0.0),
                close=float(row.get("close") or 0.0),
                volume=int(row.get("volume") or 0),
                oi=int(row["oi"]) if "oi" in row and row["oi"] is not None else None,
            )


__all__ = ["KiteMarketDataAdapter"]
