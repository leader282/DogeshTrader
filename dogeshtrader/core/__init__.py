"""Core domain types and abstractions used across DogeshTrader."""

from __future__ import annotations

from dogeshtrader.core.enums import (
    Exchange,
    InstrumentType,
    Mode,
    OrderStatus,
    OrderType,
    Product,
    Segment,
    Side,
    Validity,
    Variety,
)
from dogeshtrader.core.errors import (
    BrokerError,
    ConfigError,
    DogeshTraderError,
    OrderRejected,
    RiskRejected,
    StrategyError,
)
from dogeshtrader.core.events import (
    Event,
    FillEvent,
    MarketDataEvent,
    OrderEvent,
)
from dogeshtrader.core.models import (
    Candle,
    Fill,
    Instrument,
    OrderIntent,
    OrderRequest,
    OrderUpdate,
    Position,
    StrategyContext,
    Tick,
)

__all__ = [
    # Enums
    "Exchange",
    "InstrumentType",
    "Mode",
    "OrderStatus",
    "OrderType",
    "Product",
    "Segment",
    "Side",
    "Validity",
    "Variety",
    # Errors
    "BrokerError",
    "ConfigError",
    "DogeshTraderError",
    "OrderRejected",
    "RiskRejected",
    "StrategyError",
    # Events
    "Event",
    "FillEvent",
    "MarketDataEvent",
    "OrderEvent",
    # Models
    "Candle",
    "Fill",
    "Instrument",
    "OrderIntent",
    "OrderRequest",
    "OrderUpdate",
    "Position",
    "StrategyContext",
    "Tick",
]
