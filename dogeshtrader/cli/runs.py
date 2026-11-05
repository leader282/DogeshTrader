"""Run-mode commands: backtest, research, paper, dry-run, live."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import Annotated

import typer
from rich.table import Table

from dogeshtrader.cli._common import (
    AppConfigOption,
    RiskConfigOption,
    StrategyConfigOption,
    StrategyNameOption,
    cli_errors,
    console,
    open_run,
    print_backtest_result,
)

OutputDirOption = Annotated[Path | None, typer.Option(help="Override the reports output dir.")]
MaxEventsOption = Annotated[
    int | None, typer.Option(help="Stop after N events; useful for smoke tests.")
]


@cli_errors
def backtest(
    config: StrategyConfigOption,
    strategy: StrategyNameOption = None,
    app_config: AppConfigOption = None,
    risk_config: RiskConfigOption = None,
    output_dir: OutputDirOption = None,
) -> None:
    """Run a backtest over historical candles stored on disk."""
    run = open_run(config, strategy=strategy, app_config=app_config, risk_config=risk_config)
    print_backtest_result(run.run_backtest(output_dir))


@cli_errors
def research(
    config: StrategyConfigOption,
    strategy: StrategyNameOption = None,
    app_config: AppConfigOption = None,
    risk_config: RiskConfigOption = None,
    split: Annotated[
        str | None,
        typer.Option(help="First out-of-sample session (YYYY-MM-DD). Defaults to research.split."),
    ] = None,
    output_dir: OutputDirOption = None,
) -> None:
    """Backtest, then score the out-of-sample window after costs.

    Exits 1 when out-of-sample expectancy is not positive. That is the gate
    before paper or live trading, not a promise about future results.
    """
    run = open_run(config, strategy=strategy, app_config=app_config, risk_config=risk_config)
    result, report, written = run.run_research(
        split=date.fromisoformat(split) if split else None,
        output_dir=output_dir,
    )
    print_backtest_result(result)
    table = Table(title=f"Research split {report.split}")
    for col in ("Window", "Trades", "Expectancy", "Fees", "Max DD %", "End equity"):
        table.add_column(col, justify="left" if col == "Window" else "right")
    for label, m in (("in-sample", report.in_sample), ("out-of-sample", report.out_of_sample)):
        table.add_row(
            label,
            str(m.trade_count),
            str(m.expectancy),
            str(m.total_fees),
            str(m.max_drawdown_pct),
            str(m.end_equity),
        )
    console.print(table)
    console.print(report.costs_note)
    console.print(f"Research report: {written}")
    if report.promoted:
        console.print("[green]Out-of-sample gate passed.[/green]")
        return
    for reason in report.reasons:
        console.print(f"[red]{reason}[/red]")
    raise typer.Exit(code=1)


@cli_errors
def paper(
    config: StrategyConfigOption,
    strategy: StrategyNameOption = None,
    app_config: AppConfigOption = None,
    risk_config: RiskConfigOption = None,
    replay_historical: Annotated[
        bool, typer.Option(help="Replay stored candles instead of subscribing to live ticks.")
    ] = False,
    max_events: MaxEventsOption = None,
) -> None:
    """Run the strategy in PAPER mode. No orders reach the broker."""
    run = open_run(config, strategy=strategy, app_config=app_config, risk_config=risk_config)
    if replay_historical:
        engine = run.replay_paper(max_events=max_events)
        console.print(f"[green]Paper replay complete: run_id={engine.run_id}[/green]")
        return

    client = run.workspace.kite_client()
    tokens = run.instrument_tokens()
    engine = run.paper_engine()
    ws = client.websocket_adapter()
    ws.on_tick(engine.push_tick)
    try:
        ws.connect(tokens=tokens, mode=run.workspace.app.default_quote_mode, threaded=True)
        engine.run(max_events=max_events)
    finally:
        ws.stop()
    console.print(f"[green]Paper run complete: run_id={engine.run_id}[/green]")


@cli_errors
def dry_run(
    config: StrategyConfigOption,
    strategy: StrategyNameOption = None,
    app_config: AppConfigOption = None,
    risk_config: RiskConfigOption = None,
    n_intents: Annotated[int, typer.Option(help="Route at most N intents per candle.")] = 5,
) -> None:
    """Replay recent stored candles through risk without ever placing an order."""
    run = open_run(config, strategy=strategy, app_config=app_config, risk_config=risk_config)
    rows = run.dry_run(max_intents=n_intents)
    if not rows:
        console.print("[yellow]The strategy produced no intents on recent candles.[/yellow]")
    for accepted, detail in rows:
        if accepted:
            console.print(f"DRY_RUN OK   {detail}")
        else:
            console.print(f"[yellow]DRY_RUN risk[/yellow] {detail}")


@cli_errors
def live(
    config: StrategyConfigOption,
    strategy: StrategyNameOption = None,
    app_config: AppConfigOption = None,
    risk_config: RiskConfigOption = None,
    i_understand_this_places_real_orders: Annotated[
        bool,
        typer.Option(
            "--i-understand-this-places-real-orders",
            help="Required confirmation. WILL place real orders if every guardrail passes.",
        ),
    ] = False,
) -> None:
    """LIVE trading. Off by default; every guardrail must pass.

    Requires ``live_trading_enabled: true``, the confirmation flag, a stored
    Kite token, a non-empty ``strategy_id``, and a ``dogesh`` order tag prefix.
    """
    from dogeshtrader.runtime import check_live_guardrails

    run = open_run(config, strategy=strategy, app_config=app_config, risk_config=risk_config)
    check_live_guardrails(
        run.workspace.app, run.config, confirmed=i_understand_this_places_real_orders
    )
    client = run.workspace.kite_client()
    tokens = run.instrument_tokens()
    engine = run.live_engine(client, confirmed=i_understand_this_places_real_orders)

    console.print("[bold yellow]LIVE TRADING ENABLED.[/bold yellow] Press Ctrl-C to stop.")
    ws = client.websocket_adapter()
    ws.on_tick(engine.push_tick)
    ws.on_order_update(
        lambda raw: engine.push_order_update(engine.broker.normalize_order_update(raw))
    )
    try:
        ws.connect(tokens=tokens, mode=run.workspace.app.default_quote_mode, threaded=True)
        engine.run()
    finally:
        ws.stop()


COMMANDS: dict[str, Callable[..., None]] = {
    "backtest": backtest,
    "research": research,
    "paper": paper,
    "dry-run": dry_run,
    "live": live,
}
