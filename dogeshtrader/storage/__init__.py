"""SQLite-backed persistence layer.

* ``Database`` - thin connection helper.
* ``migrations.run_migrations`` - idempotent schema setup.
* ``repositories.*`` - small mapper classes per aggregate (runs, orders, fills,
  ticks, audit, token).
"""

from __future__ import annotations

from dogeshtrader.storage.db import Database
from dogeshtrader.storage.migrations import run_migrations
from dogeshtrader.storage.repositories import (
    AuditRepository,
    FillRepository,
    IdempotencyRepository,
    InstrumentRepository,
    OrderRepository,
    RunRepository,
    TokenRepository,
)

__all__ = [
    "AuditRepository",
    "Database",
    "FillRepository",
    "IdempotencyRepository",
    "InstrumentRepository",
    "OrderRepository",
    "RunRepository",
    "TokenRepository",
    "run_migrations",
]
