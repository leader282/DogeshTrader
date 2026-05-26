"""Fill model abstractions.

The backtest engine delegates to a :class:`FillModel` to decide *whether* and
*at what price* a working order fills given the next candle's OHLC. The model
also applies slippage and reports charges via the :class:`CostModel`.

This is deliberately separate from the broker so tests can exercise fills
without booting the full engine.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from dogeshtrader.backtest.costs import CostModel
from dogeshtrader.backtest.slippage import SlippageModel
from dogeshtrader.core.enums import OrderType, Side
from dogeshtrader.core.models import Candle, Fill, OrderRequest


@dataclass
class FillDecision:
    fill_price: float
    quantity: int
    triggered: bool  # for SL types
    fees: float


class FillModel(Protocol):
    def evaluate(
        self,
        *,
        request: OrderRequest,
        candle: Candle,
        already_filled: int,
        triggered: bool,
        last_close: float | None,
    ) -> FillDecision | None: ...


@dataclass
class CandleFillModel:
    """Deterministic fill model that operates on the *next* candle after order
    placement.

    * MARKET: fills at next candle open (configurable to next close).
    * LIMIT: fills only if the candle traverses the limit on the right side.
              Pessimistic by default (worse fill = limit price).
    * SL / SL-M: trigger when high/low crosses the trigger; then fill per type.

    Slippage is applied uniformly via ``slippage.adjust``.
    Charges are computed via ``costs.estimate``.
    """

    slippage: SlippageModel
    costs: CostModel
    fill_market_on_open: bool = True
    optimistic_limit: bool = False

    def evaluate(
        self,
        *,
        request: OrderRequest,
        candle: Candle,
        already_filled: int,
        triggered: bool,
        last_close: float | None,
    ) -> FillDecision | None:
        remaining = request.quantity - already_filled
        if remaining <= 0:
            return None

        if request.order_type == OrderType.MARKET:
            ideal = candle.open if self.fill_market_on_open else candle.close
            price = self.slippage.adjust(
                instrument=request.instrument,
                side=request.side,
                ideal_price=ideal,
                candle=candle,
            )
            fees = self.costs.estimate(
                instrument=request.instrument,
                product=request.product,
                side=request.side,
                price=price,
                quantity=remaining,
            )
            return FillDecision(fill_price=price, quantity=remaining, triggered=True, fees=fees)

        if request.order_type == OrderType.LIMIT:
            limit = request.price or 0.0
            if request.side == Side.BUY:
                touched = candle.low <= limit if self.optimistic_limit else candle.low < limit
                if not touched:
                    return None
                ideal = min(limit, candle.open) if candle.open <= limit else limit
            else:
                touched = candle.high >= limit if self.optimistic_limit else candle.high > limit
                if not touched:
                    return None
                ideal = max(limit, candle.open) if candle.open >= limit else limit
            price = self.slippage.adjust(
                instrument=request.instrument,
                side=request.side,
                ideal_price=ideal,
                candle=candle,
            )
            fees = self.costs.estimate(
                instrument=request.instrument,
                product=request.product,
                side=request.side,
                price=price,
                quantity=remaining,
            )
            return FillDecision(fill_price=price, quantity=remaining, triggered=True, fees=fees)

        if request.order_type in (OrderType.SL, OrderType.SL_M):
            trigger = request.trigger_price or 0.0
            newly_triggered = triggered
            if not newly_triggered:
                if (request.side == Side.BUY and candle.high >= trigger) or (
                    request.side == Side.SELL and candle.low <= trigger
                ):
                    newly_triggered = True
            if not newly_triggered:
                return None

            if request.order_type == OrderType.SL_M:
                ideal = trigger
            else:
                limit = request.price or trigger
                if request.side == Side.BUY:
                    if candle.low > limit:
                        return FillDecision(fill_price=0.0, quantity=0, triggered=True, fees=0.0)
                    ideal = min(limit, candle.open) if candle.open <= limit else limit
                else:
                    if candle.high < limit:
                        return FillDecision(fill_price=0.0, quantity=0, triggered=True, fees=0.0)
                    ideal = max(limit, candle.open) if candle.open >= limit else limit

            price = self.slippage.adjust(
                instrument=request.instrument,
                side=request.side,
                ideal_price=ideal,
                candle=candle,
            )
            fees = self.costs.estimate(
                instrument=request.instrument,
                product=request.product,
                side=request.side,
                price=price,
                quantity=remaining,
            )
            return FillDecision(fill_price=price, quantity=remaining, triggered=True, fees=fees)

        return None


def fill_to_event(
    *,
    order_id: str,
    request: OrderRequest,
    decision: FillDecision,
    timestamp: datetime,
    trade_id: str,
) -> Fill:
    return Fill(
        order_id=order_id,
        trade_id=trade_id,
        instrument=request.instrument,
        side=request.side,
        quantity=decision.quantity,
        price=decision.fill_price,
        timestamp=timestamp,
        fees=decision.fees,
        raw={"intent_id": request.intent_id, "tag": request.tag},
    )


def fold_fills(fills: Iterable[Fill]) -> dict[str, list[Fill]]:
    out: dict[str, list[Fill]] = {}
    for f in fills:
        out.setdefault(f.instrument.key, []).append(f)
    return out


__all__ = ["CandleFillModel", "FillDecision", "FillModel", "fill_to_event", "fold_fills"]
