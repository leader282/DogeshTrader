"""Order and position reconciliation against the broker's view."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest
from typer.testing import CliRunner

from dogeshtrader.cli import app
from dogeshtrader.core.enums import Exchange, OrderStatus, OrderType, Product, Side, Variety
from dogeshtrader.core.models import Instrument, OrderRequest, OrderUpdate, Position
from dogeshtrader.execution.reconciliation import Reconciliation, position_drifts
from dogeshtrader.runtime import Workspace
from dogeshtrader.storage import AuditRepository, Database, OrderRepository, RunRepository

from .conftest import WorkspaceFiles

INFY = Instrument(exchange=Exchange.NSE, tradingsymbol="INFY")
TCS = Instrument(exchange=Exchange.NSE, tradingsymbol="TCS")


def _record(repo: OrderRepository, order_id: str, status: OrderStatus, filled: int = 0) -> None:
    request = OrderRequest(
        intent_id=f"i_{order_id}",
        strategy_id="s",
        instrument=INFY,
        side=Side.BUY,
        quantity=10,
        order_type=OrderType.MARKET,
        product=Product.MIS,
        variety=Variety.REGULAR,
        tag="dogesh_s",
        idempotency_key=f"k_{order_id}",
    )
    update = OrderUpdate(
        order_id=order_id, status=status, filled_quantity=filled, pending_quantity=10 - filled
    )
    repo.record_intent(intent_id=request.intent_id, run_id="r1", request=request, reason="t")
    repo.record_order(run_id="r1", broker="kite", mode="LIVE", request=request, update=update)


def _broker(order_id: str, status: OrderStatus, filled: int = 0) -> OrderUpdate:
    return OrderUpdate(order_id=order_id, status=status, filled_quantity=filled)


def test_reconcile_classifies_match_drift_and_missing(memory_db: Database) -> None:
    RunRepository(memory_db).create("r1", mode="LIVE", strategy="s", strategy_id="s")
    orders = OrderRepository(memory_db)
    _record(orders, "same", OrderStatus.OPEN)
    _record(orders, "moved", OrderStatus.OPEN)
    _record(orders, "gone", OrderStatus.OPEN)

    items = Reconciliation(orders, AuditRepository(memory_db)).reconcile(
        [
            _broker("same", OrderStatus.OPEN),
            _broker("moved", OrderStatus.COMPLETE, filled=10),
            _broker("manual", OrderStatus.COMPLETE, filled=5),
        ]
    )
    kinds = {
        (i.local or {}).get("order_id") or (i.broker.order_id if i.broker else ""): i.kind
        for i in items
    }
    assert kinds == {
        "same": "match",
        "moved": "drift",
        "manual": "missing_local",
        "gone": "missing_broker",
    }
    drift = next(i for i in items if i.kind == "drift")
    assert drift.delta["status"] == {"local": "OPEN", "broker": "COMPLETE"}
    assert drift.delta["filled_quantity"]["broker"] == 10


@pytest.mark.parametrize(
    ("local", "broker", "universe", "expected"),
    [
        ({"NSE:INFY": 5}, {"NSE:INFY": 5}, {"NSE:INFY"}, []),
        ({"NSE:INFY": 5}, {"NSE:INFY": 3}, {"NSE:INFY"}, [("NSE:INFY", 5, 3)]),
        ({}, {"NSE:TCS": 1}, {"NSE:TCS"}, [("NSE:TCS", 0, 1)]),
        ({}, {"NSE:TCS": 1}, {"NSE:INFY"}, []),
    ],
    ids=["equal", "qty_differs", "unexpected", "outside_universe"],
)
def test_position_drifts(
    local: dict[str, int],
    broker: dict[str, int],
    universe: set[str],
    expected: list[tuple[str, int, int]],
) -> None:
    drifts = position_drifts(local, broker, universe=universe)
    assert [(d.instrument_key, d.local_quantity, d.broker_quantity) for d in drifts] == expected


def test_position_drifts_accepts_position_objects() -> None:
    local = {INFY.key: Position(instrument=INFY, quantity=2)}
    broker = {INFY.key: Position(instrument=INFY, quantity=2)}
    assert position_drifts(local, broker, universe={INFY.key}) == []


class _StubKite:
    def __init__(self, net: list[dict[str, Any]]) -> None:
        self._net = net

    def orders(self) -> list[dict[str, Any]]:
        return []

    def positions(self) -> dict[str, Any]:
        return {"net": self._net, "day": []}


def _run_reconcile(
    workspace: WorkspaceFiles,
    monkeypatch: pytest.MonkeyPatch,
    *,
    snapshot_qty: int | None,
    broker_qty: int,
):
    stub = _StubKite(
        [{"exchange": "NSE", "tradingsymbol": "INFY", "quantity": broker_qty, "multiplier": 1}]
    )
    monkeypatch.setattr(Workspace, "kite_client", lambda self: SimpleNamespace(kite=stub))
    ws = Workspace.open(workspace.app)
    if snapshot_qty is not None:
        ws.operator_state_path.parent.mkdir(parents=True, exist_ok=True)
        ws.operator_state_path.write_text(
            json.dumps({"positions": [{"instrument": "NSE:INFY", "quantity": snapshot_qty}]})
        )
    result = CliRunner().invoke(app, ["reconcile", "--app-config", str(workspace.app)])
    return result, ws


def test_reconcile_cli_halts_on_position_drift(
    workspace: WorkspaceFiles, monkeypatch: pytest.MonkeyPatch
) -> None:
    result, ws = _run_reconcile(workspace, monkeypatch, snapshot_qty=0, broker_qty=4)
    assert result.exit_code == 2, result.output
    assert ws.kill_switch.is_engaged()


def test_reconcile_cli_passes_when_positions_match(
    workspace: WorkspaceFiles, monkeypatch: pytest.MonkeyPatch
) -> None:
    result, ws = _run_reconcile(workspace, monkeypatch, snapshot_qty=4, broker_qty=4)
    assert result.exit_code == 0, result.output
    assert not ws.kill_switch.is_engaged()


def test_reconcile_cli_skips_position_check_without_snapshot(
    workspace: WorkspaceFiles, monkeypatch: pytest.MonkeyPatch
) -> None:
    result, _ = _run_reconcile(workspace, monkeypatch, snapshot_qty=None, broker_qty=4)
    assert result.exit_code == 0
    assert "No operator snapshot" in result.output
