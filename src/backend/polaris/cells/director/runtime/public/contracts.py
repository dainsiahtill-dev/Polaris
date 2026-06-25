"""Public contracts for the `director.runtime` cell."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from polaris.cells.director.runtime.internal.repair_kernel.advisory_policy import (
    ALLOWED_REPAIR_ADVISORY_SUGGESTED_RULE_FIELDS,
    FORBIDDEN_REPAIR_ADVISORY_METADATA_FIELDS,
    FORBIDDEN_REPAIR_ADVISORY_SUGGESTED_RULE_FIELDS,
    copy_valid_repair_advisory_metadata,
    copy_valid_repair_advisory_suggested_rules,
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
    suggested_rules: tuple[Mapping[str, Any], ...] = field(default_factory=tuple)
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "advisor_source", _require_non_empty("advisor_source", self.advisor_source))
        object.__setattr__(self, "message", str(self.message or "").strip())
        object.__setattr__(
            self,
            "suggested_rules",
            tuple(copy_valid_repair_advisory_suggested_rules(self.suggested_rules)),
        )
        object.__setattr__(self, "metadata", copy_valid_repair_advisory_metadata(self.metadata))

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable non-authoritative advisory payload."""

        return {
            "advisor_source": self.advisor_source,
            "message": self.message,
            "confidence": float(self.confidence),
            "authoritative": False,
            "suggested_rules": [dict(item) for item in self.suggested_rules],
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
    round_number: int | None = None
    errors_before: int | None = None
    errors_after: int | None = None
    net_error_reduction: int | None = None
    revalidation_evidence: Mapping[str, Any] = field(default_factory=dict)
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
        object.__setattr__(self, "round_number", None if self.round_number is None else max(0, int(self.round_number)))
        object.__setattr__(
            self, "errors_before", None if self.errors_before is None else max(0, int(self.errors_before))
        )
        object.__setattr__(self, "errors_after", None if self.errors_after is None else max(0, int(self.errors_after)))
        object.__setattr__(
            self,
            "net_error_reduction",
            None if self.net_error_reduction is None else int(self.net_error_reduction),
        )
        object.__setattr__(self, "revalidation_evidence", _to_dict_copy(self.revalidation_evidence))
        object.__setattr__(self, "advisor_notes", tuple(self.advisor_notes or ()))
        object.__setattr__(self, "metadata", _to_dict_copy(self.metadata))

    def to_dict(self) -> dict[str, Any]:
        return {
            "receipt_id": self.receipt_id,
            "plan_id": self.plan_id,
            "source_tool": self.source_tool,
            "status": self.status,
            "authoritative": self.authoritative,
            "files_changed": list(self.files_changed),
            "before_hashes": dict(self.before_hashes),
            "after_hashes": dict(self.after_hashes),
            "round_number": self.round_number,
            "errors_before": self.errors_before,
            "errors_after": self.errors_after,
            "net_error_reduction": self.net_error_reduction,
            "revalidation_evidence": dict(self.revalidation_evidence),
            "advisor_notes": [note.to_dict() for note in self.advisor_notes],
            "metadata": dict(self.metadata),
        }


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
class QueryDirectorRepairCoverageV1:
    """Query shape for read-only deterministic repair diagnostic coverage."""

    artifact_quality_errors: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "artifact_quality_errors", _to_tuple_str(list(self.artifact_quality_errors)))


@dataclass(frozen=True)
class DirectorRepairDiagnosticCoverageV1:
    """Public coverage projection for one repair diagnostic."""

    diagnostic: Mapping[str, Any]
    known_rule_matched: bool
    executable_runtime_plan_matched: bool = False
    metadata_only_match: bool = False
    matched_rule_ids: tuple[str, ...] = ()
    matched_source_tools: tuple[str, ...] = ()
    runtime_plan_rule_ids: tuple[str, ...] = ()
    archetypes: tuple[str, ...] = ()
    phases: tuple[str, ...] = ()
    languages: tuple[str, ...] = ()
    diagnostic_archetype: str = "unknown"
    diagnostic_phase: str = "unknown"
    diagnostic_language: str = "unknown"
    suggested_rule_family: str = "unknown"

    def __post_init__(self) -> None:
        object.__setattr__(self, "diagnostic", _to_dict_copy(self.diagnostic))
        object.__setattr__(self, "known_rule_matched", bool(self.known_rule_matched))
        object.__setattr__(self, "executable_runtime_plan_matched", bool(self.executable_runtime_plan_matched))
        object.__setattr__(self, "metadata_only_match", bool(self.metadata_only_match))
        object.__setattr__(self, "matched_rule_ids", _to_tuple_str(list(self.matched_rule_ids)))
        object.__setattr__(self, "matched_source_tools", _to_tuple_str(list(self.matched_source_tools)))
        object.__setattr__(self, "runtime_plan_rule_ids", _to_tuple_str(list(self.runtime_plan_rule_ids)))
        object.__setattr__(self, "archetypes", _to_tuple_str(list(self.archetypes)))
        object.__setattr__(self, "phases", _to_tuple_str(list(self.phases)))
        object.__setattr__(self, "languages", _to_tuple_str(list(self.languages)))
        object.__setattr__(self, "diagnostic_archetype", str(self.diagnostic_archetype or "unknown").strip())
        object.__setattr__(self, "diagnostic_phase", str(self.diagnostic_phase or "unknown").strip())
        object.__setattr__(self, "diagnostic_language", str(self.diagnostic_language or "unknown").strip())
        object.__setattr__(self, "suggested_rule_family", str(self.suggested_rule_family or "unknown").strip())

    def to_dict(self) -> dict[str, Any]:
        return {
            "diagnostic": dict(self.diagnostic),
            "known_rule_matched": self.known_rule_matched,
            "executable_runtime_plan_matched": self.executable_runtime_plan_matched,
            "metadata_only_match": self.metadata_only_match,
            "matched_rule_ids": list(self.matched_rule_ids),
            "matched_source_tools": list(self.matched_source_tools),
            "runtime_plan_rule_ids": list(self.runtime_plan_rule_ids),
            "archetypes": list(self.archetypes),
            "phases": list(self.phases),
            "languages": list(self.languages),
            "diagnostic_archetype": self.diagnostic_archetype,
            "diagnostic_phase": self.diagnostic_phase,
            "diagnostic_language": self.diagnostic_language,
            "suggested_rule_family": self.suggested_rule_family,
        }


@dataclass(frozen=True)
class DirectorRepairCoverageReportV1:
    """Public read-only coverage report for repair diagnostics."""

    schema_version: str
    source: str
    access: str
    total_diagnostics: int
    covered_diagnostic_count: int
    uncovered_diagnostic_count: int
    executable_runtime_plan_diagnostic_count: int = 0
    metadata_only_diagnostic_count: int = 0
    items: tuple[DirectorRepairDiagnosticCoverageV1, ...] = ()
    owner_cell: str = "director.runtime"
    execution_boundary: str = "read_only_coverage_no_writes"
    agi_execution_authority: bool = False
    director_tool_execution_required: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "schema_version", _require_non_empty("schema_version", self.schema_version))
        object.__setattr__(self, "source", _require_non_empty("source", self.source))
        object.__setattr__(self, "access", _require_non_empty("access", self.access))
        object.__setattr__(self, "total_diagnostics", max(0, int(self.total_diagnostics)))
        object.__setattr__(self, "covered_diagnostic_count", max(0, int(self.covered_diagnostic_count)))
        object.__setattr__(self, "uncovered_diagnostic_count", max(0, int(self.uncovered_diagnostic_count)))
        object.__setattr__(
            self,
            "executable_runtime_plan_diagnostic_count",
            max(0, int(self.executable_runtime_plan_diagnostic_count)),
        )
        object.__setattr__(
            self,
            "metadata_only_diagnostic_count",
            max(0, int(self.metadata_only_diagnostic_count)),
        )
        object.__setattr__(self, "items", tuple(self.items or ()))
        object.__setattr__(self, "owner_cell", _require_non_empty("owner_cell", self.owner_cell))
        object.__setattr__(
            self,
            "execution_boundary",
            _require_non_empty("execution_boundary", self.execution_boundary),
        )
        object.__setattr__(self, "agi_execution_authority", False)
        object.__setattr__(self, "director_tool_execution_required", False)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "source": self.source,
            "access": self.access,
            "owner_cell": self.owner_cell,
            "execution_boundary": self.execution_boundary,
            "agi_execution_authority": False,
            "director_tool_execution_required": False,
            "total_diagnostics": self.total_diagnostics,
            "covered_diagnostic_count": self.covered_diagnostic_count,
            "uncovered_diagnostic_count": self.uncovered_diagnostic_count,
            "executable_runtime_plan_diagnostic_count": self.executable_runtime_plan_diagnostic_count,
            "metadata_only_diagnostic_count": self.metadata_only_diagnostic_count,
            "items": [item.to_dict() for item in self.items],
            "uncovered_diagnostics": [dict(item.diagnostic) for item in self.items if not item.known_rule_matched],
        }


@dataclass(frozen=True)
class AttachDirectorRepairRevalidationEvidenceV1:
    """Command shape for projecting post-check evidence onto repair receipts."""

    summary: Mapping[str, Any]
    residual_artifact_quality_errors: tuple[str, ...] = ()
    command: tuple[str, ...] = ("materialization_quality_revalidation",)
    exit_code: int | None = None
    round_number: int | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "summary", _to_dict_copy(self.summary))
        object.__setattr__(
            self,
            "residual_artifact_quality_errors",
            _to_tuple_str(list(self.residual_artifact_quality_errors)),
        )
        object.__setattr__(self, "command", _to_tuple_str(list(self.command)))
        object.__setattr__(self, "exit_code", None if self.exit_code is None else int(self.exit_code))
        object.__setattr__(self, "round_number", None if self.round_number is None else max(0, int(self.round_number)))
        object.__setattr__(self, "metadata", _to_dict_copy(self.metadata))


@dataclass(frozen=True)
class DirectorRepairRevalidationProjectionResultV1:
    """Result for repair receipt revalidation projection."""

    schema_version: str
    source: str
    access: str
    summary: Mapping[str, Any]
    owner_cell: str = "director.runtime"
    execution_boundary: str = "receipt_revalidation_projection_no_writes_no_registration"
    writes_allowed: bool = False
    registration_allowed: bool = False
    agi_execution_authority: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "schema_version", _require_non_empty("schema_version", self.schema_version))
        object.__setattr__(self, "source", _require_non_empty("source", self.source))
        object.__setattr__(self, "access", _require_non_empty("access", self.access))
        object.__setattr__(self, "summary", _to_dict_copy(self.summary))
        object.__setattr__(self, "owner_cell", _require_non_empty("owner_cell", self.owner_cell))
        object.__setattr__(
            self,
            "execution_boundary",
            _require_non_empty("execution_boundary", self.execution_boundary),
        )
        object.__setattr__(self, "writes_allowed", False)
        object.__setattr__(self, "registration_allowed", False)
        object.__setattr__(self, "agi_execution_authority", False)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "source": self.source,
            "access": self.access,
            "owner_cell": self.owner_cell,
            "execution_boundary": self.execution_boundary,
            "writes_allowed": False,
            "registration_allowed": False,
            "agi_execution_authority": False,
            "summary": dict(self.summary),
        }


@dataclass(frozen=True)
class ProjectDirectorRepairKernelSummaryV1:
    """Command shape for projecting legacy repair tool results into kernel receipts."""

    stage: str
    tool_results: tuple[Mapping[str, Any], ...] = ()
    artifact_quality_errors: tuple[str, ...] = ()
    mode: str = "commit"
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "stage", _require_non_empty("stage", self.stage))
        object.__setattr__(self, "tool_results", tuple(_to_dict_copy(item) for item in self.tool_results))
        object.__setattr__(
            self,
            "artifact_quality_errors",
            _to_tuple_str(list(self.artifact_quality_errors)),
        )
        object.__setattr__(self, "mode", _require_non_empty("mode", self.mode))
        object.__setattr__(self, "metadata", _to_dict_copy(self.metadata))

    def to_dict(self) -> dict[str, Any]:
        return {
            "stage": self.stage,
            "tool_results": [dict(item) for item in self.tool_results],
            "artifact_quality_errors": list(self.artifact_quality_errors),
            "mode": self.mode,
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class DirectorRepairKernelSummaryProjectionResultV1:
    """Read-only result for legacy repair summary projection."""

    schema_version: str
    source: str
    access: str
    summary: Mapping[str, Any]
    owner_cell: str = "director.runtime"
    execution_boundary: str = "repair_kernel_summary_projection_no_writes_no_registration"
    writes_allowed: bool = False
    registration_allowed: bool = False
    agi_execution_authority: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "schema_version", _require_non_empty("schema_version", self.schema_version))
        object.__setattr__(self, "source", _require_non_empty("source", self.source))
        object.__setattr__(self, "access", _require_non_empty("access", self.access))
        object.__setattr__(self, "summary", _to_dict_copy(self.summary))
        object.__setattr__(self, "owner_cell", _require_non_empty("owner_cell", self.owner_cell))
        object.__setattr__(
            self,
            "execution_boundary",
            _require_non_empty("execution_boundary", self.execution_boundary),
        )
        object.__setattr__(self, "writes_allowed", False)
        object.__setattr__(self, "registration_allowed", False)
        object.__setattr__(self, "agi_execution_authority", False)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "source": self.source,
            "access": self.access,
            "owner_cell": self.owner_cell,
            "execution_boundary": self.execution_boundary,
            "writes_allowed": False,
            "registration_allowed": False,
            "agi_execution_authority": False,
            "summary": dict(self.summary),
        }


@dataclass(frozen=True)
class QueryDirectorRepairLanguageSlotsV1:
    """Query shape for reserved deterministic repair language slots."""

    include_items: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(self, "include_items", bool(self.include_items))


@dataclass(frozen=True)
class DirectorRepairLanguageSlotV1:
    """Public projection of one future language repair extension slot."""

    language: str
    aliases: tuple[str, ...] = ()
    file_extensions: tuple[str, ...] = ()
    diagnostic_sources: tuple[str, ...] = ()
    preferred_archetypes: tuple[str, ...] = ()
    notes: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "language", _require_non_empty("language", self.language))
        object.__setattr__(self, "aliases", _to_tuple_str(list(self.aliases)))
        object.__setattr__(self, "file_extensions", _to_tuple_str(list(self.file_extensions)))
        object.__setattr__(self, "diagnostic_sources", _to_tuple_str(list(self.diagnostic_sources)))
        object.__setattr__(self, "preferred_archetypes", _to_tuple_str(list(self.preferred_archetypes)))
        object.__setattr__(self, "notes", str(self.notes or "").strip())

    def to_dict(self) -> dict[str, Any]:
        return {
            "language": self.language,
            "aliases": list(self.aliases),
            "file_extensions": list(self.file_extensions),
            "diagnostic_sources": list(self.diagnostic_sources),
            "preferred_archetypes": list(self.preferred_archetypes),
            "notes": self.notes,
        }


@dataclass(frozen=True)
class DirectorRepairLanguageSlotsResultV1:
    """Read-only catalog of reserved language repair extension slots."""

    schema_version: str
    source: str
    access: str
    items: tuple[DirectorRepairLanguageSlotV1, ...] = ()
    summary: Mapping[str, Any] = field(default_factory=dict)
    owner_cell: str = "director.runtime"
    execution_boundary: str = "read_only_language_slots_no_rule_registration"
    authoritative_rule_registration: bool = False
    agi_execution_authority: bool = False
    writes_allowed: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "schema_version", _require_non_empty("schema_version", self.schema_version))
        object.__setattr__(self, "source", _require_non_empty("source", self.source))
        object.__setattr__(self, "access", _require_non_empty("access", self.access))
        object.__setattr__(self, "items", tuple(self.items or ()))
        object.__setattr__(self, "summary", _to_dict_copy(self.summary))
        object.__setattr__(self, "owner_cell", _require_non_empty("owner_cell", self.owner_cell))
        object.__setattr__(
            self,
            "execution_boundary",
            _require_non_empty("execution_boundary", self.execution_boundary),
        )
        object.__setattr__(self, "authoritative_rule_registration", False)
        object.__setattr__(self, "agi_execution_authority", False)
        object.__setattr__(self, "writes_allowed", False)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "source": self.source,
            "access": self.access,
            "owner_cell": self.owner_cell,
            "execution_boundary": self.execution_boundary,
            "authoritative_rule_registration": False,
            "agi_execution_authority": False,
            "writes_allowed": False,
            "items": [item.to_dict() for item in self.items],
            "summary": dict(self.summary),
        }


@dataclass(frozen=True)
class QueryDirectorRepairPostExecutionScheduleV1:
    """Query shape for the runtime-owned post-execution repair schedule catalog."""

    include_items: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(self, "include_items", bool(self.include_items))


@dataclass(frozen=True)
class DirectorRepairPostExecutionStepV1:
    """Public projection of one post-execution repair scheduling step."""

    step_id: str
    language: str
    phase: str
    priority: int
    source_tool: str
    depends_on: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "step_id", _require_non_empty("step_id", self.step_id))
        object.__setattr__(self, "language", _require_non_empty("language", self.language))
        object.__setattr__(self, "phase", _require_non_empty("phase", self.phase))
        object.__setattr__(self, "priority", max(0, int(self.priority)))
        object.__setattr__(self, "source_tool", _require_non_empty("source_tool", self.source_tool))
        object.__setattr__(self, "depends_on", _to_tuple_str(list(self.depends_on)))

    def to_dict(self) -> dict[str, Any]:
        return {
            "step_id": self.step_id,
            "language": self.language,
            "phase": self.phase,
            "priority": self.priority,
            "source_tool": self.source_tool,
            "depends_on": list(self.depends_on),
        }


@dataclass(frozen=True)
class DirectorRepairPostExecutionScheduleResultV1:
    """Read-only runtime-owned post-execution repair schedule catalog."""

    schema_version: str
    source: str
    access: str
    items: tuple[DirectorRepairPostExecutionStepV1, ...] = ()
    summary: Mapping[str, Any] = field(default_factory=dict)
    owner_cell: str = "director.runtime"
    execution_boundary: str = "read_only_post_execution_schedule_no_runner_binding"
    runner_binding_owner: str = "roles.adapters"
    writes_allowed: bool = False
    registration_allowed: bool = False
    agi_execution_authority: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "schema_version", _require_non_empty("schema_version", self.schema_version))
        object.__setattr__(self, "source", _require_non_empty("source", self.source))
        object.__setattr__(self, "access", _require_non_empty("access", self.access))
        object.__setattr__(self, "items", tuple(self.items or ()))
        object.__setattr__(self, "summary", _to_dict_copy(self.summary))
        object.__setattr__(self, "owner_cell", _require_non_empty("owner_cell", self.owner_cell))
        object.__setattr__(
            self,
            "execution_boundary",
            _require_non_empty("execution_boundary", self.execution_boundary),
        )
        object.__setattr__(
            self, "runner_binding_owner", _require_non_empty("runner_binding_owner", self.runner_binding_owner)
        )
        object.__setattr__(self, "writes_allowed", False)
        object.__setattr__(self, "registration_allowed", False)
        object.__setattr__(self, "agi_execution_authority", False)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "source": self.source,
            "access": self.access,
            "owner_cell": self.owner_cell,
            "execution_boundary": self.execution_boundary,
            "runner_binding_owner": self.runner_binding_owner,
            "writes_allowed": False,
            "registration_allowed": False,
            "agi_execution_authority": False,
            "items": [item.to_dict() for item in self.items],
            "summary": dict(self.summary),
        }


@dataclass(frozen=True)
class QueryDirectorRepairAdvisoryPolicyV1:
    """Query shape for the non-authoritative AGI repair advisory policy."""

    include_field_lists: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(self, "include_field_lists", bool(self.include_field_lists))


@dataclass(frozen=True)
class DirectorRepairAdvisoryPolicyResultV1:
    """Read-only policy projection for future AGI repair advisory overlays."""

    schema_version: str
    source: str
    access: str
    owner_cell: str = "director.runtime"
    execution_boundary: str = "read_only_advisory_no_writes_no_registration"
    agi_execution_authority: bool = False
    writes_allowed: bool = False
    registration_allowed: bool = False
    authoritative_receipts_allowed: bool = False
    allowed_suggested_rule_fields: tuple[str, ...] = ()
    forbidden_metadata_fields: tuple[str, ...] = ()
    forbidden_suggested_rule_fields: tuple[str, ...] = ()
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
        object.__setattr__(self, "agi_execution_authority", False)
        object.__setattr__(self, "writes_allowed", False)
        object.__setattr__(self, "registration_allowed", False)
        object.__setattr__(self, "authoritative_receipts_allowed", False)
        object.__setattr__(
            self, "allowed_suggested_rule_fields", _to_tuple_str(list(self.allowed_suggested_rule_fields))
        )
        object.__setattr__(self, "forbidden_metadata_fields", _to_tuple_str(list(self.forbidden_metadata_fields)))
        object.__setattr__(
            self,
            "forbidden_suggested_rule_fields",
            _to_tuple_str(list(self.forbidden_suggested_rule_fields)),
        )
        object.__setattr__(self, "summary", _to_dict_copy(self.summary))

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "source": self.source,
            "access": self.access,
            "owner_cell": self.owner_cell,
            "execution_boundary": self.execution_boundary,
            "agi_execution_authority": False,
            "writes_allowed": False,
            "registration_allowed": False,
            "authoritative_receipts_allowed": False,
            "allowed_suggested_rule_fields": list(self.allowed_suggested_rule_fields),
            "forbidden_metadata_fields": list(self.forbidden_metadata_fields),
            "forbidden_suggested_rule_fields": list(self.forbidden_suggested_rule_fields),
            "summary": dict(self.summary),
        }


@dataclass(frozen=True)
class QueryDirectorRepairAdvisoryValidationV1:
    """Read-only query for validating a future AGI repair advisory payload."""

    advisor_source: str
    message: str
    confidence: float = 0.0
    suggested_rules: tuple[Mapping[str, Any], ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "advisor_source", _require_non_empty("advisor_source", self.advisor_source))
        object.__setattr__(self, "message", str(self.message or "").strip())
        object.__setattr__(self, "confidence", max(0.0, min(float(self.confidence), 1.0)))
        object.__setattr__(self, "suggested_rules", tuple(dict(item or {}) for item in self.suggested_rules))
        object.__setattr__(self, "metadata", _to_dict_copy(self.metadata))


@dataclass(frozen=True)
class DirectorRepairAdvisoryValidationResultV1:
    """Read-only validation result for non-authoritative AGI repair advisory payloads."""

    schema_version: str
    source: str
    access: str
    ok: bool
    normalized_advisory: Mapping[str, Any] | None = None
    errors: tuple[str, ...] = ()
    owner_cell: str = "director.runtime"
    execution_boundary: str = "read_only_advisory_validation_no_writes_no_registration"
    agi_execution_authority: bool = False
    writes_allowed: bool = False
    registration_allowed: bool = False
    authoritative_receipts_allowed: bool = False
    summary: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "schema_version", _require_non_empty("schema_version", self.schema_version))
        object.__setattr__(self, "source", _require_non_empty("source", self.source))
        object.__setattr__(self, "access", _require_non_empty("access", self.access))
        object.__setattr__(self, "ok", bool(self.ok))
        object.__setattr__(
            self,
            "normalized_advisory",
            dict(self.normalized_advisory) if self.normalized_advisory is not None else None,
        )
        object.__setattr__(self, "errors", _to_tuple_str(list(self.errors)))
        object.__setattr__(self, "owner_cell", _require_non_empty("owner_cell", self.owner_cell))
        object.__setattr__(
            self,
            "execution_boundary",
            _require_non_empty("execution_boundary", self.execution_boundary),
        )
        object.__setattr__(self, "agi_execution_authority", False)
        object.__setattr__(self, "writes_allowed", False)
        object.__setattr__(self, "registration_allowed", False)
        object.__setattr__(self, "authoritative_receipts_allowed", False)
        object.__setattr__(self, "summary", _to_dict_copy(self.summary))

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "source": self.source,
            "access": self.access,
            "ok": self.ok,
            "owner_cell": self.owner_cell,
            "execution_boundary": self.execution_boundary,
            "agi_execution_authority": False,
            "writes_allowed": False,
            "registration_allowed": False,
            "authoritative_receipts_allowed": False,
            "normalized_advisory": dict(self.normalized_advisory) if self.normalized_advisory is not None else None,
            "errors": list(self.errors),
            "summary": dict(self.summary),
        }


@dataclass(frozen=True)
class CompareDirectorRepairShadowRunV1:
    """Read-only command for legacy-vs-kernel deterministic repair shadow comparison."""

    legacy_tool_results: tuple[Mapping[str, Any], ...] = ()
    kernel_receipts: tuple[RepairReceiptV1, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "legacy_tool_results", tuple(dict(item or {}) for item in self.legacy_tool_results))
        object.__setattr__(self, "kernel_receipts", tuple(self.kernel_receipts or ()))


@dataclass(frozen=True)
class DirectorRepairShadowComparisonResultV1:
    """Public read-only result for deterministic repair dark-launch comparison."""

    schema_version: str
    source: str
    access: str
    matched: bool
    legacy_source_tools: tuple[str, ...] = ()
    kernel_source_tools: tuple[str, ...] = ()
    legacy_paths: tuple[str, ...] = ()
    kernel_paths: tuple[str, ...] = ()
    missing_paths_in_kernel: tuple[str, ...] = ()
    extra_paths_in_kernel: tuple[str, ...] = ()
    missing_source_tools_in_kernel: tuple[str, ...] = ()
    extra_source_tools_in_kernel: tuple[str, ...] = ()
    cutover_ready: bool = False
    cutover_blockers: tuple[str, ...] = ()
    owner_cell: str = "director.runtime"
    execution_boundary: str = "read_only_shadow_comparison_no_writes"
    agi_execution_authority: bool = False
    writes_allowed: bool = False
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "schema_version", _require_non_empty("schema_version", self.schema_version))
        object.__setattr__(self, "source", _require_non_empty("source", self.source))
        object.__setattr__(self, "access", _require_non_empty("access", self.access))
        object.__setattr__(self, "matched", bool(self.matched))
        object.__setattr__(self, "legacy_source_tools", _to_tuple_str(list(self.legacy_source_tools)))
        object.__setattr__(self, "kernel_source_tools", _to_tuple_str(list(self.kernel_source_tools)))
        object.__setattr__(self, "legacy_paths", _to_tuple_str(list(self.legacy_paths)))
        object.__setattr__(self, "kernel_paths", _to_tuple_str(list(self.kernel_paths)))
        object.__setattr__(self, "missing_paths_in_kernel", _to_tuple_str(list(self.missing_paths_in_kernel)))
        object.__setattr__(self, "extra_paths_in_kernel", _to_tuple_str(list(self.extra_paths_in_kernel)))
        object.__setattr__(
            self,
            "missing_source_tools_in_kernel",
            _to_tuple_str(list(self.missing_source_tools_in_kernel)),
        )
        object.__setattr__(self, "extra_source_tools_in_kernel", _to_tuple_str(list(self.extra_source_tools_in_kernel)))
        object.__setattr__(self, "cutover_ready", bool(self.cutover_ready))
        object.__setattr__(self, "cutover_blockers", _to_tuple_str(list(self.cutover_blockers)))
        object.__setattr__(self, "owner_cell", _require_non_empty("owner_cell", self.owner_cell))
        object.__setattr__(
            self,
            "execution_boundary",
            _require_non_empty("execution_boundary", self.execution_boundary),
        )
        object.__setattr__(self, "agi_execution_authority", False)
        object.__setattr__(self, "writes_allowed", False)
        object.__setattr__(self, "metadata", _to_dict_copy(self.metadata))

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "source": self.source,
            "access": self.access,
            "owner_cell": self.owner_cell,
            "execution_boundary": self.execution_boundary,
            "agi_execution_authority": False,
            "writes_allowed": False,
            "matched": self.matched,
            "legacy_source_tools": list(self.legacy_source_tools),
            "kernel_source_tools": list(self.kernel_source_tools),
            "legacy_paths": list(self.legacy_paths),
            "kernel_paths": list(self.kernel_paths),
            "missing_paths_in_kernel": list(self.missing_paths_in_kernel),
            "extra_paths_in_kernel": list(self.extra_paths_in_kernel),
            "missing_source_tools_in_kernel": list(self.missing_source_tools_in_kernel),
            "extra_source_tools_in_kernel": list(self.extra_source_tools_in_kernel),
            "cutover_ready": self.cutover_ready,
            "cutover_blockers": list(self.cutover_blockers),
            "metadata": dict(self.metadata),
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
    "AttachDirectorRepairRevalidationEvidenceV1",
    "CompareDirectorRepairShadowRunV1",
    "DirectorRepairAdvisoryPolicyResultV1",
    "DirectorRepairAdvisoryValidationResultV1",
    "DirectorRepairCompositionIssueV1",
    "DirectorRepairCompositionSummaryV1",
    "DirectorRepairCoverageReportV1",
    "DirectorRepairDiagnosticCoverageV1",
    "DirectorRepairKernelSummaryProjectionResultV1",
    "DirectorRepairLanguageSlotV1",
    "DirectorRepairLanguageSlotsResultV1",
    "DirectorRepairPatchSummaryV1",
    "DirectorRepairPlanSummaryV1",
    "DirectorRepairPlanningResultV1",
    "DirectorRepairPostExecutionScheduleResultV1",
    "DirectorRepairPostExecutionStepV1",
    "DirectorRepairResultV1",
    "DirectorRepairRevalidationProjectionResultV1",
    "DirectorRepairShadowComparisonResultV1",
    "DirectorRepairStrategyCatalogResultV1",
    "DirectorRuntimeError",
    "ProjectDirectorRepairKernelSummaryV1",
    "QueryDirectorRepairAdvisoryPolicyV1",
    "QueryDirectorRepairAdvisoryValidationV1",
    "QueryDirectorRepairCoverageV1",
    "QueryDirectorRepairLanguageSlotsV1",
    "QueryDirectorRepairPostExecutionScheduleV1",
    "QueryDirectorRepairStrategyCatalogV1",
    "RepairAdvisoryV1",
    "RepairDiagnosticV1",
    "RepairReceiptV1",
    "RunDirectorRepairCommandV1",
]
