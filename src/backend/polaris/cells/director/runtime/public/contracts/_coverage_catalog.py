"""Strategy catalog, coverage report, revalidation attach, and kernel summary projection contracts."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from polaris.cells.director.runtime.public.contracts._helpers import (
    _require_non_empty,
    _to_dict_copy,
    _to_tuple_mapping_from_any,
    _to_tuple_str,
)


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
    artifact_quality_issues: tuple[Mapping[str, Any], ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "artifact_quality_errors", _to_tuple_str(list(self.artifact_quality_errors)))
        object.__setattr__(self, "artifact_quality_issues", _to_tuple_mapping_from_any(self.artifact_quality_issues))


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
    language: str = "unknown"
    diagnostic_archetype: str = "unknown"
    diagnostic_phase: str = "unknown"
    diagnostic_language: str = "unknown"
    diagnostic_code: str = "unknown"
    archetype_suggestion: str = "unknown"
    phase_suggestion: str = "unknown"
    suggested_rule_family: str = "unknown"
    reserved_slot_available: bool = False
    slot_status: str = "reserved_slot_missing"
    reserved_language_slot_matched: bool = False
    reserved_language_slot: Mapping[str, Any] = field(default_factory=dict)
    reserved_repairer_module: str = ""
    reserved_slot_registration_policy: str = ""
    recommended_next_owner: str = ""
    recommended_route: str = "llm_repair"
    handoff_recommendation: str = ""
    llm_advisory_recommended: bool = False
    agi_advisory_recommended: bool = False
    authoritative_rule_registration_allowed: bool = False
    recommended_registration_path: str = ""
    coverage_status: str = "coverage_gap"
    runtime_blockers: tuple[Mapping[str, Any], ...] = ()
    runtime_blocker_reasons: tuple[str, ...] = ()

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
        language = str(self.language or "").strip()
        if not language or language == "unknown":
            language = self.diagnostic_language
        object.__setattr__(self, "language", language)
        object.__setattr__(
            self,
            "diagnostic_code",
            str(self.diagnostic_code or self.diagnostic.get("code") or "unknown").strip(),
        )
        object.__setattr__(
            self,
            "archetype_suggestion",
            str(self.archetype_suggestion or self.diagnostic_archetype or "unknown").strip(),
        )
        object.__setattr__(
            self,
            "phase_suggestion",
            str(self.phase_suggestion or self.diagnostic_phase or "unknown").strip(),
        )
        object.__setattr__(self, "suggested_rule_family", str(self.suggested_rule_family or "unknown").strip())
        object.__setattr__(self, "reserved_slot_available", bool(self.reserved_slot_available))
        object.__setattr__(self, "slot_status", str(self.slot_status or "reserved_slot_missing").strip())
        object.__setattr__(self, "reserved_language_slot_matched", bool(self.reserved_language_slot_matched))
        object.__setattr__(self, "reserved_language_slot", _to_dict_copy(self.reserved_language_slot))
        object.__setattr__(self, "reserved_repairer_module", str(self.reserved_repairer_module or "").strip())
        object.__setattr__(
            self,
            "reserved_slot_registration_policy",
            str(self.reserved_slot_registration_policy or "").strip(),
        )
        object.__setattr__(self, "recommended_next_owner", str(self.recommended_next_owner or "").strip())
        object.__setattr__(self, "recommended_route", str(self.recommended_route or "llm_repair").strip())
        object.__setattr__(self, "handoff_recommendation", str(self.handoff_recommendation or "").strip())
        object.__setattr__(self, "llm_advisory_recommended", bool(self.llm_advisory_recommended))
        object.__setattr__(self, "agi_advisory_recommended", bool(self.agi_advisory_recommended))
        object.__setattr__(self, "authoritative_rule_registration_allowed", False)
        object.__setattr__(
            self,
            "recommended_registration_path",
            str(self.recommended_registration_path or "").strip(),
        )
        object.__setattr__(self, "coverage_status", str(self.coverage_status or "coverage_gap").strip())
        object.__setattr__(self, "runtime_blockers", tuple(_to_dict_copy(item) for item in self.runtime_blockers))
        object.__setattr__(
            self,
            "runtime_blocker_reasons",
            _to_tuple_str(list(self.runtime_blocker_reasons)),
        )

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
            "language": self.language,
            "diagnostic_archetype": self.diagnostic_archetype,
            "diagnostic_phase": self.diagnostic_phase,
            "diagnostic_language": self.diagnostic_language,
            "diagnostic_code": self.diagnostic_code,
            "archetype_suggestion": self.archetype_suggestion,
            "phase_suggestion": self.phase_suggestion,
            "suggested_rule_family": self.suggested_rule_family,
            "reserved_slot_available": self.reserved_slot_available,
            "slot_status": self.slot_status,
            "reserved_language_slot_matched": self.reserved_language_slot_matched,
            "reserved_language_slot": dict(self.reserved_language_slot),
            "reserved_repairer_module": self.reserved_repairer_module,
            "reserved_slot_registration_policy": self.reserved_slot_registration_policy,
            "recommended_next_owner": self.recommended_next_owner,
            "recommended_route": self.recommended_route,
            "handoff_recommendation": self.handoff_recommendation,
            "llm_advisory_recommended": self.llm_advisory_recommended,
            "agi_advisory_recommended": self.agi_advisory_recommended,
            "authoritative_rule_registration_allowed": False,
            "recommended_registration_path": self.recommended_registration_path,
            "coverage_status": self.coverage_status,
            "runtime_blockers": [dict(item) for item in self.runtime_blockers],
            "runtime_blocker_reasons": list(self.runtime_blocker_reasons),
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
        coverage_gaps = [_public_coverage_gap_payload(item) for item in self.items if not item.known_rule_matched]
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
            "coverage_gap_count": len(coverage_gaps),
            "rule_discovery_required": bool(coverage_gaps),
            "coverage_gap_languages": sorted(
                {str(gap.get("diagnostic_language") or "unknown") for gap in coverage_gaps}
            ),
            "coverage_gap_archetypes": sorted(
                {str(gap.get("diagnostic_archetype") or "unknown") for gap in coverage_gaps}
            ),
            "coverage_gap_diagnostic_codes": sorted(
                {str(gap.get("diagnostic_code") or "unknown") for gap in coverage_gaps}
            ),
            "coverage_gap_handoff_recommendations": sorted(
                {str(gap.get("handoff_recommendation") or "coverage_triage_required") for gap in coverage_gaps}
            ),
            "coverage_gap_recommended_routes": sorted(
                {str(gap.get("recommended_route") or "llm_repair") for gap in coverage_gaps}
            ),
            "coverage_gap_slot_statuses": sorted(
                {str(gap.get("slot_status") or "reserved_slot_missing") for gap in coverage_gaps}
            ),
            "executable_runtime_plan_diagnostic_count": self.executable_runtime_plan_diagnostic_count,
            "metadata_only_diagnostic_count": self.metadata_only_diagnostic_count,
            "items": [item.to_dict() for item in self.items],
            "uncovered_diagnostics": [dict(item.diagnostic) for item in self.items if not item.known_rule_matched],
            "coverage_gaps": coverage_gaps,
        }


def _public_coverage_gap_payload(item: DirectorRepairDiagnosticCoverageV1) -> dict[str, Any]:
    return {
        "diagnostic": dict(item.diagnostic),
        "diagnostic_id": str(item.diagnostic.get("diagnostic_id") or ""),
        "known_rule_matched": False,
        "executable_runtime_plan_matched": False,
        "metadata_only_match": False,
        "language": item.language,
        "diagnostic_language": item.diagnostic_language,
        "diagnostic_code": item.diagnostic_code,
        "diagnostic_phase": item.diagnostic_phase,
        "diagnostic_archetype": item.diagnostic_archetype,
        "phase_suggestion": item.phase_suggestion,
        "archetype_suggestion": item.archetype_suggestion,
        "suggested_rule_family": item.suggested_rule_family,
        "reserved_slot_available": item.reserved_slot_available,
        "slot_status": item.slot_status,
        "reserved_language_slot_matched": item.reserved_language_slot_matched,
        "reserved_language_slot": dict(item.reserved_language_slot),
        "reserved_repairer_module": item.reserved_repairer_module,
        "reserved_slot_registration_policy": item.reserved_slot_registration_policy,
        "recommended_next_owner": item.recommended_next_owner,
        "recommended_route": item.recommended_route,
        "handoff_recommendation": item.handoff_recommendation,
        "llm_advisory_recommended": item.llm_advisory_recommended,
        "agi_advisory_recommended": item.agi_advisory_recommended,
        "authoritative_rule_registration_allowed": False,
        "recommended_registration_path": item.recommended_registration_path,
        "missing_capability": "deterministic_repair_rule",
        "audit_reason": "known_rule_matched=false",
        "coverage_status": "coverage_gap",
    }


@dataclass(frozen=True)
class AttachDirectorRepairRevalidationEvidenceV1:
    """Command shape for projecting post-check evidence onto repair receipts."""

    summary: Mapping[str, Any]
    residual_artifact_quality_errors: tuple[str, ...] = ()
    residual_artifact_quality_issues: tuple[Mapping[str, Any], ...] = ()
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
        object.__setattr__(
            self,
            "residual_artifact_quality_issues",
            _to_tuple_mapping_from_any(self.residual_artifact_quality_issues),
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
    """Command shape for projecting repair tool results into kernel receipts."""

    stage: str
    tool_results: tuple[Mapping[str, Any], ...] = ()
    artifact_quality_errors: tuple[str, ...] = ()
    artifact_quality_issues: tuple[Mapping[str, Any], ...] = ()
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
        object.__setattr__(self, "artifact_quality_issues", _to_tuple_mapping_from_any(self.artifact_quality_issues))
        object.__setattr__(self, "mode", _require_non_empty("mode", self.mode))
        object.__setattr__(self, "metadata", _to_dict_copy(self.metadata))

    def to_dict(self) -> dict[str, Any]:
        return {
            "stage": self.stage,
            "tool_results": [dict(item) for item in self.tool_results],
            "artifact_quality_errors": list(self.artifact_quality_errors),
            "artifact_quality_issues": [dict(item) for item in self.artifact_quality_issues],
            "mode": self.mode,
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class DirectorRepairKernelSummaryProjectionResultV1:
    """Read-only result for repair receipt summary projection."""

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
