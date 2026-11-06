"""Turn local run records into console ingest batches.

Sources, all already written by the engines:

* SQLite ``runs``, ``orders``, ``order_intents``, ``fills``
* ``<reports_dir>/<run_id>/summary.json``, ``trades.csv``, ``equity_curve.csv``
* Parquet candles for the run's universe and window, so the console can draw
  price with the trades on top

Everything is keyed so that re-sending a run overwrites it in place.
"""

from __future__ import annotations

import csv
import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from dogeshtrader.core.enums import Exchange
from dogeshtrader.core.models import Instrument
from dogeshtrader.data import CandleStore
from dogeshtrader.runtime import Workspace


def _local_iso(value: str | None, tz: ZoneInfo) -> str | None:
    """SQLite run times are naive wall-clock times in the app timezone."""
    if not value:
        return None
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=tz)
    return parsed.isoformat()


def _utc_iso(value: str | None) -> str | None:
    """``datetime('now')`` defaults in SQLite are naive UTC."""
    if not value:
        return None
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.isoformat()


def _float(value: str | None) -> float | None:
    return float(value) if value not in (None, "") else None


def _int(value: str | None) -> int | None:
    return int(float(value)) if value not in (None, "") else None


def _read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def _parse_config(raw: str | None) -> dict[str, Any] | None:
    if not raw:
        return None
    try:
        loaded = json.loads(raw)
    except json.JSONDecodeError:
        import yaml

        loaded = yaml.safe_load(raw)
    return loaded if isinstance(loaded, dict) else None


def list_run_ids(ws: Workspace) -> list[str]:
    return [r["run_id"] for r in ws.db.fetchall("SELECT run_id FROM runs ORDER BY started_at")]


def run_batch(ws: Workspace, run_id: str, *, include_candles: bool = True) -> dict[str, Any]:
    tz = ZoneInfo(ws.app.timezone)
    row = ws.db.fetchone("SELECT * FROM runs WHERE run_id=?", (run_id,))
    if row is None:
        raise KeyError(run_id)
    run = dict(row)

    report_dir = Path(ws.app.reports_dir) / run_id
    summary_path = report_dir / "summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8")) if summary_path.exists() else {}
    config = _parse_config(run.get("config_yaml")) or summary.get("config")

    intents = {
        r["intent_id"]: dict(r)
        for r in ws.db.fetchall("SELECT * FROM order_intents WHERE run_id=?", (run_id,))
    }

    orders = []
    for o in ws.db.fetchall("SELECT * FROM orders WHERE run_id=?", (run_id,)):
        intent = intents.get(o["intent_id"] or "", {})
        orders.append(
            {
                "run_id": run_id,
                "order_id": o["order_id"],
                "intent_id": o["intent_id"],
                "broker": o["broker"],
                "mode": o["mode"],
                "instrument": o["instrument_key"],
                "side": o["side"],
                "quantity": o["quantity"],
                "filled_quantity": o["filled_quantity"] or 0,
                "order_type": o["order_type"],
                "product": o["product"],
                "price": o["price"],
                "trigger_price": o["trigger_price"],
                "average_price": o["average_price"],
                "status": o["status"],
                "status_message": o["status_message"],
                "tag": o["tag"],
                "reason": intent.get("reason"),
                "exchange_order_id": o["exchange_order_id"],
                # Market time for simulated runs; wall clock for live ones.
                "placed_at": o["exchange_timestamp"] or _utc_iso(o["placed_at"]),
                "updated_at": _utc_iso(o["updated_at"]),
            }
        )

    fills = [
        {
            "run_id": run_id,
            "trade_id": f["trade_id"],
            "order_id": f["order_id"],
            "instrument": f["instrument_key"],
            "side": f["side"],
            "quantity": f["quantity"],
            "price": f["price"],
            "fees": f["fees"] or 0.0,
            "ts": f["timestamp"],
        }
        for f in ws.db.fetchall("SELECT * FROM fills WHERE run_id=?", (run_id,))
    ]

    # The trade ledger records the closing order's tag; show the intent's reason instead.
    reason_by_tag = {o["tag"]: o["reason"] for o in orders if o["tag"] and o["reason"]}

    def _reason(value: str | None) -> str | None:
        return reason_by_tag.get(value or "", value or None)

    trades = [
        {
            "run_id": run_id,
            "seq": i,
            "instrument": t["instrument"],
            "side": t["side"],
            "entry_time": t["entry_time"],
            "exit_time": t.get("exit_time") or None,
            "entry_price": float(t["entry_price"]),
            "exit_price": _float(t.get("exit_price")),
            "quantity": int(float(t["quantity"])),
            "pnl": _float(t.get("pnl")),
            "fees": _float(t.get("fees")),
            "bars_held": _int(t.get("bars_held")),
            "reason_entry": _reason(t.get("reason_entry")),
            "reason_exit": _reason(t.get("reason_exit")),
        }
        for i, t in enumerate(_read_csv(report_dir / "trades.csv"))
    ]

    # Backtests sample equity once per instrument per bar, but the console keys
    # points on (run, ts). The last sample at a timestamp is the fully marked one.
    last_equity = {e["timestamp"]: e["equity"] for e in _read_csv(report_dir / "equity_curve.csv")}
    equity = [
        {"run_id": run_id, "ts": ts, "equity": float(value)} for ts, value in last_equity.items()
    ]

    # Evidence recorded with each entry intent doubles as the signal log.
    signals = []
    for order in orders:
        source = intents.get(order["intent_id"] or "")
        if not source or not order["placed_at"]:
            continue
        meta = json.loads(source.get("metadata_json") or "{}")
        signals.append(
            {
                "run_id": run_id,
                "ts": order["placed_at"],
                "instrument": order["instrument"],
                "side": order["side"],
                "reason": source.get("reason"),
                "evidence": {k: v for k, v in meta.items() if k != "reason"},
            }
        )

    batch: dict[str, Any] = {
        "runs": [
            {
                "run_id": run_id,
                "mode": run["mode"],
                "strategy": run["strategy"],
                "strategy_id": run["strategy_id"],
                "status": run["status"],
                "started_at": _local_iso(run["started_at"], tz),
                "ended_at": _local_iso(run.get("ended_at"), tz),
                "config": config,
                "metrics": summary.get("metrics"),
                "notes": run.get("notes"),
            }
        ],
        "orders": orders,
        "fills": fills,
        "trades": trades,
        "equity": equity,
        "signals": signals,
        "replace_signals_for": [run_id],
        "candles": [],
    }
    if include_candles and config:
        batch["candles"] = _candles_for(ws, config, fills)
    return batch


def _candles_for(
    ws: Workspace, config: dict[str, Any], fills: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    interval = str(config.get("interval") or "5minute")
    window = config.get("backtest") or {}
    start: date | None = None
    end: date | None = None
    if window.get("from") and window.get("to"):
        start, end = date.fromisoformat(window["from"]), date.fromisoformat(window["to"])
    elif fills:
        stamps = sorted(datetime.fromisoformat(f["ts"]).date() for f in fills)
        start, end = stamps[0] - timedelta(days=1), stamps[-1] + timedelta(days=1)
    if start is None or end is None:
        return []

    store = CandleStore(ws.app.data_dir)
    out: list[dict[str, Any]] = []
    for ref in config.get("instruments") or []:
        inst = Instrument(exchange=Exchange(ref["exchange"]), tradingsymbol=ref["tradingsymbol"])
        for c in store.read(inst, interval, from_dt=start, to_dt=end):
            out.append(
                {
                    "instrument": inst.key,
                    "interval": interval,
                    "ts": c.timestamp.isoformat(),
                    "open": c.open,
                    "high": c.high,
                    "low": c.low,
                    "close": c.close,
                    "volume": float(c.volume or 0),
                }
            )
    return out


__all__ = ["list_run_ids", "run_batch"]
