"""Internal event types passed between engines.

Strategies normally interact only with ``StrategyContext``, but engines may
publish events to subscribers (logger, recorder, etc.). Events are plain
dataclasses to keep them allocation-cheap.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from dogeshtrader.core.models import (
    Candle,
    Fill,
    Instrument,
    OrderRequest,
    OrderUpdate,
    Tick,
)


@dataclass
class Event:
    """Marker base. Avoids inheritance gymnastics."""

    timestamp: datetime


@dataclass
class MarketDataEvent(Event):
    instrument: Instrument
    kind: Literal["candle", "tick"]
    candle: Candle | None = None
    tick: Tick | None = None


@dataclass
class OrderEvent(Event):
    """Either a new placement or an update.

    The OrderRouter publishes ``placed`` events when a broker accepts a
    request. Brokers (real or simulated) publish ``update`` events as the
    order moves through statuses.
    """

    kind: Literal["placed", "update", "cancelled", "rejected"]
    request: OrderRequest | None = None
    update: OrderUpdate | None = None
    reason: str = ""


@dataclass
class FillEvent(Event):
    fill: Fill
    order_id: str


__all__ = ["Event", "FillEvent", "MarketDataEvent", "OrderEvent"]
