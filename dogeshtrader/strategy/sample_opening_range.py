"""Sample strategy: opening-range breakout.

For each instrument:
* observe the first ``range_minutes`` of the session and remember the high/low.
* once the range is established, BUY on a break above (with SL below the low)
  and SELL on a break below (with SL above the high).
* flatten unconditionally before the configured session end.

This is intentionally simple - it demonstrates SL-M intents rather than being
profitable.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import time

from dogeshtrader.core.clock import IST, minutes_until_close
from dogeshtrader.core.enums import OrderType, Product, Side
from dogeshtrader.core.models import Candle, Instrument, OrderIntent, StrategyContext
from dogeshtrader.strategy.base import Strategy
from dogeshtrader.strategy.registry import register


@dataclass
class _Range:
    high: float = float("-inf")
    low: float = float("inf")
    established: bool = False
    breakout_side: str | None = None  # "long" | "short"


@register("sample_opening_range")
class SampleOpeningRange(Strategy):
    name = "sample_opening_range"

    def __init__(self, params: dict | None = None) -> None:
        super().__init__(params)
        self._ranges: dict[str, _Range] = {}
        self._range_date: dict[str, str] = {}

    def on_start(self, ctx: StrategyContext) -> None:
        self._ranges.clear()
        self._range_date.clear()

    def on_candle(self, ctx: StrategyContext, candle: Candle) -> list[OrderIntent]:
        instrument: Instrument | None = ctx.extra.get("_current_instrument")
        if instrument is None:
            return []
        key = instrument.key

        local_ts = candle.timestamp.astimezone(IST) if candle.timestamp.tzinfo else candle.timestamp
        today = local_ts.date().isoformat()
        if self._range_date.get(key) != today:
            self._ranges[key] = _Range()
            self._range_date[key] = today

        r = self._ranges[key]
        session_start: time = ctx.config.get("session_start", time(9, 15))
        range_minutes = int(self.get_param("range_minutes", 15))
        range_end = _add_minutes(session_start, range_minutes)

        if local_ts.time() <= range_end:
            r.high = max(r.high, candle.high)
            r.low = min(r.low, candle.low)
            return []

        if not r.established and r.high > r.low > 0:
            r.established = True

        if not r.established:
            return []

        intents: list[OrderIntent] = []
        qty = int(self.get_param("quantity", 1))
        product = Product(self.get_param("product", "MIS"))
        protection = float(self.get_param("market_protection_pct", 1.0))
        position = ctx.position(instrument)

        if self.get_param("exit_on_session_end", True):
            close_time: time = ctx.config.get("session_end", time(15, 30))
            if minutes_until_close(candle.timestamp, close_time) <= 5 and position.quantity != 0:
                intents.append(
                    OrderIntent(
                        strategy_id=ctx.strategy_id,
                        instrument=instrument,
                        side=Side.SELL if position.quantity > 0 else Side.BUY,
                        quantity=abs(position.quantity),
                        order_type=OrderType.MARKET,
                        product=product,
                        market_protection_pct=protection,
                        reason="session_end_flatten",
                    )
                )
                return intents

        if r.breakout_side is None:
            if candle.close > r.high and position.quantity <= 0:
                intents.append(
                    OrderIntent(
                        strategy_id=ctx.strategy_id,
                        instrument=instrument,
                        side=Side.BUY,
                        quantity=qty,
                        order_type=OrderType.MARKET,
                        product=product,
                        market_protection_pct=protection,
                        reason="orb_long",
                    )
                )
                r.breakout_side = "long"
            elif (
                candle.close < r.low
                and position.quantity >= 0
                and self.get_param("allow_short", False)
            ):
                intents.append(
                    OrderIntent(
                        strategy_id=ctx.strategy_id,
                        instrument=instrument,
                        side=Side.SELL,
                        quantity=qty,
                        order_type=OrderType.MARKET,
                        product=product,
                        market_protection_pct=protection,
                        reason="orb_short",
                    )
                )
                r.breakout_side = "short"

        elif r.breakout_side == "long" and candle.close < r.low and position.quantity > 0:
            intents.append(
                OrderIntent(
                    strategy_id=ctx.strategy_id,
                    instrument=instrument,
                    side=Side.SELL,
                    quantity=position.quantity,
                    order_type=OrderType.MARKET,
                    product=product,
                    market_protection_pct=protection,
                    reason="orb_long_stop",
                )
            )
        elif r.breakout_side == "short" and candle.close > r.high and position.quantity < 0:
            intents.append(
                OrderIntent(
                    strategy_id=ctx.strategy_id,
                    instrument=instrument,
                    side=Side.BUY,
                    quantity=abs(position.quantity),
                    order_type=OrderType.MARKET,
                    product=product,
                    market_protection_pct=protection,
                    reason="orb_short_stop",
                )
            )

        return intents


def _add_minutes(t: time, minutes: int) -> time:
    total = t.hour * 60 + t.minute + minutes
    return time(total // 60, total % 60)


__all__ = ["SampleOpeningRange"]
