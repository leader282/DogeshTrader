"""Kite GTT (Good-Till-Triggered) adapter."""

from __future__ import annotations

from typing import Any

from dogeshtrader.core.errors import BrokerError


class KiteGTTAdapter:
    def __init__(self, kite: Any) -> None:
        self.kite = kite

    def list(self) -> list[dict[str, Any]]:
        try:
            return list(self.kite.get_gtts() or [])
        except Exception as e:
            raise BrokerError(f"get_gtts failed: {e}") from e

    def get(self, trigger_id: int) -> dict[str, Any]:
        try:
            return dict(self.kite.get_gtt(trigger_id=trigger_id) or {})
        except Exception as e:
            raise BrokerError(f"get_gtt failed: {e}") from e

    def place(
        self,
        *,
        trigger_type: str,
        tradingsymbol: str,
        exchange: str,
        trigger_values: list[float],
        last_price: float,
        orders: list[dict[str, Any]],
    ) -> dict[str, Any]:
        try:
            return dict(
                self.kite.place_gtt(
                    trigger_type=trigger_type,
                    tradingsymbol=tradingsymbol,
                    exchange=exchange,
                    trigger_values=trigger_values,
                    last_price=last_price,
                    orders=orders,
                )
                or {}
            )
        except Exception as e:
            raise BrokerError(f"place_gtt failed: {e}") from e

    def modify(
        self,
        *,
        trigger_id: int,
        trigger_type: str,
        tradingsymbol: str,
        exchange: str,
        trigger_values: list[float],
        last_price: float,
        orders: list[dict[str, Any]],
    ) -> dict[str, Any]:
        try:
            return dict(
                self.kite.modify_gtt(
                    trigger_id=trigger_id,
                    trigger_type=trigger_type,
                    tradingsymbol=tradingsymbol,
                    exchange=exchange,
                    trigger_values=trigger_values,
                    last_price=last_price,
                    orders=orders,
                )
                or {}
            )
        except Exception as e:
            raise BrokerError(f"modify_gtt failed: {e}") from e

    def delete(self, trigger_id: int) -> dict[str, Any]:
        try:
            return dict(self.kite.delete_gtt(trigger_id=trigger_id) or {})
        except Exception as e:
            raise BrokerError(f"delete_gtt failed: {e}") from e


__all__ = ["KiteGTTAdapter"]
