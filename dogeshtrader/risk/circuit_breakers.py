"""Circuit breakers.

A circuit breaker trips when an aggregate threshold is breached (e.g. realised
loss exceeds limit, too many consecutive rejections, data feed stale). When
tripped, the engine should refuse new orders and may optionally flatten
existing positions.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Literal

BreakerName = Literal[
    "max_daily_loss",
    "consecutive_rejections",
    "data_feed_disconnect",
    "session_end",
]


@dataclass
class BreakerState:
    name: BreakerName
    tripped: bool = False
    reason: str = ""
    tripped_at: datetime | None = None


@dataclass
class CircuitBreaker:
    """Aggregate circuit-breaker state.

    The risk engine owns one instance; engines update it as events flow in.
    Methods are intentionally stateless w.r.t. the calendar (they take the
    current time explicitly) so they're easy to unit test.
    """

    max_daily_loss: float
    max_consecutive_rejections: int
    data_feed_grace_seconds: int

    realised_pnl_today: float = 0.0
    consecutive_rejections: int = 0
    last_tick_at: datetime | None = None
    states: dict[BreakerName, BreakerState] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for n in (
            "max_daily_loss",
            "consecutive_rejections",
            "data_feed_disconnect",
            "session_end",
        ):
            self.states.setdefault(n, BreakerState(name=n))  # type: ignore[arg-type]

    # ------------------------------------------------------------------
    # Event ingestion
    # ------------------------------------------------------------------

    def on_pnl_update(self, realised: float) -> None:
        self.realised_pnl_today = realised
        if realised < 0 and abs(realised) >= self.max_daily_loss > 0:
            self._trip(
                "max_daily_loss",
                f"realised P&L {realised:.2f} exceeded -{self.max_daily_loss}",
            )

    def on_rejection(self, reason: str) -> None:
        self.consecutive_rejections += 1
        if self.consecutive_rejections >= self.max_consecutive_rejections > 0:
            self._trip(
                "consecutive_rejections",
                f"{self.consecutive_rejections} consecutive rejections; last={reason}",
            )

    def on_acceptance(self) -> None:
        self.consecutive_rejections = 0

    def on_tick(self, ts: datetime) -> None:
        self.last_tick_at = ts
        # Healing - if data was previously stale and we got a tick, untrip.
        state = self.states["data_feed_disconnect"]
        if state.tripped:
            state.tripped = False
            state.reason = ""
            state.tripped_at = None

    def check_data_feed(self, now: datetime) -> None:
        if self.last_tick_at is None or self.data_feed_grace_seconds <= 0:
            return
        if now - self.last_tick_at > timedelta(seconds=self.data_feed_grace_seconds):
            self._trip(
                "data_feed_disconnect",
                f"no ticks for {(now - self.last_tick_at).total_seconds():.0f}s",
            )

    def force_session_end(self) -> None:
        self._trip("session_end", "session ended; no new orders")

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    @property
    def is_tripped(self) -> bool:
        return any(s.tripped for s in self.states.values())

    def tripped_reasons(self) -> list[str]:
        return [f"{s.name}: {s.reason}" for s in self.states.values() if s.tripped]

    def _trip(self, name: BreakerName, reason: str) -> None:
        s = self.states[name]
        if not s.tripped:
            s.tripped = True
            s.reason = reason
            s.tripped_at = datetime.now()


__all__ = ["BreakerName", "BreakerState", "CircuitBreaker"]
