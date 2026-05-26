"""PaperBroker tests."""

from __future__ import annotations

from datetime import datetime

from dogeshtrader.brokers.paper_broker import PaperBroker, PaperConfig
from dogeshtrader.core.enums import OrderStatus, OrderType, Product, Side, Variety
from dogeshtrader.core.models import Candle, Instrument, OrderRequest


def _req(infy: Instrument, **kwargs) -> OrderRequest:
    defaults = {
        "intent_id": "intent_1",
        "strategy_id": "t",
        "instrument": infy,
        "side": Side.BUY,
        "quantity": 10,
        "order_type": OrderType.MARKET,
        "product": Product.MIS,
        "variety": Variety.REGULAR,
        "tag": "dogesh_t_001",
        "idempotency_key": "ABC",
    }
    defaults.update(kwargs)
    return OrderRequest(**defaults)


def test_market_order_fills_on_candle_open(infy: Instrument) -> None:
    broker = PaperBroker(
        config=PaperConfig(starting_cash=100_000, fill_market_on_next_candle_open=True)
    )
    broker.place_order(_req(infy))
    broker.feed_candle(
        infy,
        Candle(
            timestamp=datetime(2024, 1, 1, 10, 0),
            open=1500.0,
            high=1510.0,
            low=1490.0,
            close=1505.0,
        ),
    )
    orders = broker.orders()
    assert orders[0].status == OrderStatus.COMPLETE
    fills = broker.trades()
    assert len(fills) == 1
    assert fills[0].price == 1500.0  # opened at candle open
    # Cash debited.
    assert broker.cash < 100_000


def test_limit_buy_fills_when_low_crosses(infy: Instrument) -> None:
    broker = PaperBroker(config=PaperConfig(starting_cash=100_000))
    broker.place_order(_req(infy, order_type=OrderType.LIMIT, price=1480.0))
    broker.feed_candle(
        infy,
        Candle(
            timestamp=datetime(2024, 1, 1, 10, 0),
            open=1500.0,
            high=1505.0,
            low=1475.0,
            close=1490.0,
        ),
    )
    orders = broker.orders()
    assert orders[0].status == OrderStatus.COMPLETE
    fill = broker.trades()[0]
    assert fill.price == 1480.0


def test_limit_buy_does_not_fill_above_open_when_pessimistic(infy: Instrument) -> None:
    broker = PaperBroker(config=PaperConfig(starting_cash=100_000, optimistic_limits=False))
    broker.place_order(_req(infy, order_type=OrderType.LIMIT, price=1480.0))
    # Candle never touches 1480 -> no fill.
    broker.feed_candle(
        infy,
        Candle(
            timestamp=datetime(2024, 1, 1, 10, 0),
            open=1500.0,
            high=1510.0,
            low=1495.0,
            close=1505.0,
        ),
    )
    assert broker.orders()[0].status == OrderStatus.OPEN


def test_position_updates_on_fill(infy: Instrument) -> None:
    broker = PaperBroker(config=PaperConfig(starting_cash=100_000))
    broker.place_order(_req(infy, quantity=5))
    broker.feed_candle(
        infy,
        Candle(
            timestamp=datetime(2024, 1, 1, 10, 0),
            open=1500.0,
            high=1510.0,
            low=1490.0,
            close=1505.0,
        ),
    )
    pos = broker.positions()[infy.key]
    assert pos.quantity == 5
    assert pos.avg_price == 1500.0


def test_cancel_order(infy: Instrument) -> None:
    broker = PaperBroker()
    upd = broker.place_order(_req(infy, order_type=OrderType.LIMIT, price=100.0))
    cancelled = broker.cancel_order(upd.order_id)
    assert cancelled.status == OrderStatus.CANCELLED


def test_modify_order_updates_only_target_order(infy: Instrument) -> None:
    broker = PaperBroker()
    first = broker.place_order(
        _req(infy, intent_id="intent_a", order_type=OrderType.LIMIT, price=1450.0, quantity=10)
    )
    second = broker.place_order(
        _req(infy, intent_id="intent_b", order_type=OrderType.LIMIT, price=1500.0, quantity=20)
    )

    modified = broker.modify_order(second.order_id, price=1510.0, quantity=15)
    first_after = broker.order_history(first.order_id)[0]

    assert modified.order_id == second.order_id
    assert modified.price == 1510.0
    assert modified.pending_quantity == 15
    assert first_after.price == 1450.0
    assert first_after.pending_quantity == 10
