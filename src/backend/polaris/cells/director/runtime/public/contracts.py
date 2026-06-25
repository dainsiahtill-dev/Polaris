"""Public contracts for the `director.runtime` cell."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from polaris.cells.director.runtime.internal.repair_kernel.advisory_policy import (
    copy_valid_repair_advisory_metadata,
)


def _require_non_empty(name: str, value: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError(f"{name} must be a non-empty string")
    return normalized


def _to_dict_copy(value: Mapping[str, Any] | None) -> dict[str, Any]:
    return dict(value or {})


def _to_tuple_str(value: tuple[str, ...] | list[str] | None) -> tuple[str, ...]:
    return tuple(str(item) for item in (value or ()) if str(item or "").strip())


@dataclass(frozen=True)
class RepairDiagnosticV1:
    """Structured repair diagnostic for Director Runtime."""

    source: str
    code: str
    message: str
    path: str | None = None
    severity: str = "error"
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "source", _require_non_empty("source", self.source))
        object.__setattr__(self, "code", _require_non_empty("code", self.code))
        object.__setattr__(self, "message", str(self.message or "").strip())
        object.__setattr__(self, "metadata", _to_dict_copy(self.metadata))


@dataclass(frozen=True)
class RepairAdvisoryV1:
    """Optional future AGI advisory overlay.

    Advisory data is explicitly non-authoritative: it cannot carry repair
    plans, write decisions, policy overrides, or success verdicts.
    """

    advisor_source: str
    message: str
    confidence: float = 0.0
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "advisor_source", _require_non_empty("advisor_source", self.advisor_source))
        object.__setattr__(self, "message", str(self.message or "").strip())
        object.__setattr__(self, "metadata", copy_valid_repair_advisory_metadata(self.metadata))

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable non-authoritative advisory payload."""

        return {
            "advisor_source": self.advisor_source,
            "message": self.message,
            "confidence": float(self.confidence),
            "authoritative": False,
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class RepairReceiptV1:
    """Repair receipt projection for deterministic Director repairs."""

    receipt_id: str
    plan_id: str
    source_tool: str
    status: str
    authoritative: bool
    files_changed: tuple[str, ...] = ()
    before_hashes: Mapping[str, str] = field(default_factory=dict)
    after_hashes: Mapping[str, str] = field(default_factory=dict)
    advisor_notes: tuple[RepairAdvisoryV1, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "receipt_id", _require_non_empty("receipt_id", self.receipt_id))
        object.__setattr__(self, "plan_id", _require_non_empty("plan_id", self.plan_id))
        object.__setattr__(self, "source_tool", _require_non_empty("source_tool", self.source_tool))
        object.__setattr__(self, "status", _require_non_empty("status", self.status))
        object.__setattr__(self, "files_changed", tuple(str(item) for item in self.files_changed))
        object.__setattr__(self, "before_hashes", dict(self.before_hashes or {}))
        object.__setattr__(self, "after_hashes", dict(self.after_hashes or {}))
        object.__setattr__(self, "advisor_notes", tuple(self.advisor_notes or ()))
        object.__setattr__(self, "metadata", _to_dict_copy(self.metadata))


@dataclass(frozen=True)
class RunDirectorRepairCommandV1:
    """Command shape for future Director Runtime repair execution."""

    task_id: str
    workspace: str
    diagnostics: tuple[RepairDiagnosticV1, ...]
    mode: str = "commit"
    deterministic_only: bool = True
    allowed_paths: tuple[str, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "task_id", _require_non_empty("task_id", self.task_id))
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "diagnostics", tuple(self.diagnostics or ()))
        object.__setattr__(self, "allowed_paths", tuple(str(item) for item in self.allowed_paths))
        object.__setattr__(self, "metadata", _to_dict_copy(self.metadata))


@dataclass(frozen=True)
class QueryDirectorRepairStrategyCatalogV1:
    """Query shape for the Director deterministic repair strategy catalog."""

    include_items: bool = True
    max_items: int = 500

    def __post_init__(self) -> None:
        object.__setattr__(self, "include_items", bool(self.include_items))
        object.__setattr__(self, "max_items", max(0, min(int(self.max_items), 1000)))


@dataclass(frozen=True)
class DirectorRepairStrategyCatalogResultV1:
    """Read-only projection of hard-coded Director repair strategies."""

    schema_version: str
    source: str
    access: str
    agi_execution_authority: bool
    director_tool_execution_required: bool
    owner_cell: str = "director.runtime"
    execution_boundary: str = "director_authorized_tools_only"
    chain: str = "PM → Chief Engineer → Director"
    unknown_source_tool_policy: str = "fail_closed_high_risk"
    items: tuple[Mapping[str, Any], ...] = ()
    summary: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "schema_version", _require_non_empty("schema_version", self.schema_version))
        object.__setattr__(self, "source", _require_non_empty("source", self.source))
        object.__setattr__(self, "access", _require_non_empty("access", self.access))
        object.__setattr__(self, "owner_cell", _require_non_empty("owner_cell", self.owner_cell))
        object.__setattr__(
            self,
            "execution_boundary",
            _require_non_empty("execution_boundary", self.execution_boundary),
        )
        object.__setattr__(self, "chain", _require_non_empty("chain", self.chain))
        object.__setattr__(
            self,
            "unknown_source_tool_policy",
            _require_non_empty("unknown_source_tool_policy", self.unknown_source_tool_policy),
        )
        object.__setattr__(self, "items", tuple(dict(item or {}) for item in self.items))
        object.__setattr__(self, "summary", _to_dict_copy(self.summary))

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable catalog payload."""

        return {
            "schema_version": self.schema_version,
            "source": self.source,
            "access": self.access,
            "agi_execution_authority": self.agi_execution_authority,
            "director_tool_execution_required": self.director_tool_execution_required,
            "owner_cell": self.owner_cell,
            "execution_boundary": self.execution_boundary,
            "chain": self.chain,
            "unknown_source_tool_policy": self.unknown_source_tool_policy,
            "items": [dict(item) for item in self.items],
            "summary": dict(self.summary),
        }


@dataclass(frozen=True)
class DirectorRepairResultV1:
    """Result shape for Director Runtime repair execution."""

    ok: bool
    receipts: tuple[RepairReceiptV1, ...] = ()
    residual_diagnostics: tuple[RepairDiagnosticV1, ...] = ()
    error_code: str | None = None
    error_message: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "receipts", tuple(self.receipts or ()))
        object.__setattr__(self, "residual_diagnostics", tuple(self.residual_diagnostics or ()))
        object.__setattr__(self, "metadata", _to_dict_copy(self.metadata))
        if not self.ok and not (self.error_code or self.error_message):
            raise ValueError("failed DirectorRepairResultV1 must include error_code or error_message")


@dataclass(frozen=True)
class DirectorRepairPatchSummaryV1:
    """Public per-file patch projection for repair planning."""

    path: str
    operation_ids: tuple[str, ...]
    before_hash: str
    after_hash: str
    changed: bool
    content_after: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "path", _require_non_empty("path", self.path).replace("\\", "/"))
        object.__setattr__(self, "operation_ids", _to_tuple_str(list(self.operation_ids)))
        object.__setattr__(self, "before_hash", _require_non_empty("before_hash", self.before_hash))
        object.__setattr__(self, "after_hash", _require_non_empty("after_hash", self.after_hash))
        object.__setattr__(self, "changed", bool(self.changed))
        object.__setattr__(self, "metadata", _to_dict_copy(self.metadata))

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable patch summary."""

        return {
            "path": self.path,
            "operation_ids": list(self.operation_ids),
            "before_hash": self.before_hash,
            "after_hash": self.after_hash,
            "changed": self.changed,
            "content_after": self.content_after,
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class DirectorRepairCompositionIssueV1:
    """Public fail-closed patch composition issue."""

    code: str
    message: str
    path: str | None = None
    operation_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "code", _require_non_empty("code", self.code))
        object.__setattr__(self, "message", str(self.message or "").strip())
        object.__setattr__(self, "path", str(self.path).replace("\\", "/") if self.path is not None else None)
        object.__setattr__(self, "operation_ids", _to_tuple_str(list(self.operation_ids)))

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable composition issue."""

        return {
            "code": self.code,
            "message": self.message,
            "path": self.path,
            "operation_ids": list(self.operation_ids),
        }


@dataclass(frozen=True)
class DirectorRepairPlanSummaryV1:
    """Public repair plan summary without exposing internal kernel classes."""

    plan_id: str
    rule_id: str
    source_tool: str
    mode: str
    risk_level: str
    diagnostic_count: int
    operation_count: int
    advisor_note_count: int = 0
    agi_execution_authority: bool = False
    advisory_authoritative: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "plan_id", _require_non_empty("plan_id", self.plan_id))
        object.__setattr__(self, "rule_id", _require_non_empty("rule_id", self.rule_id))
        object.__setattr__(self, "source_tool", _require_non_empty("source_tool", self.source_tool))
        object.__setattr__(self, "mode", _require_non_empty("mode", self.mode))
        object.__setattr__(self, "risk_level", _require_non_empty("risk_level", self.risk_level))
        object.__setattr__(self, "diagnostic_count", max(0, int(self.diagnostic_count)))
        object.__setattr__(self, "operation_count", max(0, int(self.operation_count)))
        object.__setattr__(self, "advisor_note_count", max(0, int(self.advisor_note_count)))
        object.__setattr__(self, "agi_execution_authority", False)
        object.__setattr__(self, "advisory_authoritative", False)

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable plan summary."""

        return {
            "plan_id": self.plan_id,
            "rule_id": self.rule_id,
            "source_tool": self.source_tool,
            "mode": self.mode,
            "risk_level": self.risk_level,
            "diagnostic_count": self.diagnostic_count,
            "operation_count": self.operation_count,
            "advisor_note_count": self.advisor_note_count,
            "agi_execution_authority": False,
            "advisory_authoritative": False,
        }


@dataclass(frozen=True)
class DirectorRepairCompositionSummaryV1:
    """Public repair composition summary without exposing PatchComposer types."""

    ok: bool
    patches: tuple[DirectorRepairPatchSummaryV1, ...] = ()
    issues: tuple[DirectorRepairCompositionIssueV1, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "ok", bool(self.ok))
        object.__setattr__(self, "patches", tuple(self.patches or ()))
        object.__setattr__(self, "issues", tuple(self.issues or ()))

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable composition summary."""

        return {
            "ok": self.ok,
            "patch_count": len(self.patches),
            "issue_count": len(self.issues),
            "changed_paths": [patch.path for patch in self.patches if patch.changed],
            "patches": [patch.to_dict() for patch in self.patches],
            "issues": [issue.to_dict() for issue in self.issues],
        }


@dataclass(frozen=True)
class DirectorRepairPlanningResultV1:
    """Public deterministic repair planning result."""

    ok: bool
    planned: bool
    source_tool: str
    diagnostic_count: int
    plan_summary: DirectorRepairPlanSummaryV1 | None = None
    composition_summary: DirectorRepairCompositionSummaryV1 = field(
        default_factory=lambda: DirectorRepairCompositionSummaryV1(ok=False)
    )
    advisor_notes: tuple[RepairAdvisoryV1, ...] = ()
    schema_version: str = "director.repair_planning_result.v1"
    owner_cell: str = "director.runtime"
    execution_boundary: str = "director_authorized_tools_only"
    agi_execution_authority: bool = False
    advisory_authoritative: bool = False
    director_tool_execution_required: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(self, "ok", bool(self.ok))
        object.__setattr__(self, "planned", bool(self.planned))
        object.__setattr__(self, "source_tool", _require_non_empty("source_tool", self.source_tool))
        object.__setattr__(self, "diagnostic_count", max(0, int(self.diagnostic_count)))
        object.__setattr__(self, "advisor_notes", tuple(self.advisor_notes or ()))
        object.__setattr__(self, "schema_version", _require_non_empty("schema_version", self.schema_version))
        object.__setattr__(self, "owner_cell", _require_non_empty("owner_cell", self.owner_cell))
        object.__setattr__(
            self,
            "execution_boundary",
            _require_non_empty("execution_boundary", self.execution_boundary),
        )
        object.__setattr__(self, "agi_execution_authority", False)
        object.__setattr__(self, "advisory_authoritative", False)
        object.__setattr__(self, "director_tool_execution_required", True)

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable planning payload."""

        return {
            "schema_version": self.schema_version,
            "ok": self.ok,
            "planned": self.planned,
            "source_tool": self.source_tool,
            "diagnostic_count": self.diagnostic_count,
            "owner_cell": self.owner_cell,
            "execution_boundary": self.execution_boundary,
            "agi_execution_authority": False,
            "advisory_authoritative": False,
            "director_tool_execution_required": True,
            "plan_summary": self.plan_summary.to_dict() if self.plan_summary is not None else None,
            "composition_summary": self.composition_summary.to_dict(),
            "advisor_notes": [note.to_dict() for note in self.advisor_notes],
        }


class DirectorRuntimeError(RuntimeError):
    """Structured public error for director.runtime."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "director_runtime_error",
        details: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(_require_non_empty("message", message))
        self.code = _require_non_empty("code", code)
        self.details = _to_dict_copy(details)


__all__ = [
    "DirectorRepairCompositionIssueV1",
    "DirectorRepairCompositionSummaryV1",
    "DirectorRepairPatchSummaryV1",
    "DirectorRepairPlanSummaryV1",
    "DirectorRepairPlanningResultV1",
    "DirectorRepairResultV1",
    "DirectorRepairStrategyCatalogResultV1",
    "DirectorRuntimeError",
    "QueryDirectorRepairStrategyCatalogV1",
    "RepairAdvisoryV1",
    "RepairDiagnosticV1",
    "RepairReceiptV1",
    "RunDirectorRepairCommandV1",
]
