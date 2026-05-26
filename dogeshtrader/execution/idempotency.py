"""Idempotency guard.

When the framework retries a placement after a transient broker error, we must
make sure we don't double-place. The guard claims a key in SQLite *before*
calling the broker; if the key already exists we abort.
"""

from __future__ import annotations

from typing import Protocol


class IdempotencyStore(Protocol):
    def claim(self, key: str, run_id: str) -> bool: ...
    def attach_order(self, key: str, order_id: str) -> None: ...
    def lookup(self, key: str) -> dict | None: ...


class IdempotencyGuard:
    """Small wrapper that pairs a key with a placement callable."""

    def __init__(self, store: IdempotencyStore, run_id: str) -> None:
        self.store = store
        self.run_id = run_id

    def claim(self, key: str) -> bool:
        return self.store.claim(key, self.run_id)

    def attach(self, key: str, order_id: str) -> None:
        self.store.attach_order(key, order_id)

    def lookup(self, key: str) -> dict | None:
        return self.store.lookup(key)


__all__ = ["IdempotencyGuard", "IdempotencyStore"]
