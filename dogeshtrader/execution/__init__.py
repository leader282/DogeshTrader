"""Execution layer.

Surface:
* :class:`OrderRouter` - turns OrderIntents into broker calls in the active mode.
* :class:`OrderManager` - tracks the lifecycle of placed orders.
* :class:`TokenBucket` / :class:`RateLimiter` - throttles outbound order calls.
* :class:`Reconciliation` - reconciles internal state with broker truth.
"""

from __future__ import annotations

from dogeshtrader.execution.idempotency import IdempotencyGuard
from dogeshtrader.execution.order_manager import OrderManager
from dogeshtrader.execution.order_router import OrderRouter, RouterResult
from dogeshtrader.execution.rate_limiter import RateLimiter, TokenBucket
from dogeshtrader.execution.reconciliation import Reconciliation
from dogeshtrader.execution.tags import compose_tag, parse_tag

__all__ = [
    "IdempotencyGuard",
    "OrderManager",
    "OrderRouter",
    "RateLimiter",
    "Reconciliation",
    "RouterResult",
    "TokenBucket",
    "compose_tag",
    "parse_tag",
]
