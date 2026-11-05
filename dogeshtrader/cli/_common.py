"""Shared CLI options, console, and error handling."""

from __future__ import annotations

import functools
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Annotated

import typer
from rich.console import Console
from rich.table import Table

from dogeshtrader.core.errors import DogeshTraderError
from dogeshtrader.runtime import StrategyRun, Workspace

if TYPE_CHECKING:
    from dogeshtrader.backtest.engine import BacktestResult

console = Console()

AppConfigOption = Annotated[
    Path | None, typer.Option("--app-config", help="App config YAML (default: env / example).")
]
RiskConfigOption = Annotated[
    Path | None, typer.Option("--risk-config", help="Risk config YAML (default: env / example).")
]
StrategyConfigOption = Annotated[Path, typer.Option("--config", help="Strategy config YAML.")]
StrategyNameOption = Annotated[
    str | None, typer.Option("--strategy", help="Override the strategy named in the config.")
]


def cli_errors[**P, R](fn: Callable[P, R]) -> Callable[P, R]:
    """Print framework errors as one red line and exit 1 instead of a traceback."""

    @functools.wraps(fn)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        try:
            return fn(*args, **kwargs)
        except DogeshTraderError as e:
            console.print(f"[red]{e}[/red]")
            raise typer.Exit(code=1) from e

    return wrapper


def open_run(
    config: Path,
    *,
    strategy: str | None,
    app_config: Path | None,
    risk_config: Path | None,
) -> StrategyRun:
    workspace = Workspace.open(app_config, risk_config=risk_config)
    return StrategyRun.load(workspace, config, strategy_name=strategy)


def print_backtest_result(result: BacktestResult) -> None:
    table = Table(title=f"Backtest {result.run_id}", show_lines=False)
    table.add_column("Metric")
    table.add_column("Value", justify="right")
    m = result.metrics
    for label, value in [
        ("Start equity", m.start_equity),
        ("End equity", m.end_equity),
        ("Total return %", m.total_return_pct),
        ("CAGR %", m.cagr_pct),
        ("Max DD %", m.max_drawdown_pct),
        ("Sharpe-like", m.sharpe_like),
        ("Win rate %", m.win_rate_pct),
        ("Trades", m.trade_count),
        ("Profit factor", m.profit_factor),
        ("Total fees", m.total_fees),
    ]:
        table.add_row(label, str(value if value is not None else "n/a"))
    console.print(table)
    if result.report_paths:
        console.print(f"Reports: {result.report_paths.get('html')}")


__all__ = [
    "AppConfigOption",
    "RiskConfigOption",
    "StrategyConfigOption",
    "StrategyNameOption",
    "cli_errors",
    "console",
    "open_run",
    "print_backtest_result",
]
