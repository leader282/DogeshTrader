"""Reports package: HTML rendering + plots."""

from __future__ import annotations

from dogeshtrader.reports.html_report import render_summary_html
from dogeshtrader.reports.plots import plot_equity_curve

__all__ = ["plot_equity_curve", "render_summary_html"]
