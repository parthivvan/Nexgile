"""Fault-injection controls for the simulated Payment Provider."""

from __future__ import annotations

import os
import random
from dataclasses import dataclass, field


@dataclass
class FaultConfig:
    """Runtime fault-injection settings (local simulated environment only)."""

    timeout_rate: float = 0.0
    timeout_delay_seconds: float = 2.5
    slow_rate: float = 0.0
    slow_ms: int = 0
    seed: int | None = None
    _rng: random.Random = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._rng = random.Random(self.seed)

    @classmethod
    def from_env(cls) -> FaultConfig:
        """Initialize fault configuration from RUNBOOK.md environment variables."""
        return cls(
            timeout_rate=float(os.getenv("FAULT_PROVIDER_TIMEOUT_RATE", "0.0")),
            timeout_delay_seconds=float(os.getenv("FAULT_PROVIDER_TIMEOUT_DELAY_S", "2.5")),
            slow_rate=float(os.getenv("FAULT_PROVIDER_SLOW_RATE", "0.0")),
            slow_ms=int(os.getenv("FAULT_PROVIDER_SLOW_MS", "0")),
        )

    def should_timeout(self) -> bool:
        """Return True if this request should trigger a post-commit timeout."""
        if self.timeout_rate <= 0.0:
            return False
        if self.timeout_rate >= 1.0:
            return True
        return self._rng.random() < self.timeout_rate

    def should_slow(self) -> bool:
        """Return True if this request should incur an artificial slow delay."""
        if self.slow_ms <= 0:
            return False
        if self.slow_rate <= 0.0:
            return False
        if self.slow_rate >= 1.0:
            return True
        return self._rng.random() < self.slow_rate
