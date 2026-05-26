"""Kite adapter tests using a stubbed kiteconnect client.

We never touch real network; the stub mirrors the methods we depend on.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

import pytest

from dogeshtrader.adapters.kite.instruments import KiteInstrumentsAdapter
from dogeshtrader.adapters.kite.margins import KiteMarginsAdapter
from dogeshtrader.adapters.kite.market_data import KiteMarketDataAdapter
from dogeshtrader.adapters.kite.orders import KiteOrdersAdapter
from dogeshtrader.adapters.kite.portfolio import KitePortfolioAdapter
from dogeshtrader.adapters.kite.postbacks import _verify
from dogeshtrader.auth.kite_auth import build_login_url, compute_checksum
from dogeshtrader.brokers.kite_broker import KiteBroker
from dogeshtrader.core.enums import Exchange, OrderStatus, OrderType, Product, Side, Variety
from dogeshtrader.core.models import Instrument, OrderRequest


@dataclass
class _StubKite:
    instruments_payload: list[dict[str, Any]]
    historical_payload: list[dict[str, Any]]
    ltp_payload: dict[str, dict[str, float]]
    order_id_seq: list[str]
    order_history_payload: dict[str, list[dict[str, Any]]]
    placed: list[dict[str, Any]]

    # --- instruments
    def instruments(self, exchange: str | None = None) -> list[dict[str, Any]]:
        return self.instruments_payload

    # --- historical / ltp / ohlc / quote
    def historical_data(self, **kw: Any) -> list[dict[str, Any]]:
        return self.historical_payload

    def ltp(self, keys: list[str]) -> dict[str, dict[str, float]]:
        return self.ltp_payload

    def ohlc(self, keys: list[str]) -> dict[str, Any]:
        return {k: {"ohlc": {"open": 1, "high": 2, "low": 0, "close": 1}} for k in keys}

    def quote(self, keys: list[str]) -> dict[str, Any]:
        return {k: {"last_price": 1.0} for k in keys}

    # --- orders
    def place_order(self, variety: str, **payload: Any) -> str:
        self.placed.append({"variety": variety, **payload})
        return self.order_id_seq.pop(0)

    def order_history(self, order_id: str) -> list[dict[str, Any]]:
        return self.order_history_payload.get(order_id, [])

    def cancel_order(self, variety: str, order_id: str) -> str:
        return order_id

    def modify_order(self, **kw: Any) -> str:
        return kw.get("order_id", "")

    def orders(self) -> list[dict[str, Any]]:
        return [
            {"order_id": "121", "status": "COMPLETE", "filled_quantity": 5, "pending_quantity": 0},
        ]

    def trades(self) -> list[dict[str, Any]]:
        return [
            {
                "order_id": "121",
                "trade_id": "T1",
                "exchange": "NSE",
                "tradingsymbol": "INFY",
                "transaction_type": "BUY",
                "quantity": 5,
                "average_price": 1500.5,
                "fill_timestamp": "2024-01-01T10:00:00",
                "instrument_token": 408065,
            }
        ]

    def order_trades(self, order_id: str) -> list[dict[str, Any]]:
        return self.trades()

    # --- portfolio
    def positions(self) -> dict[str, Any]:
        return {
            "net": [
                {
                    "exchange": "NSE",
                    "tradingsymbol": "INFY",
                    "instrument_token": 408065,
                    "quantity": 5,
                    "average_price": 1500.0,
                    "realised": 50.0,
                    "unrealised": -10.0,
                    "last_price": 1490.0,
                    "multiplier": 1,
                }
            ],
            "day": [],
        }

    def holdings(self) -> list[dict[str, Any]]:
        return []

    # --- margins
    def order_margins(self, payloads: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [{"total": 12345.0, "exposure": 100.0}]

    def basket_order_margins(self, payloads: list[dict[str, Any]], **kw: Any) -> dict[str, Any]:
        return {"initial": {"total": 1000.0}, "final": {"total": 2000.0}}

    def virtual_contract_note(self, payloads: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [{"charges": {"total": 25.0}}]


@pytest.fixture()
def stub_kite() -> _StubKite:
    return _StubKite(
        instruments_payload=[
            {
                "exchange": "NSE",
                "tradingsymbol": "INFY",
                "instrument_token": 408065,
                "exchange_token": 1594,
                "name": "INFOSYS LIMITED",
                "instrument_type": "EQ",
                "segment": "NSE",
                "expiry": "",
                "strike": 0,
                "tick_size": 0.05,
                "lot_size": 1,
            },
            {
                "exchange": "BAD",  # should be filtered
                "tradingsymbol": "JUNK",
            },
        ],
        historical_payload=[
            {
                "date": datetime(2024, 1, 1, 9, 15),
                "open": 1500.0,
                "high": 1510.0,
                "low": 1495.0,
                "close": 1505.0,
                "volume": 1000,
            }
        ],
        ltp_payload={"NSE:INFY": {"last_price": 1500.0}},
        order_id_seq=["201"],
        order_history_payload={
            "201": [
                {
                    "order_id": "201",
                    "status": "COMPLETE",
                    "filled_quantity": 5,
                    "pending_quantity": 0,
                    "average_price": 1500.0,
                    "exchange_order_id": "x42",
                    "exchange_timestamp": "2024-01-01T10:00:00",
                }
            ]
        },
        placed=[],
    )


def test_instruments_adapter_normalizes(stub_kite: _StubKite) -> None:
    adapter = KiteInstrumentsAdapter(stub_kite)
    result = adapter.download()
    assert len(result) == 1
    inst = result[0]
    assert inst.tradingsymbol == "INFY"
    assert inst.instrument_token == 408065


def test_market_data_historical_emits_candles(stub_kite: _StubKite) -> None:
    adapter = KiteMarketDataAdapter(stub_kite)
    instrument = Instrument(
        exchange=Exchange.NSE, tradingsymbol="INFY", instrument_token=408065
    )
    candles = list(
        adapter.historical(
            instrument,
            from_dt=date(2024, 1, 1),
            to_dt=date(2024, 1, 1),
            interval="5minute",
        )
    )
    assert len(candles) == 1
    assert candles[0].close == 1505.0


def test_kite_broker_place_order_translates_payload(stub_kite: _StubKite) -> None:
    broker = KiteBroker(stub_kite)
    inst = Instrument(exchange=Exchange.NSE, tradingsymbol="INFY", instrument_token=408065)
    request = OrderRequest(
        intent_id="i_1",
        strategy_id="t",
        instrument=inst,
        side=Side.BUY,
        quantity=5,
        order_type=OrderType.MARKET,
        product=Product.MIS,
        variety=Variety.REGULAR,
        tag="dogesh_t_001",
        idempotency_key="ABC",
    )
    update = broker.place_order(request)
    assert update.order_id == "201"
    assert update.status == OrderStatus.COMPLETE
    assert stub_kite.placed[0]["transaction_type"] == "BUY"
    assert stub_kite.placed[0]["tag"] == "dogesh_t_001"


def test_kite_broker_positions(stub_kite: _StubKite) -> None:
    broker = KiteBroker(stub_kite)
    positions = broker.positions()
    assert "NSE:INFY" in positions
    assert positions["NSE:INFY"].quantity == 5


def test_kite_orders_adapter(stub_kite: _StubKite) -> None:
    adapter = KiteOrdersAdapter(stub_kite)
    assert len(adapter.orders()) == 1
    assert adapter.trades()[0]["trade_id"] == "T1"


def test_kite_portfolio_adapter(stub_kite: _StubKite) -> None:
    adapter = KitePortfolioAdapter(stub_kite)
    positions = adapter.positions()
    assert positions["net"][0]["tradingsymbol"] == "INFY"


def test_kite_margins_adapter(stub_kite: _StubKite) -> None:
    adapter = KiteMarginsAdapter(stub_kite)
    inst = Instrument(exchange=Exchange.NSE, tradingsymbol="INFY")
    req = OrderRequest(
        intent_id="i_1",
        strategy_id="t",
        instrument=inst,
        side=Side.BUY,
        quantity=1,
        order_type=OrderType.MARKET,
        product=Product.MIS,
        variety=Variety.REGULAR,
        tag="dogesh_t_001",
        idempotency_key="ABC",
    )
    assert adapter.estimate_order_margin(req)["total"] == 12345.0


def test_login_url_and_checksum() -> None:
    url = build_login_url("api_key_x")
    assert url.startswith("https://kite.zerodha.com/connect/login?")
    assert "api_key=api_key_x" in url
    assert compute_checksum("a", "b", "c").isalnum()


def test_postback_checksum_verification() -> None:
    import hashlib

    payload = {"order_id": "X1", "order_timestamp": "2022-03-03 09:24:25", "checksum": ""}
    secret = "topsecret"
    expected = hashlib.sha256(
        f"{payload['order_id']}{payload['order_timestamp']}{secret}".encode()
    ).hexdigest()
    payload["checksum"] = expected
    assert _verify(payload, secret)
    # tampered
    payload["order_id"] = "X2"
    assert not _verify(payload, secret)
