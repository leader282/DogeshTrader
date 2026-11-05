"""Candle resampling helpers."""

from __future__ import annotations

from typing import Iterable

from dogeshtrader.core.models import Candle


_PANDAS_FREQ_MAP = {
    "minute": "1min",
    "3minute": "3min",
    "5minute": "5min",
    "10minute": "10min",
    "15minute": "15min",
    "30minute": "30min",
    "60minute": "60min",
    "day": "1D",
}


def resample_candles(candles: Iterable[Candle], target_interval: str) -> list[Candle]:
    """Aggregate 1-minute candles to a coarser interval.

    Uses pandas internally; not optimised for very large streams, but plenty
    for retail backtest sizes.
    """
    import pandas as pd

    freq = _PANDAS_FREQ_MAP.get(target_interval)
    if freq is None:
        raise ValueError(f"unsupported target_interval {target_interval!r}")

    rows = [
        {
            "timestamp": c.timestamp,
            "open": c.open,
            "high": c.high,
            "low": c.low,
            "close": c.close,
            "volume": c.volume,
            "oi": c.oi,
        }
        for c in candles
    ]
    if not rows:
        return []
    df = pd.DataFrame(rows)
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    df = df.set_index("timestamp")
    agg = df.resample(freq, label="left", closed="left").agg(
        {
            "open": "first",
            "high": "max",
            "low": "min",
            "close": "last",
            "volume": "sum",
            "oi": "last",
        }
    )
    agg = agg.dropna(subset=["open", "high", "low", "close"])
    return [
        Candle(
            timestamp=ts.to_pydatetime(),
            open=float(row["open"]),
            high=float(row["high"]),
            low=float(row["low"]),
            close=float(row["close"]),
            volume=int(row["volume"]) if pd.notna(row["volume"]) else 0,
            oi=int(row["oi"]) if pd.notna(row["oi"]) else None,
        )
        for ts, row in agg.iterrows()
    ]


__all__ = ["resample_candles"]
