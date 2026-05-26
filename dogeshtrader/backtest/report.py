"""Reporting outputs for a backtest run.

Writes:
* ``trades.csv``       - round-trip ledger
* ``orders.csv``       - per-order ledger
* ``fills.csv``        - per-fill ledger
* ``equity_curve.csv`` - timestamp/equity samples
* ``summary.json``     - metrics + config snapshot
* ``report.html``      - human-readable report (uses Jinja2 template)
"""

from __future__ import annotations

import csv
import json
from collections.abc import Iterable
from dataclasses import asdict, is_dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from dogeshtrader.backtest.metrics import Metrics, Trade
from dogeshtrader.config import StrategyConfig
from dogeshtrader.core.models import Fill, OrderUpdate
from dogeshtrader.logging_config import get_logger

_log = get_logger(__name__)


def write_reports(
    *,
    output_dir: Path | str,
    metrics: Metrics,
    trades: Iterable[Trade],
    equity_curve: list[tuple[datetime, float]],
    fills: Iterable[Fill],
    orders: Iterable[OrderUpdate],
    run_id: str,
    strategy_cfg: StrategyConfig,
) -> dict[str, Path]:
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    trades_path = out / "trades.csv"
    _write_trades_csv(trades_path, trades)

    orders_path = out / "orders.csv"
    _write_orders_csv(orders_path, orders)

    fills_path = out / "fills.csv"
    _write_fills_csv(fills_path, fills)

    eq_path = out / "equity_curve.csv"
    _write_equity_csv(eq_path, equity_curve)

    summary_path = out / "summary.json"
    summary_payload = {
        "run_id": run_id,
        "strategy": strategy_cfg.strategy,
        "strategy_id": strategy_cfg.strategy_id,
        "metrics": _to_dict(metrics),
        "config": strategy_cfg.model_dump(by_alias=True, mode="json"),
    }
    summary_path.write_text(json.dumps(summary_payload, indent=2, default=str))

    html_path = out / "report.html"
    _write_html_report(html_path, summary_payload)

    _log.info("report_written", out=str(out))
    return {
        "trades": trades_path,
        "orders": orders_path,
        "fills": fills_path,
        "equity_curve": eq_path,
        "summary": summary_path,
        "html": html_path,
    }


def _write_trades_csv(path: Path, trades: Iterable[Trade]) -> None:
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(
            [
                "instrument",
                "side",
                "entry_time",
                "exit_time",
                "entry_price",
                "exit_price",
                "quantity",
                "pnl",
                "fees",
                "bars_held",
                "reason_entry",
                "reason_exit",
            ]
        )
        for t in trades:
            w.writerow(
                [
                    t.instrument_key,
                    t.side,
                    t.entry_time.isoformat(),
                    t.exit_time.isoformat(),
                    f"{t.entry_price:.4f}",
                    f"{t.exit_price:.4f}",
                    t.quantity,
                    f"{t.pnl:.4f}",
                    f"{t.fees:.4f}",
                    t.bars_held,
                    t.reason_entry,
                    t.reason_exit,
                ]
            )


def _write_orders_csv(path: Path, orders: Iterable[OrderUpdate]) -> None:
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(
            [
                "order_id",
                "status",
                "filled_qty",
                "pending_qty",
                "avg_price",
                "price",
                "trigger_price",
                "exchange_order_id",
                "exchange_timestamp",
                "status_message",
            ]
        )
        for o in orders:
            w.writerow(
                [
                    o.order_id,
                    o.status.value,
                    o.filled_quantity,
                    o.pending_quantity,
                    f"{o.average_price:.4f}",
                    o.price if o.price is not None else "",
                    o.trigger_price if o.trigger_price is not None else "",
                    o.exchange_order_id or "",
                    o.exchange_timestamp.isoformat() if o.exchange_timestamp else "",
                    o.status_message or "",
                ]
            )


def _write_fills_csv(path: Path, fills: Iterable[Fill]) -> None:
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(
            [
                "trade_id",
                "order_id",
                "instrument",
                "side",
                "quantity",
                "price",
                "fees",
                "timestamp",
            ]
        )
        for f in fills:
            w.writerow(
                [
                    f.trade_id,
                    f.order_id,
                    f.instrument.key,
                    f.side.value,
                    f.quantity,
                    f"{f.price:.4f}",
                    f"{f.fees:.4f}",
                    f.timestamp.isoformat(),
                ]
            )


def _write_equity_csv(path: Path, equity: list[tuple[datetime, float]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["timestamp", "equity"])
        for ts, value in equity:
            w.writerow([ts.isoformat(), f"{value:.4f}"])


def _write_html_report(path: Path, payload: dict[str, Any]) -> None:
    """Renders the HTML report.

    Falls back to a built-in template string if the optional file template is
    missing, so the report works out of the box.
    """
    template_text = _load_template()
    rendered = template_text.replace("{{PAYLOAD_JSON}}", json.dumps(payload, indent=2, default=str))
    path.write_text(rendered)


_BUILTIN_TEMPLATE = """<!doctype html>
<html><head><meta charset="utf-8"><title>DogeshTrader backtest</title>
<style>
body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; margin: 24px; color: #1f2937; }
h1 { margin-bottom: 0; }
h2 { margin-top: 1.5em; border-bottom: 1px solid #e5e7eb; padding-bottom: 4px; }
.metric { display: inline-block; padding: 12px 16px; margin: 6px; background: #f9fafb;
          border: 1px solid #e5e7eb; border-radius: 8px; min-width: 140px; }
.metric .label { color: #6b7280; font-size: 12px; text-transform: uppercase; }
.metric .value { font-size: 18px; font-weight: 600; }
pre { background: #0f172a; color: #e2e8f0; padding: 16px; border-radius: 8px;
      overflow: auto; }
.warning { background: #fef3c7; border: 1px solid #f59e0b; padding: 12px;
           border-radius: 6px; margin-bottom: 16px; color: #92400e; }
</style></head>
<body>
<h1>DogeshTrader Backtest</h1>
<p class="warning">Backtests are infrastructure. They are not financial advice
or a promise of future returns. Validate via paper and dry-run before live.</p>
<div id="metrics"></div>
<h2>Raw summary</h2>
<pre id="raw"></pre>
<script>
const payload = {{PAYLOAD_JSON}};
const m = payload.metrics || {};
const grid = document.getElementById("metrics");
const order = [
  ["Run ID", payload.run_id],
  ["Strategy", payload.strategy + " / " + payload.strategy_id],
  ["Start equity", m.start_equity],
  ["End equity", m.end_equity],
  ["Total return %", m.total_return_pct],
  ["CAGR %", m.cagr_pct],
  ["Max DD %", m.max_drawdown_pct],
  ["Sharpe-like", m.sharpe_like],
  ["Win rate %", m.win_rate_pct],
  ["Trades", m.trade_count],
  ["Profit factor", m.profit_factor],
  ["Total fees", m.total_fees],
];
for (const [label, value] of order) {
  const div = document.createElement("div");
  div.className = "metric";
  div.innerHTML = `<div class="label">${label}</div>
                   <div class="value">${value ?? "n/a"}</div>`;
  grid.appendChild(div);
}
document.getElementById("raw").textContent = JSON.stringify(payload, null, 2);
</script>
</body></html>
"""


def _load_template() -> str:
    template_path = Path(__file__).parent.parent / "reports" / "templates" / "backtest.html"
    if template_path.exists():
        return template_path.read_text()
    return _BUILTIN_TEMPLATE


def _to_dict(obj: Any) -> Any:
    if is_dataclass(obj):
        return asdict(obj)
    if hasattr(obj, "model_dump"):
        return obj.model_dump(mode="json")
    return obj


__all__ = ["write_reports"]
