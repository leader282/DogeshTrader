"""Sample strategy: simple moving average crossover.

For each configured instrument, maintain a deque of recent closes. When the
fast SMA crosses the slow SMA from below, emit a BUY intent; when it crosses
from above, exit (and optionally open a short, if shorting is permitted via
the ``allow_short`` param).

The strategy demonstrates:
* keeping state keyed by instrument
* emitting protected MARKET intents (default order_type)
* respecting ``exit_on_session_end`` to flatten before close
"""

from __future__ import annotations

from collections import deque
from datetime import time

from dogeshtrader.core.clock import minutes_until_close
from dogeshtrader.core.enums import OrderType, Product, Side
from dogeshtrader.core.models import Candle, Instrument, OrderIntent, StrategyContext
from dogeshtrader.strategy.base import Strategy
from dogeshtrader.strategy.registry import register


@register("sample_sma_crossover")
class SampleSMACrossover(Strategy):
    """Classic fast/slow SMA crossover on a per-instrument basis."""

    name = "sample_sma_crossover"

    def __init__(self, params: dict | None = None) -> None:
        super().__init__(params)
        self._closes: dict[str, deque[float]] = {}
        self._last_signal: dict[str, str] = {}

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def on_start(self, ctx: StrategyContext) -> None:
        slow = int(self.get_param("slow", 21))
        for sym_key in ctx.config.get("instrument_keys", []):
            self._closes[sym_key] = deque(maxlen=slow * 4)
            self._last_signal[sym_key] = "flat"

    def on_candle(self, ctx: StrategyContext, candle: Candle) -> list[OrderIntent]:
        instrument: Instrument | None = ctx.extra.get("_current_instrument")
        if instrument is None:
            return []
        key = instrument.key

        closes = self._closes.setdefault(key, deque(maxlen=int(self.get_param("slow", 21)) * 4))
        closes.append(candle.close)

        fast_period = int(self.get_param("fast", 9))
        slow_period = int(self.get_param("slow", 21))
        if len(closes) < slow_period + 1:
            return []

        fast_curr = _sma(closes, fast_period)
        slow_curr = _sma(closes, slow_period)
        fast_prev = _sma_offset(closes, fast_period, 1)
        slow_prev = _sma_offset(closes, slow_period, 1)
        if None in (fast_prev, slow_prev):
            return []

        intents: list[OrderIntent] = []
        position = ctx.position(instrument)
        qty = int(self.get_param("quantity", 1))
        product = Product(self.get_param("product", "MIS"))
        order_type = OrderType(self.get_param("order_type", "MARKET"))
        protection = float(self.get_param("market_protection_pct", 1.0))

        cross_up = fast_prev <= slow_prev and fast_curr > slow_curr  # type: ignore[operator]
        cross_dn = fast_prev >= slow_prev and fast_curr < slow_curr  # type: ignore[operator]
        allow_short = bool(self.get_param("allow_short", False))

        # Flatten before close.
        if self.get_param("exit_on_session_end", True):
            close_time: time = ctx.config.get("session_end", time(15, 30))
            if minutes_until_close(candle.timestamp, close_time) <= 3 and position.quantity != 0:
                intents.append(
                    self._mk_intent(
                        ctx,
                        instrument,
                        Side.SELL if position.quantity > 0 else Side.BUY,
                        abs(position.quantity),
                        order_type,
                        product,
                        protection,
                        reason="session_end_flatten",
                    )
                )
                self._last_signal[key] = "flat"
                return intents

        if cross_up:
            if position.quantity < 0:
                intents.append(
                    self._mk_intent(
                        ctx,
                        instrument,
                        Side.BUY,
                        abs(position.quantity),
                        order_type,
                        product,
                        protection,
                        reason="cover_short",
                    )
                )
            if position.quantity <= 0 and self._last_signal[key] != "long":
                intents.append(
                    self._mk_intent(
                        ctx,
                        instrument,
                        Side.BUY,
                        qty,
                        order_type,
                        product,
                        protection,
                        reason="sma_cross_up",
                    )
                )
                self._last_signal[key] = "long"

        elif cross_dn:
            if position.quantity > 0:
                intents.append(
                    self._mk_intent(
                        ctx,
                        instrument,
                        Side.SELL,
                        position.quantity,
                        order_type,
                        product,
                        protection,
                        reason="exit_long",
                    )
                )
            if allow_short and position.quantity >= 0 and self._last_signal[key] != "short":
                intents.append(
                    self._mk_intent(
                        ctx,
                        instrument,
                        Side.SELL,
                        qty,
                        order_type,
                        product,
                        protection,
                        reason="sma_cross_dn",
                    )
                )
                self._last_signal[key] = "short"
            else:
                self._last_signal[key] = "flat"

        return intents

    def on_end(self, ctx: StrategyContext) -> list[OrderIntent]:
        """Flatten any open positions at the end of a backtest."""
        intents: list[OrderIntent] = []
        order_type = OrderType(self.get_param("order_type", "MARKET"))
        product = Product(self.get_param("product", "MIS"))
        protection = float(self.get_param("market_protection_pct", 1.0))
        for pos in ctx.positions.values():
            if pos.quantity == 0:
                continue
            intents.append(
                self._mk_intent(
                    ctx,
                    pos.instrument,
                    Side.SELL if pos.quantity > 0 else Side.BUY,
                    abs(pos.quantity),
                    order_type,
                    product,
                    protection,
                    reason="backtest_end_flatten",
                )
            )
        return intents

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _mk_intent(
        self,
        ctx: StrategyContext,
        instrument: Instrument,
        side: Side,
        quantity: int,
        order_type: OrderType,
        product: Product,
        protection: float,
        reason: str,
    ) -> OrderIntent:
        kwargs: dict = {
            "strategy_id": ctx.strategy_id,
            "instrument": instrument,
            "side": side,
            "quantity": quantity,
            "order_type": order_type,
            "product": product,
            "reason": reason,
        }
        if order_type == OrderType.MARKET:
            kwargs["market_protection_pct"] = protection
        return OrderIntent(**kwargs)


def _sma(values: deque[float], window: int) -> float | None:
    if len(values) < window:
        return None
    return sum(list(values)[-window:]) / window


def _sma_offset(values: deque[float], window: int, offset: int) -> float | None:
    if len(values) < window + offset:
        return None
    end = len(values) - offset
    start = end - window
    return sum(list(values)[start:end]) / window


__all__ = ["SampleSMACrossover"]
