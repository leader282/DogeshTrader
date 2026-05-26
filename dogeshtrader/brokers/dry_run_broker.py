"""Dry-run broker.

Never places real orders. Optionally consults a real Kite client for
margin / charge estimates if the operator wants to validate that the order
would also be accepted by Kite without contention.
"""

from __future__ import annotations

import itertools
from collections.abc import Iterable
from datetime import datetime
from typing import Any

from dogeshtrader.brokers.base import BaseBroker
from dogeshtrader.constants import ORDER_INTERNAL_PREFIX
from dogeshtrader.core.enums import OrderStatus
from dogeshtrader.core.errors import OrderRejected
from dogeshtrader.core.models import (
    Fill,
    Instrument,
    OrderRequest,
    OrderUpdate,
    Position,
)


class DryRunBroker(BaseBroker):
    """Validates orders, returns SUBMITTED/COMPLETE-style updates without doing anything."""

    mode = "DRY_RUN"

    def __init__(self, *, kite_adapter: Any | None = None) -> None:
        super().__init__()
        self._counter = itertools.count(1)
        self._orders: dict[str, OrderUpdate] = {}
        self._intent_to_order: dict[str, str] = {}
        self._kite = kite_adapter  # optional KiteClient for margin checks

    def place_order(self, request: OrderRequest) -> OrderUpdate:
        if request.quantity <= 0:
            raise OrderRejected("non-positive quantity")
        order_id = f"{ORDER_INTERNAL_PREFIX}d{next(self._counter):06d}"
        now = datetime.now()

        margin_check: dict[str, Any] = {}
        if self._kite is not None and hasattr(self._kite, "estimate_order_margin"):
            try:
                margin_check = self._kite.estimate_order_margin(request)
            except Exception as e:
                margin_check = {"error": str(e)}

        update = OrderUpdate(
            order_id=order_id,
            status=OrderStatus.SUBMITTED,
            pending_quantity=request.quantity,
            price=request.price,
            trigger_price=request.trigger_price,
            exchange_timestamp=now,
            status_message="dry-run validated",
            raw={
                "intent_id": request.intent_id,
                "tag": request.tag,
                "margin_check": margin_check,
                "would_send": _kite_payload_preview(request),
            },
        )
        self._orders[order_id] = update
        self._intent_to_order[request.intent_id] = order_id
        self._emit_order_update(update)
        return update

    def cancel_order(self, order_id: str, variety: str | None = None) -> OrderUpdate:
        update = self._orders.get(order_id)
        if not update:
            raise OrderRejected(f"unknown order_id {order_id}")
        update.status = OrderStatus.CANCELLED
        update.pending_quantity = 0
        self._emit_order_update(update)
        return update

    def modify_order(
        self,
        order_id: str,
        *,
        quantity: int | None = None,
        price: float | None = None,
        trigger_price: float | None = None,
        order_type: str | None = None,
        validity: str | None = None,
    ) -> OrderUpdate:
        update = self._orders.get(order_id)
        if not update:
            raise OrderRejected(f"unknown order_id {order_id}")
        if quantity is not None:
            update.pending_quantity = quantity
        if price is not None:
            update.price = price
        if trigger_price is not None:
            update.trigger_price = trigger_price
        update.status_message = "dry-run modified"
        self._emit_order_update(update)
        return update

    def orders(self) -> list[OrderUpdate]:
        return list(self._orders.values())

    def order_history(self, order_id: str) -> list[OrderUpdate]:
        update = self._orders.get(order_id)
        return [update] if update else []

    def trades(self, order_id: str | None = None) -> list[Fill]:
        return []

    def positions(self) -> dict[str, Position]:
        return {}

    def holdings(self) -> dict[str, Position]:
        return {}

    def quote_ltp(self, instruments: Iterable[Instrument]) -> dict[str, float]:
        return {i.key: 0.0 for i in instruments}


def _kite_payload_preview(request: OrderRequest) -> dict[str, Any]:
    """Approximate the Kite REST body that would be sent for a live request."""
    return {
        "tradingsymbol": request.instrument.tradingsymbol,
        "exchange": request.instrument.exchange.value,
        "transaction_type": request.side.value,
        "quantity": request.quantity,
        "product": request.product.value,
        "order_type": request.order_type.value,
        "validity": request.validity.value,
        "price": request.price,
        "trigger_price": request.trigger_price,
        "tag": request.tag,
        "variety": request.variety.value,
        "disclosed_quantity": request.disclosed_quantity,
        "iceberg_legs": request.iceberg_legs,
        "iceberg_quantity": request.iceberg_quantity,
    }


__all__ = ["DryRunBroker"]
