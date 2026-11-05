"""Data quality, resampling, and on-disk stores."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd
import pytest

from dogeshtrader.core.clock import IST
from dogeshtrader.core.enums import Exchange
from dogeshtrader.core.models import Candle, Instrument, Tick
from dogeshtrader.data import CandleStore, InstrumentMaster, TickStore
from dogeshtrader.data.data_quality import candle_block_reason, check_candles
from dogeshtrader.data.resampler import resample_candles
from dogeshtrader.storage import Database

from .conftest import trending_session


def _bar(ts: datetime, price: float = 100.0, **kw: float) -> Candle:
    fields = {"open": price, "high": price + 1, "low": price - 1, "close": price, **kw}
    return Candle(timestamp=ts, volume=100, **fields)


T0 = datetime(2024, 1, 2, 9, 15, tzinfo=IST)


class TestCheckCandles:
    def test_clean_session_is_ok(self) -> None:
        report = check_candles(trending_session(date(2024, 1, 2)), interval="5minute")
        assert report.ok
        assert report.rows == 75
        assert report.gaps == []

    def test_flags_each_problem(self) -> None:
        candles = [
            _bar(T0),
            _bar(T0),  # duplicate
            _bar(T0 + timedelta(minutes=30)),  # gap
            _bar(T0 + timedelta(minutes=25)),  # out of order
            _bar(T0 + timedelta(minutes=35), high=90.0),  # high < low
            _bar(T0 + timedelta(minutes=40), price=0.5, low=-1.0),  # non-positive
        ]
        report = check_candles(candles, interval="5minute")
        assert not report.ok
        assert report.duplicates == 1
        assert report.out_of_order == 1
        assert len(report.gaps) == 1
        assert report.bad_ohlc == 1
        assert report.bad_prices == 1


class TestCandleBlockReason:
    @pytest.mark.parametrize(
        ("prev", "candle", "expected"),
        [
            (None, _bar(T0), None),
            (_bar(T0), _bar(T0 + timedelta(minutes=5)), None),
            (_bar(T0), _bar(T0 + timedelta(minutes=30)), "intraday_gap"),
            (_bar(T0), _bar(T0), "timestamp_not_advancing"),
            (_bar(T0), _bar(T0 + timedelta(days=1)), None),
            (None, _bar(T0, high=50.0), "bad_ohlc"),
            (None, _bar(T0, price=0.5, low=0.0), "non_positive_price"),
        ],
        ids=["first", "normal", "gap", "stuck", "overnight", "bad_ohlc", "zero"],
    )
    def test_reasons(self, prev: Candle | None, candle: Candle, expected: str | None) -> None:
        assert candle_block_reason(prev, candle, interval="5minute", multiple=2.0) == expected

    def test_disabled_multiple_skips_gap_check(self) -> None:
        later = _bar(T0 + timedelta(hours=2))
        assert candle_block_reason(_bar(T0), later, interval="5minute", multiple=0) is None


class TestResampler:
    def test_one_minute_to_five_minute(self) -> None:
        minutes = [
            _bar(T0 + timedelta(minutes=i), price=100 + i, high=200.0 if i == 2 else 101 + i)
            for i in range(10)
        ]
        out = resample_candles(minutes, "5minute")
        assert len(out) == 2
        first = out[0]
        assert first.open == 100
        assert first.close == 104
        assert first.high == 200.0
        assert first.low == 99
        assert first.volume == 500

    def test_empty_and_unsupported(self) -> None:
        assert resample_candles([], "5minute") == []
        with pytest.raises(ValueError, match="unsupported"):
            resample_candles([_bar(T0)], "7minute")


class TestCandleStore:
    def test_upsert_dedupes_and_iterates_chronologically(self, tmp_path: Path) -> None:
        store = CandleStore(tmp_path)
        a = Instrument(exchange=Exchange.NSE, tradingsymbol="AAA")
        b = Instrument(exchange=Exchange.NSE, tradingsymbol="BBB")
        day = trending_session(date(2024, 1, 2))
        store.upsert(a, "5minute", day[:10])
        store.upsert(a, "5minute", day[5:20])
        store.upsert(b, "5minute", day[:20])

        assert len(store.read(a, "5minute")) == 20
        merged = list(store.iter_chronological([a, b], "5minute"))
        stamps = [c.timestamp for _, c in merged]
        assert stamps == sorted(stamps)
        assert len(merged) == 40

    def test_missing_file_reads_empty(self, tmp_path: Path) -> None:
        inst = Instrument(exchange=Exchange.NSE, tradingsymbol="NONE")
        assert CandleStore(tmp_path).read(inst, "5minute") == []


def test_tick_store_keeps_same_second_ticks_and_drops_replays(tmp_path: Path) -> None:
    store = TickStore(tmp_path)
    ticks = [
        Tick(timestamp=datetime(2024, 1, 2, 10, 0), instrument_token=1, ltp=10.0),
        Tick(timestamp=datetime(2024, 1, 2, 10, 0), instrument_token=1, ltp=10.5),
        Tick(timestamp=datetime(2024, 1, 3, 10, 0), instrument_token=1, ltp=11.0),
    ]
    assert store.append(ticks) == 3
    store.append(ticks[:2])
    assert store.append([]) == 0

    day1 = pd.read_parquet(store.path_for(1, date(2024, 1, 2)))
    assert list(day1["ltp"]) == [10.0, 10.5]
    assert store.path_for(1, date(2024, 1, 3)).exists()


def test_instrument_master_roundtrip_and_cache(memory_db: Database) -> None:
    master = InstrumentMaster(memory_db)
    master.upsert_many(
        [Instrument(exchange=Exchange.NSE, tradingsymbol="INFY", instrument_token=408065)]
    )
    assert master.count() == 1
    by_key = master.get("NSE", "INFY")
    assert by_key is not None and by_key.instrument_token == 408065
    assert master.get_by_token(408065) == by_key
    assert master.get("NSE", "MISSING") is None
    assert master.get_by_token(1) is None
    fallback = master.ensure("NSE", "MISSING")
    assert fallback.instrument_token is None
