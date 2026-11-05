"""Instrument master.

Combines the SQLite-backed instruments table with a small in-memory cache so
strategy code can resolve ``NSE:INFY`` -> :class:`Instrument` without hitting
the DB on every call.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from dogeshtrader.core.enums import Exchange, InstrumentType, Segment
from dogeshtrader.core.models import Instrument
from dogeshtrader.storage import Database, InstrumentRepository


class InstrumentMaster:
    def __init__(self, db: Database) -> None:
        self.db = db
        self.repo = InstrumentRepository(db)
        self._by_key: dict[str, Instrument] = {}
        self._by_token: dict[int, Instrument] = {}

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------

    def upsert_many(self, instruments: list[Instrument]) -> int:
        self._by_key.clear()
        self._by_token.clear()
        return self.repo.upsert_many(instruments)

    # ------------------------------------------------------------------
    # Lookups
    # ------------------------------------------------------------------

    def get(self, exchange: str | Exchange, tradingsymbol: str) -> Instrument | None:
        ex = exchange.value if isinstance(exchange, Exchange) else str(exchange)
        key = f"{ex}:{tradingsymbol}"
        if key in self._by_key:
            return self._by_key[key]
        row = self.repo.find_by_key(ex, tradingsymbol)
        if not row:
            return None
        inst = _row_to_instrument(row)
        self._by_key[key] = inst
        if inst.instrument_token is not None:
            self._by_token[inst.instrument_token] = inst
        return inst

    def get_by_token(self, token: int) -> Instrument | None:
        if token in self._by_token:
            return self._by_token[token]
        row = self.repo.find_by_token(token)
        if not row:
            return None
        inst = _row_to_instrument(row)
        self._by_token[token] = inst
        self._by_key[inst.key] = inst
        return inst

    def ensure(self, exchange: str | Exchange, tradingsymbol: str) -> Instrument:
        inst = self.get(exchange, tradingsymbol)
        if inst is None:
            ex = Exchange(exchange) if isinstance(exchange, str) else exchange
            inst = Instrument(exchange=ex, tradingsymbol=tradingsymbol)
        return inst

    def count(self) -> int:
        return self.repo.count()


def _row_to_instrument(row: dict[str, Any]) -> Instrument:
    expiry: date | None = None
    if row.get("expiry"):
        try:
            from datetime import datetime

            expiry = datetime.fromisoformat(row["expiry"]).date()
        except ValueError:
            expiry = None

    segment = None
    if row.get("segment"):
        try:
            segment = Segment(row["segment"])
        except ValueError:
            segment = None

    instrument_type = None
    if row.get("instrument_type"):
        try:
            instrument_type = InstrumentType(row["instrument_type"])
        except ValueError:
            instrument_type = None

    return Instrument(
        exchange=Exchange(row["exchange"]),
        tradingsymbol=row["tradingsymbol"],
        instrument_token=row.get("instrument_token"),
        exchange_token=row.get("exchange_token"),
        name=row.get("name"),
        instrument_type=instrument_type,
        segment=segment,
        expiry=expiry,
        strike=row.get("strike"),
        tick_size=float(row.get("tick_size") or 0.05),
        lot_size=int(row.get("lot_size") or 1),
    )


__all__ = ["InstrumentMaster"]
