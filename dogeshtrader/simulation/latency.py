"""Latency simulators for the live-paper engine.

A latency model controls how long after a strategy emits an intent it is
*considered* placed at the broker. The simplest model just adds a constant
delay; richer models can sample from distributions.
"""

from __future__ import annotations

import random
from dataclasses import dataclass


@dataclass
class LatencyModel:
    """Default model: fixed delay in milliseconds with optional jitter."""

    placement_ms: float = 50.0
    jitter_ms: float = 25.0

    def sample_placement_ms(self) -> float:
        if self.jitter_ms <= 0:
            return self.placement_ms
        return max(0.0, random.gauss(self.placement_ms, self.jitter_ms))


@dataclass
class NoLatency(LatencyModel):
    placement_ms: float = 0.0
    jitter_ms: float = 0.0

    def sample_placement_ms(self) -> float:
        return 0.0


__all__ = ["LatencyModel", "NoLatency"]
