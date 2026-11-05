"""Kite-backed live broker.

Thin translation layer over the official ``kiteconnect`` SDK:
* maps DogeshTrader enums -> Kite constants
* normalises responses back into framework models
* shields the rest of the framework from kiteconnect imports

This class is *only* used when ``mode == LIVE`` *and* the operator has
explicitly enabled live trading (config flag + CLI flag). The OrderRouter is
responsible for enforcing those guardrails.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
from typing import Any

from dogeshtrader.brokers.base import BaseBroker
from dogeshtrader.core.enums import OrderStatus, Side
from dogeshtrader.core.errors import BrokerError, OrderRejected
from dogeshtrader.core.models import (
    Fill,
    Instrument,
    OrderRequest,
    OrderUpdate,
    Position,
)
from dogeshtrader.logging_config import get_logger

_log = get_logger(__name__)


_KITE_STATUS_MAP = {
    "OPEN": OrderStatus.OPEN,
    "TRIGGER PENDING": OrderStatus.OPEN,
    "PUT ORDER REQ RECEIVED": OrderStatus.SUBMITTED,
    "VALIDATION PENDING": OrderStatus.SUBMITTED,
    "MODIFY PENDING": OrderStatus.OPEN,
    "MODIFY VALIDATION PENDING": OrderStatus.OPEN,
    "OPEN PENDING": OrderStatus.SUBMITTED,
    "COMPLETE": OrderStatus.COMPLETE,
    "CANCELLED": OrderStatus.CANCELLED,
    "REJECTED": OrderStatus.REJECTED,
    "CANCELLED AMO": OrderStatus.CANCELLED,
}


class KiteBroker(BaseBroker):
    """Wraps :class:`kiteconnect.KiteConnect` with normalised models.

    Construct via :class:`dogeshtrader.adapters.kite.client.KiteClient` to
    keep auth concerns out of this class.
    """

    mode = "LIVE"

    def __init__(self, kite: Any) -> None:
        super().__init__()
        self.kite = kite  # KiteConnect instance

    # ------------------------------------------------------------------
    # Translation helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _to_kite_status(s: str | None) -> OrderStatus:
        if not s:
            return OrderStatus.UNKNOWN
        return _KITE_STATUS_MAP.get(s.upper(), OrderStatus.UNKNOWN)

    @staticmethod
    def _request_to_kite_payload(req: OrderRequest) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "tradingsymbol": req.instrument.tradingsymbol,
            "exchange": req.instrument.exchange.value,
            "transaction_type": "BUY" if req.side == Side.BUY else "SELL",
            "quantity": req.quantity,
            "product": req.product.value,
            "order_type": req.order_type.value,
            "validity": req.validity.value,
            "tag": req.tag,
        }
        if req.price is not None:
            payload["price"] = req.price
        if req.trigger_price is not None:
            payload["trigger_price"] = req.trigger_price
        if req.disclosed_quantity:
            payload["disclosed_quantity"] = req.disclosed_quantity
        if req.iceberg_legs:
            payload["iceberg_legs"] = req.iceberg_legs
        if req.iceberg_quantity:
            payload["iceberg_quantity"] = req.iceberg_quantity
        return payload

    def _normalize_order(self, k: dict[str, Any]) -> OrderUpdate:
        status_str = k.get("status") or k.get("status_message")
        return OrderUpdate(
            order_id=str(k.get("order_id") or k.get("parent_order_id") or ""),
            status=self._to_kite_status(status_str),
            filled_quantity=int(k.get("filled_quantity") or 0),
            pending_quantity=int(k.get("pending_quantity") or 0),
            average_price=float(k.get("average_price") or 0.0),
            price=k.get("price"),
            trigger_price=k.get("trigger_price"),
            exchange_order_id=k.get("exchange_order_id"),
            exchange_timestamp=_parse_kite_dt(k.get("exchange_timestamp")),
            status_message=k.get("status_message") or status_str,
            raw=k,
        )

    def normalize_order_update(self, payload: dict[str, Any]) -> OrderUpdate:
        """Public wrapper to normalize a raw Kite order payload."""
        return self._normalize_order(payload)

    # ------------------------------------------------------------------
    # IBroker
    # ------------------------------------------------------------------

    def place_order(self, request: OrderRequest) -> OrderUpdate:
        payload = self._request_to_kite_payload(request)
        variety = request.variety.value
        try:
            order_id = self.kite.place_order(variety=variety, **payload)
        except Exception as e:
            _log.error("kite_place_order_failed", error=str(e), instrument=request.instrument.key)
            raise OrderRejected(str(e), raw=getattr(e, "args", None)) from e

        # Kite returns just the order_id; fetch the current state.
        update = self._fetch_order(str(order_id))
        self._emit_order_update(update)
        return update

    def _fetch_order(self, order_id: str) -> OrderUpdate:
        try:
            history = self.kite.order_history(order_id=order_id)
        except Exception as e:
            raise BrokerError(f"order_history failed for {order_id}: {e}") from e
        if not history:
            return OrderUpdate(
                order_id=order_id,
                status=OrderStatus.SUBMITTED,
                pending_quantity=0,
                raw={"note": "no history yet"},
            )
        return self._normalize_order(history[-1])

    def cancel_order(self, order_id: str, variety: str | None = None) -> OrderUpdate:
        try:
            self.kite.cancel_order(variety=variety or "regular", order_id=order_id)
        except Exception as e:
            raise BrokerError(f"cancel_order failed: {e}") from e
        return self._fetch_order(order_id)

    def modify_order(
        self,
        order_id: str,
        *,
        quantity: int | None = None,
        price: float | None = None,
        trigger_price: float | None = None,
        order_type: str | None = None,
        validity: str | None = None,
        variety: str | None = None,
    ) -> OrderUpdate:
        kwargs: dict[str, Any] = {"variety": variety or "regular", "order_id": order_id}
        if quantity is not None:
            kwargs["quantity"] = quantity
        if price is not None:
            kwargs["price"] = price
        if trigger_price is not None:
            kwargs["trigger_price"] = trigger_price
        if order_type is not None:
            kwargs["order_type"] = order_type
        if validity is not None:
            kwargs["validity"] = validity
        try:
            self.kite.modify_order(**kwargs)
        except Exception as e:
            raise BrokerError(f"modify_order failed: {e}") from e
        return self._fetch_order(order_id)

    def orders(self) -> list[OrderUpdate]:
        try:
            raw = self.kite.orders()
        except Exception as e:
            raise BrokerError(f"orders() failed: {e}") from e
        return [self._normalize_order(o) for o in raw or []]

    def order_history(self, order_id: str) -> list[OrderUpdate]:
        try:
            raw = self.kite.order_history(order_id=order_id)
        except Exception as e:
            raise BrokerError(f"order_history failed: {e}") from e
        return [self._normalize_order(o) for o in raw or []]

    def trades(self, order_id: str | None = None) -> list[Fill]:
        try:
            raw = self.kite.order_trades(order_id=order_id) if order_id else self.kite.trades()
        except Exception as e:
            raise BrokerError(f"trades failed: {e}") from e
        fills: list[Fill] = []
        for t in raw or []:
            instrument = Instrument(
                exchange=t.get("exchange"),
                tradingsymbol=t.get("tradingsymbol"),
                instrument_token=t.get("instrument_token"),
            )
            fills.append(
                Fill(
                    order_id=str(t.get("order_id")),
                    trade_id=str(t.get("trade_id")),
                    instrument=instrument,
                    side=Side(t.get("transaction_type")),
                    quantity=int(t.get("quantity")),
                    price=float(t.get("average_price") or t.get("price") or 0.0),
                    timestamp=_parse_kite_dt(t.get("fill_timestamp")) or datetime.now(),
                    fees=0.0,
                    raw=t,
                )
            )
        return fills

    def positions(self) -> dict[str, Position]:
        try:
            raw = self.kite.positions()
        except Exception as e:
            raise BrokerError(f"positions failed: {e}") from e
        # Kite's "day" bucket is today's activity only; "net" is what is actually held.
        out: dict[str, Position] = {}
        for p in (raw or {}).get("net", []) or []:
            inst = Instrument(
                exchange=p.get("exchange"),
                tradingsymbol=p.get("tradingsymbol"),
                instrument_token=p.get("instrument_token"),
                lot_size=int(p.get("multiplier") or 1),
            )
            out[inst.key] = Position(
                instrument=inst,
                quantity=int(p.get("quantity") or 0),
                avg_price=float(p.get("average_price") or 0.0),
                realised_pnl=float(p.get("realised") or 0.0),
                unrealised_pnl=float(p.get("unrealised") or 0.0),
                last_price=float(p.get("last_price") or 0.0),
            )
        return out

    def holdings(self) -> dict[str, Position]:
        try:
            raw = self.kite.holdings()
        except Exception as e:
            raise BrokerError(f"holdings failed: {e}") from e
        out: dict[str, Position] = {}
        for h in raw or []:
            inst = Instrument(
                exchange=h.get("exchange"),
                tradingsymbol=h.get("tradingsymbol"),
                instrument_token=h.get("instrument_token"),
            )
            out[inst.key] = Position(
                instrument=inst,
                quantity=int(h.get("quantity") or 0),
                avg_price=float(h.get("average_price") or 0.0),
                realised_pnl=float(h.get("realised") or 0.0),
                unrealised_pnl=float(h.get("unrealised") or 0.0),
                last_price=float(h.get("last_price") or 0.0),
            )
        return out

    def quote_ltp(self, instruments: Iterable[Instrument]) -> dict[str, float]:
        keys = [i.key for i in instruments]
        try:
            raw = self.kite.ltp(keys)
        except Exception as e:
            raise BrokerError(f"ltp failed: {e}") from e
        return {k: float((v or {}).get("last_price") or 0.0) for k, v in (raw or {}).items()}


def _parse_kite_dt(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


__all__ = ["KiteBroker"]
