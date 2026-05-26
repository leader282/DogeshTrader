"""Broker adapters.

The framework picks a concrete broker by run mode:
* BACKTEST / PAPER -> :class:`PaperBroker`
* DRY_RUN          -> :class:`DryRunBroker` (optionally wraps a real KiteBroker
                      for margin queries)
* LIVE             -> :class:`KiteBroker`
"""

from __future__ import annotations

from dogeshtrader.brokers.base import BaseBroker
from dogeshtrader.brokers.dry_run_broker import DryRunBroker
from dogeshtrader.brokers.paper_broker import PaperBroker

__all__ = ["BaseBroker", "DryRunBroker", "PaperBroker"]

# KiteBroker is imported lazily; importing eagerly would pull in kiteconnect
# even when the user is just running backtests.
try:
    from dogeshtrader.brokers.kite_broker import KiteBroker  # noqa: F401

    __all__.append("KiteBroker")
except ImportError:  # pragma: no cover - kite client unavailable
    pass
