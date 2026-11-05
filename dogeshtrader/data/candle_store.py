"""Parquet-backed candle store.

Layout:

    data/candles/<exchange>/<tradingsymbol>/<interval>.parquet

We rely on pandas + pyarrow for speed and ergonomics. The file is overwritten
on each upsert; for the volumes a retail user trades this is plenty.
"""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from typing import Iterable, Iterator

from dogeshtrader.core.models import Candle, Instrument
from dogeshtrader.logging_config import get_logger


_log = get_logger(__name__)


_REQUIRED_COLS = ("timestamp", "open", "high", "low", "close", "volume")


class CandleStore:
    def __init__(self, root: Path | str) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Layout
    # ------------------------------------------------------------------

    def path_for(self, instrument: Instrument, interval: str) -> Path:
        return (
            self.root
            / "candles"
            / instrument.exchange.value
            / instrument.tradingsymbol
            / f"{interval}.parquet"
        )

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    def upsert(
        self,
        instrument: Instrument,
        interval: str,
        candles: Iterable[Candle],
    ) -> int:
        import pandas as pd

        rows = [_candle_to_row(c) for c in candles]
        if not rows:
            return 0
        df_new = pd.DataFrame(rows)
        path = self.path_for(instrument, interval)
        path.parent.mkdir(parents=True, exist_ok=True)

        if path.exists():
            df_existing = pd.read_parquet(path)
            df = pd.concat([df_existing, df_new], ignore_index=True)
            df = df.drop_duplicates(subset=["timestamp"], keep="last").sort_values("timestamp")
        else:
            df = df_new.sort_values("timestamp")

        df.to_parquet(path, index=False)
        _log.info(
            "candle_store_upsert",
            instrument=instrument.key,
            interval=interval,
            rows=len(rows),
            total=len(df),
        )
        return len(rows)

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    def read(
        self,
        instrument: Instrument,
        interval: str,
        *,
        from_dt: date | datetime | None = None,
        to_dt: date | datetime | None = None,
    ) -> list[Candle]:
        path = self.path_for(instrument, interval)
        if not path.exists():
            return []

        import pandas as pd

        df = pd.read_parquet(path)
        if not all(col in df.columns for col in _REQUIRED_COLS):
            return []
        df["timestamp"] = pd.to_datetime(df["timestamp"], utc=False)
        timestamp_tz = df["timestamp"].dt.tz
        if from_dt is not None:
            df = df[df["timestamp"] >= _to_pd_ts(from_dt, tz=timestamp_tz)]
        if to_dt is not None:
            df = df[df["timestamp"] <= _to_pd_ts(to_dt, end_of_day=True, tz=timestamp_tz)]
        return [_row_to_candle(row) for _, row in df.iterrows()]

    def iter_chronological(
        self,
        instruments: list[Instrument],
        interval: str,
        *,
        from_dt: date | datetime | None = None,
        to_dt: date | datetime | None = None,
    ) -> Iterator[tuple[Instrument, Candle]]:
        """Merge candles across instruments in timestamp order.

        Used by the backtest engine.
        """
        streams: list[tuple[Instrument, list[Candle]]] = []
        for inst in instruments:
            candles = self.read(inst, interval, from_dt=from_dt, to_dt=to_dt)
            streams.append((inst, candles))

        # Simple K-way merge using sorted positions.
        positions = [0] * len(streams)
        while True:
            best: tuple[datetime, int] | None = None
            for i, (_, cs) in enumerate(streams):
                if positions[i] >= len(cs):
                    continue
                ts = cs[positions[i]].timestamp
                if best is None or ts < best[0]:
                    best = (ts, i)
            if best is None:
                return
            i = best[1]
            inst, cs = streams[i]
            yield inst, cs[positions[i]]
            positions[i] += 1


def _candle_to_row(c: Candle) -> dict:
    return {
        "timestamp": c.timestamp,
        "open": float(c.open),
        "high": float(c.high),
        "low": float(c.low),
        "close": float(c.close),
        "volume": int(c.volume),
        "oi": int(c.oi) if c.oi is not None else None,
    }


def _row_to_candle(row: dict) -> Candle:
    ts = row["timestamp"]
    if hasattr(ts, "to_pydatetime"):
        ts = ts.to_pydatetime()
    return Candle(
        timestamp=ts,
        open=float(row["open"]),
        high=float(row["high"]),
        low=float(row["low"]),
        close=float(row["close"]),
        volume=int(row.get("volume") or 0),
        oi=int(row["oi"]) if row.get("oi") and not _is_nan(row["oi"]) else None,
    )


def _is_nan(v: object) -> bool:
    try:
        import math

        return math.isnan(float(v))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return False


def _to_pd_ts(value: date | datetime, *, end_of_day: bool = False, tz=None):
    import pandas as pd

    if isinstance(value, datetime):
        ts = pd.Timestamp(value)
    elif end_of_day:
        ts = pd.Timestamp(datetime.combine(value, datetime.max.time()))
    else:
        ts = pd.Timestamp(datetime.combine(value, datetime.min.time()))

    if tz is not None:
        if ts.tzinfo is None:
            return ts.tz_localize(tz)
        return ts.tz_convert(tz)
    if ts.tzinfo is not None:
        return ts.tz_localize(None)
    return ts


__all__ = ["CandleStore"]
