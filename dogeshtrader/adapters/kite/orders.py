"""Kite orders adapter.

Most of the heavy lifting actually lives in :class:`KiteBroker` (under
``dogeshtrader.brokers``). This adapter is a thin, *non-broker* facade for
reading order state - useful for reconciliation and dashboards.
"""

from __future__ import annotations

from typing import Any

from dogeshtrader.core.errors import BrokerError


class KiteOrdersAdapter:
    def __init__(self, kite: Any) -> None:
        self.kite = kite

    def orders(self) -> list[dict[str, Any]]:
        try:
            return list(self.kite.orders() or [])
        except Exception as e:
            raise BrokerError(f"orders failed: {e}") from e

    def order_history(self, order_id: str) -> list[dict[str, Any]]:
        try:
            return list(self.kite.order_history(order_id=order_id) or [])
        except Exception as e:
            raise BrokerError(f"order_history failed: {e}") from e

    def trades(self) -> list[dict[str, Any]]:
        try:
            return list(self.kite.trades() or [])
        except Exception as e:
            raise BrokerError(f"trades failed: {e}") from e

    def order_trades(self, order_id: str) -> list[dict[str, Any]]:
        try:
            return list(self.kite.order_trades(order_id=order_id) or [])
        except Exception as e:
            raise BrokerError(f"order_trades failed: {e}") from e


__all__ = ["KiteOrdersAdapter"]
