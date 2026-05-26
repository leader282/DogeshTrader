"""Stateless risk helpers used by both backtest and live engines."""

from __future__ import annotations

import math

from dogeshtrader.constants import TICK_EPSILON
from dogeshtrader.core.models import Instrument


def align_to_tick_size(price: float, instrument: Instrument) -> float:
    """Round ``price`` to the nearest valid tick for ``instrument``.

    Uses ``math.copysign`` rounding so we don't accidentally flip sign on tiny
    floats near zero (rare for prices, but cheap to be safe).
    """
    tick = instrument.tick_size or 0.05
    if tick <= 0:
        return price
    n = round(price / tick)
    return float(n) * tick


def is_tick_aligned(price: float, instrument: Instrument) -> bool:
    aligned = align_to_tick_size(price, instrument)
    return abs(aligned - price) <= TICK_EPSILON


def align_to_lot_size(quantity: int, instrument: Instrument) -> int:
    """Floor-align quantity to lot size (never increase a strategy's size)."""
    lot = instrument.lot_size or 1
    if lot <= 1:
        return int(quantity)
    return (int(quantity) // lot) * lot


def is_lot_aligned(quantity: int, instrument: Instrument) -> bool:
    lot = instrument.lot_size or 1
    return quantity % max(lot, 1) == 0


def instrument_allowed(
    instrument: Instrument,
    *,
    allowed: list[str] | None,
    blocked: list[str] | None,
) -> bool:
    """Check the instrument against allow / block lists.

    Both lists accept either ``EXCHANGE:TRADINGSYMBOL`` or just
    ``TRADINGSYMBOL``. An empty / missing allowlist means "everything is
    allowed unless blocked".
    """
    key = instrument.key
    short = instrument.tradingsymbol
    if allowed:
        if key not in allowed and short not in allowed:
            return False
    if blocked:
        if key in blocked or short in blocked:
            return False
    return True


def notional(price: float | None, quantity: int) -> float:
    if price is None or quantity == 0:
        return 0.0
    return abs(price) * abs(quantity)


def safe_market_protection_bounds(last_price: float, pct: float) -> tuple[float, float]:
    band = abs(pct) / 100.0
    return (last_price * (1 - band), last_price * (1 + band))


def is_finite_positive(value: float | None) -> bool:
    return value is not None and math.isfinite(value) and value > 0


__all__ = [
    "align_to_lot_size",
    "align_to_tick_size",
    "instrument_allowed",
    "is_finite_positive",
    "is_lot_aligned",
    "is_tick_aligned",
    "notional",
    "safe_market_protection_bounds",
]
