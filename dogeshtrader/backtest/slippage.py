"""Slippage models used by the backtest fill engine.

A slippage model returns the *adjusted* fill price after accounting for
microstructure friction: spread + impact + book pressure.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from dogeshtrader.core.enums import Side
from dogeshtrader.core.models import Candle, Instrument


class SlippageModel(Protocol):
    def adjust(
        self,
        *,
        instrument: Instrument,
        side: Side,
        ideal_price: float,
        candle: Candle | None = None,
    ) -> float: ...


@dataclass
class FixedBpsSlippage:
    """Apply a fixed basis-point haircut against the trader.

    BUY  -> price *  (1 + bps/10000)
    SELL -> price *  (1 - bps/10000)
    """

    bps: float = 2.0

    def adjust(
        self,
        *,
        instrument: Instrument,
        side: Side,
        ideal_price: float,
        candle: Candle | None = None,
    ) -> float:
        adj = self.bps / 10_000.0
        return ideal_price * (1 + adj) if side == Side.BUY else ideal_price * (1 - adj)


@dataclass
class FixedTickSlippage:
    """Move price by N ticks against the trader."""

    ticks: int = 1

    def adjust(
        self,
        *,
        instrument: Instrument,
        side: Side,
        ideal_price: float,
        candle: Candle | None = None,
    ) -> float:
        tick = instrument.tick_size or 0.05
        delta = self.ticks * tick
        return ideal_price + delta if side == Side.BUY else ideal_price - delta


@dataclass
class NoSlippage:
    def adjust(
        self,
        *,
        instrument: Instrument,
        side: Side,
        ideal_price: float,
        candle: Candle | None = None,
    ) -> float:
        return ideal_price


__all__ = ["FixedBpsSlippage", "FixedTickSlippage", "NoSlippage", "SlippageModel"]
