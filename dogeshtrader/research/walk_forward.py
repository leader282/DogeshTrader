"""Split one chronological backtest into in-sample and out-of-sample windows.

The strategy sees the full history, so indicators such as relative volume keep
their warmup. Metrics are then computed on each side of the split. A window is
promoted only when the out-of-sample slice has a positive mean trade and a
higher ending equity than it started with. Costs and slippage are already
inside those numbers because the backtest fill model applied them.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

from dogeshtrader.backtest.metrics import Metrics, Trade, compute_metrics
from dogeshtrader.core.clock import session_date


@dataclass
class ResearchReport:
    split: str
    in_sample: Metrics
    out_of_sample: Metrics
    promoted: bool
    reasons: list[str] = field(default_factory=list)
    costs_note: str = ""


def evaluate_windows(
    *,
    equity_curve: list[tuple[datetime, float]],
    trades: list[Trade],
    split: date,
    costs_note: str = "",
) -> ResearchReport:
    in_equity = [(ts, eq) for ts, eq in equity_curve if session_date(ts) < split]
    out_equity = [(ts, eq) for ts, eq in equity_curve if session_date(ts) >= split]
    in_trades = [t for t in trades if session_date(t.entry_time) < split]
    out_trades = [t for t in trades if session_date(t.entry_time) >= split]
    in_metrics = _metrics(in_equity, in_trades)
    out_metrics = _metrics(out_equity, out_trades)
    promoted, reasons = promotion_decision(out_metrics)
    return ResearchReport(
        split=split.isoformat(),
        in_sample=in_metrics,
        out_of_sample=out_metrics,
        promoted=promoted,
        reasons=reasons,
        costs_note=costs_note,
    )


def promotion_decision(metrics: Metrics) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    if metrics.trade_count <= 0:
        reasons.append("out-of-sample produced no closed trades")
    if metrics.expectancy <= 0:
        reasons.append("out-of-sample expectancy is not positive after costs")
    if metrics.end_equity < metrics.start_equity:
        reasons.append("out-of-sample equity finished below where it started")
    return (not reasons), reasons


def write_research_report(path: Path | str, report: ResearchReport) -> Path:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "split": report.split,
        "promoted": report.promoted,
        "reasons": report.reasons,
        "costs_note": report.costs_note,
        "in_sample": _metrics_dict(report.in_sample),
        "out_of_sample": _metrics_dict(report.out_of_sample),
    }
    out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return out


def _metrics(equity: list[tuple[datetime, float]], trades: list[Trade]) -> Metrics:
    if not equity:
        return Metrics()
    return compute_metrics(
        equity_curve=equity,
        trades=trades,
        bars_in_market=0,
        total_bars=len(equity),
    )


def _metrics_dict(metrics: Metrics) -> dict[str, object]:
    return {
        "start_equity": metrics.start_equity,
        "end_equity": metrics.end_equity,
        "total_return_pct": metrics.total_return_pct,
        "max_drawdown_pct": metrics.max_drawdown_pct,
        "expectancy": metrics.expectancy,
        "profit_factor": metrics.profit_factor,
        "trade_count": metrics.trade_count,
        "total_fees": metrics.total_fees,
        "win_rate_pct": metrics.win_rate_pct,
    }


__all__ = [
    "ResearchReport",
    "evaluate_windows",
    "promotion_decision",
    "write_research_report",
]
