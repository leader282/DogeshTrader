"""Kite instruments adapter.

Wraps GET /instruments and normalises the CSV/list response into our
:class:`Instrument` model.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date, datetime
from typing import Any

from dogeshtrader.core.enums import Exchange, InstrumentType, Segment
from dogeshtrader.core.errors import BrokerError
from dogeshtrader.core.models import Instrument

_TYPE_MAP = {
    "EQ": InstrumentType.EQ,
    "FUT": InstrumentType.FUT,
    "CE": InstrumentType.CE,
    "PE": InstrumentType.PE,
    "INDEX": InstrumentType.INDEX,
    "BOND": InstrumentType.BOND,
    "ETF": InstrumentType.ETF,
}


class KiteInstrumentsAdapter:
    def __init__(self, kite: Any) -> None:
        self.kite = kite

    def download(self, exchange: str | None = None) -> list[Instrument]:
        try:
            raw = self.kite.instruments(exchange) if exchange else self.kite.instruments()
        except Exception as e:
            raise BrokerError(f"instruments fetch failed: {e}") from e
        return list(self._normalize(raw or []))

    def _normalize(self, raw: Iterable[dict[str, Any]]) -> Iterable[Instrument]:
        for row in raw:
            try:
                ex_value = row.get("exchange") or ""
                if ex_value not in Exchange.__members__:
                    continue
                inst_type = _TYPE_MAP.get(row.get("instrument_type", "EQ"))
                segment_val = row.get("segment") or ""
                segment = None
                if segment_val:
                    try:
                        segment = Segment(segment_val)
                    except ValueError:
                        segment = None
                expiry = _parse_date(row.get("expiry"))
                yield Instrument(
                    exchange=Exchange(ex_value),
                    tradingsymbol=str(row.get("tradingsymbol") or "").strip(),
                    instrument_token=_to_int(row.get("instrument_token")),
                    exchange_token=_to_int(row.get("exchange_token")),
                    name=row.get("name"),
                    instrument_type=inst_type,
                    segment=segment,
                    expiry=expiry,
                    strike=_to_float(row.get("strike")),
                    tick_size=float(row.get("tick_size") or 0.05),
                    lot_size=int(row.get("lot_size") or 1),
                )
            except Exception:
                continue


def _to_int(v: Any) -> int | None:
    if v is None or v == "":
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _to_float(v: Any) -> float | None:
    if v in (None, "", 0):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _parse_date(v: Any) -> date | None:
    if not v:
        return None
    if isinstance(v, date):
        return v
    if isinstance(v, datetime):
        return v.date()
    try:
        return datetime.fromisoformat(str(v)).date()
    except ValueError:
        try:
            return datetime.strptime(str(v), "%Y-%m-%d").date()
        except ValueError:
            return None


__all__ = ["KiteInstrumentsAdapter"]
