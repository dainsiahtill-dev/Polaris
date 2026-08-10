"""Materialization-quality repair schedule and facade contracts."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from polaris.cells.director.runtime.public.contracts._helpers import (
    _ALLOWED_CALLBACK_RECEIPT_AUTHORITIES,
    _DEFAULT_ADAPTER_RECEIPT_AUTHORITY,
    _DEFAULT_CALLBACK_RECEIPT_AUTHORITY,
    _optional_non_empty_str,
    _require_non_empty,
    _to_dict_copy,
    _to_tuple_str,
)
from polaris.cells.director.runtime.public.contracts._post_execution_schedule import (
    DirectorRepairCallbackReceiptProjectionV1,
    _callback_receipt_projection_v1,
)


@dataclass(frozen=True)
class QueryDirectorRepairMaterializationQualityScheduleV1:
    """Query shape for the runtime-owned materialization-quality repair schedule catalog."""

    include_items: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(self, "include_items", bool(self.include_items))


@dataclass(frozen=True)
class DirectorRepairMaterializationQualityStepV1:
    """Public projection of one materialization-quality repair scheduling step."""

    step_id: str
    language: str
    phase: str
    priority: int
    source_tool: str
    source_tool_kind: str = "callback_schedule_label"
    executable_runtime_source_tool: bool = False
    runtime_source_tools: tuple[str, ...] = ()
    depends_on: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "step_id", _require_non_empty("step_id", self.step_id))
        object.__setattr__(self, "language", _require_non_empty("language", self.language))
        object.__setattr__(self, "phase", _require_non_empty("phase", self.phase))
        object.__setattr__(self, "priority", max(0, int(self.priority)))
        object.__setattr__(self, "source_tool", _require_non_empty("source_tool", self.source_tool))
        source_tool_kind = _require_non_empty("source_tool_kind", self.source_tool_kind)
        object.__setattr__(self, "source_tool_kind", source_tool_kind)
        object.__setattr__(self, "executable_runtime_source_tool", source_tool_kind == "executable_runtime")
        object.__setattr__(self, "runtime_source_tools", _to_tuple_str(list(self.runtime_source_tools)))
        object.__setattr__(self, "depends_on", _to_tuple_str(list(self.depends_on)))

    def to_dict(self) -> dict[str, Any]:
        return {
            "step_id": self.step_id,
            "language": self.language,
            "phase": self.phase,
            "priority": self.priority,
            "source_tool": self.source_tool,
            "source_tool_kind": self.source_tool_kind,
            "executable_runtime_source_tool": self.executable_runtime_source_tool,
            "runtime_source_tools": list(self.runtime_source_tools),
            "depends_on": list(self.depends_on),
        }


@dataclass(frozen=True)
class DirectorRepairMaterializationQualityScheduleResultV1:
    """Read-only runtime-owned materialization-quality repair schedule catalog."""

    schema_version: str
    source: str
    access: str
    items: tuple[DirectorRepairMaterializationQualityStepV1, ...] = ()
    summary: Mapping[str, Any] = field(default_factory=dict)
    owner_cell: str = "director.runtime"
    execution_boundary: str = "read_only_materialization_quality_schedule_no_runner_binding"
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
class DirectorRepairMaterializationQualityScheduleRunResultV1:
    """Projection-only result from running materialization-quality callback schedule."""

    schema_version: str
    source: str
    ordered_steps: tuple[DirectorRepairMaterializationQualityStepV1, ...] = ()
    tool_results: tuple[Mapping[str, Any], ...] = field(default_factory=tuple)
    receipt_projections: tuple[DirectorRepairCallbackReceiptProjectionV1, ...] = field(default_factory=tuple)
    summary: Mapping[str, Any] = field(default_factory=dict)
    max_rounds: int = 1
    rounds_run: int = 0
    convergence_status: str = "not_run"
    stopped_reason: str = "not_run"
    owner_cell: str = "director.runtime"
    runner_binding_owner: str = "roles.adapters"
    adapter_callback_bridge: bool = False
    adapter_projection_bridge: bool = True
    typed_receipt_path_available: bool = False
    authoritative_receipts_allowed: bool = False
    projection_only: bool = True
    receipt_authority: str = _DEFAULT_ADAPTER_RECEIPT_AUTHORITY

    def __post_init__(self) -> None:
        object.__setattr__(self, "schema_version", _require_non_empty("schema_version", self.schema_version))
        object.__setattr__(self, "source", _require_non_empty("source", self.source))
        object.__setattr__(self, "ordered_steps", tuple(self.ordered_steps or ()))
        object.__setattr__(self, "tool_results", tuple(dict(item) for item in (self.tool_results or ())))
        object.__setattr__(
            self,
            "receipt_projections",
            tuple(_callback_receipt_projection_v1(item) for item in (self.receipt_projections or ())),
        )
        object.__setattr__(self, "summary", _to_dict_copy(self.summary))
        object.__setattr__(self, "max_rounds", max(0, int(self.max_rounds)))
        object.__setattr__(self, "rounds_run", max(0, int(self.rounds_run)))
        object.__setattr__(
            self,
            "convergence_status",
            _require_non_empty("convergence_status", self.convergence_status),
        )
        object.__setattr__(self, "stopped_reason", _require_non_empty("stopped_reason", self.stopped_reason))
        object.__setattr__(self, "owner_cell", _require_non_empty("owner_cell", self.owner_cell))
        object.__setattr__(
            self, "runner_binding_owner", _require_non_empty("runner_binding_owner", self.runner_binding_owner)
        )
        object.__setattr__(self, "adapter_callback_bridge", False)
        object.__setattr__(self, "adapter_projection_bridge", True)
        object.__setattr__(self, "typed_receipt_path_available", False)
        object.__setattr__(self, "authoritative_receipts_allowed", False)
        object.__setattr__(self, "projection_only", True)
        receipt_authority = _optional_non_empty_str(self.receipt_authority)
        if receipt_authority not in _ALLOWED_CALLBACK_RECEIPT_AUTHORITIES:
            receipt_authority = _DEFAULT_CALLBACK_RECEIPT_AUTHORITY
        object.__setattr__(self, "receipt_authority", receipt_authority)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "source": self.source,
            "owner_cell": self.owner_cell,
            "runner_binding_owner": self.runner_binding_owner,
            "adapter_callback_bridge": False,
            "adapter_projection_bridge": True,
            "ordered_steps": [item.to_dict() for item in self.ordered_steps],
            "tool_results": [dict(item) for item in self.tool_results],
            "receipt_projections": [item.to_dict() for item in self.receipt_projections],
            "summary": dict(self.summary),
            "max_rounds": self.max_rounds,
            "rounds_run": self.rounds_run,
            "convergence_status": self.convergence_status,
            "stopped_reason": self.stopped_reason,
            "typed_receipt_path_available": False,
            "authoritative_receipts_allowed": False,
            "projection_only": True,
            "receipt_authority": self.receipt_authority,
        }


@dataclass(frozen=True)
class DirectorRepairMaterializationQualityFacadeResultV1:
    """Runtime-owned facade result for materialization-quality repair execution."""

    schema_version: str
    source: str
    ordered_steps: tuple[DirectorRepairMaterializationQualityStepV1, ...] = ()
    tool_results: tuple[Mapping[str, Any], ...] = field(default_factory=tuple)
    receipt_projections: tuple[Mapping[str, Any], ...] = field(default_factory=tuple)
    coverage_preaudit: Mapping[str, Any] = field(default_factory=dict)
    plan_probe_preaudit: Mapping[str, Any] = field(default_factory=dict)
    schedule_summary: Mapping[str, Any] = field(default_factory=dict)
    schedule_reconciliation: Mapping[str, Any] = field(default_factory=dict)
    summary: Mapping[str, Any] = field(default_factory=dict)
    max_rounds: int = 1
    rounds_run: int = 0
    convergence_status: str = "not_run"
    stopped_reason: str = "not_run"
    owner_cell: str = "director.runtime"
    runner_binding_owner: str = "roles.adapters"
    execution_boundary: str = "runtime_materialization_quality_facade_no_direct_writes"
    director_tool_execution_required: bool = True
    writes_allowed: bool = False
    registration_allowed: bool = False
    agi_execution_authority: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "schema_version", _require_non_empty("schema_version", self.schema_version))
        object.__setattr__(self, "source", _require_non_empty("source", self.source))
        object.__setattr__(self, "ordered_steps", tuple(self.ordered_steps or ()))
        object.__setattr__(self, "tool_results", tuple(dict(item) for item in (self.tool_results or ())))
        object.__setattr__(self, "receipt_projections", tuple(dict(item) for item in (self.receipt_projections or ())))
        object.__setattr__(self, "coverage_preaudit", _to_dict_copy(self.coverage_preaudit))
        object.__setattr__(self, "plan_probe_preaudit", _to_dict_copy(self.plan_probe_preaudit))
        object.__setattr__(self, "schedule_summary", _to_dict_copy(self.schedule_summary))
        object.__setattr__(self, "schedule_reconciliation", _to_dict_copy(self.schedule_reconciliation))
        object.__setattr__(self, "summary", _to_dict_copy(self.summary))
        object.__setattr__(self, "max_rounds", max(0, int(self.max_rounds)))
        object.__setattr__(self, "rounds_run", max(0, int(self.rounds_run)))
        object.__setattr__(
            self, "convergence_status", _require_non_empty("convergence_status", self.convergence_status)
        )
        object.__setattr__(self, "stopped_reason", _require_non_empty("stopped_reason", self.stopped_reason))
        object.__setattr__(self, "owner_cell", _require_non_empty("owner_cell", self.owner_cell))
        object.__setattr__(
            self, "runner_binding_owner", _require_non_empty("runner_binding_owner", self.runner_binding_owner)
        )
        object.__setattr__(
            self,
            "execution_boundary",
            _require_non_empty("execution_boundary", self.execution_boundary),
        )
        object.__setattr__(self, "director_tool_execution_required", True)
        object.__setattr__(self, "writes_allowed", False)
        object.__setattr__(self, "registration_allowed", False)
        object.__setattr__(self, "agi_execution_authority", False)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "source": self.source,
            "owner_cell": self.owner_cell,
            "runner_binding_owner": self.runner_binding_owner,
            "execution_boundary": self.execution_boundary,
            "director_tool_execution_required": True,
            "writes_allowed": False,
            "registration_allowed": False,
            "agi_execution_authority": False,
            "ordered_steps": [item.to_dict() for item in self.ordered_steps],
            "tool_results": [dict(item) for item in self.tool_results],
            "receipt_projections": [dict(item) for item in self.receipt_projections],
            "coverage_preaudit": dict(self.coverage_preaudit),
            "plan_probe_preaudit": dict(self.plan_probe_preaudit),
            "schedule_summary": dict(self.schedule_summary),
            "schedule_reconciliation": dict(self.schedule_reconciliation),
            "summary": dict(self.summary),
            "max_rounds": self.max_rounds,
            "rounds_run": self.rounds_run,
            "convergence_status": self.convergence_status,
            "stopped_reason": self.stopped_reason,
        }
