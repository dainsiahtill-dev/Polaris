"""Public contracts for `docs.court_workflow`."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from collections.abc import Mapping


def _require_non_empty(name: str, value: str) -> str:
    normalized = str(value).strip()
    if not normalized:
        raise ValueError(f"{name} must be a non-empty string")
    return normalized


def _to_dict_copy(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    return dict(payload or {})


@dataclass(frozen=True)
class GenerateCourtDocsCommandV1:
    workspace: str
    case_id: str
    directive: str
    context: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "case_id", _require_non_empty("case_id", self.case_id))
        object.__setattr__(self, "directive", _require_non_empty("directive", self.directive))
        object.__setattr__(self, "context", _to_dict_copy(self.context))


@dataclass(frozen=True)
class ApplyCourtDocsCommandV1:
    workspace: str
    case_id: str
    draft_path: str
    apply_mode: str = "merge"

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "case_id", _require_non_empty("case_id", self.case_id))
        object.__setattr__(self, "draft_path", _require_non_empty("draft_path", self.draft_path))
        object.__setattr__(self, "apply_mode", _require_non_empty("apply_mode", self.apply_mode))


@dataclass(frozen=True)
class PreviewCourtDocsQueryV1:
    workspace: str
    case_id: str
    include_diff: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "case_id", _require_non_empty("case_id", self.case_id))


@dataclass(frozen=True)
class QueryCourtProjectionV1:
    workspace: str
    case_id: str
    include_history: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "case_id", _require_non_empty("case_id", self.case_id))


@dataclass(frozen=True)
class CourtDocsGeneratedEventV1:
    event_id: str
    workspace: str
    case_id: str
    draft_path: str
    generated_at: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "event_id", _require_non_empty("event_id", self.event_id))
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "case_id", _require_non_empty("case_id", self.case_id))
        object.__setattr__(self, "draft_path", _require_non_empty("draft_path", self.draft_path))
        object.__setattr__(self, "generated_at", _require_non_empty("generated_at", self.generated_at))


@dataclass(frozen=True)
class CourtDocsResultV1:
    ok: bool
    workspace: str
    case_id: str
    status: str
    docs_path: str | None = None
    summary: str = ""
    changed_files: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "case_id", _require_non_empty("case_id", self.case_id))
        object.__setattr__(self, "status", _require_non_empty("status", self.status))
        object.__setattr__(self, "changed_files", tuple(str(v) for v in self.changed_files if str(v).strip()))


class CourtWorkflowError(RuntimeError):
    """Raised when `docs.court_workflow` contract processing fails."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "court_workflow_error",
        details: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(_require_non_empty("message", message))
        self.code = _require_non_empty("code", code)
        self.details = _to_dict_copy(details)


@runtime_checkable
class IDocsCourtWorkflow(Protocol):
    async def start_court(self, document_id: str, context: Mapping[str, Any]) -> Mapping[str, Any]:
        """Compatibility API kept for existing integrations."""


__all__ = [
    "ApplyCourtDocsCommandV1",
    "CourtDocsGeneratedEventV1",
    "CourtDocsResultV1",
    "CourtWorkflowError",
    "GenerateCourtDocsCommandV1",
    "IDocsCourtWorkflow",
    "PreviewCourtDocsQueryV1",
    "QueryCourtProjectionV1",
]
