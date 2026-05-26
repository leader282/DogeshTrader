"""Backtest engine."""

from __future__ import annotations

from dogeshtrader.backtest.costs import (
    BpsCostModel,
    CostModel,
    IndiaEquitySimpleCostModel,
)
from dogeshtrader.backtest.engine import BacktestEngine, BacktestResult
from dogeshtrader.backtest.fills import CandleFillModel, FillModel
from dogeshtrader.backtest.metrics import compute_metrics
from dogeshtrader.backtest.report import write_reports
from dogeshtrader.backtest.slippage import FixedBpsSlippage, FixedTickSlippage, SlippageModel

__all__ = [
    "BacktestEngine",
    "BacktestResult",
    "BpsCostModel",
    "CandleFillModel",
    "CostModel",
    "FillModel",
    "FixedBpsSlippage",
    "FixedTickSlippage",
    "IndiaEquitySimpleCostModel",
    "SlippageModel",
    "compute_metrics",
    "write_reports",
]
