"""Kite WebSocket adapter.

Wraps :class:`kiteconnect.KiteTicker`. The framework prefers WebSocket for
order updates (Kite's recommended channel for individual developers) and for
streaming market quotes.

For testability the WS class is imported lazily.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from datetime import datetime
from typing import Any

from dogeshtrader.core.errors import BrokerError
from dogeshtrader.core.models import Depth, Tick
from dogeshtrader.logging_config import get_logger

_log = get_logger(__name__)


TickCallback = Callable[[Tick], None]
OrderUpdateCallback = Callable[[dict[str, Any]], None]
ConnectCallback = Callable[[], None]
DisconnectCallback = Callable[[Exception | None], None]


_MODE_MAP = {
    "ltp": "MODE_LTP",
    "quote": "MODE_QUOTE",
    "full": "MODE_FULL",
}


class KiteWebSocketAdapter:
    """Convenience layer over ``kiteconnect.KiteTicker``.

    Streams ticks and order postbacks. Callbacks are wired with ``on_tick``,
    ``on_order_update``, ``on_connect`` and ``on_disconnect`` setters.
    """

    def __init__(self, *, api_key: str, access_token: str) -> None:
        self.api_key = api_key
        self.access_token = access_token
        self._ticker: Any | None = None
        self._tick_cbs: list[TickCallback] = []
        self._order_cbs: list[OrderUpdateCallback] = []
        self._connect_cbs: list[ConnectCallback] = []
        self._disconnect_cbs: list[DisconnectCallback] = []

    # ------------------------------------------------------------------
    # Callback setters
    # ------------------------------------------------------------------

    def on_tick(self, callback: TickCallback) -> None:
        self._tick_cbs.append(callback)

    def on_order_update(self, callback: OrderUpdateCallback) -> None:
        self._order_cbs.append(callback)

    def on_connect(self, callback: ConnectCallback) -> None:
        self._connect_cbs.append(callback)

    def on_disconnect(self, callback: DisconnectCallback) -> None:
        self._disconnect_cbs.append(callback)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def connect(
        self,
        *,
        tokens: Iterable[int],
        mode: str = "quote",
        threaded: bool = True,
    ) -> None:
        try:
            from kiteconnect import KiteTicker
        except ImportError as e:
            raise BrokerError("kiteconnect not installed; cannot start WebSocket") from e

        if mode not in _MODE_MAP:
            raise BrokerError(f"invalid subscription mode: {mode!r}")
        kt = KiteTicker(self.api_key, self.access_token)
        self._ticker = kt
        token_list = list(tokens)

        def _on_ticks(ws: Any, ticks: list[dict[str, Any]]) -> None:
            for raw in ticks:
                t = _to_tick(raw)
                if t is None:
                    continue
                for cb in self._tick_cbs:
                    try:
                        cb(t)
                    except Exception:
                        _log.exception("tick_callback_failed")

        def _on_connect(ws: Any, response: Any) -> None:
            try:
                ws.subscribe(token_list)
                ws.set_mode(getattr(ws, _MODE_MAP[mode]), token_list)
            except Exception:
                _log.exception("ws_subscribe_failed")
            for cb in self._connect_cbs:
                cb()

        def _on_close(ws: Any, code: int, reason: str) -> None:
            for cb in self._disconnect_cbs:
                cb(None)

        def _on_error(ws: Any, code: int, reason: str) -> None:
            _log.warning("ws_error", code=code, reason=reason)

        def _on_order_update(ws: Any, data: dict[str, Any]) -> None:
            for cb in self._order_cbs:
                try:
                    cb(data)
                except Exception:
                    _log.exception("order_update_callback_failed")

        kt.on_ticks = _on_ticks
        kt.on_connect = _on_connect
        kt.on_close = _on_close
        kt.on_error = _on_error
        kt.on_order_update = _on_order_update

        kt.connect(threaded=threaded)

    def stop(self) -> None:
        if self._ticker is None:
            return
        try:
            self._ticker.close()
        except Exception:
            pass


def _to_tick(raw: dict[str, Any]) -> Tick | None:
    token = raw.get("instrument_token")
    if token is None:
        return None
    ts = raw.get("exchange_timestamp") or raw.get("timestamp") or datetime.now()
    if isinstance(ts, str):
        try:
            ts = datetime.fromisoformat(ts)
        except ValueError:
            ts = datetime.now()
    depth = raw.get("depth") or {}
    bid_depth = [
        Depth(
            price=float(d.get("price", 0)),
            quantity=int(d.get("quantity", 0)),
            orders=int(d.get("orders", 0)),
        )
        for d in (depth.get("buy") or [])
    ]
    ask_depth = [
        Depth(
            price=float(d.get("price", 0)),
            quantity=int(d.get("quantity", 0)),
            orders=int(d.get("orders", 0)),
        )
        for d in (depth.get("sell") or [])
    ]
    return Tick(
        timestamp=ts,
        instrument_token=int(token),
        ltp=float(raw.get("last_price") or 0.0),
        last_quantity=_int_or_none(raw.get("last_quantity")),
        volume_traded=_int_or_none(raw.get("volume_traded") or raw.get("volume")),
        average_traded_price=_float_or_none(
            raw.get("average_traded_price") or raw.get("average_price")
        ),
        total_buy_quantity=_int_or_none(raw.get("total_buy_quantity")),
        total_sell_quantity=_int_or_none(raw.get("total_sell_quantity")),
        oi=_int_or_none(raw.get("oi")),
        bid=bid_depth[0].price if bid_depth else None,
        ask=ask_depth[0].price if ask_depth else None,
        depth_buy=bid_depth,
        depth_sell=ask_depth,
        raw=raw,
    )


def _int_or_none(v: Any) -> int | None:
    if v in (None, ""):
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _float_or_none(v: Any) -> float | None:
    if v in (None, ""):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


__all__ = ["KiteWebSocketAdapter"]
