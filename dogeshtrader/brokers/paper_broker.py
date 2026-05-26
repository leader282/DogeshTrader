"""Paper / simulated broker.

Used by both BACKTEST (with a candle-driven fill loop) and PAPER (with live or
replayed ticks). The broker maintains cash, positions, an order book and a
simple matching engine.

Two ways to drive fills:
1. The engine pushes prices into :meth:`feed_price` -> any working orders that
   should now fill are filled at that price.
2. The engine pushes a candle via :meth:`feed_candle` -> the broker fills
   working orders based on the OHLC window.

Both are guaranteed deterministic given the same inputs - critical for tests.
"""

from __future__ import annotations

import itertools
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime

from dogeshtrader.brokers.base import BaseBroker
from dogeshtrader.constants import (
    ORDER_INTERNAL_PREFIX,
    TRADE_INTERNAL_PREFIX,
)
from dogeshtrader.core.enums import OrderStatus, OrderType, Side
from dogeshtrader.core.errors import OrderRejected
from dogeshtrader.core.models import (
    Candle,
    Fill,
    Instrument,
    OrderRequest,
    OrderUpdate,
    Position,
)


@dataclass
class _OpenOrder:
    order_id: str
    request: OrderRequest
    placed_at: datetime
    triggered: bool = False  # for SL / SL-M
    filled_qty: int = 0


@dataclass
class PaperConfig:
    starting_cash: float = 200_000.0
    fill_market_on_next_candle_open: bool = True
    optimistic_limits: bool = False
    """If True, limit orders fill on touch of the level (best case for buyer/seller).
    If False (default), fill only when the candle crosses the limit on the right side."""
    fee_per_order: float = 0.0
    bps_cost: float = 0.0


class PaperBroker(BaseBroker):
    """In-process simulated broker.

    Sign convention: positions are positive long, negative short.
    """

    mode = "PAPER"

    def __init__(
        self,
        *,
        config: PaperConfig | None = None,
        time_fn: Callable[[], datetime] = datetime.now,
    ) -> None:
        super().__init__()
        self.config = config or PaperConfig()
        self.cash: float = self.config.starting_cash
        self._time_fn = time_fn
        self._open: list[_OpenOrder] = []
        self._orders: dict[str, OrderUpdate] = {}
        self._fills: dict[str, list[Fill]] = {}
        self._positions: dict[str, Position] = {}
        self._latest_prices: dict[str, float] = {}
        self._order_counter = itertools.count(1)
        self._trade_counter = itertools.count(1)
        self._request_by_intent: dict[str, OrderRequest] = {}

    # ------------------------------------------------------------------
    # Lifecycle hooks for engines
    # ------------------------------------------------------------------

    def reset(self, starting_cash: float | None = None) -> None:
        self.cash = starting_cash if starting_cash is not None else self.config.starting_cash
        self._open.clear()
        self._orders.clear()
        self._fills.clear()
        self._positions.clear()
        self._latest_prices.clear()
        self._request_by_intent.clear()

    def feed_price(self, instrument: Instrument, price: float, ts: datetime) -> None:
        """Update LTP for ``instrument`` and trigger any fills that match."""
        self._latest_prices[instrument.key] = price
        self._mark(instrument, price)
        self._try_match_on_price(instrument, price, ts)

    def feed_candle(self, instrument: Instrument, candle: Candle) -> None:
        """Drive fills from a single candle. Used by the backtest engine."""
        self._latest_prices[instrument.key] = candle.close
        self._mark(instrument, candle.close)
        self._try_match_on_candle(instrument, candle)

    # ------------------------------------------------------------------
    # IBroker implementation
    # ------------------------------------------------------------------

    def place_order(self, request: OrderRequest) -> OrderUpdate:
        if request.quantity <= 0:
            raise OrderRejected("non-positive quantity")
        order_id = f"{ORDER_INTERNAL_PREFIX}p{next(self._order_counter):06d}"
        now = self._time_fn()
        update = OrderUpdate(
            order_id=order_id,
            status=OrderStatus.OPEN,
            pending_quantity=request.quantity,
            price=request.price,
            trigger_price=request.trigger_price,
            exchange_timestamp=now,
            raw={"intent_id": request.intent_id, "tag": request.tag},
        )
        self._orders[order_id] = update
        self._fills.setdefault(order_id, [])
        self._open.append(_OpenOrder(order_id=order_id, request=request, placed_at=now))
        self._request_by_intent[request.intent_id] = request
        self._emit_order_update(update)
        # MARKET orders fill immediately if we have a current price.
        if request.order_type == OrderType.MARKET:
            price = self._latest_prices.get(request.instrument.key)
            if price is not None and not self.config.fill_market_on_next_candle_open:
                self._fill_order(order_id, request, price, request.quantity, ts=now)
        return self._orders[order_id]

    def cancel_order(self, order_id: str, variety: str | None = None) -> OrderUpdate:
        update = self._orders.get(order_id)
        if update is None:
            raise OrderRejected(f"unknown order_id {order_id}")
        if update.status in (OrderStatus.COMPLETE, OrderStatus.REJECTED):
            return update
        self._remove_open(order_id)
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
        if update is None:
            raise OrderRejected(f"unknown order_id {order_id}")
        o = next((open_order for open_order in self._open if open_order.order_id == order_id), None)
        if o is None:
            raise OrderRejected(f"order {order_id} not open")

        req = o.request
        patch: dict = {}
        if quantity is not None:
            if quantity <= 0:
                raise OrderRejected("quantity must be positive")
            if quantity < update.filled_quantity:
                raise OrderRejected(
                    f"quantity {quantity} is below already filled qty {update.filled_quantity}"
                )
            patch["quantity"] = quantity
        if price is not None:
            patch["price"] = price
        if trigger_price is not None:
            patch["trigger_price"] = trigger_price
        if order_type is not None:
            patch["order_type"] = OrderType(order_type)

        if patch:
            o.request = req.model_copy(update=patch)
            self._request_by_intent[o.request.intent_id] = o.request

        update.price = o.request.price
        update.trigger_price = o.request.trigger_price
        update.pending_quantity = max(o.request.quantity - update.filled_quantity, 0)
        if update.pending_quantity == 0:
            update.status = OrderStatus.COMPLETE
            self._remove_open(order_id)
        self._emit_order_update(update)
        return update

    def orders(self) -> list[OrderUpdate]:
        return list(self._orders.values())

    def order_history(self, order_id: str) -> list[OrderUpdate]:
        update = self._orders.get(order_id)
        return [update] if update else []

    def trades(self, order_id: str | None = None) -> list[Fill]:
        if order_id:
            return list(self._fills.get(order_id, []))
        return [f for fills in self._fills.values() for f in fills]

    def positions(self) -> dict[str, Position]:
        return dict(self._positions)

    def holdings(self) -> dict[str, Position]:
        return {}

    def quote_ltp(self, instruments: Iterable[Instrument]) -> dict[str, float]:
        return {i.key: self._latest_prices.get(i.key, 0.0) for i in instruments}

    def request_for_intent(self, intent_id: str | None) -> OrderRequest | None:
        if not intent_id:
            return None
        return self._request_by_intent.get(intent_id)

    # ------------------------------------------------------------------
    # Matching engine
    # ------------------------------------------------------------------

    def _try_match_on_price(self, instrument: Instrument, price: float, ts: datetime) -> None:
        for o in list(self._open):
            if o.request.instrument.key != instrument.key:
                continue
            self._try_fill_open(o, ref_open=price, high=price, low=price, close=price, ts=ts)

    def _try_match_on_candle(self, instrument: Instrument, candle: Candle) -> None:
        for o in list(self._open):
            if o.request.instrument.key != instrument.key:
                continue
            self._try_fill_open(
                o,
                ref_open=candle.open,
                high=candle.high,
                low=candle.low,
                close=candle.close,
                ts=candle.timestamp,
            )

    def _try_fill_open(
        self,
        o: _OpenOrder,
        *,
        ref_open: float,
        high: float,
        low: float,
        close: float,
        ts: datetime,
    ) -> None:
        req = o.request
        update = self._orders[o.order_id]
        if update.status in (OrderStatus.COMPLETE, OrderStatus.CANCELLED, OrderStatus.REJECTED):
            self._remove_open(update.order_id)
            return

        if req.order_type == OrderType.MARKET:
            fill_price = ref_open if self.config.fill_market_on_next_candle_open else close
            self._fill_order(update.order_id, req, fill_price, req.quantity - o.filled_qty, ts=ts)
            return

        if req.order_type == OrderType.LIMIT:
            limit = req.price or 0.0
            if req.side == Side.BUY:
                if (self.config.optimistic_limits and low <= limit) or (low < limit):
                    fill_price = min(limit, ref_open) if ref_open <= limit else limit
                    self._fill_order(
                        update.order_id, req, fill_price, req.quantity - o.filled_qty, ts=ts
                    )
            elif (self.config.optimistic_limits and high >= limit) or (high > limit):
                fill_price = max(limit, ref_open) if ref_open >= limit else limit
                self._fill_order(
                    update.order_id, req, fill_price, req.quantity - o.filled_qty, ts=ts
                )
            return

        if req.order_type in (OrderType.SL, OrderType.SL_M):
            trigger = req.trigger_price or 0.0
            if not o.triggered:
                if (req.side == Side.BUY and high >= trigger) or (
                    req.side == Side.SELL and low <= trigger
                ):
                    o.triggered = True
            if o.triggered:
                if req.order_type == OrderType.SL_M:
                    fill_price = trigger
                    self._fill_order(
                        update.order_id, req, fill_price, req.quantity - o.filled_qty, ts=ts
                    )
                else:
                    # SL: behave like a LIMIT once triggered.
                    limit = req.price or trigger
                    if req.side == Side.BUY and low <= limit:
                        self._fill_order(
                            update.order_id,
                            req,
                            min(limit, ref_open),
                            req.quantity - o.filled_qty,
                            ts=ts,
                        )
                    elif req.side == Side.SELL and high >= limit:
                        self._fill_order(
                            update.order_id,
                            req,
                            max(limit, ref_open),
                            req.quantity - o.filled_qty,
                            ts=ts,
                        )

    def _fill_order(
        self,
        order_id: str,
        request: OrderRequest,
        price: float,
        qty: int,
        *,
        ts: datetime,
    ) -> None:
        if qty <= 0:
            return
        fees = self._estimate_fees(price, qty)
        trade_id = f"{TRADE_INTERNAL_PREFIX}p{next(self._trade_counter):07d}"
        fill = Fill(
            order_id=order_id,
            trade_id=trade_id,
            instrument=request.instrument,
            side=request.side,
            quantity=qty,
            price=price,
            timestamp=ts,
            fees=fees,
            raw={"intent_id": request.intent_id, "tag": request.tag},
        )
        self._fills.setdefault(order_id, []).append(fill)
        self._apply_to_position(fill)
        self._update_cash(fill)
        update = self._orders[order_id]
        update.filled_quantity += qty
        update.pending_quantity = max(request.quantity - update.filled_quantity, 0)
        # Weighted average price.
        if update.average_price == 0.0:
            update.average_price = price
        else:
            update.average_price = (
                (update.average_price * (update.filled_quantity - qty)) + price * qty
            ) / max(update.filled_quantity, 1)
        update.exchange_timestamp = ts
        if update.pending_quantity == 0:
            update.status = OrderStatus.COMPLETE
            self._remove_open(order_id)
        else:
            update.status = OrderStatus.PARTIAL
        self._emit_fill(fill)
        self._emit_order_update(update)

    def _estimate_fees(self, price: float, qty: int) -> float:
        notional = price * qty
        bps = (self.config.bps_cost / 10_000.0) * notional
        return float(bps + self.config.fee_per_order)

    def _apply_to_position(self, fill: Fill) -> None:
        pos = self._positions.setdefault(fill.instrument.key, Position(instrument=fill.instrument))
        pos.apply_fill(fill)

    def _update_cash(self, fill: Fill) -> None:
        notional = fill.price * fill.quantity
        if fill.side == Side.BUY:
            self.cash -= notional
        else:
            self.cash += notional
        self.cash -= fill.fees

    def _mark(self, instrument: Instrument, price: float) -> None:
        pos = self._positions.get(instrument.key)
        if pos:
            pos.mark(price)

    def _remove_open(self, order_id: str) -> None:
        self._open = [o for o in self._open if o.order_id != order_id]


__all__ = ["PaperBroker", "PaperConfig"]
