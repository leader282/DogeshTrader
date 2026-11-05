"""``dogesh auth ...``: manual daily Kite login helpers."""

from __future__ import annotations

from typing import Annotated

import typer
from rich.table import Table

from dogeshtrader.cli._common import AppConfigOption, cli_errors, console
from dogeshtrader.core.errors import AuthError
from dogeshtrader.runtime import Workspace

app = typer.Typer(help="Kite authentication helpers.", no_args_is_help=True)


@app.command("login-url")
@cli_errors
def login_url(app_config: AppConfigOption = None) -> None:
    """Print the Kite login URL for the configured API key."""
    from dogeshtrader.auth import build_login_url

    ws = Workspace.open(app_config)
    if not ws.settings.kite_api_key:
        raise AuthError("KITE_API_KEY is empty in .env")
    console.print(build_login_url(ws.settings.kite_api_key))


@app.command("callback-server")
@cli_errors
def callback_server(
    port: Annotated[int, typer.Option(help="Port for the local callback server.")] = 8000,
    host: Annotated[str, typer.Option()] = "127.0.0.1",
    app_config: AppConfigOption = None,
    max_seconds: Annotated[
        int, typer.Option(help="Stop after this many seconds without a token.")
    ] = 600,
) -> None:
    """Run the local FastAPI callback that captures the request_token."""
    from dogeshtrader.auth import CallbackServer, TokenStore

    ws = Workspace.open(app_config)
    if not ws.settings.kite_api_key or not ws.settings.kite_api_secret:
        raise AuthError("KITE_API_KEY and KITE_API_SECRET must be set in .env")
    token_store = TokenStore(data_dir=ws.app.data_dir, db=ws.db)
    server = CallbackServer(token_store=token_store, settings=ws.settings, host=host, port=port)
    console.print(f"Listening on http://{host}:{port}{server.callback_path}")
    console.print(
        "Open the URL from [bold]dogesh auth login-url[/bold] and make sure the Kite app "
        f"redirect is http://{host}:{port}{server.callback_path}"
    )
    if not server.run_until_token(max_seconds=max_seconds):
        console.print("[yellow]Timed out without capturing a token.[/yellow]")
        raise typer.Exit(code=2)
    console.print("[green]Token captured and saved.[/green]")


@app.command("status")
@cli_errors
def status(app_config: AppConfigOption = None) -> None:
    """Show the latest stored token without exposing the secret."""
    from dogeshtrader.auth import TokenStore

    ws = Workspace.open(app_config)
    if not ws.settings.kite_api_key:
        raise AuthError("KITE_API_KEY is empty in .env")
    payload = TokenStore(data_dir=ws.app.data_dir, db=ws.db).load(ws.settings.kite_api_key)
    if not payload:
        console.print("[yellow]No token stored yet. Run `dogesh auth callback-server`.[/yellow]")
        raise typer.Exit(code=2)
    token = payload.get("access_token", "") or ""
    masked = token[:6] + "…" + token[-4:] if len(token) > 12 else "***"
    table = Table(title="Kite token", show_header=False)
    table.add_row("user_id", str(payload.get("user_id", "")))
    table.add_row("user_name", str(payload.get("user_name", "")))
    table.add_row("login_time", str(payload.get("login_time", "")))
    table.add_row("access_token", masked)
    console.print(table)
