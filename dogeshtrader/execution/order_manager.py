"""In-memory order book / lifecycle manager.

The OrderManager mirrors what the broker reports (or, in PAPER/BACKTEST, what
the simulator emits). It's the single source of truth used by the strategy
context to surface positions and open orders without going back to the broker.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable

from dogeshtrader.core.enums import TERMINAL_ORDER_STATUSES
from dogeshtrader.core.models import (
    Fill,
    Instrument,
    OrderRequest,
    OrderUpdate,
    Position,
)


class OrderManager:
    def __init__(self) -> None:
        self._orders: dict[str, OrderUpdate] = {}
        self._requests: dict[str, OrderRequest] = {}
        self._fills_by_order: dict[str, list[Fill]] = defaultdict(list)
        self._positions: dict[str, Position] = {}

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def register_request(self, request: OrderRequest) -> None:
        self._requests[request.intent_id] = request

    def apply_update(self, update: OrderUpdate) -> None:
        self._orders[update.order_id] = update

    def apply_fill(self, fill: Fill) -> None:
        self._fills_by_order[fill.order_id].append(fill)
        pos = self._positions.setdefault(fill.instrument.key, Position(instrument=fill.instrument))
        pos.apply_fill(fill)

    def mark_positions(self, last_prices: dict[str, float]) -> None:
        for key, pos in self._positions.items():
            price = last_prices.get(key)
            if price is not None:
                pos.mark(price)

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def positions(self) -> dict[str, Position]:
        return dict(self._positions)

    def position(self, instrument: Instrument) -> Position:
        return self._positions.get(instrument.key, Position(instrument=instrument))

    def open_orders(self) -> list[OrderUpdate]:
        return [o for o in self._orders.values() if o.status not in TERMINAL_ORDER_STATUSES]

    def all_orders(self) -> list[OrderUpdate]:
        return list(self._orders.values())

    def fills(self, order_id: str | None = None) -> list[Fill]:
        if order_id:
            return list(self._fills_by_order.get(order_id, []))
        return [f for fills in self._fills_by_order.values() for f in fills]

    def request_for(self, intent_id: str) -> OrderRequest | None:
        return self._requests.get(intent_id)

    def aggregate_pnl(self) -> tuple[float, float]:
        realised = sum(p.realised_pnl for p in self._positions.values())
        unrealised = sum(p.unrealised_pnl for p in self._positions.values())
        return realised, unrealised

    def reset(self) -> None:
        self._orders.clear()
        self._requests.clear()
        self._fills_by_order.clear()
        self._positions.clear()

    def import_positions(self, positions: Iterable[Position]) -> None:
        for p in positions:
            self._positions[p.instrument.key] = p


__all__ = ["OrderManager"]
