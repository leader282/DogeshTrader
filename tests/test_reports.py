"""Report artefacts: equity plot and HTML."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

from dogeshtrader.reports.plots import plot_equity_curve


def test_equity_plot_writes_png(tmp_path: Path) -> None:
    start = datetime(2024, 1, 2, 9, 15)
    curve = [(start + timedelta(minutes=5 * i), 100_000.0 + i) for i in range(20)]
    out = plot_equity_curve(curve, output_path=tmp_path / "plots" / "equity.png")
    assert out is not None
    assert out.read_bytes().startswith(b"\x89PNG")


def test_empty_curve_produces_no_plot(tmp_path: Path) -> None:
    assert plot_equity_curve([], output_path=tmp_path / "x.png") is None
