"""Interface discrepancy receipt, task-boundary quality loop, and runtime error contracts."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from polaris.cells.director.runtime.public.contracts._diagnostics_receipts import RepairAdvisoryV1
from polaris.cells.director.runtime.public.contracts._helpers import (
    _require_non_empty,
    _to_dict_copy,
    _to_tuple_mapping_from_any,
    _to_tuple_str,
)
from polaris.cells.director.runtime.public.contracts._plan_probe import DirectorRepairPlanProbeResultV1
from polaris.cells.director.runtime.public.contracts._repair_commands import DirectorRepairConvergenceResultV1


@dataclass(frozen=True)
class DirectorInterfaceDiscrepancyReceiptV1:
    """Canonical receipt for task-boundary interface discrepancies."""

    task_id: str
    status: str = "semantic_discrepancy_triage_required"
    source: str = "director.runtime.interface_discrepancy"
    plan_probe_status: str = ""
    diagnostics: tuple[Mapping[str, Any], ...] = ()
    source_tools: tuple[str, ...] = ()
    recommended_owner: str = "chief_engineer"
    recommended_route: str = "pending_design_interface_contract"
    triage_policy: str = "ce_contract_if_missing_else_director_local_repair"
    macro_blueprint_regeneration_allowed: bool = False
    task_interface_contract_present: bool = False
    llm_fallback_blocked: bool = True
    director_retry_allowed: bool = False
    reason: str = "coverage_matched_but_unplannable"
    interface_delta: Mapping[str, Any] = field(default_factory=dict)
    triage_summary: Mapping[str, Any] = field(default_factory=dict)
    metadata: Mapping[str, Any] = field(default_factory=dict)
    schema_version: str = "director.interface_discrepancy_receipt.v1"

    def __post_init__(self) -> None:
        object.__setattr__(self, "task_id", _require_non_empty("task_id", self.task_id))
        object.__setattr__(self, "status", _require_non_empty("status", self.status))
        object.__setattr__(self, "source", _require_non_empty("source", self.source))
        object.__setattr__(self, "plan_probe_status", str(self.plan_probe_status or "").strip())
        object.__setattr__(
            self,
            "diagnostics",
            tuple(dict(item) for item in self.diagnostics if isinstance(item, Mapping)),
        )
        object.__setattr__(self, "source_tools", _to_tuple_str(list(self.source_tools)))
        object.__setattr__(
            self,
            "recommended_owner",
            str(self.recommended_owner or "chief_engineer").strip() or "chief_engineer",
        )
        object.__setattr__(
            self,
            "recommended_route",
            str(self.recommended_route or "pending_design_interface_contract").strip()
            or "pending_design_interface_contract",
        )
        object.__setattr__(self, "triage_policy", str(self.triage_policy or "").strip())
        object.__setattr__(
            self,
            "macro_blueprint_regeneration_allowed",
            bool(self.macro_blueprint_regeneration_allowed),
        )
        object.__setattr__(
            self,
            "task_interface_contract_present",
            bool(self.task_interface_contract_present),
        )
        object.__setattr__(self, "director_retry_allowed", bool(self.director_retry_allowed))
        object.__setattr__(
            self,
            "llm_fallback_blocked",
            bool(self.llm_fallback_blocked) and not bool(self.director_retry_allowed),
        )
        object.__setattr__(self, "reason", str(self.reason or "").strip())
        object.__setattr__(self, "interface_delta", _to_dict_copy(self.interface_delta))
        object.__setattr__(self, "triage_summary", _to_dict_copy(self.triage_summary))
        object.__setattr__(self, "metadata", _to_dict_copy(self.metadata))
        object.__setattr__(self, "schema_version", _require_non_empty("schema_version", self.schema_version))

    @classmethod
    def from_mapping(
        cls,
        value: Mapping[str, Any],
        *,
        task_id: str = "",
    ) -> DirectorInterfaceDiscrepancyReceiptV1:
        task = str(value.get("task_id") or task_id or "unknown-task").strip()
        source_tools = value.get("source_tools") or value.get("covered_unplannable_source_tools") or ()
        diagnostics = value.get("diagnostics") or value.get("covered_unplannable_diagnostics") or ()
        return cls(
            task_id=task,
            status=str(value.get("status") or "semantic_discrepancy_triage_required"),
            source=str(value.get("source") or value.get("route") or "director.runtime.interface_discrepancy"),
            plan_probe_status=str(value.get("plan_probe_status") or ""),
            diagnostics=tuple(item for item in diagnostics if isinstance(item, Mapping))
            if isinstance(diagnostics, (list, tuple))
            else (),
            source_tools=(_to_tuple_str(list(source_tools)) if isinstance(source_tools, (list, tuple)) else ()),
            recommended_owner=str(value.get("recommended_owner") or "chief_engineer"),
            recommended_route=str(value.get("recommended_route") or "pending_design_interface_contract"),
            triage_policy=str(value.get("triage_policy") or "ce_contract_if_missing_else_director_local_repair"),
            macro_blueprint_regeneration_allowed=bool(value.get("macro_blueprint_regeneration_allowed")),
            task_interface_contract_present=bool(value.get("task_interface_contract_present")),
            llm_fallback_blocked=bool(value.get("llm_fallback_blocked", True)),
            director_retry_allowed=bool(value.get("director_retry_allowed")),
            reason=str(value.get("reason") or "coverage_matched_but_unplannable"),
            interface_delta=_to_dict_copy(
                value.get("interface_delta") if isinstance(value.get("interface_delta"), Mapping) else {}
            ),
            triage_summary=_to_dict_copy(
                value.get("triage_summary") if isinstance(value.get("triage_summary"), Mapping) else {}
            ),
            metadata=_to_dict_copy(value.get("metadata") if isinstance(value.get("metadata"), Mapping) else {}),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "task_id": self.task_id,
            "status": self.status,
            "source": self.source,
            "plan_probe_status": self.plan_probe_status,
            "covered_unplannable": self.reason == "coverage_matched_but_unplannable",
            "diagnostics": [dict(item) for item in self.diagnostics],
            "source_tools": list(self.source_tools),
            "covered_unplannable_source_tools": list(self.source_tools),
            "covered_unplannable_diagnostic_count": len(self.diagnostics),
            "recommended_owner": self.recommended_owner,
            "recommended_route": self.recommended_route,
            "triage_policy": self.triage_policy,
            "macro_blueprint_regeneration_allowed": self.macro_blueprint_regeneration_allowed,
            "task_interface_contract_present": self.task_interface_contract_present,
            "llm_fallback_blocked": self.llm_fallback_blocked,
            "director_retry_allowed": self.director_retry_allowed,
            "reason": self.reason,
            "interface_delta": dict(self.interface_delta),
            "triage_summary": dict(self.triage_summary),
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class RunDirectorTaskBoundaryQualityLoopCommandV1:
    """Command shape for validating a complete CE task boundary through runtime repair convergence."""

    task_id: str
    workspace: str
    artifact_quality_errors: tuple[str, ...]
    base_files: Mapping[str, str]
    artifact_quality_issues: tuple[Mapping[str, Any], ...] = ()
    allowed_paths: tuple[str, ...] = ()
    source_tools: tuple[str, ...] = ()
    advisor_notes: tuple[RepairAdvisoryV1, ...] = ()
    mode: str = "commit"
    max_rounds: int = 3
    task_interface_contract: Mapping[str, Any] = field(default_factory=dict)
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "task_id", _require_non_empty("task_id", self.task_id))
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "artifact_quality_errors", _to_tuple_str(list(self.artifact_quality_errors)))
        object.__setattr__(self, "base_files", dict(self.base_files or {}))
        object.__setattr__(self, "artifact_quality_issues", _to_tuple_mapping_from_any(self.artifact_quality_issues))
        object.__setattr__(self, "allowed_paths", _to_tuple_str(list(self.allowed_paths)))
        object.__setattr__(self, "source_tools", _to_tuple_str(list(self.source_tools)))
        object.__setattr__(self, "advisor_notes", tuple(self.advisor_notes or ()))
        object.__setattr__(self, "mode", str(self.mode or "commit").strip() or "commit")
        object.__setattr__(self, "max_rounds", max(1, int(self.max_rounds)))
        object.__setattr__(self, "task_interface_contract", _to_dict_copy(self.task_interface_contract))
        object.__setattr__(self, "metadata", _to_dict_copy(self.metadata))


@dataclass(frozen=True)
class DirectorTaskBoundaryQualityResultV1:
    """Result for the task-boundary quality loop consumed by QA and Factory validation."""

    task_id: str
    ok: bool
    status: str
    plan_probe: DirectorRepairPlanProbeResultV1
    convergence_result: DirectorRepairConvergenceResultV1 | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)
    error_code: str | None = None
    error_message: str | None = None
    schema_version: str = "director.task_boundary_quality_result.v1"
    owner_cell: str = "director.runtime"
    execution_boundary: str = "runtime_plan_probe_then_convergence_with_adapter_effects"

    def __post_init__(self) -> None:
        object.__setattr__(self, "task_id", _require_non_empty("task_id", self.task_id))
        object.__setattr__(self, "ok", bool(self.ok))
        object.__setattr__(self, "status", _require_non_empty("status", self.status))
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
            raise ValueError("failed DirectorTaskBoundaryQualityResultV1 must include error_code or error_message")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "task_id": self.task_id,
            "ok": self.ok,
            "status": self.status,
            "owner_cell": self.owner_cell,
            "execution_boundary": self.execution_boundary,
            "plan_probe": self.plan_probe.to_dict(),
            "convergence_result": self.convergence_result.to_dict() if self.convergence_result is not None else None,
            "error_code": self.error_code,
            "error_message": self.error_message,
            "metadata": dict(self.metadata),
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
