"""Kite WebSocket and GTT adapters against fake SDK objects."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from unittest.mock import MagicMock

import pytest

from dogeshtrader.adapters.kite import websocket as ws_module
from dogeshtrader.adapters.kite.gtt import KiteGTTAdapter
from dogeshtrader.adapters.kite.websocket import KiteWebSocketAdapter, _to_tick
from dogeshtrader.core.errors import BrokerError
from dogeshtrader.core.models import Tick


class _FakeTicker:
    MODE_LTP = "ltp"
    MODE_QUOTE = "quote"
    MODE_FULL = "full"
    last: _FakeTicker | None = None

    def __init__(self, api_key: str, access_token: str) -> None:
        self.subscribed: list[int] = []
        self.mode: tuple[str, list[int]] | None = None
        self.closed = False
        _FakeTicker.last = self

    def connect(self, threaded: bool = True) -> None:
        self.on_connect(self, {})

    def subscribe(self, tokens: list[int]) -> None:
        self.subscribed = tokens

    def set_mode(self, mode: str, tokens: list[int]) -> None:
        self.mode = (mode, tokens)

    def close(self) -> None:
        self.closed = True


@pytest.fixture()
def fake_ticker(monkeypatch: pytest.MonkeyPatch) -> type[_FakeTicker]:
    import kiteconnect

    monkeypatch.setattr(kiteconnect, "KiteTicker", _FakeTicker)
    return _FakeTicker


def test_connect_subscribes_and_streams_ticks(fake_ticker: type[_FakeTicker]) -> None:
    adapter = KiteWebSocketAdapter(api_key="k", access_token="t")
    ticks: list[Tick] = []
    orders: list[dict[str, Any]] = []
    connected: list[bool] = []
    disconnected: list[Exception | None] = []
    adapter.on_tick(ticks.append)
    adapter.on_tick(lambda _t: (_ for _ in ()).throw(RuntimeError("bad callback")))
    adapter.on_order_update(orders.append)
    adapter.on_connect(lambda: connected.append(True))
    adapter.on_disconnect(disconnected.append)

    adapter.connect(tokens=[1, 2], mode="full")
    kt = fake_ticker.last
    assert kt is not None
    assert kt.subscribed == [1, 2]
    assert kt.mode == ("full", [1, 2])
    assert connected == [True]

    kt.on_ticks(kt, [{"instrument_token": 1, "last_price": 10.0}, {"last_price": 5.0}])
    kt.on_order_update(kt, {"order_id": "9"})
    kt.on_error(kt, 1006, "boom")
    kt.on_close(kt, 1000, "bye")

    assert [t.instrument_token for t in ticks] == [1]
    assert orders == [{"order_id": "9"}]
    assert disconnected == [None]

    adapter.stop()
    assert kt.closed


def test_invalid_mode_is_rejected(fake_ticker: type[_FakeTicker]) -> None:
    with pytest.raises(BrokerError, match="invalid subscription mode"):
        KiteWebSocketAdapter(api_key="k", access_token="t").connect(tokens=[1], mode="turbo")


def test_stop_before_connect_is_noop() -> None:
    KiteWebSocketAdapter(api_key="k", access_token="t").stop()


class TestToTick:
    def test_full_mode_payload(self) -> None:
        tick = _to_tick(
            {
                "instrument_token": 408065,
                "last_price": 1500.5,
                "exchange_timestamp": "2024-01-02T10:00:00",
                "volume_traded": 1200,
                "average_traded_price": 1499.0,
                "oi": "",
                "depth": {
                    "buy": [{"price": 1500.0, "quantity": 10, "orders": 2}],
                    "sell": [{"price": 1501.0, "quantity": 5, "orders": 1}],
                },
            }
        )
        assert tick is not None
        assert tick.timestamp == datetime(2024, 1, 2, 10, 0)
        assert (tick.bid, tick.ask) == (1500.0, 1501.0)
        assert tick.volume_traded == 1200
        assert tick.oi is None

    @pytest.mark.parametrize(
        "raw",
        [
            {"last_price": 10.0},
            {"instrument_token": 1},
            {"instrument_token": 1, "last_price": 0},
            {"instrument_token": 1, "last_price": "n/a"},
        ],
        ids=["no_token", "no_price", "zero_price", "garbage_price"],
    )
    def test_unusable_ticks_are_dropped(self, raw: dict[str, Any]) -> None:
        assert _to_tick(raw) is None

    def test_bad_timestamp_falls_back_to_now(self) -> None:
        tick = _to_tick({"instrument_token": 1, "last_price": 1.0, "timestamp": "garbage"})
        assert tick is not None
        assert abs((datetime.now() - tick.timestamp).total_seconds()) < 5

    def test_numeric_helpers(self) -> None:
        assert ws_module._int_or_none("x") is None
        assert ws_module._float_or_none(object()) is None


def test_gtt_adapter_round_trip() -> None:
    kite = MagicMock()
    kite.get_gtts.return_value = [{"id": 1}]
    kite.get_gtt.return_value = {"id": 1}
    kite.place_gtt.return_value = {"trigger_id": 7}
    kite.modify_gtt.return_value = {"trigger_id": 7}
    kite.delete_gtt.return_value = {"trigger_id": 7}
    gtt = KiteGTTAdapter(kite)
    order = {"transaction_type": "SELL", "quantity": 1, "price": 90.0}
    common = {
        "trigger_type": "single",
        "tradingsymbol": "INFY",
        "exchange": "NSE",
        "trigger_values": [90.0],
        "last_price": 100.0,
        "orders": [order],
    }

    assert gtt.list() == [{"id": 1}]
    assert gtt.get(1) == {"id": 1}
    assert gtt.place(**common) == {"trigger_id": 7}
    assert gtt.modify(trigger_id=7, **common) == {"trigger_id": 7}
    assert gtt.delete(7) == {"trigger_id": 7}


@pytest.mark.parametrize("method", ["get_gtts", "get_gtt", "delete_gtt"])
def test_gtt_failures_become_broker_errors(method: str) -> None:
    kite = MagicMock()
    getattr(kite, method).side_effect = RuntimeError("down")
    gtt = KiteGTTAdapter(kite)
    call = {
        "get_gtts": gtt.list,
        "get_gtt": lambda: gtt.get(1),
        "delete_gtt": lambda: gtt.delete(1),
    }
    with pytest.raises(BrokerError):
        call[method]()
