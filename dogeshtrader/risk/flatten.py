"""Build exit intents for every open position.

Used when the kill switch is engaged. These intents reduce exposure, so the
risk engine lets them through after new entries have been blocked.
"""

from __future__ import annotations

from dogeshtrader.core.enums import OrderType, Product, Side
from dogeshtrader.core.models import OrderIntent, StrategyContext


def flatten_intents(
    ctx: StrategyContext,
    *,
    market_protection_pct: float = 1.0,
    product: Product = Product.MIS,
    reason: str = "kill_switch_flatten",
) -> list[OrderIntent]:
    intents: list[OrderIntent] = []
    for pos in ctx.positions.values():
        if pos.quantity == 0:
            continue
        intents.append(
            OrderIntent(
                strategy_id=ctx.strategy_id,
                instrument=pos.instrument,
                side=Side.SELL if pos.quantity > 0 else Side.BUY,
                quantity=abs(pos.quantity),
                order_type=OrderType.MARKET,
                product=product,
                market_protection_pct=market_protection_pct,
                reason=reason,
                signal_ts=ctx.timestamp,
            )
        )
    return intents


__all__ = ["flatten_intents"]
