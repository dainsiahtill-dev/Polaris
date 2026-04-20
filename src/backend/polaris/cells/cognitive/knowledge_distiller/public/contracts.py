"""Public contracts for cognitive.knowledge_distiller."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


def _require_non_empty(name: str, value: str) -> str:
    normalized = str(value).strip()
    if not normalized:
        raise ValueError(f"{name} must be a non-empty string")
    return normalized


def _to_dict_copy(payload: dict[str, Any] | None) -> dict[str, Any]:
    return dict(payload or {})


@dataclass(frozen=True)
class DistillSessionCommandV1:
    """Command to distill patterns from a completed session."""

    workspace: str
    session_id: str
    run_id: str | None = None
    structured_findings: dict[str, Any] = field(default_factory=dict)
    task_progress: str = "done"
    outcome: str = "completed"  # completed | failed | stagnation
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "session_id", _require_non_empty("session_id", self.session_id))
        object.__setattr__(self, "outcome", str(self.outcome))
        object.__setattr__(self, "metadata", _to_dict_copy(self.metadata))


@dataclass(frozen=True)
class RetrieveKnowledgeQueryV1:
    """Query to retrieve relevant distilled knowledge."""

    workspace: str
    query: str
    top_k: int = 5
    role_filter: str | None = None
    knowledge_type: str | None = None  # error_pattern | success_pattern | stagnation_pattern
    min_confidence: float = 0.5


@dataclass(frozen=True)
class DistilledKnowledgeUnitV1:
    """A distilled knowledge unit from cross-session analysis."""

    knowledge_id: str
    knowledge_type: str  # error_pattern | success_pattern | stagnation_pattern | generic_pattern
    pattern_summary: str
    confidence: float
    occurrence_count: int
    related_findings: list[str]  # session_ids
    extracted_insight: str
    prevention_hint: str | None = None
    created_at: datetime = field(default_factory=datetime.now)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class KnowledgeRetrievalResultV1:
    """Result of knowledge retrieval query."""

    knowledge_units: list[DistilledKnowledgeUnitV1]
    query: str
    total_available: int


@dataclass(frozen=True)
class SessionDistillationResultV1:
    """Result of session distillation."""

    session_id: str
    knowledge_units_created: int
    patterns_extracted: list[str]
    knowledge_ids: list[str]


class KnowledgeDistillerError(RuntimeError):
    """Exception raised by KnowledgeDistiller operations."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "knowledge_distiller_error",
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(_require_non_empty("message", message))
        self.code = _require_non_empty("code", code)
        self.details = _to_dict_copy(details)
