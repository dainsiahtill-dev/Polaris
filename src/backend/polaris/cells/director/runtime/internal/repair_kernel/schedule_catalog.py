"""Runtime-owned schedule catalogs for deterministic repair migration."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from .scheduler import CONVERGENCE_PIPELINE_ORDER, convergence_envelope_metadata

DEFAULT_REPAIR_SCHEDULE_MAX_ROUNDS = 1
_MAX_REPAIR_SCHEDULE_MAX_ROUNDS = 10
_ADAPTER_RECEIPT_PROJECTION_SCHEMA_VERSION = "director.repair_adapter_receipt_projection.v1"
_ADAPTER_RECEIPT_PROJECTION_AUTHORITY = "non_authoritative_adapter_projection"
_ADAPTER_RECEIPT_PROJECTION_MIGRATION_BLOCKER = (
    "adapter schedule runners still return tool_results instead of RepairReceipt"
)
_CALLBACK_RECEIPT_PROJECTION_SCHEMA_VERSION = _ADAPTER_RECEIPT_PROJECTION_SCHEMA_VERSION
_CALLBACK_RECEIPT_PROJECTION_AUTHORITY = _ADAPTER_RECEIPT_PROJECTION_AUTHORITY
_CALLBACK_RECEIPT_PROJECTION_MIGRATION_BLOCKER = _ADAPTER_RECEIPT_PROJECTION_MIGRATION_BLOCKER
_CANONICAL_CONVERGENCE_EXECUTOR = "RepairConvergenceScheduler"
_FINAL_TYPED_RECEIPT_ENTRYPOINT = "run_runtime_repair_convergence"
_CALLBACK_ROUND_ACCOUNTING_FIELDS = ("max_rounds", "rounds_run", "convergence_status", "stopped_reason")
_SOURCE_TOOL_KIND_EXECUTABLE_RUNTIME = "executable_runtime"
_SOURCE_TOOL_KIND_CALLBACK_SCHEDULE_LABEL = "callback_schedule_label"
_ALLOWED_SOURCE_TOOL_KINDS = frozenset(
    {
        _SOURCE_TOOL_KIND_EXECUTABLE_RUNTIME,
        _SOURCE_TOOL_KIND_CALLBACK_SCHEDULE_LABEL,
    }
)


def _non_empty(value: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError("repair schedule field must be non-empty")
    return normalized


def _source_tool_kind(value: str) -> str:
    normalized = _non_empty(value)
    if normalized not in _ALLOWED_SOURCE_TOOL_KINDS:
        raise ValueError(f"repair schedule source_tool_kind must be one of {sorted(_ALLOWED_SOURCE_TOOL_KINDS)}")
    return normalized


@dataclass(frozen=True)
class PostExecutionRepairScheduleStep:
    """Internal scheduling metadata for one post-execution repair step."""

    step_id: str
    language: str
    phase: str
    priority: int
    source_tool: str
    source_tool_kind: str = _SOURCE_TOOL_KIND_EXECUTABLE_RUNTIME
    runtime_source_tools: tuple[str, ...] = ()
    depends_on: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "step_id", _non_empty(self.step_id))
        object.__setattr__(self, "language", _non_empty(self.language))
        object.__setattr__(self, "phase", _non_empty(self.phase))
        object.__setattr__(self, "priority", max(0, int(self.priority)))
        object.__setattr__(self, "source_tool", _non_empty(self.source_tool))
        object.__setattr__(self, "source_tool_kind", _source_tool_kind(self.source_tool_kind))
        runtime_source_tools = tuple(
            _non_empty(item) for item in self.runtime_source_tools if str(item or "").strip()
        ) or (self.source_tool,)
        object.__setattr__(self, "runtime_source_tools", runtime_source_tools)
        object.__setattr__(self, "depends_on", tuple(str(item) for item in self.depends_on if str(item or "").strip()))

    @property
    def executable_runtime_source_tool(self) -> bool:
        return self.source_tool_kind == _SOURCE_TOOL_KIND_EXECUTABLE_RUNTIME

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
class MaterializationQualityRepairScheduleStep:
    """Internal scheduling metadata for one materialization-quality repair step."""

    step_id: str
    language: str
    phase: str
    priority: int
    source_tool: str
    source_tool_kind: str = _SOURCE_TOOL_KIND_CALLBACK_SCHEDULE_LABEL
    runtime_source_tools: tuple[str, ...] = ()
    depends_on: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "step_id", _non_empty(self.step_id))
        object.__setattr__(self, "language", _non_empty(self.language))
        object.__setattr__(self, "phase", _non_empty(self.phase))
        object.__setattr__(self, "priority", max(0, int(self.priority)))
        object.__setattr__(self, "source_tool", _non_empty(self.source_tool))
        object.__setattr__(self, "source_tool_kind", _source_tool_kind(self.source_tool_kind))
        object.__setattr__(
            self,
            "runtime_source_tools",
            tuple(_non_empty(item) for item in self.runtime_source_tools if str(item or "").strip()),
        )
        object.__setattr__(self, "depends_on", tuple(str(item) for item in self.depends_on if str(item or "").strip()))

    @property
    def executable_runtime_source_tool(self) -> bool:
        return self.source_tool_kind == _SOURCE_TOOL_KIND_EXECUTABLE_RUNTIME

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


PostExecutionStepRunner = Callable[[PostExecutionRepairScheduleStep], Sequence[Mapping[str, Any]]]
MaterializationQualityStepRunner = Callable[[MaterializationQualityRepairScheduleStep], Sequence[Mapping[str, Any]]]


@dataclass(frozen=True)
class PostExecutionRepairScheduleRun:
    """Result of invoking migration runner callbacks through the runtime schedule."""

    ordered_steps: tuple[PostExecutionRepairScheduleStep, ...]
    tool_results: tuple[dict[str, Any], ...] = ()
    max_rounds: int = DEFAULT_REPAIR_SCHEDULE_MAX_ROUNDS
    rounds_run: int = 0
    convergence_status: str = "not_started"
    stopped_reason: str = "not_started"

    def __post_init__(self) -> None:
        object.__setattr__(self, "ordered_steps", tuple(self.ordered_steps or ()))
        object.__setattr__(self, "tool_results", tuple(dict(item or {}) for item in self.tool_results))
        object.__setattr__(self, "max_rounds", _coerce_max_rounds(self.max_rounds))
        object.__setattr__(self, "rounds_run", max(0, int(self.rounds_run)))
        object.__setattr__(self, "convergence_status", _non_empty(self.convergence_status))
        object.__setattr__(self, "stopped_reason", _non_empty(self.stopped_reason))

    def to_dict(self) -> dict[str, Any]:
        receipt_projections = _project_callback_schedule_receipts(
            tool_results=self.tool_results,
            schedule_kind="post_execution",
            ordered_steps=self.ordered_steps,
            max_rounds=self.max_rounds,
            rounds_run=self.rounds_run,
            convergence_status=self.convergence_status,
            stopped_reason=self.stopped_reason,
        )
        return {
            "ordered_steps": [step.to_dict() for step in self.ordered_steps],
            "tool_results": [dict(item) for item in self.tool_results],
            "receipt_projections": receipt_projections,
            "max_rounds": self.max_rounds,
            "rounds_run": self.rounds_run,
            "convergence_status": self.convergence_status,
            "stopped_reason": self.stopped_reason,
            "summary": _callback_schedule_summary(
                schedule_kind="post_execution",
                ordered_steps=self.ordered_steps,
                max_rounds=self.max_rounds,
                rounds_run=self.rounds_run,
                convergence_status=self.convergence_status,
                stopped_reason=self.stopped_reason,
                receipt_projection_count=len(receipt_projections),
                receipt_projections=receipt_projections,
            ),
        }


@dataclass(frozen=True)
class MaterializationQualityRepairScheduleRun:
    """Result of invoking materialization-quality callbacks through the runtime schedule."""

    ordered_steps: tuple[MaterializationQualityRepairScheduleStep, ...]
    tool_results: tuple[dict[str, Any], ...] = ()
    max_rounds: int = DEFAULT_REPAIR_SCHEDULE_MAX_ROUNDS
    rounds_run: int = 0
    convergence_status: str = "not_started"
    stopped_reason: str = "not_started"

    def __post_init__(self) -> None:
        object.__setattr__(self, "ordered_steps", tuple(self.ordered_steps or ()))
        object.__setattr__(self, "tool_results", tuple(dict(item or {}) for item in self.tool_results))
        object.__setattr__(self, "max_rounds", _coerce_max_rounds(self.max_rounds))
        object.__setattr__(self, "rounds_run", max(0, int(self.rounds_run)))
        object.__setattr__(self, "convergence_status", _non_empty(self.convergence_status))
        object.__setattr__(self, "stopped_reason", _non_empty(self.stopped_reason))

    def to_dict(self) -> dict[str, Any]:
        receipt_projections = _project_callback_schedule_receipts(
            tool_results=self.tool_results,
            schedule_kind="materialization_quality",
            ordered_steps=self.ordered_steps,
            max_rounds=self.max_rounds,
            rounds_run=self.rounds_run,
            convergence_status=self.convergence_status,
            stopped_reason=self.stopped_reason,
        )
        return {
            "ordered_steps": [step.to_dict() for step in self.ordered_steps],
            "tool_results": [dict(item) for item in self.tool_results],
            "receipt_projections": receipt_projections,
            "max_rounds": self.max_rounds,
            "rounds_run": self.rounds_run,
            "convergence_status": self.convergence_status,
            "stopped_reason": self.stopped_reason,
            "summary": _callback_schedule_summary(
                schedule_kind="materialization_quality",
                ordered_steps=self.ordered_steps,
                max_rounds=self.max_rounds,
                rounds_run=self.rounds_run,
                convergence_status=self.convergence_status,
                stopped_reason=self.stopped_reason,
                receipt_projection_count=len(receipt_projections),
                receipt_projections=receipt_projections,
            ),
        }


_POST_EXECUTION_REPAIR_SCHEDULE: tuple[PostExecutionRepairScheduleStep, ...] = (
    PostExecutionRepairScheduleStep(
        step_id="go.module_import",
        language="go",
        phase="dependency_resolution",
        priority=0,
        source_tool="deterministic_go_module_import_repair",
    ),
    PostExecutionRepairScheduleStep(
        step_id="rust.dependency_resolution",
        language="rust",
        phase="dependency_resolution",
        priority=0,
        source_tool="deterministic_rust_dependency_repair",
    ),
    PostExecutionRepairScheduleStep(
        step_id="rust.post_execution_convergence",
        language="rust",
        phase="multi_phase_convergence",
        priority=0,
        source_tool="deterministic_rust_post_repair",
        source_tool_kind=_SOURCE_TOOL_KIND_CALLBACK_SCHEDULE_LABEL,
    ),
    PostExecutionRepairScheduleStep(
        step_id="cpp.post_execution",
        language="cpp",
        phase="post_execution",
        priority=1,
        source_tool="deterministic_cpp_post_repair",
    ),
    PostExecutionRepairScheduleStep(
        step_id="java.post_execution",
        language="java",
        phase="post_execution",
        priority=1,
        source_tool="deterministic_java_post_repair",
    ),
)

_MATERIALIZATION_RUST_RUNTIME_SOURCE_TOOLS = (
    "deterministic_rust_crate_import_rewrite_repair",
    "deterministic_rust_dependency_repair",
    "deterministic_rust_missing_lib_target_repair",
    "deterministic_rust_missing_module_file_repair",
    "deterministic_rust_duplicate_module_file_repair",
    "deterministic_rust_lib_root_facade_repair",
    "deterministic_rust_serde_derive_repair",
    "deterministic_rust_line_suggestion_repair",
    "deterministic_rust_unresolved_pub_use_repair",
    "deterministic_rust_trait_import_repair",
)
_MATERIALIZATION_TYPESCRIPT_COMPILER_RUNTIME_SOURCE_TOOLS = (
    "deterministic_typescript_canvas_scale_return_type_repair",
    "deterministic_typescript_commonjs_package_type_repair",
    "deterministic_typescript_duplicate_object_property_repair",
    "deterministic_typescript_entrypoint_repair",
    "deterministic_typescript_enum_member_separator_repair",
    "deterministic_typescript_escaped_newline_repair",
    "deterministic_typescript_hyphenated_identifier_repair",
    "deterministic_typescript_member_alias_repair",
    "deterministic_typescript_missing_closing_brace_repair",
    "deterministic_typescript_missing_export_repair",
    "deterministic_typescript_missing_member_repair",
    "deterministic_typescript_nullable_canvas_context_repair",
    "deterministic_typescript_number_to_string_argument_repair",
    "deterministic_typescript_readonly_assignment_repair",
    "deterministic_typescript_reexport_repair",
    "deterministic_typescript_reexported_type_binding_repair",
    "deterministic_typescript_relative_import_case_repair",
    "deterministic_typescript_return_object_semicolon_repair",
    "deterministic_typescript_sourcefile_diagnostics_repair",
    "deterministic_typescript_too_few_arguments_repair",
    "deterministic_typescript_tsconfig_lib_repair",
    "deterministic_typescript_unknown_member_access_repair",
    "deterministic_typescript_uninitialized_property_repair",
    "deterministic_typescript_unique_export_import_repair",
    "deterministic_typescript_unresolved_identifier_repair",
    "deterministic_typescript_unused_import_repair",
    "deterministic_typescript_vitest_globals_repair",
    "deterministic_typescript_zod_type_class_collision_repair",
    "deterministic_javascript_typescript_annotation_repair",
    "deterministic_javascript_missing_export_repair",
    "deterministic_javascript_esm_commonjs_entrypoint_repair",
    "deterministic_javascript_dom_global_runtime_guard_repair",
    "deterministic_javascript_missing_method_runtime_repair",
)
_MATERIALIZATION_HTML_RUNTIME_SOURCE_TOOLS = ("deterministic_html_typescript_module_script_repair",)
_MATERIALIZATION_GO_RUNTIME_SOURCE_TOOLS = (
    "deterministic_go_bare_import_string_repair",
    "deterministic_go_nested_import_repair",
    "deterministic_go_module_import_repair",
    "deterministic_go_bare_import_repair",
    "deterministic_go_subpath_repair",
    "deterministic_go_unused_import_repair",
    "deterministic_go_error_string_helper_repair",
    "deterministic_go_dedup_repair",
)
_MATERIALIZATION_PYTHON_RUNTIME_SOURCE_TOOLS = (
    "deterministic_python_unittest_runtime_failure_repair",
    "deterministic_python_package_shadow_bridge_repair",
    "deterministic_unresolved_import_symbol_repair",
)
_MATERIALIZATION_HYGIENE_RUNTIME_SOURCE_TOOLS = (
    "deterministic_scaffold_marker_cleanup",
    "deterministic_scaffold_marker_quality_cleanup",
)
_MATERIALIZATION_TYPESCRIPT_SCAFFOLD_RUNTIME_SOURCE_TOOLS = (
    "deterministic_typescript_scaffold_repair",
    "deterministic_typeorm_model_normalization_repair",
)
_MATERIALIZATION_NODE_MANIFEST_RUNTIME_SOURCE_TOOLS = (
    "deterministic_node_test_script_contract_repair",
    "deterministic_npm_script_contract_repair",
    "deterministic_runtime_dependency_repair",
)
_MATERIALIZATION_TARGET_RUNTIME_SOURCE_TOOLS = (
    "deterministic_javascript_test_missing_target_repair",
    "deterministic_javascript_typescript_annotation_repair",
    "deterministic_javascript_missing_export_repair",
    "deterministic_javascript_esm_commonjs_entrypoint_repair",
    "deterministic_javascript_dom_global_runtime_guard_repair",
    "deterministic_javascript_missing_method_runtime_repair",
    "deterministic_typescript_html_container_selector_repair",
)


_MATERIALIZATION_QUALITY_REPAIR_SCHEDULE: tuple[MaterializationQualityRepairScheduleStep, ...] = (
    MaterializationQualityRepairScheduleStep(
        step_id="materialization.hygiene_scaffold",
        language="multi",
        phase="hygiene",
        priority=0,
        source_tool="deterministic_materialization_hygiene_repair",
        runtime_source_tools=_MATERIALIZATION_HYGIENE_RUNTIME_SOURCE_TOOLS,
    ),
    MaterializationQualityRepairScheduleStep(
        step_id="materialization.typescript_scaffold",
        language="typescript",
        phase="scaffold",
        priority=10,
        source_tool="deterministic_typescript_scaffold_materialization_repair",
        runtime_source_tools=_MATERIALIZATION_TYPESCRIPT_SCAFFOLD_RUNTIME_SOURCE_TOOLS,
        depends_on=("materialization.hygiene_scaffold",),
    ),
    MaterializationQualityRepairScheduleStep(
        step_id="materialization.typescript_compiler",
        language="typescript",
        phase="compiler",
        priority=20,
        source_tool="deterministic_typescript_materialization_repair",
        runtime_source_tools=_MATERIALIZATION_TYPESCRIPT_COMPILER_RUNTIME_SOURCE_TOOLS,
        depends_on=("materialization.typescript_scaffold",),
    ),
    MaterializationQualityRepairScheduleStep(
        step_id="materialization.html_entrypoint",
        language="html",
        phase="entrypoint",
        priority=25,
        source_tool="deterministic_html_typescript_module_script_repair",
        runtime_source_tools=_MATERIALIZATION_HTML_RUNTIME_SOURCE_TOOLS,
        depends_on=("materialization.typescript_compiler",),
    ),
    MaterializationQualityRepairScheduleStep(
        step_id="materialization.node_manifest",
        language="javascript",
        phase="manifest",
        priority=30,
        source_tool="deterministic_node_manifest_materialization_repair",
        runtime_source_tools=_MATERIALIZATION_NODE_MANIFEST_RUNTIME_SOURCE_TOOLS,
        depends_on=("materialization.html_entrypoint",),
    ),
    MaterializationQualityRepairScheduleStep(
        step_id="materialization.rust_compiler",
        language="rust",
        phase="compiler",
        priority=40,
        source_tool="deterministic_rust_materialization_repair",
        runtime_source_tools=_MATERIALIZATION_RUST_RUNTIME_SOURCE_TOOLS,
        depends_on=("materialization.node_manifest",),
    ),
    MaterializationQualityRepairScheduleStep(
        step_id="materialization.target_runtime",
        language="multi",
        phase="runtime_smoke",
        priority=50,
        source_tool="deterministic_target_runtime_materialization_repair",
        runtime_source_tools=_MATERIALIZATION_TARGET_RUNTIME_SOURCE_TOOLS,
        depends_on=("materialization.rust_compiler",),
    ),
    MaterializationQualityRepairScheduleStep(
        step_id="materialization.python_import",
        language="python",
        phase="compiler",
        priority=60,
        source_tool="deterministic_python_materialization_repair",
        runtime_source_tools=_MATERIALIZATION_PYTHON_RUNTIME_SOURCE_TOOLS,
        depends_on=("materialization.target_runtime",),
    ),
    MaterializationQualityRepairScheduleStep(
        step_id="materialization.go_import",
        language="go",
        phase="dependency_resolution",
        priority=70,
        source_tool="deterministic_go_materialization_repair",
        runtime_source_tools=_MATERIALIZATION_GO_RUNTIME_SOURCE_TOOLS,
        depends_on=("materialization.python_import",),
    ),
)


def post_execution_repair_schedule() -> tuple[PostExecutionRepairScheduleStep, ...]:
    """Return the runtime-owned dependency-aware post-execution repair schedule."""

    return _ordered_post_execution_schedule_steps(_POST_EXECUTION_REPAIR_SCHEDULE)


def materialization_quality_repair_schedule() -> tuple[MaterializationQualityRepairScheduleStep, ...]:
    """Return the runtime-owned materialization-quality repair schedule."""

    return _ordered_materialization_quality_schedule_steps(_MATERIALIZATION_QUALITY_REPAIR_SCHEDULE)


def _callback_schedule_summary(
    *,
    schedule_kind: str,
    ordered_steps: Sequence[PostExecutionRepairScheduleStep] | Sequence[MaterializationQualityRepairScheduleStep],
    max_rounds: int,
    rounds_run: int,
    convergence_status: str,
    stopped_reason: str,
    receipt_projection_count: int = 0,
    receipt_projections: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    evidence_summary = _callback_projection_evidence_summary(receipt_projections)
    source_tool_kind_counts: dict[str, int] = {}
    executable_runtime_source_tools: list[str] = []
    callback_schedule_label_source_tools: list[str] = []
    for step in ordered_steps:
        source_tool_kind_counts[step.source_tool_kind] = source_tool_kind_counts.get(step.source_tool_kind, 0) + 1
        if step.executable_runtime_source_tool:
            executable_runtime_source_tools.append(step.source_tool)
        else:
            callback_schedule_label_source_tools.append(step.source_tool)
    return {
        **convergence_envelope_metadata(
            preferred_entrypoint="run_runtime_repair_convergence",
            typed_receipt_path_available=False,
            callback_migration_envelope=True,
        ),
        "schedule_kind": schedule_kind,
        "step_count": len(tuple(ordered_steps or ())),
        "ordered_step_ids": [step.step_id for step in ordered_steps],
        "source_tools": [step.source_tool for step in ordered_steps],
        "source_tool_kinds": [step.source_tool_kind for step in ordered_steps],
        "source_tool_kind_counts": dict(sorted(source_tool_kind_counts.items())),
        "executable_runtime_source_tools": executable_runtime_source_tools,
        "callback_schedule_label_source_tools": callback_schedule_label_source_tools,
        "max_rounds": _coerce_max_rounds(max_rounds),
        "rounds_run": max(0, int(rounds_run)),
        "convergence_status": convergence_status,
        "stopped_reason": stopped_reason,
        "adapter_projection_bridge": True,
        "adapter_bridge_uses_repair_convergence_scheduler": False,
        "callback_bridge_uses_repair_convergence_scheduler": False,
        "typed_convergence_scheduler_cutover_required": True,
        "adapter_runner_self_loop_allowed": False,
        "callback_runner_self_loop_allowed": False,
        "bounded_round_accounting_visible": True,
        "round_accounting_fields": list(_CALLBACK_ROUND_ACCOUNTING_FIELDS),
        "receipt_projection_count": max(0, int(receipt_projection_count)),
        "adapter_receipt_projection_available": receipt_projection_count > 0,
        "callback_receipt_projection_available": receipt_projection_count > 0,
        "native_receipt_count": 0,
        "post_check_evidence_complete": False,
        "native_post_check_evidence_complete": False,
        "missing_native_revalidation_evidence": receipt_projection_count > 0,
        "adapter_projection_not_authoritative": True,
        "non_authoritative_projection": True,
        "cutover_ready": False,
        "cutover_blockers": [
            "missing_native_revalidation_evidence",
            "adapter_projection_not_authoritative_receipt",
        ],
        **evidence_summary,
        "adapter_callback_bridge": False,
        "migration_callback_envelope": False,
        "adapter_projection_envelope": True,
        "runner_binding_owner": "roles.adapters",
        "produces_tool_results_only": True,
        "final_typed_receipt_path": _FINAL_TYPED_RECEIPT_ENTRYPOINT,
        "typed_receipt_path": "unavailable_in_callback_bridge",
        "migration_blocker": _CALLBACK_RECEIPT_PROJECTION_MIGRATION_BLOCKER,
    }


def _callback_projection_evidence_summary(receipt_projections: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    evidence_status_counts = {
        "missing_evidence": 0,
        "failed_evidence": 0,
        "resolved_evidence": 0,
    }
    projection_ids: dict[str, list[str]] = {
        "missing_evidence": [],
        "failed_evidence": [],
        "resolved_evidence": [],
    }
    source_tools: dict[str, set[str]] = {
        "missing_evidence": set(),
        "failed_evidence": set(),
        "resolved_evidence": set(),
    }
    for projection in receipt_projections:
        status = _callback_projection_evidence_status(projection)
        evidence_status_counts[status] = evidence_status_counts.get(status, 0) + 1
        projection_id = _first_non_empty(projection.get("projection_id"))
        source_tool = _first_non_empty(projection.get("source_tool"))
        if projection_id:
            projection_ids.setdefault(status, []).append(projection_id)
        if source_tool:
            source_tools.setdefault(status, set()).add(source_tool)

    return {
        "evidence_status_counts": dict(sorted(evidence_status_counts.items())),
        "callback_projection_evidence_status_counts": dict(sorted(evidence_status_counts.items())),
        "missing_evidence_receipt_ids": [],
        "missing_evidence_source_tools": [],
        "failed_evidence_receipt_ids": [],
        "failed_evidence_source_tools": [],
        "resolved_evidence_receipt_ids": [],
        "resolved_evidence_source_tools": [],
        "missing_evidence_projection_ids": sorted(projection_ids.get("missing_evidence", ())),
        "missing_evidence_projection_source_tools": sorted(source_tools.get("missing_evidence", ())),
        "failed_evidence_projection_ids": sorted(projection_ids.get("failed_evidence", ())),
        "failed_evidence_projection_source_tools": sorted(source_tools.get("failed_evidence", ())),
        "resolved_evidence_projection_ids": sorted(projection_ids.get("resolved_evidence", ())),
        "resolved_evidence_projection_source_tools": sorted(source_tools.get("resolved_evidence", ())),
    }


def _callback_projection_evidence_status(projection: Mapping[str, Any]) -> str:
    if not bool(projection.get("revalidation_evidence_present")):
        return "missing_evidence"
    exit_code = _optional_int(projection.get("revalidation_exit_code"))
    residual_count = _optional_int(projection.get("revalidation_residual_count"))
    if exit_code is None:
        return "missing_evidence"
    if exit_code != 0 or (residual_count is not None and residual_count > 0):
        return "failed_evidence"
    return "resolved_evidence"


def _project_callback_schedule_receipts(
    *,
    tool_results: Sequence[Mapping[str, Any]],
    schedule_kind: str,
    ordered_steps: Sequence[PostExecutionRepairScheduleStep] | Sequence[MaterializationQualityRepairScheduleStep],
    max_rounds: int,
    rounds_run: int,
    convergence_status: str,
    stopped_reason: str,
) -> list[dict[str, Any]]:
    steps_by_id = {step.step_id: step for step in ordered_steps}
    projections: list[dict[str, Any]] = []
    for index, tool_result in enumerate(tool_results):
        result = tool_result.get("result")
        payload: Mapping[str, Any] = result if isinstance(result, Mapping) else {}
        step_id = _first_non_empty(
            payload.get("bridge_step_id"),
            payload.get("step_id"),
            payload.get("scheduler_step_id"),
            tool_result.get("bridge_step_id"),
            tool_result.get("step_id"),
        )
        step = steps_by_id.get(step_id or "")
        scheduled_source_tool = step.source_tool if step is not None else None
        scheduled_source_tool_kind = step.source_tool_kind if step is not None else None
        scheduled_source_tool_executable_runtime = step.executable_runtime_source_tool if step is not None else False
        callback_source_tool = _first_non_empty(payload.get("source_tool"), tool_result.get("source_tool"))
        source_tool = callback_source_tool or scheduled_source_tool
        round_number = _first_int(payload.get("round_number"), payload.get("scheduler_round_number"))
        touched_paths = _extract_callback_touched_paths(payload=payload, tool_result=tool_result)
        revalidation_projection = _project_callback_revalidation(payload)

        projections.append(
            {
                "schema_version": _CALLBACK_RECEIPT_PROJECTION_SCHEMA_VERSION,
                "projection_id": _callback_receipt_projection_id(
                    schedule_kind=schedule_kind,
                    step_id=step_id,
                    round_number=round_number,
                    index=index,
                ),
                "receipt_authority": _CALLBACK_RECEIPT_PROJECTION_AUTHORITY,
                "schedule_kind": schedule_kind,
                "step_id": step_id,
                "source_tool": source_tool,
                "scheduled_source_tool": scheduled_source_tool,
                "scheduled_source_tool_kind": scheduled_source_tool_kind,
                "scheduled_source_tool_executable_runtime": scheduled_source_tool_executable_runtime,
                "callback_source_tool": callback_source_tool,
                "adapter_source_tool": callback_source_tool,
                "round_number": round_number,
                "tool_name": _first_non_empty(
                    tool_result.get("tool_name"),
                    tool_result.get("tool"),
                    payload.get("tool_name"),
                    payload.get("tool"),
                ),
                "touched_path": touched_paths[0] if touched_paths else None,
                "touched_paths": touched_paths,
                "convergence_status": convergence_status,
                "convergence_stopped_reason": stopped_reason,
                "scheduler_rounds_run": max(0, int(rounds_run)),
                "max_rounds": _coerce_max_rounds(max_rounds),
                "projection_only": True,
                "typed_receipt_path_available": False,
                "authoritative": False,
                "canonical_convergence_executor": _CANONICAL_CONVERGENCE_EXECUTOR,
                "pipeline_order": CONVERGENCE_PIPELINE_ORDER,
                "hidden_language_loop_allowed": False,
                "language_self_loop_allowed": False,
                "adapter_runner_self_loop_allowed": False,
                "callback_runner_self_loop_allowed": False,
                "typed_convergence_scheduler_cutover_required": True,
                "preferred_typed_receipt_entrypoint": _FINAL_TYPED_RECEIPT_ENTRYPOINT,
                "migration_blocker": _CALLBACK_RECEIPT_PROJECTION_MIGRATION_BLOCKER,
                **revalidation_projection,
            }
        )
    return projections


def _callback_receipt_projection_id(
    *,
    schedule_kind: str,
    step_id: str | None,
    round_number: int | None,
    index: int,
) -> str:
    normalized_step_id = step_id or "unknown_step"
    normalized_round = str(round_number) if round_number is not None else "unknown_round"
    return f"{schedule_kind}:{normalized_step_id}:round-{normalized_round}:tool-result-{index}"


def _first_non_empty(*values: Any) -> str | None:
    for value in values:
        if value is None:
            continue
        normalized = str(value).strip()
        if normalized:
            return normalized
    return None


def _first_int(*values: Any) -> int | None:
    for value in values:
        normalized = _optional_int(value)
        if normalized is not None:
            return normalized
    return None


def _optional_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return None


def _extract_callback_touched_paths(
    *,
    payload: Mapping[str, Any],
    tool_result: Mapping[str, Any],
) -> list[str]:
    touched_paths: list[str] = []
    for source in (payload, tool_result):
        for key in ("file", "path", "target_path", "touched_path", "changed_path"):
            _extend_touched_paths(touched_paths, source.get(key))
        for key in ("files", "paths", "touched_paths", "files_changed", "changed_paths"):
            _extend_touched_paths(touched_paths, source.get(key))
    return touched_paths


def _extend_touched_paths(touched_paths: list[str], value: Any) -> None:
    if value is None:
        return
    if isinstance(value, Mapping):
        for path in value:
            _extend_touched_paths(touched_paths, path)
        return
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for path in value:
            _extend_touched_paths(touched_paths, path)
        return
    normalized = str(value).strip()
    if normalized and normalized not in touched_paths:
        touched_paths.append(normalized)


def _project_callback_revalidation(payload: Mapping[str, Any]) -> dict[str, Any]:
    revalidation = payload.get("revalidation")
    if not isinstance(revalidation, Mapping):
        revalidation = payload.get("revalidation_evidence")
    if not isinstance(revalidation, Mapping):
        return {
            "revalidation_evidence_present": False,
            "revalidation_exit_code": None,
            "revalidation_residual_count": None,
        }

    residual_count = _first_int(
        revalidation.get("revalidation_residual_count"),
        revalidation.get("residual_count"),
        revalidation.get("residual_diagnostic_count"),
    )
    residual_diagnostic_ids = revalidation.get("residual_diagnostic_ids")
    if (
        residual_count is None
        and isinstance(residual_diagnostic_ids, Sequence)
        and not isinstance(
            residual_diagnostic_ids,
            (str, bytes, bytearray),
        )
    ):
        residual_count = len(residual_diagnostic_ids)
    if residual_count is None:
        residual_count = _first_int(revalidation.get("errors_after"), revalidation.get("errors_after_count"))

    return {
        "revalidation_evidence_present": True,
        "revalidation_exit_code": _first_int(revalidation.get("exit_code"), revalidation.get("revalidation_exit_code")),
        "revalidation_residual_count": residual_count,
    }


def run_post_execution_repair_schedule_callbacks(
    *,
    runner_step_ids: Sequence[str],
    runner: PostExecutionStepRunner,
    max_rounds: int = DEFAULT_REPAIR_SCHEDULE_MAX_ROUNDS,
) -> PostExecutionRepairScheduleRun:
    """Run migration callbacks in runtime-owned schedule order with bounded convergence."""

    ordered_steps = post_execution_repair_schedule()
    _validate_runner_bindings(ordered_steps=ordered_steps, runner_step_ids=runner_step_ids)
    tool_results: list[dict[str, Any]] = []
    rounds_run, convergence_status, stopped_reason = _run_scheduled_repair_rounds(
        ordered_steps=ordered_steps,
        runner=runner,
        max_rounds=max_rounds,
        tool_results=tool_results,
    )
    _annotate_convergence_result(
        tool_results,
        max_rounds=_coerce_max_rounds(max_rounds),
        rounds_run=rounds_run,
        convergence_status=convergence_status,
        stopped_reason=stopped_reason,
    )
    return PostExecutionRepairScheduleRun(
        ordered_steps=ordered_steps,
        tool_results=tuple(tool_results),
        max_rounds=max_rounds,
        rounds_run=rounds_run,
        convergence_status=convergence_status,
        stopped_reason=stopped_reason,
    )


def run_materialization_quality_repair_schedule_callbacks(
    *,
    runner_step_ids: Sequence[str],
    runner: MaterializationQualityStepRunner,
    max_rounds: int = DEFAULT_REPAIR_SCHEDULE_MAX_ROUNDS,
) -> MaterializationQualityRepairScheduleRun:
    """Run materialization-quality callbacks in runtime-owned order with bounded convergence."""

    ordered_steps = materialization_quality_repair_schedule()
    _validate_materialization_quality_runner_bindings(ordered_steps=ordered_steps, runner_step_ids=runner_step_ids)
    tool_results: list[dict[str, Any]] = []
    rounds_run, convergence_status, stopped_reason = _run_scheduled_repair_rounds(
        ordered_steps=ordered_steps,
        runner=runner,
        max_rounds=max_rounds,
        tool_results=tool_results,
    )
    _annotate_convergence_result(
        tool_results,
        max_rounds=_coerce_max_rounds(max_rounds),
        rounds_run=rounds_run,
        convergence_status=convergence_status,
        stopped_reason=stopped_reason,
    )
    return MaterializationQualityRepairScheduleRun(
        ordered_steps=ordered_steps,
        tool_results=tuple(tool_results),
        max_rounds=max_rounds,
        rounds_run=rounds_run,
        convergence_status=convergence_status,
        stopped_reason=stopped_reason,
    )


def _run_scheduled_repair_rounds(
    *,
    ordered_steps: Sequence[PostExecutionRepairScheduleStep] | Sequence[MaterializationQualityRepairScheduleStep],
    runner: Callable[[Any], Sequence[Mapping[str, Any]]],
    max_rounds: int,
    tool_results: list[dict[str, Any]],
) -> tuple[int, str, str]:
    bounded_max_rounds = _coerce_max_rounds(max_rounds)
    seen_round_fingerprints: set[tuple[tuple[str, ...], ...]] = set()
    rounds_run = 0
    for round_number in range(1, bounded_max_rounds + 1):
        round_results: list[dict[str, Any]] = []
        for step in ordered_steps:
            step_results = [dict(item or {}) for item in runner(step)]
            for result in step_results:
                _annotate_tool_result(
                    result,
                    step,
                    round_number=round_number,
                    max_rounds=bounded_max_rounds,
                )
            round_results.extend(step_results)
        rounds_run = round_number
        if not round_results:
            stopped_reason = "no_repairs_applied" if not tool_results else "converged_no_repairs_applied"
            return rounds_run, "converged", stopped_reason
        fingerprint = _round_fingerprint(round_results)
        if fingerprint in seen_round_fingerprints:
            return rounds_run, "cycle_broken", "repeated_round_fingerprint"
        seen_round_fingerprints.add(fingerprint)
        tool_results.extend(round_results)
    return rounds_run, "max_rounds_reached", "max_rounds_reached"


def _ordered_post_execution_schedule_steps(
    steps: Sequence[PostExecutionRepairScheduleStep],
) -> tuple[PostExecutionRepairScheduleStep, ...]:
    completed: set[str] = set()
    pending = list(steps or ())
    ordered: list[PostExecutionRepairScheduleStep] = []
    while pending:
        ready = [step for step in pending if all(depends_on in completed for depends_on in step.depends_on)]
        if not ready:
            blocked = sorted(step.step_id for step in pending)
            raise RuntimeError(f"post-execution repair step dependency cycle detected: {blocked}")
        ready.sort(key=lambda step: (step.priority, step.step_id))
        for step in ready:
            ordered.append(step)
            completed.add(step.step_id)
            pending.remove(step)
    return tuple(ordered)


def _ordered_materialization_quality_schedule_steps(
    steps: Sequence[MaterializationQualityRepairScheduleStep],
) -> tuple[MaterializationQualityRepairScheduleStep, ...]:
    completed: set[str] = set()
    pending = list(steps or ())
    ordered: list[MaterializationQualityRepairScheduleStep] = []
    while pending:
        ready = [step for step in pending if all(depends_on in completed for depends_on in step.depends_on)]
        if not ready:
            blocked = sorted(step.step_id for step in pending)
            raise RuntimeError(f"materialization quality repair step dependency cycle detected: {blocked}")
        ready.sort(key=lambda step: (step.priority, step.step_id))
        for step in ready:
            ordered.append(step)
            completed.add(step.step_id)
            pending.remove(step)
    return tuple(ordered)


def _validate_runner_bindings(
    *,
    ordered_steps: Sequence[PostExecutionRepairScheduleStep],
    runner_step_ids: Sequence[str],
) -> None:
    scheduled_step_ids = {step.step_id for step in ordered_steps}
    runner_ids = {str(step_id or "").strip() for step_id in runner_step_ids if str(step_id or "").strip()}
    missing_runner_step_ids = sorted(scheduled_step_ids - runner_ids)
    if missing_runner_step_ids:
        raise RuntimeError(f"post-execution repair schedule has no runner binding: {missing_runner_step_ids}")
    extra_runner_step_ids = sorted(runner_ids - scheduled_step_ids)
    if extra_runner_step_ids:
        raise RuntimeError(f"post-execution repair runner is not declared in runtime schedule: {extra_runner_step_ids}")


def _validate_materialization_quality_runner_bindings(
    *,
    ordered_steps: Sequence[MaterializationQualityRepairScheduleStep],
    runner_step_ids: Sequence[str],
) -> None:
    scheduled_step_ids = {step.step_id for step in ordered_steps}
    runner_ids = {str(step_id or "").strip() for step_id in runner_step_ids if str(step_id or "").strip()}
    missing_runner_step_ids = sorted(scheduled_step_ids - runner_ids)
    if missing_runner_step_ids:
        raise RuntimeError(f"materialization quality repair schedule has no runner binding: {missing_runner_step_ids}")
    extra_runner_step_ids = sorted(runner_ids - scheduled_step_ids)
    if extra_runner_step_ids:
        raise RuntimeError(
            f"materialization quality repair runner is not declared in runtime schedule: {extra_runner_step_ids}"
        )


def _coerce_max_rounds(value: int) -> int:
    try:
        normalized = int(value)
    except (TypeError, ValueError):
        normalized = DEFAULT_REPAIR_SCHEDULE_MAX_ROUNDS
    return min(_MAX_REPAIR_SCHEDULE_MAX_ROUNDS, max(1, normalized))


def _round_fingerprint(tool_results: Sequence[dict[str, Any]]) -> tuple[tuple[str, ...], ...]:
    return tuple(sorted(_tool_result_fingerprint(item) for item in tool_results))


def _tool_result_fingerprint(tool_result: Mapping[str, Any]) -> tuple[str, ...]:
    result = tool_result.get("result")
    payload = result if isinstance(result, dict) else {}
    return (
        str(tool_result.get("tool_name") or tool_result.get("tool") or ""),
        str(payload.get("source_tool") or ""),
        str(payload.get("file") or ""),
        str(payload.get("operation") or payload.get("action") or ""),
        str(payload.get("before_hash") or ""),
        str(payload.get("after_hash") or ""),
        str(payload.get("bridge_step_id") or ""),
    )


def _annotate_tool_result(
    tool_result: dict[str, Any],
    step: PostExecutionRepairScheduleStep | MaterializationQualityRepairScheduleStep,
    *,
    round_number: int,
    max_rounds: int,
) -> None:
    result = tool_result.get("result")
    payload = result if isinstance(result, dict) else {}
    if not payload:
        return
    payload.setdefault("bridge_step_id", step.step_id)
    payload.setdefault("language", step.language)
    payload.setdefault("phase", step.phase)
    payload.setdefault("priority", step.priority)
    payload.setdefault("scheduled_source_tool", step.source_tool)
    payload.setdefault("scheduled_source_tool_kind", step.source_tool_kind)
    payload.setdefault("scheduled_source_tool_executable_runtime", step.executable_runtime_source_tool)
    payload.setdefault("schedule_source_tool_kind", step.source_tool_kind)
    payload.setdefault("schedule_source_tool_is_runtime_executable", step.executable_runtime_source_tool)
    payload.setdefault("depends_on", list(step.depends_on))
    payload.setdefault("round_number", round_number)
    payload.setdefault("max_rounds", max_rounds)
    payload.setdefault("scheduler_round_number", round_number)
    payload.setdefault("scheduler_max_rounds", max_rounds)
    payload.setdefault("convergence_scheduler_required", True)
    payload.setdefault("canonical_convergence_executor", _CANONICAL_CONVERGENCE_EXECUTOR)
    payload.setdefault("pipeline_order", CONVERGENCE_PIPELINE_ORDER)
    payload.setdefault("typed_receipt_path_available", False)
    payload.setdefault("callback_migration_envelope", True)
    payload.setdefault("migration_callback_envelope", True)
    payload.setdefault("adapter_projection_bridge", True)
    payload.setdefault("adapter_callback_bridge", False)
    payload.setdefault("produces_tool_results_only", True)
    payload.setdefault("hidden_language_loop_allowed", False)
    payload.setdefault("language_self_loop_allowed", False)
    payload.setdefault("callback_runner_self_loop_allowed", False)
    payload.setdefault("typed_convergence_scheduler_cutover_required", True)
    payload.setdefault("bounded_round_accounting_visible", True)
    payload.setdefault("round_accounting_fields", list(_CALLBACK_ROUND_ACCOUNTING_FIELDS))
    payload.setdefault("preferred_typed_receipt_entrypoint", _FINAL_TYPED_RECEIPT_ENTRYPOINT)
    payload.setdefault("final_typed_receipt_path", _FINAL_TYPED_RECEIPT_ENTRYPOINT)
    payload.setdefault("typed_receipt_path", "unavailable_in_callback_bridge")
    revalidation = payload.get("revalidation")
    if isinstance(revalidation, dict):
        revalidation.setdefault("round_number", payload.get("round_number"))
        revalidation.setdefault("max_rounds", max_rounds)
        revalidation.setdefault("convergence_scheduler_required", True)
        revalidation.setdefault("canonical_convergence_executor", _CANONICAL_CONVERGENCE_EXECUTOR)
        revalidation.setdefault("pipeline_order", CONVERGENCE_PIPELINE_ORDER)
        revalidation.setdefault("typed_receipt_path_available", False)
        revalidation.setdefault("callback_migration_envelope", True)
        revalidation.setdefault("hidden_language_loop_allowed", False)
        revalidation.setdefault("callback_runner_self_loop_allowed", False)


def _annotate_convergence_result(
    tool_results: Sequence[dict[str, Any]],
    *,
    max_rounds: int,
    rounds_run: int,
    convergence_status: str,
    stopped_reason: str,
) -> None:
    for tool_result in tool_results:
        result = tool_result.get("result")
        payload = result if isinstance(result, dict) else {}
        if not payload:
            continue
        payload.setdefault("scheduler_rounds_run", rounds_run)
        payload.setdefault("convergence_status", convergence_status)
        payload.setdefault("convergence_stopped_reason", stopped_reason)
        payload.setdefault("convergence_scheduler_required", True)
        payload.setdefault("canonical_convergence_executor", _CANONICAL_CONVERGENCE_EXECUTOR)
        payload.setdefault("pipeline_order", CONVERGENCE_PIPELINE_ORDER)
        payload.setdefault("typed_receipt_path_available", False)
        payload.setdefault("callback_migration_envelope", True)
        payload.setdefault("migration_callback_envelope", True)
        payload.setdefault("adapter_projection_bridge", True)
        payload.setdefault("adapter_callback_bridge", False)
        payload.setdefault("produces_tool_results_only", True)
        payload.setdefault("callback_receipt_projection_available", True)
        payload.setdefault("callback_receipt_projection_schema_version", _CALLBACK_RECEIPT_PROJECTION_SCHEMA_VERSION)
        payload.setdefault("hidden_language_loop_allowed", False)
        payload.setdefault("language_self_loop_allowed", False)
        payload.setdefault("callback_runner_self_loop_allowed", False)
        payload.setdefault("typed_convergence_scheduler_cutover_required", True)
        payload.setdefault("bounded_round_accounting_visible", True)
        payload.setdefault("round_accounting_fields", list(_CALLBACK_ROUND_ACCOUNTING_FIELDS))
        payload.setdefault("preferred_typed_receipt_entrypoint", _FINAL_TYPED_RECEIPT_ENTRYPOINT)
        payload.setdefault("final_typed_receipt_path", _FINAL_TYPED_RECEIPT_ENTRYPOINT)
        payload.setdefault("typed_receipt_path", "unavailable_in_callback_bridge")
        revalidation = payload.get("revalidation")
        if isinstance(revalidation, dict):
            revalidation.setdefault("scheduler_rounds_run", rounds_run)
            revalidation.setdefault("convergence_status", convergence_status)
            revalidation.setdefault("convergence_stopped_reason", stopped_reason)
            revalidation.setdefault("max_rounds", max_rounds)
            revalidation.setdefault("convergence_scheduler_required", True)
            revalidation.setdefault("canonical_convergence_executor", _CANONICAL_CONVERGENCE_EXECUTOR)
            revalidation.setdefault("pipeline_order", CONVERGENCE_PIPELINE_ORDER)
            revalidation.setdefault("typed_receipt_path_available", False)
            revalidation.setdefault("callback_migration_envelope", True)
            revalidation.setdefault("callback_receipt_projection_available", True)
            revalidation.setdefault("hidden_language_loop_allowed", False)
            revalidation.setdefault("callback_runner_self_loop_allowed", False)


__all__ = [
    "DEFAULT_REPAIR_SCHEDULE_MAX_ROUNDS",
    "MaterializationQualityRepairScheduleRun",
    "MaterializationQualityRepairScheduleStep",
    "MaterializationQualityStepRunner",
    "PostExecutionRepairScheduleRun",
    "PostExecutionRepairScheduleStep",
    "PostExecutionStepRunner",
    "materialization_quality_repair_schedule",
    "post_execution_repair_schedule",
    "run_materialization_quality_repair_schedule_callbacks",
    "run_post_execution_repair_schedule_callbacks",
]
