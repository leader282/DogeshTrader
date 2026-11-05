"""Strategy registry.

Strategies are found three ways, in this order:

1. ``@register("name")`` on a class that has already been imported.
2. Built-in modules under ``dogeshtrader.strategy``.
3. Installed plugins that declare a ``dogeshtrader.strategies`` entry point::

       [project.entry-points."dogeshtrader.strategies"]
       my_breakout = "my_pkg.breakout:MyBreakout"

A plugin package needs no change inside this repository.
"""

from __future__ import annotations

import importlib
from collections.abc import Callable
from importlib.metadata import entry_points
from pathlib import Path
from typing import TYPE_CHECKING, TypeVar

from dogeshtrader.core.errors import StrategyError

if TYPE_CHECKING:
    from dogeshtrader.strategy.base import Strategy


ENTRY_POINT_GROUP = "dogeshtrader.strategies"
BUILTIN_MODULES = ("sample_sma_crossover", "sample_opening_range")
# Infrastructure modules in this package. Every other module is a strategy and
# is imported when present, including private ones that are not in git.
_SKIP_MODULES = frozenset({"base", "registry", "signals"})

_REGISTRY: dict[str, type[Strategy]] = {}
T = TypeVar("T", bound="Strategy")


def register(name: str | None = None) -> Callable[[type[T]], type[T]]:
    """Register a strategy class under ``name``.

    Usage::

        @register("sample_sma_crossover")
        class SMACrossover(Strategy): ...
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
    """Import ``module_name`` so that its ``@register`` decorators run."""
    try:
        importlib.import_module(module_name)
    except ImportError as e:
        raise StrategyError(f"could not import strategy module {module_name}: {e}") from e


def _load_plugin(name: str) -> bool:
    for ep in entry_points(group=ENTRY_POINT_GROUP):
        if ep.name != name:
            continue
        try:
            obj = ep.load()
        except Exception as e:
            raise StrategyError(f"strategy plugin {name!r} failed to load: {e}") from e
        if name not in _REGISTRY:
            register(name)(obj)
        return True
    return False


def get_strategy(name: str) -> type[Strategy]:
    """Resolve a strategy class by registered name."""
    if name in _REGISTRY:
        return _REGISTRY[name]

    for mod in (f"dogeshtrader.strategy.{name}", f"dogeshtrader.strategy.sample_{name}"):
        try:
            importlib.import_module(mod)
        except ModuleNotFoundError as e:
            if e.name != mod:
                raise StrategyError(f"strategy module {mod} failed to import: {e}") from e
            continue
        if name in _REGISTRY:
            return _REGISTRY[name]

    if _load_plugin(name) and name in _REGISTRY:
        return _REGISTRY[name]
    raise StrategyError(f"strategy {name!r} not found (built-ins, modules, or plugins)")


def _import_package_strategies() -> None:
    """Import every strategy module that exists on disk.

    Private strategies are gitignored, so a checkout of the public repo only
    sees the samples. A local file such as ``intraday_orb_vwap.py`` registers
    itself when this runs.
    """
    for path in sorted(Path(__file__).parent.glob("*.py")):
        if path.stem.startswith("_") or path.stem in _SKIP_MODULES:
            continue
        importlib.import_module(f"dogeshtrader.strategy.{path.stem}")


def available_strategies() -> list[str]:
    """Names of every on-disk and installed strategy."""
    _import_package_strategies()
    names = set(_REGISTRY)
    names.update(ep.name for ep in entry_points(group=ENTRY_POINT_GROUP))
    return sorted(names)


__all__ = [
    "BUILTIN_MODULES",
    "ENTRY_POINT_GROUP",
    "available_strategies",
    "get_strategy",
    "register",
    "register_module",
]
