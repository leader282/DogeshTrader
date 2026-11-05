"""End-to-end CLI behaviour through Typer's test runner."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from dogeshtrader.cli import app

from .conftest import WorkspaceFiles, build_workspace

runner = CliRunner()


def _invoke(*args: str):
    return runner.invoke(app, list(args), catch_exceptions=False)


def test_help_lists_every_command_group() -> None:
    result = _invoke("--help")
    assert result.exit_code == 0
    for name in ("auth", "data", "instruments", "backtest", "research", "live", "kill"):
        assert name in result.output


def test_strategies_lists_builtins() -> None:
    result = _invoke("strategies")
    assert result.exit_code == 0
    for name in ("sample_sma_crossover", "sample_opening_range"):
        assert name in result.output


def test_missing_config_is_one_line_error_not_traceback(workspace: WorkspaceFiles) -> None:
    result = _invoke("backtest", "--config", "/nope.yaml", *workspace.common_args())
    assert result.exit_code == 1
    assert "config not found" in result.output
    assert "Traceback" not in result.output


def test_unknown_strategy_override_fails_cleanly(workspace: WorkspaceFiles) -> None:
    result = _invoke("backtest", *workspace.cli_args("--strategy", "does_not_exist"))
    assert result.exit_code == 1
    assert "does_not_exist" in result.output


def test_backtest_writes_reports(workspace: WorkspaceFiles, tmp_path: Path) -> None:
    out = tmp_path / "out"
    result = _invoke("backtest", *workspace.cli_args("--output-dir", str(out)))
    assert result.exit_code == 0, result.output
    assert "End equity" in result.output
    assert any(out.iterdir())


def test_research_passes_gate_on_profitable_out_of_sample(
    workspace: WorkspaceFiles, tmp_path: Path
) -> None:
    out = tmp_path / "research"
    result = _invoke("research", *workspace.cli_args("--output-dir", str(out)))
    assert result.exit_code == 0, result.output
    payload = json.loads((out / "research.json").read_text())
    assert payload["promoted"] is True
    assert payload["out_of_sample"]["trade_count"] > 0


def test_research_fails_gate_when_out_of_sample_has_no_trades(
    workspace: WorkspaceFiles, tmp_path: Path
) -> None:
    result = _invoke(
        "research",
        *workspace.cli_args("--split", "2030-01-01", "--output-dir", str(tmp_path / "r")),
    )
    assert result.exit_code == 1
    assert "no closed trades" in result.output


def test_dry_run_routes_without_placing_orders(workspace: WorkspaceFiles) -> None:
    result = _invoke("dry-run", *workspace.cli_args())
    assert result.exit_code == 0, result.output
    assert "DRY_RUN" in result.output


def test_paper_replay_completes(workspace: WorkspaceFiles) -> None:
    result = _invoke("paper", *workspace.cli_args("--replay-historical", "--max-events", "50"))
    assert result.exit_code == 0, result.output
    assert "Paper replay complete" in result.output


def test_live_refuses_when_disabled_in_config(workspace: WorkspaceFiles) -> None:
    result = _invoke("live", *workspace.cli_args("--i-understand-this-places-real-orders"))
    assert result.exit_code == 1
    assert "live_trading_enabled is false" in result.output


def test_live_refuses_without_confirmation_flag(tmp_path: Path) -> None:
    files = build_workspace(tmp_path, app_overrides={"live_trading_enabled": True})
    result = _invoke("live", *files.cli_args())
    assert result.exit_code == 1
    assert "--i-understand-this-places-real-orders" in result.output


def test_kill_and_resume_toggle_the_switch_file(workspace: WorkspaceFiles) -> None:
    kill_file = workspace.root / "data" / "KILL"

    result = _invoke("kill", "--reason", "drill", *workspace.common_args()[:2])
    assert result.exit_code == 0
    assert kill_file.exists()
    assert "drill" in kill_file.read_text()

    result = _invoke("resume", *workspace.common_args()[:2])
    assert result.exit_code == 0
    assert not kill_file.exists()


def test_report_lists_runs_after_backtest(workspace: WorkspaceFiles, tmp_path: Path) -> None:
    app_args = workspace.common_args()[:2]
    empty = _invoke("report", *app_args)
    assert "No runs recorded yet" in empty.output

    _invoke("backtest", *workspace.cli_args("--output-dir", str(tmp_path / "o")))
    listed = _invoke("report", *app_args)
    assert listed.exit_code == 0
    assert "BACKTEST" in listed.output


def test_report_unknown_run_id_exits_nonzero(workspace: WorkspaceFiles) -> None:
    result = _invoke("report", "--run-id", "missing", *workspace.common_args()[:2])
    assert result.exit_code == 1


@pytest.mark.parametrize("command", ["paper", "live"])
def test_network_modes_need_kite_credentials(workspace: WorkspaceFiles, command: str) -> None:
    files = workspace
    if command == "live":
        files = build_workspace(
            workspace.root / "live", app_overrides={"live_trading_enabled": True}
        )
        args = files.cli_args("--i-understand-this-places-real-orders")
    else:
        args = files.cli_args()
    result = runner.invoke(app, [command, *args], env={"KITE_API_KEY": ""})
    assert result.exit_code == 1
    assert "KITE_API_KEY" in result.output
