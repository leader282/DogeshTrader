"""Strategy registry.

Strategies register themselves either through the ``@register`` decorator or
by being importable under their module name. The CLI uses :func:`get_strategy`
to resolve a name -> class.
"""

from __future__ import annotations

import importlib
from collections.abc import Callable
from typing import TYPE_CHECKING, TypeVar

from dogeshtrader.core.errors import StrategyError

if TYPE_CHECKING:
    from dogeshtrader.strategy.base import Strategy


_REGISTRY: dict[str, type[Strategy]] = {}
T = TypeVar("T", bound="Strategy")


def register(name: str | None = None) -> Callable[[type[T]], type[T]]:
    """Decorator that registers a strategy class under ``name``.

    Usage:
        @register("sample_sma_crossover")
        class SMACrossover(Strategy):
            ...
    """

    def _decorator(cls: type[T]) -> type[T]:
        key = name or getattr(cls, "name", "") or cls.__name__.lower()
        if not key:
            raise StrategyError(f"cannot register strategy without a name: {cls!r}")
        if key in _REGISTRY and _REGISTRY[key] is not cls:
            raise StrategyError(f"strategy name collision: {key!r}")
        _REGISTRY[key] = cls
        cls.name = key
        return cls

    return _decorator


def register_module(module_name: str) -> None:
    """Import ``module_name`` so that its @register decorators run."""
    try:
        importlib.import_module(module_name)
    except ImportError as e:
        raise StrategyError(f"could not import strategy module {module_name}: {e}") from e


def get_strategy(name: str) -> type[Strategy]:
    """Resolve a strategy class by registered name.

    If the name is not yet registered, try to import the corresponding
    ``dogeshtrader.strategy.<name>`` module first (covers the convention used
    by the sample strategies that ship with the framework).
    """
    if name in _REGISTRY:
        return _REGISTRY[name]

    candidate_modules = (
        f"dogeshtrader.strategy.{name}",
        f"dogeshtrader.strategy.sample_{name}",
    )
    last_err: Exception | None = None
    for mod in candidate_modules:
        try:
            importlib.import_module(mod)
        except ImportError as e:
            last_err = e
            continue
        if name in _REGISTRY:
            return _REGISTRY[name]

    if last_err:
        raise StrategyError(f"strategy {name!r} not found: {last_err}")
    raise StrategyError(f"strategy {name!r} not found")


def available_strategies() -> list[str]:
    # Ensure sample strategies are imported even before any explicit lookup.
    for sample in ("sample_sma_crossover", "sample_opening_range"):
        try:
            importlib.import_module(f"dogeshtrader.strategy.{sample}")
        except ImportError:
            pass
    return sorted(_REGISTRY.keys())


__all__ = ["available_strategies", "get_strategy", "register", "register_module"]
