"""Live-paper simulation engine.

Used when the user wants to run a strategy against live ticks (or replayed
historical candles) without placing real orders. The engine pipes data through
the same strategy contract as backtest and live, the only difference is the
broker is :class:`PaperBroker` and the data source is configurable (live
WebSocket via Kite, or candle replay).
"""

from __future__ import annotations

from dogeshtrader.simulation.latency import LatencyModel
from dogeshtrader.simulation.live_paper_engine import LivePaperEngine

__all__ = ["LatencyModel", "LivePaperEngine"]
