"""Operator dashboard: engine health, the JSON view, and the engines that feed it."""

from __future__ import annotations

import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from dogeshtrader.cli import app as cli_app
from dogeshtrader.config import AppConfig, InstrumentRef, RiskConfig, StrategyConfig
from dogeshtrader.core.enums import Exchange, Mode
from dogeshtrader.core.models import Candle, Instrument, OrderIntent, Position, StrategyContext
from dogeshtrader.operator.dashboard import build_dashboard_app, build_view, page_html
from dogeshtrader.operator.state import OperatorBook, load_state, publish_run, state_path
from dogeshtrader.risk.kill_switch import KillSwitch
from dogeshtrader.runtime import StrategyRun, Workspace
from dogeshtrader.simulation.live_paper_engine import LivePaperEngine
from dogeshtrader.storage import Database, run_migrations
from dogeshtrader.strategy.base import Strategy

from .conftest import WorkspaceFiles

NOW = datetime(2026, 1, 5, 4, 0, tzinfo=UTC)
INFY = Instrument(exchange=Exchange.NSE, tradingsymbol="INFY", instrument_token=408065)


def _snapshot(**overrides: Any) -> dict[str, Any]:
    state: dict[str, Any] = {
        "run_id": "run_x",
        "mode": "PAPER",
        "status": "running",
        "timestamp": "2026-01-05T09:30:00+05:30",
        "published_at": (NOW - timedelta(seconds=2)).isoformat(),
        "equity": 100_250.0,
        "day_pnl": 250.0,
        "positions": [
            {"instrument": "NSE:INFY", "quantity": 10, "avg_price": 100.0},
            {"instrument": "NSE:TCS", "quantity": 0, "avg_price": 0.0},
        ],
        "signals": [
            {"timestamp": "09:20", "instrument": "NSE:INFY", "reason": "first"},
            {"timestamp": "09:25", "instrument": "NSE:INFY", "reason": "second"},
        ],
        "risk": {"trades_today": 1, "month_pnl": 900.0, "breakers": [], "bad_data": {}},
    }
    state.update(overrides)
    return state


@pytest.fixture()
def switch(tmp_path: Path) -> KillSwitch:
    return KillSwitch(tmp_path / "KILL")


class TestHealth:
    def test_no_snapshot_is_waiting(self, switch: KillSwitch) -> None:
        view = build_view(None, kill_switch=switch, now=NOW)
        assert view["health"] == "waiting"
        assert view["positions"] == []

    def test_recent_running_snapshot_is_live(self, switch: KillSwitch) -> None:
        view = build_view(_snapshot(), kill_switch=switch, now=NOW)
        assert view["health"] == "live"
        assert view["age_seconds"] == 2.0

    def test_old_running_snapshot_is_stale(self, switch: KillSwitch) -> None:
        old = (NOW - timedelta(seconds=30)).isoformat()
        view = build_view(_snapshot(published_at=old), kill_switch=switch, now=NOW)
        assert view["health"] == "stale"
        assert "30s" in view["health_text"]

    def test_threshold_is_configurable(self, switch: KillSwitch) -> None:
        old = (NOW - timedelta(seconds=30)).isoformat()
        view = build_view(_snapshot(published_at=old), kill_switch=switch, now=NOW, stale_after=60)
        assert view["health"] == "live"

    def test_snapshot_without_publish_time_is_stale(self, switch: KillSwitch) -> None:
        state = _snapshot()
        del state["published_at"]
        assert build_view(state, kill_switch=switch, now=NOW)["health"] == "stale"

    def test_finished_run_is_stopped_not_stale(self, switch: KillSwitch) -> None:
        old = (NOW - timedelta(hours=3)).isoformat()
        view = build_view(
            _snapshot(status="stopped", published_at=old), kill_switch=switch, now=NOW
        )
        assert view["health"] == "stopped"


def test_view_shows_open_positions_newest_signals_and_kill_reason(switch: KillSwitch) -> None:
    switch.engage("news event")
    view = build_view(_snapshot(), kill_switch=switch, now=NOW)

    assert [p["instrument"] for p in view["positions"]] == ["NSE:INFY"]
    assert [s["reason"] for s in view["signals"]] == ["second", "first"]
    assert view["kill_switch"] == {"engaged": True, "reason": "news event"}
    assert view["month_pnl"] == 900.0
    assert view["trades_today"] == 1


def test_api_page_and_healthz(tmp_path: Path, switch: KillSwitch) -> None:
    book = OperatorBook(state_path(tmp_path))
    book.publish(
        run_id="run_api",
        mode="LIVE",
        timestamp="t",
        equity=1.0,
        day_pnl=0.0,
        positions=[],
        kill_switch=False,
        kill_reason="",
        risk={},
    )
    client = TestClient(build_dashboard_app(state_path=book.path, kill_path=switch.path))

    state = client.get("/api/state").json()
    assert state["run_id"] == "run_api"
    assert state["health"] == "live"

    page = client.get("/")
    assert page.status_code == 200
    assert "/api/state" in page.text
    assert 'http-equiv="refresh"' not in page.text
    assert client.get("/healthz").json() == {"ok": True}


def test_injected_clock_drives_staleness(tmp_path: Path, switch: KillSwitch) -> None:
    book = OperatorBook(state_path(tmp_path))
    book.publish(
        run_id="r",
        mode="PAPER",
        timestamp="t",
        equity=1.0,
        day_pnl=0.0,
        positions=[],
        kill_switch=False,
        kill_reason="",
        risk={},
    )
    later = lambda: datetime.now(UTC) + timedelta(minutes=5)  # noqa: E731
    client = TestClient(
        build_dashboard_app(state_path=book.path, kill_path=switch.path, clock=later)
    )
    assert client.get("/api/state").json()["health"] == "stale"


def test_page_text_is_rendered_with_text_content_not_html() -> None:
    html = page_html()
    assert "innerHTML" not in html
    assert "textContent" in html


class TestOperatorBook:
    def _publish(self, book: OperatorBook, **kw: Any) -> bool:
        return book.publish(
            run_id="r",
            mode="PAPER",
            timestamp="t",
            equity=1.0,
            day_pnl=0.0,
            positions=[],
            kill_switch=False,
            kill_reason="",
            risk={},
            **kw,
        )

    def test_writes_are_throttled_unless_forced(self, tmp_path: Path) -> None:
        book = OperatorBook(tmp_path / "s.json", min_interval=60)
        assert self._publish(book)
        book.add_signal({"reason": "kept"})
        assert not self._publish(book)
        assert load_state(book.path)["signals"] == []

        assert self._publish(book, status="stopped", force=True)
        saved = load_state(book.path)
        assert saved["status"] == "stopped"
        assert saved["signals"] == [{"reason": "kept"}]

    def test_signal_buffer_is_bounded(self, tmp_path: Path) -> None:
        book = OperatorBook(tmp_path / "s.json")
        for i in range(80):
            book.add_signal({"n": i})
        assert len(book.signals) == 50
        assert book.signals[0] == {"n": 30}


def test_record_signal_reaches_the_snapshot(tmp_path: Path) -> None:
    ctx = StrategyContext(
        run_id="r",
        strategy_id="s",
        mode="PAPER",
        timestamp=datetime(2026, 1, 5, 9, 30),
        cash=0.0,
        positions={INFY.key: Position(instrument=INFY, quantity=1)},
    )
    ctx.record_signal(INFY, side="BUY", reason="breakout", evidence={"rvol": 2.0})
    ctx.record_signal("NSE:TCS", side="SELL", reason="fade")

    class _Risk:
        def status(self) -> dict[str, object]:
            return {"day_pnl": 12.5}

    book = OperatorBook(tmp_path / "s.json")
    publish_run(book, ctx, run_id="r", mode="PAPER", equity=5.0, risk=_Risk(), kill_switch=None)

    saved = load_state(book.path)
    assert [s["reason"] for s in saved["signals"]] == ["breakout", "fade"]
    assert saved["signals"][0]["evidence"] == {"rvol": 2.0}
    assert saved["day_pnl"] == 12.5
    assert ctx.drain_signals() == []


def test_backtest_marks_snapshot_stopped_with_final_equity(workspace: WorkspaceFiles) -> None:
    ws = Workspace.open(workspace.app, risk_config=workspace.risk)
    result = StrategyRun.load(ws, workspace.strategy).run_backtest(workspace.root / "out")

    saved = load_state(ws.operator_state_path)
    assert saved is not None
    assert saved["status"] == "stopped"
    assert saved["run_id"] == result.run_id
    assert saved["equity"] == pytest.approx(result.metrics.end_equity)


def test_paper_replay_marks_snapshot_stopped(workspace: WorkspaceFiles) -> None:
    ws = Workspace.open(workspace.app, risk_config=workspace.risk)
    engine = StrategyRun.load(ws, workspace.strategy).replay_paper(max_events=20)
    saved = load_state(ws.operator_state_path)
    assert saved["status"] == "stopped"
    assert saved["run_id"] == engine.run_id


class _Idle(Strategy):
    def on_candle(self, ctx: StrategyContext, candle: Candle) -> list[OrderIntent]:
        return []


def test_idle_engine_keeps_publishing_heartbeats(tmp_path: Path) -> None:
    db = Database(":memory:", in_memory=True)
    run_migrations(db)
    engine = LivePaperEngine(
        strategy=_Idle({}),
        instruments=[INFY],
        strategy_cfg=StrategyConfig(
            strategy="idle",
            strategy_id="hb",
            instruments=[InstrumentRef(exchange="NSE", tradingsymbol="INFY")],
            interval="tick",
        ),
        app_cfg=AppConfig(data_dir=tmp_path, db_path=tmp_path / "x.sqlite"),
        risk_cfg=RiskConfig(),
        db=db,
        mode=Mode.PAPER,
    )
    engine.poll_seconds = 0.02
    engine.operator.min_interval = 0.0
    path = state_path(tmp_path)

    worker = threading.Thread(target=engine.run, daemon=True)
    worker.start()
    try:
        deadline = time.monotonic() + 5
        first = None
        while time.monotonic() < deadline:
            saved = load_state(path)
            if saved and first is None:
                first = saved["published_at"]
            elif saved and saved["published_at"] != first:
                break
            time.sleep(0.02)
        assert saved is not None
        assert saved["status"] == "running"
        assert saved["published_at"] != first, "no heartbeat while the queue was empty"
    finally:
        engine.stop()
        worker.join(timeout=5)
    assert load_state(path)["status"] == "stopped"


def test_dashboard_command_serves_locally(
    workspace: WorkspaceFiles, monkeypatch: pytest.MonkeyPatch
) -> None:
    import uvicorn

    served: dict[str, Any] = {}

    def fake_run(app: Any, **kwargs: Any) -> None:
        served["app"] = app
        served.update(kwargs)

    monkeypatch.setattr(uvicorn, "run", fake_run)
    result = CliRunner().invoke(
        cli_app,
        ["dashboard", "--app-config", str(workspace.app), "--port", "9999", "--stale-after", "3"],
    )

    assert result.exit_code == 0, result.output
    assert served["host"] == "127.0.0.1"
    assert served["port"] == 9999
    view = TestClient(served["app"]).get("/api/state").json()
    assert view["stale_after"] == 3.0
    assert view["health"] == "waiting"
