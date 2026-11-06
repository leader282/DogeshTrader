"""Hosted console commands: ``dogesh console ping`` and ``dogesh console sync``."""

from __future__ import annotations

from typing import Annotated

import typer
from rich.table import Table

from dogeshtrader.cli._common import AppConfigOption, cli_errors, console
from dogeshtrader.runtime import Workspace

app = typer.Typer(
    help="Push runs, trades, and metrics to the hosted console.", no_args_is_help=True
)


@app.command("ping")
@cli_errors
def ping() -> None:
    """Check that DOGESH_CONSOLE_URL and DOGESH_CONSOLE_TOKEN are accepted."""
    from dogeshtrader.cloud import ConsoleClient

    with ConsoleClient.from_settings() as client:
        reply = client.heartbeat()
    console.print(f"[green]Console reachable.[/green] server_time={reply.get('server_time')}")


@app.command("sync")
@cli_errors
def sync(
    run_id: Annotated[
        list[str] | None, typer.Option("--run-id", help="Run to push; repeatable. Default: all.")
    ] = None,
    no_candles: Annotated[
        bool, typer.Option("--no-candles", help="Skip price history for the charts.")
    ] = False,
    app_config: AppConfigOption = None,
) -> None:
    """Upload local runs. Safe to repeat: existing rows are overwritten in place."""
    from dogeshtrader.cloud import ConsoleClient, list_run_ids, run_batch

    ws = Workspace.open(app_config)
    targets = run_id or list_run_ids(ws)
    if not targets:
        console.print("[yellow]No runs recorded yet.[/yellow]")
        return

    table = Table(title="Console sync")
    for col in ("Run", "Orders", "Fills", "Trades", "Equity pts", "Candles"):
        table.add_column(col, justify="left" if col == "Run" else "right")
    with ConsoleClient.from_settings() as client:
        for rid in targets:
            try:
                batch = run_batch(ws, rid, include_candles=not no_candles)
            except KeyError:
                console.print(f"[red]Unknown run_id {rid}[/red]")
                raise typer.Exit(code=1) from None
            counts = client.ingest(batch)
            table.add_row(
                rid,
                *(str(counts[k]) for k in ("orders", "fills", "trades", "equity", "candles")),
            )
    console.print(table)
