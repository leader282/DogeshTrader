"""Operator commands: reconcile, kill, resume, dashboard, report, strategies."""

from __future__ import annotations

from collections.abc import Callable
from typing import Annotated

import typer
from rich.table import Table

from dogeshtrader.cli._common import AppConfigOption, cli_errors, console
from dogeshtrader.runtime import Workspace


@cli_errors
def reconcile(app_config: AppConfigOption = None) -> None:
    """Compare local orders and positions with Kite. Halts on position drift."""
    from dogeshtrader.brokers.kite_broker import KiteBroker
    from dogeshtrader.execution.reconciliation import Reconciliation, position_drifts
    from dogeshtrader.operator.state import load_state
    from dogeshtrader.storage import AuditRepository, OrderRepository

    ws = Workspace.open(app_config)
    broker = KiteBroker(ws.kite_client().kite)
    items = Reconciliation(OrderRepository(ws.db), AuditRepository(ws.db)).reconcile(
        broker.orders()
    )
    table = Table(title="Reconciliation")
    for col in ("Kind", "Order/Broker id", "Delta"):
        table.add_column(col)
    for item in items:
        oid = (item.local or {}).get("order_id") or (item.broker.order_id if item.broker else "")
        table.add_row(item.kind, str(oid), str(item.delta))

    snapshot = load_state(ws.operator_state_path)
    drifts = []
    if snapshot and snapshot.get("positions"):
        local_qty = {str(r["instrument"]): int(r["quantity"]) for r in snapshot["positions"]}
        broker_qty = {key: pos.quantity for key, pos in broker.positions().items()}
        drifts = position_drifts(local_qty, broker_qty, universe=set(local_qty))
        for drift in drifts:
            table.add_row(
                "position",
                drift.instrument_key,
                f"local={drift.local_quantity} broker={drift.broker_quantity}",
            )
    else:
        console.print("[yellow]No operator snapshot yet; position halt check skipped.[/yellow]")
    console.print(table)
    if drifts:
        ws.kill_switch.engage("reconcile found an unexpected broker position")
        console.print(
            "[red]Kill switch engaged. Broker position does not match the last run snapshot.[/red]"
        )
        raise typer.Exit(code=2)


@cli_errors
def kill(
    reason: Annotated[str, typer.Option(help="Why trading is being halted.")] = "operator",
    app_config: AppConfigOption = None,
) -> None:
    """Block new entries and tell a running engine to flatten."""
    Workspace.open(app_config).kill_switch.engage(reason)
    console.print("[red]Kill switch engaged. New entries are blocked.[/red]")


@cli_errors
def resume(app_config: AppConfigOption = None) -> None:
    """Clear the kill switch so new entries are allowed again."""
    Workspace.open(app_config).kill_switch.clear()
    console.print("[green]Kill switch cleared.[/green]")


@cli_errors
def dashboard(
    app_config: AppConfigOption = None,
    port: Annotated[int, typer.Option(help="Local port for the operator page.")] = 8765,
    stale_after: Annotated[
        float,
        typer.Option(help="Seconds without an engine update before the page warns."),
    ] = 10.0,
) -> None:
    """Serve the local positions, signals, and P&L page (127.0.0.1 only)."""
    import uvicorn

    from dogeshtrader.operator.dashboard import build_dashboard_app

    ws = Workspace.open(app_config)
    page = build_dashboard_app(
        state_path=ws.operator_state_path,
        kill_path=ws.kill_switch.path,
        stale_after=stale_after,
    )
    console.print(f"Operator page at http://127.0.0.1:{port}")
    uvicorn.run(page, host="127.0.0.1", port=port, log_level="warning")


@cli_errors
def report(
    run_id: Annotated[
        str | None, typer.Option(help="Run id to show; lists recent runs when omitted.")
    ] = None,
    app_config: AppConfigOption = None,
) -> None:
    """List recent runs or show details for one run."""
    from dogeshtrader.storage import RunRepository

    runs = RunRepository(Workspace.open(app_config).db)
    if run_id is None:
        recent = runs.list_recent()
        if not recent:
            console.print("[yellow]No runs recorded yet.[/yellow]")
            return
        cols = ("run_id", "mode", "strategy", "strategy_id", "status", "started_at")
        table = Table(title="Recent runs")
        for col in cols:
            table.add_column(col)
        for r in recent:
            table.add_row(*(str(r[c]) for c in cols))
        console.print(table)
        return
    record = runs.get(run_id)
    if not record:
        console.print(f"[red]Unknown run_id {run_id}[/red]")
        raise typer.Exit(code=1)
    console.print(record)


def strategies() -> None:
    """List every registered strategy, including installed plugins."""
    from dogeshtrader.strategy import available_strategies

    for name in available_strategies():
        console.print(name)


COMMANDS: dict[str, Callable[..., None]] = {
    "reconcile": reconcile,
    "kill": kill,
    "resume": resume,
    "dashboard": dashboard,
    "report": report,
    "strategies": strategies,
}
