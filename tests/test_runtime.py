"""Composition root: workspace, strategy runs, and live guardrails."""

from __future__ import annotations

from pathlib import Path

import pytest

from dogeshtrader.auth import TokenStore
from dogeshtrader.config import AppConfig, AppSettings, StrategyConfig
from dogeshtrader.core.enums import Exchange
from dogeshtrader.core.errors import AuthError, ConfigError, DataError
from dogeshtrader.core.models import Instrument
from dogeshtrader.data import InstrumentMaster
from dogeshtrader.runtime import StrategyRun, Workspace, check_live_guardrails

from .conftest import WorkspaceFiles, build_workspace, write_yaml


def _open(files: WorkspaceFiles) -> Workspace:
    return Workspace.open(files.app, risk_config=files.risk)


def test_open_creates_data_dir_and_database(workspace: WorkspaceFiles) -> None:
    ws = _open(workspace)
    assert Path(ws.app.data_dir).is_dir()
    assert Path(ws.app.db_path).exists()
    assert ws.risk.max_order_quantity == 100


def test_resolve_prefers_instrument_master(workspace: WorkspaceFiles) -> None:
    ws = _open(workspace)
    InstrumentMaster(ws.db).upsert_many(
        [Instrument(exchange=Exchange.NSE, tradingsymbol="DEMO", instrument_token=42)]
    )
    run = StrategyRun.load(ws, workspace.strategy)
    assert run.instruments[0].instrument_token == 42
    assert run.instrument_tokens() == [42]


def test_unresolved_instruments_have_no_tokens(workspace: WorkspaceFiles) -> None:
    run = StrategyRun.load(_open(workspace), workspace.strategy)
    assert run.instruments[0].instrument_token is None
    with pytest.raises(DataError, match="instruments download"):
        run.instrument_tokens()


def test_missing_backtest_window_is_config_error(workspace: WorkspaceFiles) -> None:
    run = StrategyRun.load(_open(workspace), workspace.strategy)
    run.config = run.config.model_copy(update={"backtest": None})
    with pytest.raises(ConfigError, match=r"backtest\.from"):
        run.backtest_window()


def test_research_requires_a_split(workspace: WorkspaceFiles) -> None:
    run = StrategyRun.load(_open(workspace), workspace.strategy)
    run.config = run.config.model_copy(update={"research": None})
    with pytest.raises(ConfigError, match="split"):
        run.run_research()


def test_dry_run_without_candles_is_data_error(tmp_path: Path) -> None:
    files = build_workspace(tmp_path, with_candles=False)
    run = StrategyRun.load(_open(files), files.strategy)
    with pytest.raises(DataError, match="no candles"):
        run.dry_run()


def test_invalid_strategy_yaml_is_config_error(workspace: WorkspaceFiles) -> None:
    bad = write_yaml(workspace.root / "bad.yaml", {"strategy": "x"})
    with pytest.raises(ConfigError):
        StrategyRun.load(_open(workspace), bad)


def test_non_mapping_yaml_is_config_error(workspace: WorkspaceFiles) -> None:
    bad = workspace.root / "list.yaml"
    bad.write_text("- just\n- a list\n")
    with pytest.raises(ConfigError):
        Workspace.open(bad)


class TestAccessToken:
    def test_missing_api_key(self, workspace: WorkspaceFiles) -> None:
        ws = _open(workspace)
        ws.settings = AppSettings(kite_api_key="")
        with pytest.raises(AuthError, match="KITE_API_KEY"):
            ws.access_token()

    def test_missing_token(self, workspace: WorkspaceFiles) -> None:
        ws = _open(workspace)
        ws.settings = AppSettings(kite_api_key="key")
        with pytest.raises(AuthError, match="callback-server"):
            ws.access_token()

    def test_stored_token_is_returned(self, workspace: WorkspaceFiles) -> None:
        ws = _open(workspace)
        ws.settings = AppSettings(kite_api_key="key")
        TokenStore(data_dir=ws.app.data_dir, db=ws.db).save(
            {"access_token": "tok", "user_id": "AB1234"}, api_key="key"
        )
        assert ws.access_token() == "tok"


class TestLiveGuardrails:
    @staticmethod
    def _strategy() -> StrategyConfig:
        return StrategyConfig(strategy="x", strategy_id="s1", instruments=[])

    def test_all_opt_ins_pass(self) -> None:
        check_live_guardrails(
            AppConfig(live_trading_enabled=True), self._strategy(), confirmed=True
        )

    def test_config_flag_required(self) -> None:
        with pytest.raises(ConfigError, match="live_trading_enabled"):
            check_live_guardrails(AppConfig(), self._strategy(), confirmed=True)

    def test_cli_confirmation_required(self) -> None:
        with pytest.raises(ConfigError, match="i-understand"):
            check_live_guardrails(
                AppConfig(live_trading_enabled=True), self._strategy(), confirmed=False
            )

    def test_order_tag_prefix_required(self) -> None:
        app = AppConfig(live_trading_enabled=True, order_tag_prefix="other_")
        with pytest.raises(ConfigError, match="order_tag_prefix"):
            check_live_guardrails(app, self._strategy(), confirmed=True)
