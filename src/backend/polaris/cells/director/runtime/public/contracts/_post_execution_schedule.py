"""Post-execution repair schedule and callback receipt projection contracts."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from polaris.cells.director.runtime.public.contracts._helpers import (
    _ALLOWED_CALLBACK_RECEIPT_AUTHORITIES,
    _CALLBACK_RECEIPT_PROJECTION_SCHEMA_VERSION,
    _DEFAULT_ADAPTER_RECEIPT_AUTHORITY,
    _DEFAULT_CALLBACK_RECEIPT_AUTHORITY,
    _DEFAULT_CALLBACK_RECEIPT_MIGRATION_BLOCKER,
    _optional_non_empty_str,
    _optional_non_negative_int,
    _require_non_empty,
    _strict_bool_claim,
    _to_dict_copy,
    _to_tuple_str,
    _to_tuple_str_from_any,
)


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
    source_tool_kind: str = "executable_runtime"
    executable_runtime_source_tool: bool = True
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
        runtime_source_tools = _to_tuple_str(list(self.runtime_source_tools)) or (self.source_tool,)
        object.__setattr__(self, "runtime_source_tools", runtime_source_tools)
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
class DirectorRepairCallbackReceiptProjectionV1:
    """Non-authoritative callback receipt projection for migration schedules."""

    schema_version: str = _CALLBACK_RECEIPT_PROJECTION_SCHEMA_VERSION
    projection_id: str | None = None
    receipt_authority: str = _DEFAULT_CALLBACK_RECEIPT_AUTHORITY
    schedule_kind: str | None = None
    step_id: str | None = None
    source_tool: str | None = None
    scheduled_source_tool: str | None = None
    scheduled_source_tool_kind: str | None = None
    scheduled_source_tool_executable_runtime: bool = False
    callback_source_tool: str | None = None
    adapter_source_tool: str | None = None
    round_number: int | None = None
    tool_name: str | None = None
    touched_path: str | None = None
    touched_paths: tuple[str, ...] = ()
    convergence_status: str | None = None
    convergence_stopped_reason: str | None = None
    scheduler_rounds_run: int | None = None
    max_rounds: int | None = None
    projection_only: bool = True
    typed_receipt_path_available: bool = False
    authoritative: bool = False
    migration_blocker: str = _DEFAULT_CALLBACK_RECEIPT_MIGRATION_BLOCKER
    revalidation_evidence_present: bool = False
    revalidation_command: tuple[str, ...] = ()
    revalidation_exit_code: int | None = None
    revalidation_residual_count: int | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "schema_version",
            _require_non_empty("schema_version", self.schema_version or _CALLBACK_RECEIPT_PROJECTION_SCHEMA_VERSION),
        )
        object.__setattr__(self, "projection_id", _optional_non_empty_str(self.projection_id))
        receipt_authority = _optional_non_empty_str(self.receipt_authority)
        if receipt_authority not in _ALLOWED_CALLBACK_RECEIPT_AUTHORITIES:
            receipt_authority = _DEFAULT_CALLBACK_RECEIPT_AUTHORITY
        object.__setattr__(self, "receipt_authority", receipt_authority)
        object.__setattr__(self, "schedule_kind", _optional_non_empty_str(self.schedule_kind))
        object.__setattr__(self, "step_id", _optional_non_empty_str(self.step_id))
        object.__setattr__(self, "source_tool", _optional_non_empty_str(self.source_tool))
        object.__setattr__(self, "scheduled_source_tool", _optional_non_empty_str(self.scheduled_source_tool))
        object.__setattr__(self, "scheduled_source_tool_kind", _optional_non_empty_str(self.scheduled_source_tool_kind))
        object.__setattr__(
            self,
            "scheduled_source_tool_executable_runtime",
            bool(self.scheduled_source_tool_executable_runtime),
        )
        object.__setattr__(self, "callback_source_tool", _optional_non_empty_str(self.callback_source_tool))
        adapter_source_tool = _optional_non_empty_str(self.adapter_source_tool) or _optional_non_empty_str(
            self.callback_source_tool
        )
        object.__setattr__(self, "adapter_source_tool", adapter_source_tool)
        object.__setattr__(self, "round_number", _optional_non_negative_int(self.round_number))
        object.__setattr__(self, "tool_name", _optional_non_empty_str(self.tool_name))
        object.__setattr__(self, "touched_path", _optional_non_empty_str(self.touched_path))
        touched_paths = _to_tuple_str_from_any(self.touched_paths)
        if self.touched_path and self.touched_path not in touched_paths:
            touched_paths = (self.touched_path, *touched_paths)
        object.__setattr__(self, "touched_paths", touched_paths)
        object.__setattr__(self, "convergence_status", _optional_non_empty_str(self.convergence_status))
        object.__setattr__(
            self,
            "convergence_stopped_reason",
            _optional_non_empty_str(self.convergence_stopped_reason),
        )
        object.__setattr__(self, "scheduler_rounds_run", _optional_non_negative_int(self.scheduler_rounds_run))
        object.__setattr__(self, "max_rounds", _optional_non_negative_int(self.max_rounds))
        claimed_typed_receipt_path_available = _strict_bool_claim(self.typed_receipt_path_available)
        object.__setattr__(self, "projection_only", True)
        object.__setattr__(self, "typed_receipt_path_available", False)
        object.__setattr__(self, "authoritative", False)
        object.__setattr__(
            self,
            "migration_blocker",
            _optional_non_empty_str(self.migration_blocker) or _DEFAULT_CALLBACK_RECEIPT_MIGRATION_BLOCKER,
        )
        object.__setattr__(self, "revalidation_evidence_present", bool(self.revalidation_evidence_present))
        object.__setattr__(self, "revalidation_command", _to_tuple_str_from_any(self.revalidation_command))
        object.__setattr__(self, "revalidation_exit_code", _optional_non_negative_int(self.revalidation_exit_code))
        object.__setattr__(
            self,
            "revalidation_residual_count",
            _optional_non_negative_int(self.revalidation_residual_count),
        )
        metadata = _to_dict_copy(self.metadata)
        if claimed_typed_receipt_path_available:
            metadata.setdefault("claimed_typed_receipt_path_available", True)
        object.__setattr__(self, "metadata", metadata)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "projection_id": self.projection_id,
            "receipt_authority": self.receipt_authority,
            "schedule_kind": self.schedule_kind,
            "step_id": self.step_id,
            "source_tool": self.source_tool,
            "scheduled_source_tool": self.scheduled_source_tool,
            "scheduled_source_tool_kind": self.scheduled_source_tool_kind,
            "scheduled_source_tool_executable_runtime": self.scheduled_source_tool_executable_runtime,
            "callback_source_tool": self.callback_source_tool,
            "adapter_source_tool": self.adapter_source_tool,
            "round_number": self.round_number,
            "tool_name": self.tool_name,
            "touched_path": self.touched_path,
            "touched_paths": list(self.touched_paths),
            "convergence_status": self.convergence_status,
            "convergence_stopped_reason": self.convergence_stopped_reason,
            "scheduler_rounds_run": self.scheduler_rounds_run,
            "max_rounds": self.max_rounds,
            "projection_only": True,
            "typed_receipt_path_available": False,
            "authoritative": False,
            "migration_blocker": self.migration_blocker,
            "revalidation_evidence_present": self.revalidation_evidence_present,
            "revalidation_command": list(self.revalidation_command),
            "revalidation_exit_code": self.revalidation_exit_code,
            "revalidation_residual_count": self.revalidation_residual_count,
            "metadata": dict(self.metadata),
        }


def _callback_receipt_projection_v1(
    value: DirectorRepairCallbackReceiptProjectionV1 | Mapping[str, Any],
) -> DirectorRepairCallbackReceiptProjectionV1:
    if isinstance(value, DirectorRepairCallbackReceiptProjectionV1):
        return value
    payload = dict(value or {})
    known_fields = DirectorRepairCallbackReceiptProjectionV1.__dataclass_fields__
    constructor_payload = {key: payload[key] for key in known_fields if key in payload}
    extra_fields = {key: payload[key] for key in payload if key not in known_fields}
    metadata = dict(constructor_payload.get("metadata") or {})
    if extra_fields:
        metadata.setdefault("extra_projection_fields", extra_fields)
    constructor_payload["metadata"] = metadata
    return DirectorRepairCallbackReceiptProjectionV1(**constructor_payload)


@dataclass(frozen=True)
class DirectorRepairPostExecutionScheduleRunResultV1:
    """Projection-only result from running post-execution callback schedule."""

    schema_version: str
    source: str
    ordered_steps: tuple[DirectorRepairPostExecutionStepV1, ...] = ()
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
