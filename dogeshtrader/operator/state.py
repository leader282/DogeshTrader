"""Operator snapshot written by the engines and read by the dashboard.

The trading loop and the dashboard are separate processes. They share a JSON
file plus the kill-switch file. The snapshot is a view of the latest run, not
the order of record (that stays in SQLite).
"""

from __future__ import annotations

import json
import os
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from dogeshtrader.core.models import StrategyContext

RunStatus = Literal["running", "stopped"]

#: Seconds between snapshot writes while a run is in progress. Signals are
#: buffered in memory in between, so nothing is lost by skipping a write.
DEFAULT_MIN_INTERVAL = 0.5
MAX_SIGNALS = 50


def state_path(data_dir: Path | str) -> Path:
    return Path(data_dir) / "operator" / "state.json"


class OperatorBook:
    def __init__(self, path: Path | str, *, min_interval: float = DEFAULT_MIN_INTERVAL) -> None:
        self.path = Path(path)
        self.min_interval = min_interval
        self.signals: list[dict[str, Any]] = []
        self._last_write = float("-inf")

    def add_signal(self, payload: dict[str, Any]) -> None:
        self.signals.append(payload)
        if len(self.signals) > MAX_SIGNALS:
            self.signals = self.signals[-MAX_SIGNALS:]

    def drain(self, ctx: StrategyContext) -> None:
        for item in ctx.drain_signals():
            self.add_signal(item)

    def publish(
        self,
        *,
        run_id: str,
        mode: str,
        timestamp: str,
        equity: float,
        day_pnl: float,
        positions: list[dict[str, Any]],
        kill_switch: bool,
        kill_reason: str,
        risk: dict[str, Any],
        status: RunStatus = "running",
        force: bool = False,
    ) -> bool:
        """Write the snapshot. Returns False when throttled."""
        now = time.monotonic()
        if not force and now - self._last_write < self.min_interval:
            return False
        payload = {
            "run_id": run_id,
            "mode": mode,
            "status": status,
            "timestamp": timestamp,
            "published_at": datetime.now(UTC).isoformat(),
            "pid": os.getpid(),
            "equity": equity,
            "day_pnl": day_pnl,
            "positions": positions,
            "signals": list(self.signals),
            "kill_switch": kill_switch,
            "kill_reason": kill_reason,
            "risk": risk,
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
        tmp.replace(self.path)
        self._last_write = now
        return True


def load_state(path: Path | str) -> dict[str, Any] | None:
    file = Path(path)
    if not file.exists():
        return None
    try:
        loaded = json.loads(file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return loaded if isinstance(loaded, dict) else None


def publish_run(
    book: OperatorBook,
    ctx: StrategyContext,
    *,
    run_id: str,
    mode: str,
    equity: float,
    risk: Any,
    kill_switch: Any,
    status: RunStatus = "running",
    force: bool = False,
) -> bool:
    book.drain(ctx)
    engaged = bool(kill_switch is not None and kill_switch.is_engaged())
    risk_status = risk.status() or {}
    return book.publish(
        run_id=run_id,
        mode=mode,
        timestamp=ctx.timestamp.isoformat(),
        equity=equity,
        day_pnl=float(risk_status.get("day_pnl", 0.0)),
        positions=positions_payload(ctx),
        kill_switch=engaged,
        kill_reason=kill_switch.reason() if engaged else "",
        risk=risk_status,
        status=status,
        force=force,
    )


def positions_payload(ctx: StrategyContext) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for key, pos in ctx.positions.items():
        rows.append(
            {
                "instrument": key,
                "quantity": pos.quantity,
                "avg_price": pos.avg_price,
                "last_price": pos.last_price or ctx.latest_prices.get(key, 0.0),
                "unrealised_pnl": pos.unrealised_pnl,
                "realised_pnl": pos.realised_pnl,
            }
        )
    return rows


__all__ = [
    "DEFAULT_MIN_INTERVAL",
    "OperatorBook",
    "RunStatus",
    "load_state",
    "positions_payload",
    "publish_run",
    "state_path",
]
