"""Strategy package.

A strategy is the only piece of user-specific trading logic. Strategies must
inherit from :class:`Strategy` and register themselves via the :func:`register`
decorator (or by being importable under a known name).
"""

from __future__ import annotations

from dogeshtrader.strategy.base import Strategy
from dogeshtrader.strategy.registry import (
    available_strategies,
    get_strategy,
    register,
    register_module,
)

__all__ = [
    "Strategy",
    "available_strategies",
    "get_strategy",
    "register",
    "register_module",
]
