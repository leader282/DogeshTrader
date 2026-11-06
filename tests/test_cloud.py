"""Console bridge: batch export from a real backtest, chunking, and the HTTP client."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
import yaml
from typer.testing import CliRunner

from dogeshtrader.cli import app
from dogeshtrader.cloud import ConsoleClient, ConsoleError, list_run_ids, run_batch, split_batch
from dogeshtrader.cloud import client as client_mod
from dogeshtrader.core.enums import Exchange
from dogeshtrader.core.errors import ConfigError
from dogeshtrader.core.models import Instrument
from dogeshtrader.data import CandleStore
from dogeshtrader.runtime import Workspace

from .conftest import SESSION_DAYS, WorkspaceFiles, trending_session, write_yaml

runner = CliRunner()


def _backtest(workspace: WorkspaceFiles) -> tuple[Workspace, str]:
    result = runner.invoke(app, ["backtest", *workspace.cli_args()], catch_exceptions=False)
    assert result.exit_code == 0, result.output
    ws = Workspace.open(workspace.app, risk_config=workspace.risk)
    run_ids = list_run_ids(ws)
    assert len(run_ids) == 1
    return ws, run_ids[0]


def test_run_batch_carries_ledger_equity_and_candles(workspace: WorkspaceFiles) -> None:
    ws, run_id = _backtest(workspace)
    batch = run_batch(ws, run_id)

    (run,) = batch["runs"]
    assert run["run_id"] == run_id
    assert run["mode"] == "BACKTEST"
    assert run["started_at"].endswith("+05:30")
    assert run["metrics"]["trade_count"] == len(batch["trades"]) > 0
    assert run["config"]["strategy_id"] == "test_run"

    assert 2 * len(batch["trades"]) <= len(batch["fills"]) <= len(batch["orders"])
    assert {f["order_id"] for f in batch["fills"]} <= {o["order_id"] for o in batch["orders"]}
    assert batch["equity"]
    assert {c["instrument"] for c in batch["candles"]} == {"NSE:DEMO"}
    assert batch["replace_signals_for"] == [run_id]
    # Exit reasons come from the closing intent, not the raw order tag.
    assert all(not (t["reason_exit"] or "").startswith("dogesh_") for t in batch["trades"])
    json.dumps(batch)


def test_run_batch_has_one_equity_point_per_bar_with_two_instruments(
    workspace: WorkspaceFiles,
) -> None:
    second = Instrument(exchange=Exchange.NSE, tradingsymbol="DEMO2")
    CandleStore(workspace.root / "data").upsert(
        second, "5minute", [c for day in SESSION_DAYS for c in trending_session(day, start=200.0)]
    )
    strategy = yaml.safe_load(workspace.strategy.read_text(encoding="utf-8"))
    strategy["instruments"].append({"exchange": "NSE", "tradingsymbol": "DEMO2"})
    write_yaml(workspace.strategy, strategy)

    ws, run_id = _backtest(workspace)
    batch = run_batch(ws, run_id)

    # The console upserts equity on (run_id, ts) and rejects a batch that repeats one.
    stamps = [e["ts"] for e in batch["equity"]]
    assert len(stamps) == len(set(stamps)) == len({c["ts"] for c in batch["candles"]})


def test_run_batch_without_candles_and_unknown_run(workspace: WorkspaceFiles) -> None:
    ws, run_id = _backtest(workspace)
    assert run_batch(ws, run_id, include_candles=False)["candles"] == []
    with pytest.raises(KeyError):
        run_batch(ws, "nope")


def test_split_batch_respects_limit_and_keeps_runs_first() -> None:
    batch = {
        "runs": [{"run_id": "r"}],
        "orders": [{"i": i} for i in range(7)],
        "candles": [{"i": i} for i in range(5)],
        "replace_signals_for": ["r"],
    }
    parts = split_batch(batch, limit=4)
    assert parts[0]["runs"] == [{"run_id": "r"}]
    assert parts[0]["replace_signals_for"] == ["r"]
    for part in parts:
        assert sum(len(v) for k, v in part.items() if k not in ("runs", "replace_signals_for")) <= 4
    assert sum(len(p.get("orders", [])) for p in parts) == 7
    assert sum(len(p.get("candles", [])) for p in parts) == 5


def _mock_client(handler: Any) -> ConsoleClient:
    return ConsoleClient("http://console.test", "dga_x", transport=httpx.MockTransport(handler))


def test_client_sends_bearer_and_sums_counts() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        body = json.loads(request.content)
        return httpx.Response(
            200, json={"counts": {k: len(v) for k, v in body.items() if isinstance(v, list)}}
        )

    with _mock_client(handler) as client:
        counts = client.ingest({"runs": [{}], "orders": [{}] * 3})
        client.heartbeat({"status": "running"})
    assert counts["orders"] == 3
    assert counts["runs"] == 1
    assert all(r.headers["authorization"] == "Bearer dga_x" for r in seen)
    assert json.loads(seen[-1].content)["live_state"] == {"status": "running"}


def test_client_raises_on_rejection_and_retries_server_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with (
        _mock_client(lambda r: httpx.Response(422, text="bad batch")) as client,
        pytest.raises(ConsoleError, match="422"),
    ):
        client.heartbeat()

    monkeypatch.setattr(client_mod.ConsoleClient._send.retry, "sleep", lambda _s: None)  # type: ignore[attr-defined]
    calls = {"n": 0}

    def flaky(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] < 3:
            return httpx.Response(503)
        return httpx.Response(200, json={"ok": True})

    with _mock_client(flaky) as client:
        assert client.heartbeat()["ok"] is True
    assert calls["n"] == 3


def _refused(request: httpx.Request) -> httpx.Response:
    raise httpx.ConnectError("connection refused", request=request)


@pytest.mark.parametrize(
    ("handler", "message"),
    [
        (_refused, "could not reach the console at http://console.test"),
        (lambda r: httpx.Response(500, text="boom"), "HTTP 500 .*: boom"),
        (
            lambda r: httpx.Response(308, headers={"location": "https://console.test/x"}),
            "HTTP 308 .*: https://console.test/x",
        ),
        (lambda r: httpx.Response(200, text="<html>"), "non-JSON"),
    ],
)
def test_client_reports_every_failure_as_console_error(
    monkeypatch: pytest.MonkeyPatch, handler: Any, message: str
) -> None:
    monkeypatch.setattr(client_mod.ConsoleClient._send.retry, "sleep", lambda _s: None)  # type: ignore[attr-defined]
    with _mock_client(handler) as client, pytest.raises(ConsoleError, match=message):
        client.heartbeat()


def test_cli_ping_prints_one_line_when_console_is_down(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(client_mod.ConsoleClient._send.retry, "sleep", lambda _s: None)  # type: ignore[attr-defined]
    monkeypatch.setattr(
        ConsoleClient, "from_settings", classmethod(lambda cls, s=None: _mock_client(_refused))
    )
    result = runner.invoke(app, ["console", "ping"], catch_exceptions=False)
    assert result.exit_code == 1
    assert "could not reach the console" in result.output


def test_client_requires_url_and_token() -> None:
    with pytest.raises(ConfigError):
        ConsoleClient("", "")


def test_cli_sync_pushes_every_run(
    workspace: WorkspaceFiles, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, run_id = _backtest(workspace)
    received: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        received.append(body)
        return httpx.Response(
            200, json={"counts": {k: len(v) for k, v in body.items() if isinstance(v, list)}}
        )

    monkeypatch.setattr(
        ConsoleClient, "from_settings", classmethod(lambda cls, s=None: _mock_client(handler))
    )
    result = runner.invoke(
        app, ["console", "sync", "--app-config", str(workspace.app)], catch_exceptions=False
    )
    assert result.exit_code == 0, result.output
    assert received[0]["runs"][0]["run_id"] == run_id

    result = runner.invoke(
        app,
        ["console", "sync", "--run-id", "missing", "--app-config", str(workspace.app)],
        catch_exceptions=False,
    )
    assert result.exit_code == 1
