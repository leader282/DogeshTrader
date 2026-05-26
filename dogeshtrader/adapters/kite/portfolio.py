"""Kite portfolio adapter (holdings, positions, conversions)."""

from __future__ import annotations

from typing import Any

from dogeshtrader.core.errors import BrokerError


class KitePortfolioAdapter:
    def __init__(self, kite: Any) -> None:
        self.kite = kite

    def holdings(self) -> list[dict[str, Any]]:
        try:
            return list(self.kite.holdings() or [])
        except Exception as e:
            raise BrokerError(f"holdings failed: {e}") from e

    def positions(self) -> dict[str, list[dict[str, Any]]]:
        try:
            raw = self.kite.positions() or {}
        except Exception as e:
            raise BrokerError(f"positions failed: {e}") from e
        return {"net": list(raw.get("net") or []), "day": list(raw.get("day") or [])}

    def convert_position(
        self,
        *,
        exchange: str,
        tradingsymbol: str,
        transaction_type: str,
        position_type: str,
        quantity: int,
        old_product: str,
        new_product: str,
    ) -> dict[str, Any]:
        try:
            return dict(
                self.kite.convert_position(
                    exchange=exchange,
                    tradingsymbol=tradingsymbol,
                    transaction_type=transaction_type,
                    position_type=position_type,
                    quantity=quantity,
                    old_product=old_product,
                    new_product=new_product,
                )
                or {}
            )
        except Exception as e:
            raise BrokerError(f"convert_position failed: {e}") from e


__all__ = ["KitePortfolioAdapter"]
