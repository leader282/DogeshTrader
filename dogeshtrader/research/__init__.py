"""Research helpers that sit on top of the backtest engine."""

from dogeshtrader.research.walk_forward import (
    ResearchReport,
    evaluate_windows,
    promotion_decision,
    write_research_report,
)

__all__ = [
    "ResearchReport",
    "evaluate_windows",
    "promotion_decision",
    "write_research_report",
]
