"""Kite margins / charges adapter.

Useful in DRY_RUN to ask Kite if an order would be acceptable margin-wise
*without* placing it. The official client exposes ``order_margins``,
``basket_order_margins`` and ``virtual_contract_note``.
"""

from __future__ import annotations

from typing import Any

from dogeshtrader.core.enums import Side
from dogeshtrader.core.errors import BrokerError
from dogeshtrader.core.models import OrderRequest


class KiteMarginsAdapter:
    def __init__(self, kite: Any) -> None:
        self.kite = kite

    def order_margins(self, payloads: list[dict[str, Any]]) -> list[dict[str, Any]]:
        try:
            return list(self.kite.order_margins(payloads) or [])
        except Exception as e:
            raise BrokerError(f"order_margins failed: {e}") from e

    def basket_order_margins(
        self,
        payloads: list[dict[str, Any]],
        *,
        consider_positions: bool = True,
        mode: str | None = None,
    ) -> dict[str, Any]:
        try:
            return dict(
                self.kite.basket_order_margins(
                    payloads,
                    consider_positions=consider_positions,
                    mode=mode,
                )
                or {}
            )
        except Exception as e:
            raise BrokerError(f"basket_order_margins failed: {e}") from e

    def order_charges(self, payloads: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Virtual contract note - estimates charges for hypothetical orders."""
        try:
            return list(self.kite.virtual_contract_note(payloads) or [])
        except Exception:
            try:
                return list(self.kite.order_charges(payloads) or [])
            except Exception as e2:
                raise BrokerError(f"order_charges failed: {e2}") from e2

    def estimate_order_margin(self, request: OrderRequest) -> dict[str, Any]:
        payload = {
            "exchange": request.instrument.exchange.value,
            "tradingsymbol": request.instrument.tradingsymbol,
            "transaction_type": "BUY" if request.side == Side.BUY else "SELL",
            "variety": request.variety.value,
            "product": request.product.value,
            "order_type": request.order_type.value,
            "quantity": request.quantity,
            "price": request.price or 0.0,
            "trigger_price": request.trigger_price or 0.0,
        }
        result = self.order_margins([payload])
        return result[0] if result else {}


__all__ = ["KiteMarginsAdapter"]
