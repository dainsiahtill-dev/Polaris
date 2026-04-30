"""Configuration and defaults for holographic benchmark runner."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class RunStatus(str, Enum):
    PASSED = "passed"
    FAILED = "failed"
    SKIPPED = "skipped"
    ERROR = "error"


@dataclass(frozen=True, kw_only=True)
class HolographicRunResult:
    case_id: str
    status: RunStatus
    metrics: dict[str, float | int | str] = field(default_factory=dict)
    failures: tuple[str, ...] = ()
    duration_ms: float = 0.0
    message: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "status": self.status.value,
            "metrics": dict(self.metrics),
            "failures": list(self.failures),
            "duration_ms": round(self.duration_ms, 3),
            "message": self.message,
        }


@dataclass(frozen=True, kw_only=True)
class HolographicSuiteResult:
    run_id: str
    timestamp_utc: str
    total_cases: int
    passed: int
    failed: int
    skipped: int
    errored: int
    results: tuple[HolographicRunResult, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "timestamp_utc": self.timestamp_utc,
            "total_cases": self.total_cases,
            "passed": self.passed,
            "failed": self.failed,
            "skipped": self.skipped,
            "errored": self.errored,
            "results": [result.to_dict() for result in self.results],
        }
