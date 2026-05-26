"""Reconcile local state with the broker.

The reconciliation engine compares orders we *believe* exist (in our SQLite)
with orders the broker reports. Three categories:
* match - quantities, statuses, prices line up
* drift - same id but different state -> log delta
* missing_broker - we have it, broker doesn't (or vice versa)

Reconciliation runs:
* on startup (catch postback drops)
* on demand via ``dogesh reconcile``
* periodically inside the live-paper / live engines (optional)
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from dogeshtrader.core.models import OrderUpdate
from dogeshtrader.logging_config import get_logger
from dogeshtrader.storage import AuditRepository, OrderRepository

_log = get_logger(__name__)


@dataclass
class ReconcileItem:
    kind: str  # "match" | "drift" | "missing_broker" | "missing_local"
    local: dict[str, Any] | None
    broker: OrderUpdate | None
    delta: dict[str, Any]


class Reconciliation:
    def __init__(
        self,
        orders: OrderRepository,
        audit: AuditRepository,
        *,
        run_id: str | None = None,
    ) -> None:
        self.orders = orders
        self.audit = audit
        self.run_id = run_id

    def reconcile(self, broker_orders: Iterable[OrderUpdate]) -> list[ReconcileItem]:
        broker_map: dict[str, OrderUpdate] = {o.order_id: o for o in broker_orders}

        local_open = self.orders.open_orders(run_id=self.run_id)
        local_map: dict[str, dict[str, Any]] = {o["order_id"]: o for o in local_open}

        items: list[ReconcileItem] = []
        for oid, b in broker_map.items():
            l = local_map.pop(oid, None)
            if l is None:
                items.append(
                    ReconcileItem(
                        kind="missing_local",
                        local=None,
                        broker=b,
                        delta={"order_id": oid, "status": b.status.value},
                    )
                )
                self.audit.record_reconcile(
                    run_id=self.run_id,
                    intent_id=None,
                    order_id=None,
                    broker_order_id=oid,
                    kind="missing_local",
                    delta={"status": b.status.value},
                )
                continue

            delta: dict[str, Any] = {}
            if l["status"] != b.status.value:
                delta["status"] = {"local": l["status"], "broker": b.status.value}
            if int(l.get("filled_quantity") or 0) != b.filled_quantity:
                delta["filled_quantity"] = {
                    "local": l.get("filled_quantity"),
                    "broker": b.filled_quantity,
                }
            if delta:
                items.append(ReconcileItem(kind="drift", local=l, broker=b, delta=delta))
                self.audit.record_reconcile(
                    run_id=self.run_id,
                    intent_id=l.get("intent_id"),
                    order_id=oid,
                    broker_order_id=oid,
                    kind="drift",
                    delta=delta,
                )
            else:
                items.append(ReconcileItem(kind="match", local=l, broker=b, delta={}))

        for oid, l in local_map.items():
            items.append(
                ReconcileItem(
                    kind="missing_broker",
                    local=l,
                    broker=None,
                    delta={"status": l["status"]},
                )
            )
            self.audit.record_reconcile(
                run_id=self.run_id,
                intent_id=l.get("intent_id"),
                order_id=oid,
                broker_order_id=None,
                kind="missing_broker",
                delta={"status": l["status"]},
            )

        for item in items:
            if item.kind != "match":
                _log.info("reconcile", kind=item.kind, delta=item.delta)
        return items


__all__ = ["ReconcileItem", "Reconciliation"]
