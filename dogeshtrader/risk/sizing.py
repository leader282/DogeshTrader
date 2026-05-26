"""Position-sizing helpers.

Sizing decisions belong to the *risk* layer, not the strategy. Strategies
express *intent*; the risk engine sizes the intent to fit configured limits.
"""

from __future__ import annotations

from dogeshtrader.core.models import Instrument
from dogeshtrader.risk.rules import align_to_lot_size


def clamp_quantity_by_notional(
    quantity: int,
    price: float | None,
    *,
    max_notional: float,
    instrument: Instrument,
) -> int:
    """Reduce ``quantity`` so that ``price * quantity <= max_notional``.

    Returns 0 if ``price`` is missing or non-positive and the caller cannot
    therefore sanity-check notional.
    """
    if price is None or price <= 0 or quantity <= 0 or max_notional <= 0:
        return 0
    affordable = int(max_notional // price)
    capped = min(quantity, affordable)
    return align_to_lot_size(capped, instrument)


def fixed_fraction_quantity(
    cash: float, last_price: float | None, fraction: float, instrument: Instrument
) -> int:
    if last_price is None or last_price <= 0 or cash <= 0 or fraction <= 0:
        return 0
    budget = max(cash, 0.0) * min(fraction, 1.0)
    qty = int(budget // last_price)
    return align_to_lot_size(qty, instrument)


__all__ = ["clamp_quantity_by_notional", "fixed_fraction_quantity"]
