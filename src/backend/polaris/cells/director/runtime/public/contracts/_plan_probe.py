"""Plan-probe, materialization allowed-paths/plan-probe, and bridge metadata contracts."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from polaris.cells.director.runtime.public.contracts._coverage_catalog import DirectorRepairCoverageReportV1
from polaris.cells.director.runtime.public.contracts._diagnostics_receipts import RepairAdvisoryV1
from polaris.cells.director.runtime.public.contracts._helpers import (
    _require_non_empty,
    _to_dict_copy,
    _to_tuple_mapping_from_any,
    _to_tuple_str,
)
from polaris.cells.director.runtime.public.contracts._materialization_quality_schedule import (
    DirectorRepairMaterializationQualityStepV1,
)
from polaris.cells.director.runtime.public.contracts._planning_results import DirectorRepairPlanningResultV1


@dataclass(frozen=True)
class QueryDirectorRepairPlanProbeV1:
    """Read-only probe that verifies coverage matches can produce concrete repair plans."""

    artifact_quality_errors: tuple[str, ...]
    artifact_quality_issues: tuple[Mapping[str, Any], ...] = ()
    base_files: Mapping[str, str] = field(default_factory=dict)
    source_tools: tuple[str, ...] = ()
    mode: str = "shadow"
    advisor_notes: tuple[RepairAdvisoryV1, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "artifact_quality_errors", _to_tuple_str(list(self.artifact_quality_errors)))
        object.__setattr__(self, "artifact_quality_issues", _to_tuple_mapping_from_any(self.artifact_quality_issues))
        object.__setattr__(self, "base_files", dict(self.base_files or {}))
        object.__setattr__(self, "source_tools", _to_tuple_str(list(self.source_tools)))
        object.__setattr__(self, "mode", str(self.mode or "shadow").strip() or "shadow")
        object.__setattr__(self, "advisor_notes", tuple(self.advisor_notes or ()))
        object.__setattr__(self, "metadata", _to_dict_copy(self.metadata))


@dataclass(frozen=True)
class QueryDirectorRepairMaterializationAllowedPathsV1:
    """Read-only materialization helper for repair execution allowed paths."""

    source_tool: str
    base_files: Mapping[str, str] = field(default_factory=dict)
    artifact_quality_errors: tuple[str, ...] = ()
    artifact_quality_issues: tuple[Mapping[str, Any], ...] = ()
    mode: str = "shadow"
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "source_tool", _require_non_empty("source_tool", self.source_tool))
        object.__setattr__(self, "base_files", dict(self.base_files or {}))
        object.__setattr__(self, "artifact_quality_errors", _to_tuple_str(list(self.artifact_quality_errors)))
        object.__setattr__(self, "artifact_quality_issues", _to_tuple_mapping_from_any(self.artifact_quality_issues))
        object.__setattr__(self, "mode", str(self.mode or "shadow").strip() or "shadow")
        object.__setattr__(self, "metadata", _to_dict_copy(self.metadata))


@dataclass(frozen=True)
class DirectorRepairMaterializationAllowedPathsResultV1:
    """Read-only runtime projection of materialization repair allowed paths."""

    source_tool: str
    planning_result: DirectorRepairPlanningResultV1
    base_paths: tuple[str, ...] = ()
    changed_paths: tuple[str, ...] = ()
    allowed_paths: tuple[str, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)
    schema_version: str = "director.materialization_allowed_paths_plan.v1"
    owner_cell: str = "director.runtime"
    execution_boundary: str = "read_only_materialization_allowed_paths_no_writes"
    agi_execution_authority: bool = False
    director_tool_execution_required: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "source_tool", _require_non_empty("source_tool", self.source_tool))
        object.__setattr__(self, "base_paths", _to_tuple_str(list(self.base_paths)))
        object.__setattr__(self, "changed_paths", _to_tuple_str(list(self.changed_paths)))
        object.__setattr__(self, "allowed_paths", _to_tuple_str(list(self.allowed_paths)))
        object.__setattr__(self, "metadata", _to_dict_copy(self.metadata))
        object.__setattr__(self, "schema_version", _require_non_empty("schema_version", self.schema_version))
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
            "source_tool": self.source_tool,
            "owner_cell": self.owner_cell,
            "execution_boundary": self.execution_boundary,
            "agi_execution_authority": False,
            "director_tool_execution_required": False,
            "base_paths": list(self.base_paths),
            "changed_paths": list(self.changed_paths),
            "allowed_paths": list(self.allowed_paths),
            "planning_result": self.planning_result.to_dict(),
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class QueryDirectorRepairMaterializationPlanProbeV1:
    """Read-only materialization probe that owns source-tool candidate filtering."""

    artifact_quality_errors: tuple[str, ...]
    artifact_quality_issues: tuple[Mapping[str, Any], ...] = ()
    source_tools: tuple[str, ...] = ()
    base_files: Mapping[str, str] = field(default_factory=dict)
    step_id: str | None = None
    mode: str = "shadow"
    fallback_to_step_source_tools: bool = False
    advisor_notes: tuple[RepairAdvisoryV1, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "artifact_quality_errors", _to_tuple_str(list(self.artifact_quality_errors)))
        object.__setattr__(self, "artifact_quality_issues", _to_tuple_mapping_from_any(self.artifact_quality_issues))
        object.__setattr__(self, "source_tools", _to_tuple_str(list(self.source_tools)))
        object.__setattr__(self, "base_files", dict(self.base_files or {}))
        object.__setattr__(self, "step_id", str(self.step_id or "").strip() or None)
        object.__setattr__(self, "mode", str(self.mode or "shadow").strip() or "shadow")
        object.__setattr__(self, "fallback_to_step_source_tools", bool(self.fallback_to_step_source_tools))
        object.__setattr__(self, "advisor_notes", tuple(self.advisor_notes or ()))
        object.__setattr__(self, "metadata", _to_dict_copy(self.metadata))


@dataclass(frozen=True)
class DirectorRepairMaterializationPlanProbeResultV1:
    """Runtime-owned source-tool filtering and planning proof for materialization repair."""

    status: str
    coverage_report: DirectorRepairCoverageReportV1
    plan_probe_result: DirectorRepairPlanProbeResultV1 | None = None
    requested_source_tools: tuple[str, ...] = ()
    candidate_source_tools: tuple[str, ...] = ()
    plannable_source_tools: tuple[str, ...] = ()
    base_file_count: int = 0
    metadata: Mapping[str, Any] = field(default_factory=dict)
    schema_version: str = "director.materialization_plan_probe_preaudit.v1"
    owner_cell: str = "director.runtime"
    execution_boundary: str = "read_only_materialization_plan_probe_no_writes"
    agi_execution_authority: bool = False
    director_tool_execution_required: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "status", _require_non_empty("status", self.status))
        object.__setattr__(self, "requested_source_tools", _to_tuple_str(list(self.requested_source_tools)))
        object.__setattr__(self, "candidate_source_tools", _to_tuple_str(list(self.candidate_source_tools)))
        object.__setattr__(self, "plannable_source_tools", _to_tuple_str(list(self.plannable_source_tools)))
        object.__setattr__(self, "base_file_count", max(0, int(self.base_file_count)))
        object.__setattr__(self, "metadata", _to_dict_copy(self.metadata))
        object.__setattr__(self, "schema_version", _require_non_empty("schema_version", self.schema_version))
        object.__setattr__(self, "owner_cell", _require_non_empty("owner_cell", self.owner_cell))
        object.__setattr__(
            self,
            "execution_boundary",
            _require_non_empty("execution_boundary", self.execution_boundary),
        )
        object.__setattr__(self, "agi_execution_authority", False)
        object.__setattr__(self, "director_tool_execution_required", False)

    def to_dict(self) -> dict[str, Any]:
        plan_probe = self.plan_probe_result.to_dict() if self.plan_probe_result is not None else None
        return {
            "schema_version": self.schema_version,
            "status": self.status,
            "owner_cell": self.owner_cell,
            "execution_boundary": self.execution_boundary,
            "agi_execution_authority": False,
            "director_tool_execution_required": False,
            "requested_source_tools": list(self.requested_source_tools),
            "candidate_source_tools": list(self.candidate_source_tools),
            "plannable_source_tools": list(self.plannable_source_tools),
            "base_file_count": self.base_file_count,
            "coverage_report": self.coverage_report.to_dict(),
            "runtime_plan_probe": plan_probe,
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class ProjectDirectorRepairMaterializationBridgeMetadataV1:
    """Project materialization bridge metadata from runtime-owned evidence."""

    ordered_steps: tuple[DirectorRepairMaterializationQualityStepV1, ...]
    repair_kernel: Mapping[str, Any] = field(default_factory=dict)
    schedule_reconciliation: Mapping[str, Any] = field(default_factory=dict)
    scheduler_bridge_evidence: Mapping[str, Any] = field(default_factory=dict)
    coverage_preaudit: Mapping[str, Any] = field(default_factory=dict)
    plan_probe_preaudit: Mapping[str, Any] = field(default_factory=dict)
    repair_kernel_migration_debt: Mapping[str, Any] = field(default_factory=dict)
    receipt_lifecycle_by_step: Mapping[str, Any] = field(default_factory=dict)
    dark_launch_comparison: Mapping[str, Any] = field(default_factory=dict)
    convergence_verifier_present: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "ordered_steps", tuple(self.ordered_steps or ()))
        object.__setattr__(self, "repair_kernel", _to_dict_copy(self.repair_kernel))
        object.__setattr__(self, "schedule_reconciliation", _to_dict_copy(self.schedule_reconciliation))
        object.__setattr__(self, "scheduler_bridge_evidence", _to_dict_copy(self.scheduler_bridge_evidence))
        object.__setattr__(self, "coverage_preaudit", _to_dict_copy(self.coverage_preaudit))
        object.__setattr__(self, "plan_probe_preaudit", _to_dict_copy(self.plan_probe_preaudit))
        object.__setattr__(self, "repair_kernel_migration_debt", _to_dict_copy(self.repair_kernel_migration_debt))
        object.__setattr__(self, "receipt_lifecycle_by_step", _to_dict_copy(self.receipt_lifecycle_by_step))
        object.__setattr__(self, "dark_launch_comparison", _to_dict_copy(self.dark_launch_comparison))
        object.__setattr__(self, "convergence_verifier_present", bool(self.convergence_verifier_present))


@dataclass(frozen=True)
class DirectorRepairMaterializationBridgeMetadataResultV1:
    """Runtime-owned materialization runtime-port metadata projection."""

    summary: Mapping[str, Any]
    schema_version: str = "director.materialization_quality_runtime_ports_metadata_projection.v1"
    source: str = "director.runtime.repair_kernel.materialization_projection"
    access: str = "read_only"
    owner_cell: str = "director.runtime"
    execution_boundary: str = "read_only_materialization_runtime_ports_metadata_no_writes"
    agi_execution_authority: bool = False
    director_tool_execution_required: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "summary", _to_dict_copy(self.summary))
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
            "summary": dict(self.summary),
        }


@dataclass(frozen=True)
class DirectorRepairPlanProbeItemV1:
    """One source-tool planning probe result for a covered diagnostic subset."""

    source_tool: str
    status: str
    planning_result: DirectorRepairPlanningResultV1
    matched_diagnostic_ids: tuple[str, ...] = ()
    matched_diagnostic_count: int = 0
    patch_count: int = 0
    changed_paths: tuple[str, ...] = ()
    error_code: str | None = None
    error_message: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "source_tool", _require_non_empty("source_tool", self.source_tool))
        object.__setattr__(self, "status", _require_non_empty("status", self.status))
        object.__setattr__(self, "matched_diagnostic_ids", _to_tuple_str(list(self.matched_diagnostic_ids)))
        object.__setattr__(self, "matched_diagnostic_count", max(0, int(self.matched_diagnostic_count)))
        object.__setattr__(self, "patch_count", max(0, int(self.patch_count)))
        object.__setattr__(self, "changed_paths", _to_tuple_str(list(self.changed_paths)))
        object.__setattr__(self, "error_code", str(self.error_code or "").strip() or None)
        object.__setattr__(self, "error_message", str(self.error_message or "").strip() or None)
        object.__setattr__(self, "metadata", _to_dict_copy(self.metadata))

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_tool": self.source_tool,
            "status": self.status,
            "matched_diagnostic_ids": list(self.matched_diagnostic_ids),
            "matched_diagnostic_count": self.matched_diagnostic_count,
            "patch_count": self.patch_count,
            "changed_paths": list(self.changed_paths),
            "error_code": self.error_code,
            "error_message": self.error_message,
            "planning_result": self.planning_result.to_dict(),
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class DirectorRepairPlanProbeResultV1:
    """Coverage plus planning proof for task-boundary repair selection."""

    status: str
    coverage_report: DirectorRepairCoverageReportV1
    items: tuple[DirectorRepairPlanProbeItemV1, ...] = ()
    plannable_source_tools: tuple[str, ...] = ()
    covered_unplannable_source_tools: tuple[str, ...] = ()
    covered_unplannable_diagnostics: tuple[Mapping[str, Any], ...] = ()
    uncovered_diagnostics: tuple[Mapping[str, Any], ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)
    schema_version: str = "director.repair_plan_probe_result.v1"
    owner_cell: str = "director.runtime"
    execution_boundary: str = "read_only_plan_probe_no_writes"
    agi_execution_authority: bool = False
    director_tool_execution_required: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "status", _require_non_empty("status", self.status))
        object.__setattr__(self, "items", tuple(self.items or ()))
        object.__setattr__(self, "plannable_source_tools", _to_tuple_str(list(self.plannable_source_tools)))
        object.__setattr__(
            self,
            "covered_unplannable_source_tools",
            _to_tuple_str(list(self.covered_unplannable_source_tools)),
        )
        object.__setattr__(
            self,
            "covered_unplannable_diagnostics",
            tuple(_to_dict_copy(item) for item in self.covered_unplannable_diagnostics),
        )
        object.__setattr__(
            self,
            "uncovered_diagnostics",
            tuple(_to_dict_copy(item) for item in self.uncovered_diagnostics),
        )
        object.__setattr__(self, "metadata", _to_dict_copy(self.metadata))
        object.__setattr__(self, "schema_version", _require_non_empty("schema_version", self.schema_version))
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
            "status": self.status,
            "owner_cell": self.owner_cell,
            "execution_boundary": self.execution_boundary,
            "agi_execution_authority": False,
            "director_tool_execution_required": False,
            "coverage_report": self.coverage_report.to_dict(),
            "items": [item.to_dict() for item in self.items],
            "plannable_source_tools": list(self.plannable_source_tools),
            "covered_unplannable_source_tools": list(self.covered_unplannable_source_tools),
            "covered_unplannable_diagnostic_count": len(self.covered_unplannable_diagnostics),
            "covered_unplannable_diagnostics": [dict(item) for item in self.covered_unplannable_diagnostics],
            "coverage_gap_count": len(self.uncovered_diagnostics),
            "uncovered_diagnostics": [dict(item) for item in self.uncovered_diagnostics],
            "metadata": dict(self.metadata),
        }
