from __future__ import annotations

from datetime import date, datetime

from dogeshtrader.core.clock import IST
from dogeshtrader.core.enums import Exchange
from dogeshtrader.core.models import Candle, Instrument
from dogeshtrader.data.candle_store import CandleStore


def test_read_filters_timezone_aware_candles_with_date_bounds(tmp_path) -> None:
    store = CandleStore(tmp_path)
    instrument = Instrument(exchange=Exchange.NSE, tradingsymbol="DEMO")
    candles = [
        Candle(
            timestamp=datetime(2024, 1, 1, 9, 15, tzinfo=IST),
            open=100.0,
            high=101.0,
            low=99.0,
            close=100.5,
            volume=1000,
        ),
        Candle(
            timestamp=datetime(2024, 1, 2, 9, 15, tzinfo=IST),
            open=102.0,
            high=103.0,
            low=101.0,
            close=102.5,
            volume=1000,
        ),
    ]

    store.upsert(instrument, "5minute", candles)

    result = store.read(
        instrument,
        "5minute",
        from_dt=date(2024, 1, 1),
        to_dt=date(2024, 1, 1),
    )

    assert len(result) == 1
    assert result[0].timestamp.date() == date(2024, 1, 1)
