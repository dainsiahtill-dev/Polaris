"""Holographic benchmark suite models.

Canonical case definitions for the 13-subsystem benchmark matrix.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class CaseReadiness(str, Enum):
    """Execution readiness of a benchmark case."""

    READY = "ready"
    PENDING = "pending"


@dataclass(frozen=True, kw_only=True)
class HolographicCase:
    """Single benchmark case definition."""

    case_id: str
    subsystem: str
    title: str
    target_path: str
    summary: str
    readiness: CaseReadiness
    warmup_rounds: int = 3
    min_samples: int = 100
    thresholds: dict[str, Any] = field(default_factory=dict)
    reproducibility: tuple[str, ...] = ("fixed_seed", "deterministic_mock", "vcr")
    blocker: str = ""

    @property
    def is_ready(self) -> bool:
        return self.readiness == CaseReadiness.READY

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "subsystem": self.subsystem,
            "title": self.title,
            "target_path": self.target_path,
            "summary": self.summary,
            "readiness": self.readiness.value,
            "warmup_rounds": self.warmup_rounds,
            "min_samples": self.min_samples,
            "thresholds": dict(self.thresholds),
            "reproducibility": list(self.reproducibility),
            "blocker": self.blocker,
        }
