"""Backtest metrics.

Inputs:
* an equity curve (list of (timestamp, equity_value))
* a list of "trades" (round-trips computed by the engine)

Outputs:
* a dataclass-friendly dict suitable for JSON / HTML rendering.

Implementation philosophy: we don't pull in scipy/empyrical; pure pandas/numpy
keeps the install slim and the numbers transparent.
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime

import numpy as np


@dataclass
class Trade:
    """A round-trip trade (entry + exit) reported by the engine."""

    instrument_key: str
    side: str  # "LONG" | "SHORT"
    entry_time: datetime
    exit_time: datetime
    entry_price: float
    exit_price: float
    quantity: int
    pnl: float
    fees: float = 0.0
    bars_held: int = 0
    reason_entry: str = ""
    reason_exit: str = ""


@dataclass
class Metrics:
    total_return_pct: float = 0.0
    cagr_pct: float | None = None
    max_drawdown_pct: float = 0.0
    sharpe_like: float = 0.0
    win_rate_pct: float = 0.0
    profit_factor: float | None = None
    avg_win: float = 0.0
    avg_loss: float = 0.0
    expectancy: float = 0.0
    trade_count: int = 0
    winners: int = 0
    losers: int = 0
    exposure_time_pct: float = 0.0
    turnover: float = 0.0
    total_fees: float = 0.0
    start_equity: float = 0.0
    end_equity: float = 0.0
    extras: dict[str, float] = field(default_factory=dict)


def compute_metrics(
    *,
    equity_curve: list[tuple[datetime, float]],
    trades: Iterable[Trade],
    bars_in_market: int = 0,
    total_bars: int = 0,
) -> Metrics:
    trades_list = list(trades)
    if not equity_curve:
        return Metrics()

    equity = np.array([e for _, e in equity_curve], dtype=float)
    times = [t for t, _ in equity_curve]
    start = float(equity[0])
    end = float(equity[-1])
    total_return = (end / start - 1.0) if start > 0 else 0.0

    # CAGR (only if span >= 1 day).
    cagr: float | None = None
    if len(times) >= 2:
        delta_days = (times[-1] - times[0]).total_seconds() / 86400.0
        if delta_days > 1 and start > 0 and end > 0:
            years = delta_days / 365.25
            cagr = (end / start) ** (1.0 / years) - 1.0 if years > 0 else None

    # Returns and Sharpe-like.
    rets = np.diff(equity) / np.where(equity[:-1] == 0, 1, equity[:-1])
    sharpe = 0.0
    if rets.size > 1 and float(rets.std()) > 0:
        per_year = _annualisation_factor(times)
        sharpe = float(rets.mean() / rets.std() * math.sqrt(per_year))

    # Max drawdown.
    peaks = np.maximum.accumulate(equity)
    dd = (equity / np.where(peaks == 0, 1, peaks)) - 1.0
    max_dd = float(dd.min()) if dd.size else 0.0

    # Trade stats.
    pnl = np.array([t.pnl for t in trades_list], dtype=float)
    fees = float(sum(t.fees for t in trades_list))
    turnover = float(
        sum(abs(t.entry_price * t.quantity) + abs(t.exit_price * t.quantity) for t in trades_list)
    )

    win_mask = pnl > 0
    winners = int(win_mask.sum())
    losers = int((pnl < 0).sum())
    wins = pnl[win_mask]
    losses = pnl[pnl < 0]
    avg_win = float(wins.mean()) if wins.size else 0.0
    avg_loss = float(losses.mean()) if losses.size else 0.0
    win_rate = (winners / len(trades_list) * 100.0) if trades_list else 0.0
    gross_win = float(wins.sum()) if wins.size else 0.0
    gross_loss = float(-losses.sum()) if losses.size else 0.0
    pf = (gross_win / gross_loss) if gross_loss > 0 else None
    expectancy = float(pnl.mean()) if pnl.size else 0.0

    exposure_pct = (bars_in_market / total_bars * 100.0) if total_bars else 0.0

    return Metrics(
        total_return_pct=round(total_return * 100, 4),
        cagr_pct=round(cagr * 100, 4) if cagr is not None else None,
        max_drawdown_pct=round(max_dd * 100, 4),
        sharpe_like=round(sharpe, 4),
        win_rate_pct=round(win_rate, 2),
        profit_factor=round(pf, 4) if pf is not None else None,
        avg_win=round(avg_win, 2),
        avg_loss=round(avg_loss, 2),
        expectancy=round(expectancy, 2),
        trade_count=len(trades_list),
        winners=winners,
        losers=losers,
        exposure_time_pct=round(exposure_pct, 2),
        turnover=round(turnover, 2),
        total_fees=round(fees, 2),
        start_equity=round(start, 2),
        end_equity=round(end, 2),
    )


def _annualisation_factor(times: list[datetime]) -> int:
    """Estimate bars-per-year from an equity timeline."""
    if len(times) < 2:
        return 252
    span = (times[-1] - times[0]).total_seconds()
    if span <= 0:
        return 252
    bar_seconds = span / (len(times) - 1)
    if bar_seconds <= 0:
        return 252
    # Equity-trading minutes per year ~ 252 * 6.25h * 60min = 94,500
    return int(94_500 * 60 / bar_seconds) if bar_seconds < 86_400 else 252


__all__ = ["Metrics", "Trade", "compute_metrics"]
