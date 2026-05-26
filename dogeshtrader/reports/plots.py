"""Matplotlib-backed plot helpers.

Plot generation is optional - matplotlib import is deferred so the framework
remains importable on environments without a working display.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path


def plot_equity_curve(
    equity_curve: list[tuple[datetime, float]],
    *,
    output_path: Path | str,
    title: str = "Equity curve",
) -> Path | None:
    """Render the equity curve as PNG. Returns the output path or None on failure."""
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        return None

    if not equity_curve:
        return None
    xs = [ts for ts, _ in equity_curve]
    ys = [v for _, v in equity_curve]
    fig, ax = plt.subplots(figsize=(10, 4))
    ax.plot(xs, ys, color="#2563eb", linewidth=1.5)
    ax.set_title(title)
    ax.set_ylabel("Equity (INR)")
    ax.grid(True, linestyle="--", alpha=0.4)
    fig.autofmt_xdate()
    fig.tight_layout()
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_path, dpi=120)
    plt.close(fig)
    return output_path


__all__ = ["plot_equity_curve"]
