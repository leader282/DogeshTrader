"""KiteBroker translation and error handling, with a stubbed SDK client."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from dogeshtrader.brokers.kite_broker import KiteBroker
from dogeshtrader.core.enums import Exchange, OrderStatus, OrderType, Product, Side, Variety
from dogeshtrader.core.errors import BrokerError, OrderRejected
from dogeshtrader.core.models import Instrument, OrderRequest

INFY = Instrument(exchange=Exchange.NSE, tradingsymbol="INFY", instrument_token=408065)


def _request(**overrides: Any) -> OrderRequest:
    fields: dict[str, Any] = {
        "intent_id": "i1",
        "strategy_id": "s",
        "instrument": INFY,
        "side": Side.SELL,
        "quantity": 3,
        "order_type": OrderType.SL,
        "product": Product.MIS,
        "variety": Variety.REGULAR,
        "price": 99.0,
        "trigger_price": 100.0,
        "tag": "dogesh_s",
        "idempotency_key": "k",
        **overrides,
    }
    return OrderRequest(**fields)


def test_payload_carries_prices_and_iceberg_fields() -> None:
    payload = KiteBroker._request_to_kite_payload(
        _request(disclosed_quantity=1, iceberg_legs=2, iceberg_quantity=2)
    )
    assert payload["transaction_type"] == "SELL"
    assert payload["price"] == 99.0
    assert payload["trigger_price"] == 100.0
    assert payload["iceberg_legs"] == 2
    assert payload["disclosed_quantity"] == 1


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("TRIGGER PENDING", OrderStatus.OPEN),
        ("complete", OrderStatus.COMPLETE),
        ("VALIDATION PENDING", OrderStatus.SUBMITTED),
        ("SOMETHING NEW", OrderStatus.UNKNOWN),
        (None, OrderStatus.UNKNOWN),
    ],
)
def test_status_mapping(raw: str | None, expected: OrderStatus) -> None:
    assert KiteBroker._to_kite_status(raw) is expected


def test_positions_use_net_bucket_only() -> None:
    kite = MagicMock()
    kite.positions.return_value = {
        "net": [{"exchange": "NSE", "tradingsymbol": "INFY", "quantity": 10}],
        "day": [{"exchange": "NSE", "tradingsymbol": "INFY", "quantity": -5}],
    }
    assert KiteBroker(kite).positions()["NSE:INFY"].quantity == 10


def test_place_order_rejection_is_wrapped() -> None:
    kite = MagicMock()
    kite.place_order.side_effect = RuntimeError("insufficient margin")
    with pytest.raises(OrderRejected, match="insufficient margin"):
        KiteBroker(kite).place_order(_request())


def test_place_order_without_history_reports_submitted() -> None:
    kite = MagicMock()
    kite.place_order.return_value = "9"
    kite.order_history.return_value = []
    update = KiteBroker(kite).place_order(_request())
    assert update.order_id == "9"
    assert update.status is OrderStatus.SUBMITTED


def test_modify_forwards_only_given_fields() -> None:
    kite = MagicMock()
    kite.order_history.return_value = [{"order_id": "9", "status": "OPEN"}]
    KiteBroker(kite).modify_order("9", price=101.0)
    kite.modify_order.assert_called_once_with(variety="regular", order_id="9", price=101.0)


def test_cancel_returns_latest_state() -> None:
    kite = MagicMock()
    kite.order_history.return_value = [{"order_id": "9", "status": "CANCELLED"}]
    assert KiteBroker(kite).cancel_order("9").status is OrderStatus.CANCELLED


def test_trades_and_holdings_normalise() -> None:
    kite = MagicMock()
    kite.order_trades.return_value = [
        {
            "order_id": "9",
            "trade_id": "t1",
            "exchange": "NSE",
            "tradingsymbol": "INFY",
            "transaction_type": "BUY",
            "quantity": 2,
            "average_price": 1500.0,
            "fill_timestamp": "not-a-date",
        }
    ]
    kite.holdings.return_value = [{"exchange": "NSE", "tradingsymbol": "TCS", "quantity": 4}]
    kite.ltp.return_value = {"NSE:INFY": {"last_price": 1501.5}}
    broker = KiteBroker(kite)

    fill = broker.trades("9")[0]
    assert fill.side is Side.BUY
    assert fill.price == 1500.0
    assert broker.holdings()["NSE:TCS"].quantity == 4
    assert broker.quote_ltp([INFY]) == {"NSE:INFY": 1501.5}


@pytest.mark.parametrize(
    ("method", "call"),
    [
        ("orders", lambda b: b.orders()),
        ("order_history", lambda b: b.order_history("9")),
        ("trades", lambda b: b.trades()),
        ("positions", lambda b: b.positions()),
        ("holdings", lambda b: b.holdings()),
        ("ltp", lambda b: b.quote_ltp([INFY])),
        ("cancel_order", lambda b: b.cancel_order("9")),
        ("modify_order", lambda b: b.modify_order("9", quantity=1)),
    ],
)
def test_sdk_failures_become_broker_errors(method: str, call: Any) -> None:
    kite = MagicMock()
    getattr(kite, method).side_effect = RuntimeError("network down")
    with pytest.raises(BrokerError):
        call(KiteBroker(kite))
