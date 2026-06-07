"""Public contracts for the roles.scout cell (UTF-8)."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

_VALID_MODES = ("locate", "boundary")


@dataclass(frozen=True)
class ScoutFinding:
    """A single read-only reconnaissance finding."""

    path: str
    snippet: str
    symbol: str | None = None
    line: int | None = None
    why_relevant: str = ""
    confidence: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "snippet": self.snippet,
            "symbol": self.symbol,
            "line": self.line,
            "why_relevant": self.why_relevant,
            "confidence": self.confidence,
        }


@dataclass(frozen=True)
class ScoutProbeTargetV1:
    """Read-only probe target descriptor (no access to caller control plane)."""

    query: str
    mode: str = "locate"
    hints: dict[str, Any] = field(default_factory=dict)
    max_findings: int = 12
    token_budget: int = 1200
    caller_role: str = ""
    run_id: str = ""
    task_id: str = ""
    allow_escalation: bool = False

    def __post_init__(self) -> None:
        if not str(self.query or "").strip():
            raise ValueError("query must be a non-empty string")
        if self.mode not in _VALID_MODES:
            raise ValueError(f"mode must be one of {_VALID_MODES}, got {self.mode!r}")
        if self.max_findings < 1:
            raise ValueError("max_findings must be >= 1")
        if self.token_budget < 1:
            raise ValueError("token_budget must be >= 1")

    def cache_key(self) -> str:
        """Stable, order-independent hash of the semantic target."""
        basis = {
            "query": str(self.query).strip(),
            "mode": self.mode,
            "hints": _normalize(self.hints),
            "max_findings": self.max_findings,
            "token_budget": self.token_budget,
        }
        blob = json.dumps(basis, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True)
class ScoutReportV1:
    """Structured reconnaissance result returned to the caller."""

    findings: tuple[ScoutFinding, ...]
    summary: str
    coverage: dict[str, Any]
    confidence: float
    content_hash: str
    usage: dict[str, Any]
    cache_hit: bool = False
    escalated: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "findings": [f.to_dict() for f in self.findings],
            "summary": self.summary,
            "coverage": self.coverage,
            "confidence": self.confidence,
            "content_hash": self.content_hash,
            "usage": self.usage,
            "cache_hit": self.cache_hit,
            "escalated": self.escalated,
        }


def _normalize(value: Any) -> Any:
    """Recursively sort lists so hint ordering does not affect the cache key."""
    if isinstance(value, dict):
        return {k: _normalize(value[k]) for k in sorted(value)}
    if isinstance(value, list):
        return sorted(json.dumps(_normalize(v), sort_keys=True, ensure_ascii=False) for v in value)
    return value
