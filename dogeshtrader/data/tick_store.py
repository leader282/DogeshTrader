"""Parquet tick store.

Append-only; we rotate by day to keep individual files manageable.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Iterable

from dogeshtrader.core.models import Tick


class TickStore:
    def __init__(self, root: Path | str) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def path_for(self, instrument_token: int, day: date) -> Path:
        return (
            self.root
            / "ticks"
            / str(instrument_token)
            / f"{day.isoformat()}.parquet"
        )

    def append(self, ticks: Iterable[Tick]) -> int:
        import pandas as pd

        ticks_list = list(ticks)
        if not ticks_list:
            return 0
        rows = [
            {
                "timestamp": t.timestamp,
                "instrument_token": t.instrument_token,
                "ltp": t.ltp,
                "last_quantity": t.last_quantity,
                "volume_traded": t.volume_traded,
                "average_traded_price": t.average_traded_price,
                "total_buy_quantity": t.total_buy_quantity,
                "total_sell_quantity": t.total_sell_quantity,
                "oi": t.oi,
                "bid": t.bid,
                "ask": t.ask,
            }
            for t in ticks_list
        ]
        df = pd.DataFrame(rows)
        df["timestamp"] = pd.to_datetime(df["timestamp"])
        # Group by (token, date) and append per day.
        df["date_"] = df["timestamp"].dt.date
        for (token, day), grp in df.groupby(["instrument_token", "date_"]):
            path = self.path_for(int(token), day)
            path.parent.mkdir(parents=True, exist_ok=True)
            grp = grp.drop(columns=["date_"])
            if path.exists():
                grp = pd.concat([pd.read_parquet(path), grp], ignore_index=True)
            # Kite stamps ticks to the second, so several distinct ticks can share
            # a timestamp. Only exact repeats (e.g. a replayed batch) are dropped.
            grp.drop_duplicates(keep="first").to_parquet(path, index=False)
        return len(ticks_list)


__all__ = ["TickStore"]
