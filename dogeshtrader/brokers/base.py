"""Common broker base class.

Concrete brokers implement the IBroker interface and may register listeners
for synchronous fill / update events.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable

from dogeshtrader.core.interfaces import IBroker
from dogeshtrader.core.models import (
    Fill,
    Instrument,
    OrderRequest,
    OrderUpdate,
    Position,
)

OrderUpdateCallback = Callable[[OrderUpdate], None]
FillCallback = Callable[[Fill], None]


class BaseBroker(IBroker):
    """Concrete brokers should inherit this for callback wiring."""

    mode: str = "PAPER"

    def __init__(self) -> None:
        self._order_update_cbs: list[OrderUpdateCallback] = []
        self._fill_cbs: list[FillCallback] = []

    def on_order_update(self, callback: OrderUpdateCallback) -> None:
        self._order_update_cbs.append(callback)

    def on_fill(self, callback: FillCallback) -> None:
        self._fill_cbs.append(callback)

    # ------------------------------------------------------------------
    # Helpers for subclasses
    # ------------------------------------------------------------------

    def _emit_order_update(self, update: OrderUpdate) -> None:
        for cb in list(self._order_update_cbs):
            try:
                cb(update)
            except Exception:
                # callbacks must never break the broker - log via stdlib later
                pass

    def _emit_fill(self, fill: Fill) -> None:
        for cb in list(self._fill_cbs):
            try:
                cb(fill)
            except Exception:
                pass

    # ------------------------------------------------------------------
    # IBroker stubs - subclasses must override
    # ------------------------------------------------------------------

    def place_order(self, request: OrderRequest) -> OrderUpdate:  # pragma: no cover
        raise NotImplementedError

    def cancel_order(
        self, order_id: str, variety: str | None = None
    ) -> OrderUpdate:  # pragma: no cover
        raise NotImplementedError

    def modify_order(
        self,
        order_id: str,
        *,
        quantity: int | None = None,
        price: float | None = None,
        trigger_price: float | None = None,
        order_type: str | None = None,
        validity: str | None = None,
    ) -> OrderUpdate:  # pragma: no cover
        raise NotImplementedError

    def orders(self) -> list[OrderUpdate]:  # pragma: no cover
        return []

    def order_history(self, order_id: str) -> list[OrderUpdate]:  # pragma: no cover
        return []

    def trades(self, order_id: str | None = None) -> list[Fill]:  # pragma: no cover
        return []

    def positions(self) -> dict[str, Position]:  # pragma: no cover
        return {}

    def holdings(self) -> dict[str, Position]:  # pragma: no cover
        return {}

    def quote_ltp(self, instruments: Iterable[Instrument]) -> dict[str, float]:  # pragma: no cover
        return {}


__all__ = ["BaseBroker", "FillCallback", "OrderUpdateCallback"]
