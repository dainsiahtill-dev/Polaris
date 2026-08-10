"""Plan/run/convergence repair commands and revalidation request contracts."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from polaris.cells.director.runtime.public.contracts._diagnostics_receipts import (
    RepairAdvisoryV1,
    RepairDiagnosticV1,
    RepairReceiptV1,
    _repair_diagnostic_v1_to_dict,
)
from polaris.cells.director.runtime.public.contracts._environment_prep import (
    DirectorRepairEnvironmentPrepPlanV1,
    DirectorRepairEnvironmentPrepReceiptV1,
    _to_environment_prep_receipt_tuple_from_any,
)
from polaris.cells.director.runtime.public.contracts._helpers import (
    _require_non_empty,
    _to_dict_copy,
    _to_tuple_mapping_from_any,
    _to_tuple_str,
)


@dataclass(frozen=True)
class PlanDirectorRepairCommandV1:
    """Command shape for generic Director Runtime repair planning."""

    source_tool: str
    base_files: Mapping[str, str] = field(default_factory=dict)
    artifact_quality_errors: tuple[str, ...] = ()
    artifact_quality_issues: tuple[Mapping[str, Any], ...] = ()
    diagnostics: tuple[RepairDiagnosticV1, ...] = ()
    mode: str = "commit"
    deterministic_only: bool = True
    advisor_notes: tuple[RepairAdvisoryV1, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "source_tool", _require_non_empty("source_tool", self.source_tool))
        object.__setattr__(self, "base_files", dict(self.base_files or {}))
        object.__setattr__(self, "artifact_quality_errors", tuple(str(item) for item in self.artifact_quality_errors))
        object.__setattr__(self, "artifact_quality_issues", _to_tuple_mapping_from_any(self.artifact_quality_issues))
        object.__setattr__(self, "diagnostics", tuple(self.diagnostics or ()))
        object.__setattr__(self, "advisor_notes", tuple(self.advisor_notes or ()))
        object.__setattr__(self, "metadata", _to_dict_copy(self.metadata))


@dataclass(frozen=True)
class RunDirectorRepairCommandV1:
    """Command shape for generic Director Runtime repair execution."""

    task_id: str
    workspace: str
    source_tool: str
    base_files: Mapping[str, str] = field(default_factory=dict)
    artifact_quality_errors: tuple[str, ...] = ()
    artifact_quality_issues: tuple[Mapping[str, Any], ...] = ()
    diagnostics: tuple[RepairDiagnosticV1, ...] = ()
    mode: str = "commit"
    deterministic_only: bool = True
    allowed_paths: tuple[str, ...] = ()
    advisor_notes: tuple[RepairAdvisoryV1, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "task_id", _require_non_empty("task_id", self.task_id))
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "source_tool", _require_non_empty("source_tool", self.source_tool))
        object.__setattr__(self, "base_files", dict(self.base_files or {}))
        object.__setattr__(self, "artifact_quality_errors", tuple(str(item) for item in self.artifact_quality_errors))
        object.__setattr__(self, "artifact_quality_issues", _to_tuple_mapping_from_any(self.artifact_quality_issues))
        object.__setattr__(self, "diagnostics", tuple(self.diagnostics or ()))
        object.__setattr__(self, "allowed_paths", tuple(str(item) for item in self.allowed_paths))
        object.__setattr__(self, "advisor_notes", tuple(self.advisor_notes or ()))
        object.__setattr__(self, "metadata", _to_dict_copy(self.metadata))


@dataclass(frozen=True)
class RunDirectorRepairConvergenceCommandV1:
    """Command shape for public typed Director Runtime repair convergence."""

    task_id: str
    workspace: str
    source_tools: tuple[str, ...]
    artifact_quality_errors: tuple[str, ...]
    base_files: Mapping[str, str]
    artifact_quality_issues: tuple[Mapping[str, Any], ...] = ()
    allowed_paths: tuple[str, ...] = ()
    advisor_notes: tuple[RepairAdvisoryV1, ...] = ()
    mode: str = "commit"
    max_rounds: int = 3
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "task_id", _require_non_empty("task_id", self.task_id))
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        source_tools = _to_tuple_str(list(self.source_tools))
        if not source_tools:
            raise ValueError("source_tools must include at least one repair source tool")
        object.__setattr__(self, "source_tools", source_tools)
        object.__setattr__(self, "artifact_quality_errors", _to_tuple_str(list(self.artifact_quality_errors)))
        object.__setattr__(self, "base_files", dict(self.base_files or {}))
        object.__setattr__(self, "artifact_quality_issues", _to_tuple_mapping_from_any(self.artifact_quality_issues))
        object.__setattr__(self, "allowed_paths", tuple(str(item) for item in self.allowed_paths))
        object.__setattr__(self, "advisor_notes", tuple(self.advisor_notes or ()))
        object.__setattr__(self, "mode", str(self.mode or "commit").strip() or "commit")
        object.__setattr__(self, "max_rounds", max(1, int(self.max_rounds)))
        object.__setattr__(self, "metadata", _to_dict_copy(self.metadata))


@dataclass(frozen=True)
class DirectorRepairVerifierSnapshotInputV1:
    """Adapter-supplied verifier snapshot for a convergence round."""

    residual_artifact_quality_errors: tuple[str, ...] = ()
    residual_artifact_quality_issues: tuple[Mapping[str, Any], ...] = ()
    command: tuple[str, ...] = ()
    exit_code: int | None = None
    raw_output_ref: str | None = None
    environment_prep_receipts: tuple[DirectorRepairEnvironmentPrepReceiptV1, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
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
        object.__setattr__(self, "raw_output_ref", str(self.raw_output_ref or "").strip() or None)
        object.__setattr__(
            self,
            "environment_prep_receipts",
            _to_environment_prep_receipt_tuple_from_any(self.environment_prep_receipts),
        )
        object.__setattr__(self, "metadata", _to_dict_copy(self.metadata))

    def to_dict(self) -> dict[str, Any]:
        return {
            "residual_artifact_quality_errors": list(self.residual_artifact_quality_errors),
            "residual_artifact_quality_issues": [dict(item) for item in self.residual_artifact_quality_issues],
            "command": list(self.command),
            "exit_code": self.exit_code,
            "raw_output_ref": self.raw_output_ref,
            "environment_prep_receipt_count": len(self.environment_prep_receipts),
            "environment_prep_receipts": [receipt.to_dict() for receipt in self.environment_prep_receipts],
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class DirectorRepairConvergenceVerifierRequestV1:
    """Context passed to an adapter-supplied convergence verifier callback."""

    task_id: str
    workspace: str
    round_number: int
    source_tools: tuple[str, ...]
    receipts: tuple[RepairReceiptV1, ...] = ()
    environment_prep_plans: tuple[DirectorRepairEnvironmentPrepPlanV1, ...] = ()
    max_rounds: int = 3
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "task_id", _require_non_empty("task_id", self.task_id))
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "round_number", max(0, int(self.round_number)))
        object.__setattr__(self, "source_tools", _to_tuple_str(list(self.source_tools)))
        object.__setattr__(self, "receipts", tuple(self.receipts or ()))
        object.__setattr__(self, "environment_prep_plans", tuple(self.environment_prep_plans or ()))
        object.__setattr__(self, "max_rounds", max(1, int(self.max_rounds)))
        object.__setattr__(self, "metadata", _to_dict_copy(self.metadata))

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "workspace": self.workspace,
            "round_number": self.round_number,
            "source_tools": list(self.source_tools),
            "receipt_count": len(self.receipts),
            "receipts": [receipt.to_dict() for receipt in self.receipts],
            "environment_prep_plan_count": len(self.environment_prep_plans),
            "environment_prep_plans": [plan.to_dict() for plan in self.environment_prep_plans],
            "max_rounds": self.max_rounds,
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class DirectorRepairConvergenceRoundResultV1:
    """Public projection for one Director Runtime convergence round."""

    round_number: int
    status: str
    schedule: Mapping[str, Any] = field(default_factory=dict)
    diagnostics_before: tuple[RepairDiagnosticV1, ...] = ()
    diagnostics_after: tuple[RepairDiagnosticV1, ...] = ()
    receipts: tuple[RepairReceiptV1, ...] = ()
    revalidation_evidence: Mapping[str, Any] = field(default_factory=dict)
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "round_number", max(0, int(self.round_number)))
        object.__setattr__(self, "status", _require_non_empty("status", self.status))
        object.__setattr__(self, "schedule", _to_dict_copy(self.schedule))
        object.__setattr__(self, "diagnostics_before", tuple(self.diagnostics_before or ()))
        object.__setattr__(self, "diagnostics_after", tuple(self.diagnostics_after or ()))
        object.__setattr__(self, "receipts", tuple(self.receipts or ()))
        object.__setattr__(self, "revalidation_evidence", _to_dict_copy(self.revalidation_evidence))
        object.__setattr__(self, "metadata", _to_dict_copy(self.metadata))

    def to_dict(self) -> dict[str, Any]:
        return {
            "round_number": self.round_number,
            "status": self.status,
            "schedule": dict(self.schedule),
            "errors_before": len(self.diagnostics_before),
            "errors_after": len(self.diagnostics_after),
            "net_error_reduction": len(self.diagnostics_before) - len(self.diagnostics_after),
            "diagnostics_before": [diagnostic.__dict__ for diagnostic in self.diagnostics_before],
            "diagnostics_after": [diagnostic.__dict__ for diagnostic in self.diagnostics_after],
            "receipts": [receipt.to_dict() for receipt in self.receipts],
            "revalidation_evidence": dict(self.revalidation_evidence),
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class DirectorRepairConvergenceResultV1:
    """Result shape for public typed Director Runtime repair convergence."""

    ok: bool
    converged: bool
    status: str
    final_diagnostics: tuple[RepairDiagnosticV1, ...] = ()
    receipts: tuple[RepairReceiptV1, ...] = ()
    rounds: tuple[DirectorRepairConvergenceRoundResultV1, ...] = ()
    max_rounds: int = 3
    metadata: Mapping[str, Any] = field(default_factory=dict)
    error_code: str | None = None
    error_message: str | None = None
    schema_version: str = "director.repair_convergence_result.v1"
    owner_cell: str = "director.runtime"
    execution_boundary: str = "adapter_supplied_verifier_callback_no_command_execution"

    def __post_init__(self) -> None:
        object.__setattr__(self, "ok", bool(self.ok))
        object.__setattr__(self, "converged", bool(self.converged))
        object.__setattr__(self, "status", _require_non_empty("status", self.status))
        object.__setattr__(self, "final_diagnostics", tuple(self.final_diagnostics or ()))
        object.__setattr__(self, "receipts", tuple(self.receipts or ()))
        object.__setattr__(self, "rounds", tuple(self.rounds or ()))
        object.__setattr__(self, "max_rounds", max(1, int(self.max_rounds)))
        object.__setattr__(self, "metadata", _to_dict_copy(self.metadata))
        object.__setattr__(self, "error_code", str(self.error_code or "").strip() or None)
        object.__setattr__(self, "error_message", str(self.error_message or "").strip() or None)
        object.__setattr__(self, "schema_version", _require_non_empty("schema_version", self.schema_version))
        object.__setattr__(self, "owner_cell", _require_non_empty("owner_cell", self.owner_cell))
        object.__setattr__(
            self,
            "execution_boundary",
            _require_non_empty("execution_boundary", self.execution_boundary),
        )
        if not self.ok and not (self.error_code or self.error_message):
            raise ValueError("failed DirectorRepairConvergenceResultV1 must include error_code or error_message")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "ok": self.ok,
            "converged": self.converged,
            "status": self.status,
            "owner_cell": self.owner_cell,
            "execution_boundary": self.execution_boundary,
            "max_rounds": self.max_rounds,
            "round_count": len(self.rounds),
            "receipt_count": len(self.receipts),
            "final_error_count": len(self.final_diagnostics),
            "final_diagnostics": [_repair_diagnostic_v1_to_dict(diagnostic) for diagnostic in self.final_diagnostics],
            "receipts": [receipt.to_dict() for receipt in self.receipts],
            "rounds": [round_result.to_dict() for round_result in self.rounds],
            "error_code": self.error_code,
            "error_message": self.error_message,
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class DirectorRepairRevalidationInputV1:
    """Adapter-supplied post-check evidence for a Director repair run."""

    residual_artifact_quality_errors: tuple[str, ...] = ()
    residual_artifact_quality_issues: tuple[Mapping[str, Any], ...] = ()
    command: tuple[str, ...] = ()
    exit_code: int | None = None
    raw_output_ref: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
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
        object.__setattr__(self, "raw_output_ref", str(self.raw_output_ref or "").strip() or None)
        object.__setattr__(self, "metadata", _to_dict_copy(self.metadata))

    def to_dict(self) -> dict[str, Any]:
        return {
            "residual_artifact_quality_errors": list(self.residual_artifact_quality_errors),
            "command": list(self.command),
            "exit_code": self.exit_code,
            "raw_output_ref": self.raw_output_ref,
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class DirectorRepairRevalidationRequestV1:
    """Context passed to a local revalidator after runtime repair execution."""

    task_id: str
    workspace: str
    source_tool: str
    receipt_id: str
    plan_id: str
    files_changed: tuple[str, ...] = ()
    before_hashes: Mapping[str, str] = field(default_factory=dict)
    after_hashes: Mapping[str, str] = field(default_factory=dict)
    diagnostics_before: tuple[Mapping[str, Any], ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "task_id", _require_non_empty("task_id", self.task_id))
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "source_tool", _require_non_empty("source_tool", self.source_tool))
        object.__setattr__(self, "receipt_id", _require_non_empty("receipt_id", self.receipt_id))
        object.__setattr__(self, "plan_id", _require_non_empty("plan_id", self.plan_id))
        object.__setattr__(self, "files_changed", tuple(str(item) for item in self.files_changed))
        object.__setattr__(self, "before_hashes", dict(self.before_hashes or {}))
        object.__setattr__(self, "after_hashes", dict(self.after_hashes or {}))
        object.__setattr__(self, "diagnostics_before", tuple(_to_dict_copy(item) for item in self.diagnostics_before))
        object.__setattr__(self, "metadata", _to_dict_copy(self.metadata))

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "workspace": self.workspace,
            "source_tool": self.source_tool,
            "receipt_id": self.receipt_id,
            "plan_id": self.plan_id,
            "files_changed": list(self.files_changed),
            "before_hashes": dict(self.before_hashes),
            "after_hashes": dict(self.after_hashes),
            "diagnostics_before": [dict(item) for item in self.diagnostics_before],
            "metadata": dict(self.metadata),
        }
