"""Abstract base classes for the major framework collaborators.

Concrete classes for each interface live elsewhere:
* Strategy -> dogeshtrader.strategy.base.Strategy
* Broker   -> dogeshtrader.brokers.base.BaseBroker
* MarketDataSource -> dogeshtrader.data.*
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterable, Iterator
from datetime import date
from typing import Any

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


class IStrategy(ABC):
    """Strategy contract.

    Strategies must be *pure* in the sense that they don't touch the network
    or place orders. They only inspect the context and return a list of
    :class:`OrderIntent`.
    """

    name: str = ""

    @abstractmethod
    def on_start(self, ctx: StrategyContext) -> None: ...

    @abstractmethod
    def on_candle(self, ctx: StrategyContext, candle: Candle) -> list[OrderIntent]: ...

    def on_tick(self, ctx: StrategyContext, tick: Tick) -> list[OrderIntent]:
        return []

    def on_order_update(self, ctx: StrategyContext, update: OrderUpdate) -> list[OrderIntent]:
        return []

    def on_end(self, ctx: StrategyContext) -> list[OrderIntent]:
        return []


class IBroker(ABC):
    """Broker contract used by the OrderRouter."""

    mode: str = "PAPER"

    @abstractmethod
    def place_order(self, request: OrderRequest) -> OrderUpdate: ...

    @abstractmethod
    def cancel_order(self, order_id: str, variety: str | None = None) -> OrderUpdate: ...

    @abstractmethod
    def modify_order(
        self,
        order_id: str,
        *,
        quantity: int | None = None,
        price: float | None = None,
        trigger_price: float | None = None,
        order_type: str | None = None,
        validity: str | None = None,
    ) -> OrderUpdate: ...

    @abstractmethod
    def orders(self) -> list[OrderUpdate]: ...

    @abstractmethod
    def order_history(self, order_id: str) -> list[OrderUpdate]: ...

    @abstractmethod
    def trades(self, order_id: str | None = None) -> list[Fill]: ...

    @abstractmethod
    def positions(self) -> dict[str, Position]: ...

    @abstractmethod
    def holdings(self) -> dict[str, Position]: ...

    @abstractmethod
    def quote_ltp(self, instruments: Iterable[Instrument]) -> dict[str, float]: ...

    def on_order_update(self, callback: Any) -> None:
        """Register a callback for OrderUpdate notifications.

        Optional - paper brokers may push these synchronously inside
        place_order, live brokers receive them via WebSocket / postback.
        """


class IMarketDataSource(ABC):
    """Source of historical or live market data."""

    @abstractmethod
    def candles(
        self,
        instrument: Instrument,
        *,
        interval: str,
        from_date: date,
        to_date: date,
    ) -> Iterator[Candle]: ...

    def ticks(self, instrument: Instrument) -> Iterator[Tick]:
        """Default no-op iterator; live sources override."""
        return iter(())


class IRiskEngine(ABC):
    @abstractmethod
    def check(self, intent: OrderIntent, ctx: StrategyContext) -> OrderRequest: ...

    @abstractmethod
    def on_fill(self, fill: Fill) -> None: ...

    @abstractmethod
    def on_rejection(self, reason: str) -> None: ...


__all__ = [
    "IBroker",
    "IMarketDataSource",
    "IRiskEngine",
    "IStrategy",
]
