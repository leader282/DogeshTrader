"""``dogesh instruments ...`` and ``dogesh data ...``: market metadata and history."""

from __future__ import annotations

from datetime import date
from typing import Annotated

import typer

from dogeshtrader.cli._common import AppConfigOption, cli_errors, console
from dogeshtrader.core.errors import DataError
from dogeshtrader.runtime import Workspace

instruments_app = typer.Typer(help="Instrument master management.", no_args_is_help=True)
data_app = typer.Typer(help="Market data tooling.", no_args_is_help=True)


@instruments_app.command("download")
@cli_errors
def instruments_download(
    exchange: Annotated[
        str | None, typer.Option(help="Optional exchange filter (NSE, BSE, ...).")
    ] = None,
    app_config: AppConfigOption = None,
) -> None:
    """Refresh the local instrument master from Kite."""
    from dogeshtrader.data import InstrumentMaster

    ws = Workspace.open(app_config)
    instruments = ws.kite_client().instruments_adapter().download(exchange=exchange)
    saved = InstrumentMaster(ws.db).upsert_many(instruments)
    console.print(
        f"[green]Saved {saved} instruments[/green] (exchange filter: {exchange or 'ALL'})"
    )


@data_app.command("historical")
@cli_errors
def historical(
    exchange: Annotated[str, typer.Option(help="Exchange code (NSE, BSE, NFO...).")],
    symbol: Annotated[str, typer.Option(help="Trading symbol (case-sensitive).")],
    from_: Annotated[str, typer.Option("--from", help="YYYY-MM-DD")],
    to: Annotated[str, typer.Option("--to", help="YYYY-MM-DD")],
    interval: Annotated[str, typer.Option()] = "5minute",
    app_config: AppConfigOption = None,
) -> None:
    """Download historical candles for one instrument."""
    from dogeshtrader.data import CandleStore, HistoricalDownloader, InstrumentMaster

    ws = Workspace.open(app_config)
    client = ws.kite_client()
    inst = InstrumentMaster(ws.db).get(exchange, symbol)
    if inst is None:
        raise DataError(
            f"instrument {exchange}:{symbol} not in master; "
            f"run `dogesh instruments download --exchange {exchange}` first"
        )
    downloader = HistoricalDownloader(client.market_data_adapter(), CandleStore(ws.app.data_dir))
    progress = downloader.download(
        inst,
        from_dt=date.fromisoformat(from_),
        to_dt=date.fromisoformat(to),
        interval=interval,
    )
    console.print(
        f"[green]Downloaded {progress.candles} candles in {progress.chunks} chunks "
        f"for {inst.key} @ {interval}[/green]"
    )
