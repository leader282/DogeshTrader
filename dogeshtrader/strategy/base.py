"""Base strategy class.

The framework guarantees that :class:`Strategy` instances are *not* given any
broker handles. They receive a :class:`StrategyContext` snapshot and return
:class:`OrderIntent` instances. The execution layer is responsible for
turning intents into orders (or rejecting them).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, ClassVar

from pydantic import BaseModel, ValidationError

from dogeshtrader.core.errors import StrategyError
from dogeshtrader.core.interfaces import IStrategy
from dogeshtrader.core.models import (
    Candle,
    OrderIntent,
    OrderUpdate,
    StrategyContext,
    Tick,
)


class Strategy(IStrategy, ABC):
    """Base class for all DogeshTrader strategies.

    Lifecycle:
        ``on_start`` -> repeated ``on_candle`` / ``on_tick`` / ``on_order_update``
        -> ``on_end``

    Set ``Params`` to a pydantic model to validate the YAML ``params`` block
    when the strategy is built. Unknown or mistyped keys then fail at load
    time instead of silently falling back to a default mid-session.
    """

    #: Human-readable, snake_case name. Set by ``@register``.
    name: str = ""

    #: Optional typed parameter schema.
    Params: ClassVar[type[BaseModel] | None] = None

    def __init__(self, params: dict[str, Any] | None = None) -> None:
        raw = dict(params or {})
        self.settings: BaseModel | None = None
        if self.Params is not None:
            try:
                self.settings = self.Params(**raw)
            except ValidationError as e:
                raise StrategyError(
                    f"invalid params for {self.name or type(self).__name__}: {e}"
                ) from e
            raw = self.settings.model_dump()
        self.params: dict[str, Any] = raw

    # ------------------------------------------------------------------
    # Lifecycle - override as needed.
    # ------------------------------------------------------------------

    def on_start(self, ctx: StrategyContext) -> None:
        """Called once before any market events."""

    @abstractmethod
    def on_candle(self, ctx: StrategyContext, candle: Candle) -> list[OrderIntent]:
        """Called for every emitted candle."""

    def on_tick(self, ctx: StrategyContext, tick: Tick) -> list[OrderIntent]:
        """Called for every tick (PAPER and LIVE modes). Default no-op."""
        return []

    def on_order_update(self, ctx: StrategyContext, update: OrderUpdate) -> list[OrderIntent]:
        """Called whenever an order moves through a status. Default no-op."""
        return []

    def on_end(self, ctx: StrategyContext) -> list[OrderIntent]:
        """Called once at the end of a run. Can emit exit intents."""
        return []

    # ------------------------------------------------------------------
    # Convenience helpers
    # ------------------------------------------------------------------

    def get_param(self, key: str, default: Any = None) -> Any:
        return self.params.get(key, default)


__all__ = ["Strategy"]
