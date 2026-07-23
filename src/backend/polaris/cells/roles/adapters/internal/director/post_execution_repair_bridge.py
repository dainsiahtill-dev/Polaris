"""Post-execution deterministic repair runner bindings for Director adapter.

The Director runtime repair kernel owns scheduling, policy, and receipt
semantics. This module only binds language-specific post-execution source
collection to the policy-gated Director tool executor used by role adapters.
"""

from __future__ import annotations

import contextlib
import re
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

import tomllib
from polaris.cells.director.runtime.public.service import (
    DirectorRepairPostExecutionStepV1,
    QueryDirectorRepairAdvisoryValidationV1,
    QueryDirectorRepairStrategyCatalogV1,
    RepairAdvisoryV1,
    query_director_repair_post_execution_schedule,
    query_director_repair_strategy_catalog,
    run_director_post_execution_repair_schedule_result,
    validate_director_repair_advisory,
)
from polaris.cells.runtime.task_runtime.public import TaskRuntimeExecutionAttemptIdentityV1

from .repair_profile_projection import project_repair_kernel_summary
from .runtime_repair_tool_adapter import run_runtime_repair_with_director_tools

StepRunner = Callable[[Any, Path, str], list[dict[str, Any]]]
RuntimeAdvisorNotes = tuple[RepairAdvisoryV1, ...]
ConvergenceVerifier = Callable[[Any], Any]

_CPP_REPAIR_FILE_SUFFIXES = (".c", ".cc", ".cpp", ".cxx", ".h", ".hh", ".hpp", ".hxx")
_POST_EXECUTION_REPAIR_MAX_ROUNDS = 3
_CALLBACK_RECEIPT_MIGRATION_BLOCKER = "adapter schedule runners still return tool_results instead of RepairReceipt"
_RUST_BASE_FILE_IGNORES = frozenset({".git", ".venv", "__pycache__", "node_modules", "target"})
_RUST_TYPED_RECEIPT_CUTOVER_SOURCE_TOOLS = frozenset(
    {
        "deterministic_rust_missing_fields_repair",
        "deterministic_rust_lib_root_facade_repair",
    }
)
_RUST_MISSING_FIELDS_SOURCE_TOOL = "deterministic_rust_missing_fields_repair"
_RUST_LIB_ROOT_FACADE_SOURCE_TOOL = "deterministic_rust_lib_root_facade_repair"
_RUST_MISSING_FIELDS_FIELD_DECLARATION_SUBCASE = f"{_RUST_MISSING_FIELDS_SOURCE_TOOL}:field_declaration"
_RUST_LIB_ROOT_FACADE_PATH_REWRITE_SUBCASE = f"{_RUST_LIB_ROOT_FACADE_SOURCE_TOOL}:path_rewrite"
_RUST_LIB_ROOT_FACADE_EXPORT_OR_MODULE_DECLARATION_SUBCASE = (
    f"{_RUST_LIB_ROOT_FACADE_SOURCE_TOOL}:export_or_module_declaration"
)
_RUST_TYPED_RECEIPT_CUTOVER_SUBCASES_BY_SOURCE_TOOL: Mapping[str, frozenset[str]] = {
    _RUST_MISSING_FIELDS_SOURCE_TOOL: frozenset({_RUST_MISSING_FIELDS_FIELD_DECLARATION_SUBCASE}),
    _RUST_LIB_ROOT_FACADE_SOURCE_TOOL: frozenset(
        {
            _RUST_LIB_ROOT_FACADE_EXPORT_OR_MODULE_DECLARATION_SUBCASE,
            _RUST_LIB_ROOT_FACADE_PATH_REWRITE_SUBCASE,
        }
    ),
}
_RUST_TYPED_RECEIPT_SOURCE_TOOL_BLOCKER = "rust_typed_receipt_source_tool_not_runtime_executable"
_GO_POST_EXECUTION_RUNTIME_SOURCE_TOOLS = (
    "deterministic_go_bare_import_string_repair",
    "deterministic_go_nested_import_repair",
    "deterministic_go_module_import_repair",
    "deterministic_go_bare_import_repair",
    "deterministic_go_subpath_repair",
    "deterministic_go_unused_import_repair",
    "deterministic_go_error_string_helper_repair",
    "deterministic_go_dedup_repair",
)
_ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_RUST_E0583_HELP_LINE_RE = re.compile(
    r"to create the module [`'\"](?P<module>[A-Za-z_][A-Za-z0-9_]*)[`'\"].*?"
    r"create file (?P<candidates>[^\n]+)",
    re.IGNORECASE,
)
_RUST_QUOTED_RS_PATH_RE = re.compile(r"[`'\"](?P<path>[^`'\"]+\.rs)[`'\"]")


_POST_EXECUTION_REPAIR_RUNNERS: dict[str, StepRunner] = {
    "go.module_import": lambda adapter, workspace, task_id: _run_go_post_repairs(adapter, task_id=task_id),
    "rust.dependency_resolution": lambda adapter, workspace, task_id: _run_rust_dependency_repair(
        adapter,
        task_id=task_id,
    ),
    "rust.post_execution_convergence": lambda adapter, workspace, task_id: _run_rust_post_repairs(
        adapter,
        workspace,
        task_id=task_id,
    ),
    "cpp.post_execution": lambda adapter, workspace, task_id: run_cpp_post_repairs_as_tool_results(
        workspace,
        adapter=adapter,
        task_id=task_id,
    ),
    "java.post_execution": lambda adapter, workspace, task_id: _run_java_post_repairs(
        adapter,
        workspace,
        task_id=task_id,
    ),
}


def run_post_execution_language_repairs(
    adapter: Any,
    *,
    task_id: str,
    resident_agi_repair_advisory_overlay: dict[str, Any] | None = None,
    convergence_verifier: ConvergenceVerifier | None = None,
    execution_attempt: TaskRuntimeExecutionAttemptIdentityV1 | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    """Run post-execution language repairs and return normalized tool results."""

    workspace = Path(str(getattr(adapter, "workspace", "") or ""))
    agi_advisory_overlay = _normalize_resident_agi_repair_advisory_overlay(
        resident_agi_repair_advisory_overlay,
    )
    runtime_advisor_notes = _runtime_advisor_notes_from_overlay(agi_advisory_overlay)

    def _run_step(step: DirectorRepairPostExecutionStepV1) -> list[dict[str, Any]]:
        runner = _runner_for_post_execution_step(step)
        execution_attempt_kwargs = (
            {"execution_attempt": execution_attempt}
            if type(execution_attempt) is TaskRuntimeExecutionAttemptIdentityV1
            else {}
        )
        if step.step_id == "cpp.post_execution":
            return run_cpp_post_repairs_as_tool_results(
                workspace,
                adapter=adapter,
                task_id=task_id,
                advisor_notes=runtime_advisor_notes,
                convergence_verifier=convergence_verifier,
                **execution_attempt_kwargs,
            )
        if step.step_id == "go.module_import":
            return _run_go_post_repairs(
                adapter,
                task_id=task_id,
                advisor_notes=runtime_advisor_notes,
                convergence_verifier=convergence_verifier,
                **execution_attempt_kwargs,
            )
        if step.step_id == "rust.dependency_resolution":
            return _run_rust_dependency_repair(
                adapter,
                task_id=task_id,
                advisor_notes=runtime_advisor_notes,
                convergence_verifier=convergence_verifier,
                **execution_attempt_kwargs,
            )
        if step.step_id == "rust.post_execution_convergence":
            return _run_rust_post_repairs(
                adapter,
                workspace,
                task_id=task_id,
                advisor_notes=runtime_advisor_notes,
                convergence_verifier=convergence_verifier,
                **execution_attempt_kwargs,
            )
        if step.step_id == "java.post_execution":
            return _run_java_post_repairs(
                adapter,
                workspace,
                task_id=task_id,
                advisor_notes=runtime_advisor_notes,
                convergence_verifier=convergence_verifier,
                **execution_attempt_kwargs,
            )
        return runner(adapter, workspace, task_id)

    schedule_result = run_director_post_execution_repair_schedule_result(
        runner_step_ids=tuple(_POST_EXECUTION_REPAIR_RUNNERS),
        runner=_run_step,
        # Deferred effects may plan one round only.  Later rounds require the
        # first round's lifecycle, receipt and revalidation facts (DEO-3).
        max_rounds=1,
    )
    tool_results = [dict(item) for item in schedule_result.tool_results]
    ordered_steps = schedule_result.ordered_steps
    if not tool_results:
        return [], None
    repair_kernel = project_repair_kernel_summary(
        stage="post_execution_language_repairs",
        tool_results=tool_results,
        artifact_quality_errors=(),
        mode="commit",
    )
    repair_kernel["agi_advisory"] = {
        **dict(repair_kernel.get("agi_advisory") or {}),
        **agi_advisory_overlay,
    }
    migration_debt = _build_repair_kernel_migration_debt(
        tool_results,
        ordered_steps=ordered_steps,
        convergence_verifier_present=convergence_verifier is not None,
    )
    repair_kernel["repair_kernel_migration_debt"] = migration_debt
    rust_typed_receipt_cutover_evidence = dict(migration_debt.get("rust_typed_receipt_cutover_evidence") or {})
    repair_kernel.update(_rust_typed_receipt_cutover_projection_fields(rust_typed_receipt_cutover_evidence))
    scheduler_bridge = _build_scheduler_bridge_summary(
        tool_results,
        repair_kernel=repair_kernel,
        ordered_steps=ordered_steps,
        schedule_summary=dict(schedule_result.summary),
        receipt_projections=[
            _callback_receipt_projection_to_dict(item) for item in schedule_result.receipt_projections
        ],
        resident_agi_repair_advisory_overlay=agi_advisory_overlay,
    )
    return tool_results, {
        "schema_version": "director.post_execution_repair_kernel.v1",
        "repair_kernel": repair_kernel,
        "scheduler_bridge": scheduler_bridge,
        "repair_kernel_migration_debt": migration_debt,
        "rust_typed_receipt_cutover_evidence": rust_typed_receipt_cutover_evidence,
        "resident_agi_repair_advisory_overlay": agi_advisory_overlay,
    }


def run_cpp_post_repairs_as_tool_results(
    workspace: str | Path,
    *,
    adapter: Any,
    task_id: str = "director-cpp-post-repair",
    advisor_notes: RuntimeAdvisorNotes = (),
    convergence_verifier: ConvergenceVerifier | None = None,
    execution_attempt: TaskRuntimeExecutionAttemptIdentityV1 | None = None,
) -> list[dict[str, Any]]:
    """Run C++ post repairs and normalize them as write-tool results."""

    workspace_path = Path(workspace)
    if not _looks_like_cpp_workspace(workspace_path):
        return []
    if adapter is None:
        return _policy_gated_adapter_missing_tool_result(
            task_id=task_id,
            source_tool="deterministic_cpp_post_repair",
        )

    tool_results = _run_cpp_include_path_runtime_repair(
        adapter,
        workspace_path,
        task_id=task_id,
        advisor_notes=advisor_notes,
        convergence_verifier=convergence_verifier,
        execution_attempt=execution_attempt,
    )
    tool_results.extend(
        _run_cpp_standard_include_runtime_repair(
            adapter,
            workspace_path,
            task_id=task_id,
            advisor_notes=advisor_notes,
            convergence_verifier=convergence_verifier,
            execution_attempt=execution_attempt,
        )
    )
    tool_results.extend(
        _run_cpp_placeholder_declaration_runtime_repair(
            adapter,
            workspace_path,
            task_id=task_id,
            advisor_notes=advisor_notes,
            convergence_verifier=convergence_verifier,
            execution_attempt=execution_attempt,
        )
    )
    tool_results.extend(
        _run_cpp_struct_getter_field_access_runtime_repair(
            adapter,
            workspace_path,
            task_id=task_id,
            advisor_notes=advisor_notes,
            convergence_verifier=convergence_verifier,
            execution_attempt=execution_attempt,
        )
    )
    tool_results.extend(
        _run_cpp_missing_private_members_runtime_repair(
            adapter,
            workspace_path,
            task_id=task_id,
            advisor_notes=advisor_notes,
            convergence_verifier=convergence_verifier,
            execution_attempt=execution_attempt,
        )
    )
    tool_results.extend(
        _run_cpp_runtime_repair(
            adapter,
            workspace_path,
            task_id=task_id,
            source_tool="deterministic_cpp_post_repair",
            advisor_notes=advisor_notes,
            convergence_verifier=convergence_verifier,
            execution_attempt=execution_attempt,
        )
    )
    return tool_results


def _run_cpp_include_path_runtime_repair(
    adapter: Any | None,
    workspace: Path,
    *,
    task_id: str,
    advisor_notes: RuntimeAdvisorNotes = (),
    convergence_verifier: ConvergenceVerifier | None = None,
    execution_attempt: TaskRuntimeExecutionAttemptIdentityV1 | None = None,
) -> list[dict[str, Any]]:
    return _run_cpp_runtime_repair(
        adapter,
        workspace,
        task_id=task_id,
        source_tool="deterministic_cpp_include_path_repair",
        advisor_notes=advisor_notes,
        convergence_verifier=convergence_verifier,
        execution_attempt=execution_attempt,
    )


def _run_cpp_standard_include_runtime_repair(
    adapter: Any | None,
    workspace: Path,
    *,
    task_id: str,
    advisor_notes: RuntimeAdvisorNotes = (),
    convergence_verifier: ConvergenceVerifier | None = None,
    execution_attempt: TaskRuntimeExecutionAttemptIdentityV1 | None = None,
) -> list[dict[str, Any]]:
    return _run_cpp_runtime_repair(
        adapter,
        workspace,
        task_id=task_id,
        source_tool="deterministic_cpp_standard_include_repair",
        advisor_notes=advisor_notes,
        convergence_verifier=convergence_verifier,
        execution_attempt=execution_attempt,
    )


def _run_cpp_placeholder_declaration_runtime_repair(
    adapter: Any | None,
    workspace: Path,
    *,
    task_id: str,
    advisor_notes: RuntimeAdvisorNotes = (),
    convergence_verifier: ConvergenceVerifier | None = None,
    execution_attempt: TaskRuntimeExecutionAttemptIdentityV1 | None = None,
) -> list[dict[str, Any]]:
    return _run_cpp_runtime_repair(
        adapter,
        workspace,
        task_id=task_id,
        source_tool="deterministic_cpp_placeholder_declaration_repair",
        advisor_notes=advisor_notes,
        convergence_verifier=convergence_verifier,
        execution_attempt=execution_attempt,
    )


def _run_cpp_struct_getter_field_access_runtime_repair(
    adapter: Any | None,
    workspace: Path,
    *,
    task_id: str,
    advisor_notes: RuntimeAdvisorNotes = (),
    convergence_verifier: ConvergenceVerifier | None = None,
    execution_attempt: TaskRuntimeExecutionAttemptIdentityV1 | None = None,
) -> list[dict[str, Any]]:
    return _run_cpp_runtime_repair(
        adapter,
        workspace,
        task_id=task_id,
        source_tool="deterministic_cpp_struct_getter_field_access_repair",
        advisor_notes=advisor_notes,
        convergence_verifier=convergence_verifier,
        execution_attempt=execution_attempt,
    )


def _run_cpp_missing_private_members_runtime_repair(
    adapter: Any | None,
    workspace: Path,
    *,
    task_id: str,
    advisor_notes: RuntimeAdvisorNotes = (),
    convergence_verifier: ConvergenceVerifier | None = None,
    execution_attempt: TaskRuntimeExecutionAttemptIdentityV1 | None = None,
) -> list[dict[str, Any]]:
    return _run_cpp_runtime_repair(
        adapter,
        workspace,
        task_id=task_id,
        source_tool="deterministic_cpp_missing_private_members_repair",
        advisor_notes=advisor_notes,
        convergence_verifier=convergence_verifier,
        execution_attempt=execution_attempt,
    )


def _run_cpp_runtime_repair(
    adapter: Any | None,
    workspace: Path,
    *,
    task_id: str,
    source_tool: str,
    advisor_notes: RuntimeAdvisorNotes = (),
    convergence_verifier: ConvergenceVerifier | None = None,
    execution_attempt: TaskRuntimeExecutionAttemptIdentityV1 | None = None,
) -> list[dict[str, Any]]:
    workspace_path = workspace.resolve()
    base_files = _collect_cpp_base_files(workspace_path)
    if not base_files:
        return []

    if adapter is None:
        return _policy_gated_adapter_missing_tool_result(
            task_id=task_id,
            source_tool=source_tool,
        )
    return run_runtime_repair_with_director_tools(
        adapter,
        workspace_path=workspace_path,
        task_id=task_id,
        source_tool=source_tool,
        execution_attempt=execution_attempt,
        base_files=base_files,
        allowed_paths=tuple(base_files.keys()),
        advisor_notes=advisor_notes,
        use_editor=False,
        convergence_verifier=convergence_verifier,
        max_rounds=1,
    )


def _run_go_post_repairs(
    adapter: Any,
    *,
    task_id: str,
    advisor_notes: RuntimeAdvisorNotes = (),
    convergence_verifier: ConvergenceVerifier | None = None,
    execution_attempt: TaskRuntimeExecutionAttemptIdentityV1 | None = None,
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for source_tool in _GO_POST_EXECUTION_RUNTIME_SOURCE_TOOLS:
        runtime_results = _run_go_runtime_repair(
            adapter,
            task_id=task_id,
            source_tool=source_tool,
            advisor_notes=advisor_notes,
            convergence_verifier=convergence_verifier,
            execution_attempt=execution_attempt,
        )
        if any(not bool(item.get("success", False)) for item in runtime_results):
            return runtime_results
        results.extend(runtime_results)
    return results


def _run_go_runtime_repair(
    adapter: Any,
    *,
    task_id: str,
    source_tool: str,
    advisor_notes: RuntimeAdvisorNotes = (),
    convergence_verifier: ConvergenceVerifier | None = None,
    execution_attempt: TaskRuntimeExecutionAttemptIdentityV1 | None = None,
) -> list[dict[str, Any]]:
    workspace = Path(str(getattr(adapter, "workspace", "") or ""))
    if not workspace.is_dir():
        return []
    workspace_path = workspace.resolve()
    base_files = _collect_go_base_files(workspace_path)
    if not base_files:
        return []
    return run_runtime_repair_with_director_tools(
        adapter,
        workspace_path=workspace_path,
        task_id=task_id,
        source_tool=source_tool,
        base_files=base_files,
        allowed_paths=tuple(base_files.keys()),
        advisor_notes=advisor_notes,
        use_editor=True,
        convergence_verifier=convergence_verifier,
        execution_attempt=execution_attempt,
        max_rounds=1,
    )


def _run_rust_post_repairs(
    adapter: Any,
    workspace: Path,
    *,
    task_id: str,
    advisor_notes: RuntimeAdvisorNotes = (),
    convergence_verifier: ConvergenceVerifier | None = None,
    execution_attempt: TaskRuntimeExecutionAttemptIdentityV1 | None = None,
) -> list[dict[str, Any]]:
    workspace_path = workspace.resolve()
    if not (workspace_path / "Cargo.toml").is_file():
        return []

    tool_results = _run_rust_crate_import_rewrite_runtime_repair(
        adapter,
        workspace_path,
        task_id=task_id,
        execution_attempt=execution_attempt,
    )
    tool_results.extend(
        _run_rust_method_self_signature_runtime_repair(
            adapter,
            workspace_path,
            task_id=task_id,
            execution_attempt=execution_attempt,
        )
    )
    tool_results.extend(
        _run_rust_wrong_crate_path_runtime_repair(
            adapter,
            workspace_path,
            task_id=task_id,
            execution_attempt=execution_attempt,
        )
    )
    tool_results.extend(
        _run_rust_incompatible_copy_derive_runtime_repair(
            adapter,
            workspace_path,
            task_id=task_id,
            execution_attempt=execution_attempt,
        )
    )
    tool_results.extend(
        _run_rust_missing_trait_derive_runtime_repair(
            adapter,
            workspace_path,
            task_id=task_id,
            execution_attempt=execution_attempt,
        )
    )
    tool_results.extend(
        _run_rust_unused_import_runtime_repair(
            adapter,
            workspace_path,
            task_id=task_id,
            execution_attempt=execution_attempt,
        )
    )
    tool_results.extend(
        _run_rust_unresolved_pub_use_runtime_repair(
            adapter,
            workspace_path,
            task_id=task_id,
            execution_attempt=execution_attempt,
        )
    )
    tool_results.extend(
        _run_rust_trait_import_runtime_repair(
            adapter,
            workspace_path,
            task_id=task_id,
            execution_attempt=execution_attempt,
        )
    )
    tool_results.extend(
        _run_rust_line_suggestion_runtime_repair(
            adapter,
            workspace_path,
            task_id=task_id,
            execution_attempt=execution_attempt,
        )
    )
    tool_results.extend(
        _run_rust_field_rename_suggestion_runtime_repair(
            adapter,
            workspace_path,
            task_id=task_id,
            execution_attempt=execution_attempt,
        )
    )
    tool_results.extend(
        _run_rust_missing_binary_entrypoint_runtime_repair(
            adapter,
            workspace_path,
            task_id=task_id,
            execution_attempt=execution_attempt,
        )
    )
    tool_results.extend(
        _run_rust_missing_module_file_runtime_repair(
            adapter,
            workspace_path,
            task_id=task_id,
            execution_attempt=execution_attempt,
        )
    )
    tool_results.extend(
        _run_rust_duplicate_module_file_runtime_repair(
            adapter,
            workspace_path,
            task_id=task_id,
            execution_attempt=execution_attempt,
        )
    )
    tool_results.extend(
        _run_rust_missing_fields_runtime_repair(
            adapter,
            workspace_path,
            task_id=task_id,
            advisor_notes=advisor_notes,
            convergence_verifier=convergence_verifier,
            execution_attempt=execution_attempt,
        )
    )
    tool_results.extend(
        _run_rust_lib_root_facade_runtime_repair(
            adapter,
            workspace_path,
            task_id=task_id,
            advisor_notes=advisor_notes,
            convergence_verifier=convergence_verifier,
            execution_attempt=execution_attempt,
        )
    )
    return tool_results


def _run_rust_dependency_repair(
    adapter: Any,
    *,
    task_id: str,
    advisor_notes: RuntimeAdvisorNotes = (),
    convergence_verifier: ConvergenceVerifier | None = None,
    execution_attempt: TaskRuntimeExecutionAttemptIdentityV1 | None = None,
) -> list[dict[str, Any]]:
    workspace_path = Path(str(getattr(adapter, "workspace", "") or "")).resolve()
    if not (workspace_path / "Cargo.toml").is_file():
        return []
    base_files = _collect_rust_base_files(workspace_path)
    if not base_files:
        return []
    return run_runtime_repair_with_director_tools(
        adapter,
        workspace_path=workspace_path,
        task_id=task_id,
        source_tool="deterministic_rust_dependency_repair",
        base_files=base_files,
        artifact_quality_errors=_rust_post_execution_artifact_quality_errors(adapter),
        allowed_paths=tuple(base_files.keys()),
        advisor_notes=advisor_notes,
        use_editor=False,
        convergence_verifier=convergence_verifier,
        execution_attempt=execution_attempt,
        max_rounds=1,
    )


def _run_rust_crate_import_rewrite_runtime_repair(
    adapter: Any,
    workspace: Path,
    *,
    task_id: str,
    execution_attempt: TaskRuntimeExecutionAttemptIdentityV1 | None = None,
) -> list[dict[str, Any]]:
    workspace_path = workspace.resolve()
    base_files = _collect_rust_base_files(workspace_path)
    artifact_quality_errors = _rust_post_execution_artifact_quality_errors(adapter)
    if not base_files or not artifact_quality_errors:
        return []
    return run_runtime_repair_with_director_tools(
        adapter,
        workspace_path=workspace_path,
        task_id=task_id,
        source_tool="deterministic_rust_crate_import_rewrite_repair",
        base_files=base_files,
        artifact_quality_errors=artifact_quality_errors,
        allowed_paths=tuple(base_files.keys()),
        use_editor=True,
        execution_attempt=execution_attempt,
    )


def _run_rust_method_self_signature_runtime_repair(
    adapter: Any,
    workspace: Path,
    *,
    task_id: str,
    execution_attempt: TaskRuntimeExecutionAttemptIdentityV1 | None = None,
) -> list[dict[str, Any]]:
    workspace_path = workspace.resolve()
    base_files = _collect_rust_base_files(workspace_path)
    artifact_quality_errors = _rust_post_execution_artifact_quality_errors(adapter)
    if not base_files or not artifact_quality_errors:
        return []
    return run_runtime_repair_with_director_tools(
        adapter,
        workspace_path=workspace_path,
        task_id=task_id,
        source_tool="deterministic_rust_method_self_signature_repair",
        base_files=base_files,
        artifact_quality_errors=artifact_quality_errors,
        allowed_paths=tuple(base_files.keys()),
        use_editor=True,
        execution_attempt=execution_attempt,
    )


def _run_rust_wrong_crate_path_runtime_repair(
    adapter: Any,
    workspace: Path,
    *,
    task_id: str,
    execution_attempt: TaskRuntimeExecutionAttemptIdentityV1 | None = None,
) -> list[dict[str, Any]]:
    workspace_path = workspace.resolve()
    base_files = _collect_rust_base_files(workspace_path)
    artifact_quality_errors = _rust_post_execution_artifact_quality_errors(adapter)
    if not base_files or not artifact_quality_errors:
        return []
    return run_runtime_repair_with_director_tools(
        adapter,
        workspace_path=workspace_path,
        task_id=task_id,
        source_tool="deterministic_rust_wrong_crate_path_repair",
        base_files=base_files,
        artifact_quality_errors=artifact_quality_errors,
        allowed_paths=tuple(base_files.keys()),
        use_editor=True,
        execution_attempt=execution_attempt,
    )


def _run_rust_incompatible_copy_derive_runtime_repair(
    adapter: Any,
    workspace: Path,
    *,
    task_id: str,
    execution_attempt: TaskRuntimeExecutionAttemptIdentityV1 | None = None,
) -> list[dict[str, Any]]:
    workspace_path = workspace.resolve()
    base_files = _collect_rust_base_files(workspace_path)
    artifact_quality_errors = _rust_post_execution_artifact_quality_errors(adapter)
    if not base_files or not artifact_quality_errors:
        return []
    return run_runtime_repair_with_director_tools(
        adapter,
        workspace_path=workspace_path,
        task_id=task_id,
        source_tool="deterministic_rust_incompatible_copy_derive_repair",
        base_files=base_files,
        artifact_quality_errors=artifact_quality_errors,
        allowed_paths=tuple(base_files.keys()),
        use_editor=True,
        execution_attempt=execution_attempt,
    )


def _run_rust_missing_trait_derive_runtime_repair(
    adapter: Any,
    workspace: Path,
    *,
    task_id: str,
    execution_attempt: TaskRuntimeExecutionAttemptIdentityV1 | None = None,
) -> list[dict[str, Any]]:
    workspace_path = workspace.resolve()
    base_files = _collect_rust_base_files(workspace_path)
    artifact_quality_errors = _rust_post_execution_artifact_quality_errors(adapter)
    if not base_files or not artifact_quality_errors:
        return []
    return run_runtime_repair_with_director_tools(
        adapter,
        workspace_path=workspace_path,
        task_id=task_id,
        source_tool="deterministic_rust_derive_repair",
        base_files=base_files,
        artifact_quality_errors=artifact_quality_errors,
        allowed_paths=tuple(base_files.keys()),
        use_editor=True,
        execution_attempt=execution_attempt,
    )


def _run_rust_unused_import_runtime_repair(
    adapter: Any,
    workspace: Path,
    *,
    task_id: str,
    execution_attempt: TaskRuntimeExecutionAttemptIdentityV1 | None = None,
) -> list[dict[str, Any]]:
    workspace_path = workspace.resolve()
    base_files = _collect_rust_base_files(workspace_path)
    artifact_quality_errors = _rust_post_execution_artifact_quality_errors(adapter)
    if not base_files or not artifact_quality_errors:
        return []
    return run_runtime_repair_with_director_tools(
        adapter,
        workspace_path=workspace_path,
        task_id=task_id,
        source_tool="deterministic_rust_unused_import_repair",
        base_files=base_files,
        artifact_quality_errors=artifact_quality_errors,
        allowed_paths=tuple(base_files.keys()),
        use_editor=True,
        execution_attempt=execution_attempt,
    )


def _run_rust_unresolved_pub_use_runtime_repair(
    adapter: Any,
    workspace: Path,
    *,
    task_id: str,
    execution_attempt: TaskRuntimeExecutionAttemptIdentityV1 | None = None,
) -> list[dict[str, Any]]:
    workspace_path = workspace.resolve()
    base_files = _collect_rust_base_files(workspace_path)
    artifact_quality_errors = _rust_post_execution_artifact_quality_errors(adapter)
    if not base_files or not artifact_quality_errors:
        return []
    return run_runtime_repair_with_director_tools(
        adapter,
        workspace_path=workspace_path,
        task_id=task_id,
        source_tool="deterministic_rust_unresolved_pub_use_repair",
        base_files=base_files,
        artifact_quality_errors=artifact_quality_errors,
        allowed_paths=tuple(base_files.keys()),
        use_editor=True,
        execution_attempt=execution_attempt,
    )


def _run_rust_trait_import_runtime_repair(
    adapter: Any,
    workspace: Path,
    *,
    task_id: str,
    execution_attempt: TaskRuntimeExecutionAttemptIdentityV1 | None = None,
) -> list[dict[str, Any]]:
    workspace_path = workspace.resolve()
    base_files = _collect_rust_base_files(workspace_path)
    artifact_quality_errors = _rust_post_execution_artifact_quality_errors(adapter)
    if not base_files or not artifact_quality_errors:
        return []
    return run_runtime_repair_with_director_tools(
        adapter,
        workspace_path=workspace_path,
        task_id=task_id,
        source_tool="deterministic_rust_trait_import_repair",
        base_files=base_files,
        artifact_quality_errors=artifact_quality_errors,
        allowed_paths=tuple(base_files.keys()),
        use_editor=True,
        execution_attempt=execution_attempt,
    )


def _run_rust_line_suggestion_runtime_repair(
    adapter: Any,
    workspace: Path,
    *,
    task_id: str,
    execution_attempt: TaskRuntimeExecutionAttemptIdentityV1 | None = None,
) -> list[dict[str, Any]]:
    workspace_path = workspace.resolve()
    base_files = _collect_rust_base_files(workspace_path)
    artifact_quality_errors = _rust_post_execution_artifact_quality_errors(adapter)
    if not base_files or not artifact_quality_errors:
        return []
    return run_runtime_repair_with_director_tools(
        adapter,
        workspace_path=workspace_path,
        task_id=task_id,
        source_tool="deterministic_rust_line_suggestion_repair",
        base_files=base_files,
        artifact_quality_errors=artifact_quality_errors,
        allowed_paths=tuple(base_files.keys()),
        use_editor=True,
        execution_attempt=execution_attempt,
    )


def _run_rust_field_rename_suggestion_runtime_repair(
    adapter: Any,
    workspace: Path,
    *,
    task_id: str,
    execution_attempt: TaskRuntimeExecutionAttemptIdentityV1 | None = None,
) -> list[dict[str, Any]]:
    workspace_path = workspace.resolve()
    base_files = _collect_rust_base_files(workspace_path)
    artifact_quality_errors = _rust_post_execution_artifact_quality_errors(adapter)
    if not base_files or not artifact_quality_errors:
        return []
    return run_runtime_repair_with_director_tools(
        adapter,
        workspace_path=workspace_path,
        task_id=task_id,
        source_tool="deterministic_rust_field_rename_suggestion_repair",
        base_files=base_files,
        artifact_quality_errors=artifact_quality_errors,
        allowed_paths=tuple(base_files.keys()),
        use_editor=True,
        execution_attempt=execution_attempt,
    )


def _run_rust_missing_binary_entrypoint_runtime_repair(
    adapter: Any,
    workspace: Path,
    *,
    task_id: str,
    execution_attempt: TaskRuntimeExecutionAttemptIdentityV1 | None = None,
) -> list[dict[str, Any]]:
    workspace_path = workspace.resolve()
    base_files = _collect_rust_base_files(workspace_path)
    if "Cargo.toml" not in base_files:
        return []
    allowed_paths = tuple(dict.fromkeys((*base_files.keys(), *_rust_declared_binary_paths(base_files["Cargo.toml"]))))
    return run_runtime_repair_with_director_tools(
        adapter,
        workspace_path=workspace_path,
        task_id=task_id,
        source_tool="deterministic_rust_missing_binary_entrypoint_repair",
        base_files=base_files,
        artifact_quality_errors=_rust_post_execution_artifact_quality_errors(adapter),
        allowed_paths=allowed_paths,
        use_editor=False,
        execution_attempt=execution_attempt,
    )


def _run_rust_missing_module_file_runtime_repair(
    adapter: Any,
    workspace: Path,
    *,
    task_id: str,
    execution_attempt: TaskRuntimeExecutionAttemptIdentityV1 | None = None,
) -> list[dict[str, Any]]:
    workspace_path = workspace.resolve()
    base_files = _collect_rust_base_files(workspace_path)
    artifact_quality_errors = _rust_post_execution_artifact_quality_errors(adapter)
    if not base_files or not artifact_quality_errors:
        return []
    allowed_paths = tuple(
        dict.fromkeys(
            (
                *base_files.keys(),
                *_rust_missing_module_candidate_paths_from_errors(artifact_quality_errors),
            )
        )
    )
    return run_runtime_repair_with_director_tools(
        adapter,
        workspace_path=workspace_path,
        task_id=task_id,
        source_tool="deterministic_rust_missing_module_file_repair",
        base_files=base_files,
        artifact_quality_errors=artifact_quality_errors,
        allowed_paths=allowed_paths,
        use_editor=False,
        execution_attempt=execution_attempt,
    )


def _run_rust_duplicate_module_file_runtime_repair(
    adapter: Any,
    workspace: Path,
    *,
    task_id: str,
    execution_attempt: TaskRuntimeExecutionAttemptIdentityV1 | None = None,
) -> list[dict[str, Any]]:
    workspace_path = workspace.resolve()
    base_files = _collect_rust_base_files(workspace_path)
    artifact_quality_errors = _rust_post_execution_artifact_quality_errors(adapter)
    if not base_files or not artifact_quality_errors:
        return []
    allowed_paths = tuple(
        dict.fromkeys(
            (
                *base_files.keys(),
                *_rust_duplicate_module_candidate_paths_from_errors(artifact_quality_errors),
            )
        )
    )
    return run_runtime_repair_with_director_tools(
        adapter,
        workspace_path=workspace_path,
        task_id=task_id,
        source_tool="deterministic_rust_duplicate_module_file_repair",
        base_files=base_files,
        artifact_quality_errors=artifact_quality_errors,
        allowed_paths=allowed_paths,
        use_editor=False,
        execution_attempt=execution_attempt,
    )


def _run_rust_missing_fields_runtime_repair(
    adapter: Any,
    workspace: Path,
    *,
    task_id: str,
    advisor_notes: RuntimeAdvisorNotes = (),
    convergence_verifier: ConvergenceVerifier | None = None,
    execution_attempt: TaskRuntimeExecutionAttemptIdentityV1 | None = None,
) -> list[dict[str, Any]]:
    workspace_path = workspace.resolve()
    base_files = _collect_rust_base_files(workspace_path)
    artifact_quality_errors = _rust_post_execution_artifact_quality_errors(adapter)
    if not base_files or not artifact_quality_errors:
        return []
    return run_runtime_repair_with_director_tools(
        adapter,
        workspace_path=workspace_path,
        task_id=task_id,
        source_tool=_RUST_MISSING_FIELDS_SOURCE_TOOL,
        base_files=base_files,
        artifact_quality_errors=artifact_quality_errors,
        allowed_paths=tuple(base_files.keys()),
        advisor_notes=advisor_notes,
        use_editor=True,
        convergence_verifier=convergence_verifier,
        execution_attempt=execution_attempt,
        max_rounds=1,
    )


def _run_rust_lib_root_facade_runtime_repair(
    adapter: Any,
    workspace: Path,
    *,
    task_id: str,
    advisor_notes: RuntimeAdvisorNotes = (),
    convergence_verifier: ConvergenceVerifier | None = None,
    execution_attempt: TaskRuntimeExecutionAttemptIdentityV1 | None = None,
) -> list[dict[str, Any]]:
    workspace_path = workspace.resolve()
    base_files = _collect_rust_base_files(workspace_path)
    artifact_quality_errors = _rust_post_execution_artifact_quality_errors(adapter)
    if not base_files or not artifact_quality_errors:
        return []
    return run_runtime_repair_with_director_tools(
        adapter,
        workspace_path=workspace_path,
        task_id=task_id,
        source_tool=_RUST_LIB_ROOT_FACADE_SOURCE_TOOL,
        base_files=base_files,
        artifact_quality_errors=artifact_quality_errors,
        allowed_paths=tuple(base_files.keys()),
        advisor_notes=advisor_notes,
        use_editor=True,
        convergence_verifier=convergence_verifier,
        execution_attempt=execution_attempt,
        max_rounds=1,
    )


def _run_java_post_repairs(
    adapter: Any,
    workspace: Path,
    *,
    task_id: str,
    advisor_notes: RuntimeAdvisorNotes = (),
    convergence_verifier: ConvergenceVerifier | None = None,
    execution_attempt: TaskRuntimeExecutionAttemptIdentityV1 | None = None,
) -> list[dict[str, Any]]:
    if not any(workspace.rglob("*.java")):
        return []

    workspace_path = workspace.resolve()
    base_files = _collect_java_base_files(workspace_path)
    if not base_files:
        return []
    return run_runtime_repair_with_director_tools(
        adapter,
        workspace_path=workspace_path,
        task_id=task_id,
        source_tool="deterministic_java_post_repair",
        base_files=base_files,
        artifact_quality_errors=_post_execution_artifact_quality_errors(adapter),
        allowed_paths=tuple(base_files.keys()),
        advisor_notes=advisor_notes,
        use_editor=True,
        convergence_verifier=convergence_verifier,
        execution_attempt=execution_attempt,
        max_rounds=1,
    )


def _canonical_repair_result_to_tool_results(
    canonical_result: Any,
    *,
    write_results: dict[str, dict[str, Any]],
    workspace: Path,
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for receipt in canonical_result.receipts:
        for patch_path in receipt.files_changed:
            write_result = write_results.get(patch_path, {})
            if not bool(write_result.get("ok")) and receipt.authoritative:
                continue
            bytes_written = write_result.get("bytes_written")
            if bytes_written is None:
                full_path = (workspace / patch_path).resolve()
                with contextlib.suppress(OSError, ValueError):
                    bytes_written = len(full_path.read_text(encoding="utf-8").encode("utf-8"))
            results.append(
                {
                    "tool": "write_file",
                    "tool_name": "write_file",
                    "success": True,
                    "result": {
                        "ok": True,
                        "source_tool": receipt.source_tool,
                        "file": patch_path,
                        "bytes_written": int(bytes_written or 0),
                        "operation": str(write_result.get("operation") or "modify"),
                        "before_hash": str(receipt.before_hashes.get(patch_path) or ""),
                        "after_hash": str(receipt.after_hashes.get(patch_path) or ""),
                        "evidence_status": receipt.evidence_status,
                        "broadcast_ok": bool(write_result.get("broadcast_ok")),
                        "director_policy": write_result.get("director_policy"),
                        "repair_kernel": {
                            "owner_cell": "director.runtime",
                            "receipt_id": receipt.receipt_id,
                            "plan_id": receipt.plan_id,
                            "status": receipt.status,
                            "authoritative": receipt.authoritative,
                            "requires_revalidation": _receipt_requires_revalidation(receipt),
                            "authority_hash": receipt.authority_hash,
                            "projection_hash": receipt.projection_hash,
                            "advisor_notes": [note.to_dict() for note in receipt.advisor_notes],
                            "before_hashes": dict(receipt.before_hashes),
                            "after_hashes": dict(receipt.after_hashes),
                            "round_number": receipt.round_number,
                            "evidence_status": receipt.evidence_status,
                            "errors_before": receipt.errors_before,
                            "errors_after": receipt.errors_after,
                            "net_error_reduction": receipt.net_error_reduction,
                            "revalidation_evidence": dict(receipt.revalidation_evidence),
                            "metadata": dict(receipt.metadata),
                            "planning": dict(canonical_result.metadata.get("planning") or {}),
                            "plan_policy": dict(canonical_result.metadata.get("plan_policy") or {}),
                            "composition_policy": dict(canonical_result.metadata.get("composition_policy") or {}),
                        },
                    },
                }
            )
    return results


def _canonical_repair_failure_to_tool_results(
    canonical_result: Any,
    *,
    source_tool: str,
) -> list[dict[str, Any]]:
    if not canonical_result.error_code or canonical_result.error_code == "repair_not_planned":
        return []
    return [
        {
            "tool": "director_repair_kernel",
            "tool_name": "director_repair_kernel",
            "success": False,
            "result": {
                "ok": False,
                "source_tool": source_tool,
                "error_code": canonical_result.error_code,
                "error_message": canonical_result.error_message,
                "repair_kernel": {
                    "owner_cell": "director.runtime",
                    "receipts": [receipt.to_dict() for receipt in canonical_result.receipts],
                    "planning": dict(canonical_result.metadata.get("planning") or {}),
                    "planning_error": dict(canonical_result.metadata.get("planning_error") or {}),
                    "plan_policy": dict(canonical_result.metadata.get("plan_policy") or {}),
                    "composition_policy": dict(canonical_result.metadata.get("composition_policy") or {}),
                    "execution_error": canonical_result.metadata.get("execution_error"),
                    "rolled_back": bool(canonical_result.metadata.get("rolled_back")),
                },
            },
        }
    ]


def _receipt_requires_revalidation(receipt: Any) -> bool:
    metadata = dict(getattr(receipt, "metadata", {}) or {})
    if "requires_revalidation" in metadata:
        return bool(metadata.get("requires_revalidation"))
    return not bool(getattr(receipt, "revalidation_evidence", {}) or {})


def _policy_gated_adapter_missing_tool_result(*, task_id: str, source_tool: str) -> list[dict[str, Any]]:
    """Return a fail-closed repair result when no Director tool adapter exists."""

    error_code = "director_adapter_required_for_policy_gated_repair"
    return [
        {
            "tool": "director_repair_kernel",
            "tool_name": "director_repair_kernel",
            "success": False,
            "result": {
                "ok": False,
                "source_tool": source_tool,
                "error_code": error_code,
                "error_message": (
                    "Director post-execution repair requires a policy-gated Director adapter; "
                    "direct workspace writes are not permitted."
                ),
                "repair_kernel": {
                    "owner_cell": "director.runtime",
                    "task_id": task_id,
                    "execution_skipped": True,
                    "execution_skip_reason": error_code,
                    "direct_write_allowed": False,
                    "writer_boundary": "director_tool_executor_required",
                },
            },
        }
    ]


def _collect_cpp_base_files(workspace: Path) -> dict[str, str]:
    base_files: dict[str, str] = {}
    for path in sorted(workspace.rglob("*")):
        if not path.is_file() or path.suffix not in _CPP_REPAIR_FILE_SUFFIXES or _is_generated_build_path(path):
            continue
        try:
            relative_path = path.relative_to(workspace).as_posix()
        except ValueError:
            continue
        try:
            base_files[relative_path] = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
    return base_files


def _collect_go_base_files(workspace: Path) -> dict[str, str]:
    base_files: dict[str, str] = {}
    go_manifest = workspace / "go.mod"
    if go_manifest.is_file():
        with contextlib.suppress(OSError, UnicodeDecodeError):
            base_files["go.mod"] = go_manifest.read_text(encoding="utf-8")
    for path in sorted(workspace.rglob("*.go")):
        if not path.is_file() or _is_generated_build_path(path) or path.name.endswith("_test.go"):
            continue
        try:
            relative_path = path.relative_to(workspace).as_posix()
        except ValueError:
            continue
        try:
            base_files[relative_path] = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
    return base_files


def _collect_rust_base_files(workspace: Path) -> dict[str, str]:
    base_files: dict[str, str] = {}
    cargo_manifest = workspace / "Cargo.toml"
    if cargo_manifest.is_file():
        with contextlib.suppress(OSError, UnicodeDecodeError):
            base_files["Cargo.toml"] = cargo_manifest.read_text(encoding="utf-8")
    for path in sorted(workspace.rglob("*.rs")):
        if not path.is_file() or any(part in _RUST_BASE_FILE_IGNORES for part in path.parts):
            continue
        try:
            relative_path = path.relative_to(workspace).as_posix()
        except ValueError:
            continue
        try:
            base_files[relative_path] = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
    return base_files


def _rust_declared_binary_paths(cargo_text: str) -> tuple[str, ...]:
    try:
        cargo = tomllib.loads(str(cargo_text or ""))
    except (RuntimeError, tomllib.TOMLDecodeError, TypeError, ValueError):
        return ()
    bins = cargo.get("bin")
    if not isinstance(bins, list):
        return ()
    paths: list[str] = []
    seen: set[str] = set()
    for entry in bins:
        if not isinstance(entry, dict):
            continue
        path = _normalize_rust_declared_binary_path(str(entry.get("path") or ""))
        if not path or path in seen:
            continue
        seen.add(path)
        paths.append(path)
    return tuple(paths)


def _normalize_rust_declared_binary_path(path: str) -> str:
    normalized = str(path or "").strip().replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    if not normalized or normalized.startswith("/") or not normalized.endswith(".rs"):
        return ""
    if any(part == ".." for part in normalized.split("/")):
        return ""
    return normalized


def _rust_missing_module_candidate_paths_from_errors(errors: tuple[str, ...]) -> tuple[str, ...]:
    paths: list[str] = []
    seen: set[str] = set()
    for error in errors:
        raw_text = _ANSI_ESCAPE_RE.sub("", str(error or ""))
        for help_match in _RUST_E0583_HELP_LINE_RE.finditer(raw_text):
            candidates = str(help_match.group("candidates") or "")
            for path_match in _RUST_QUOTED_RS_PATH_RE.finditer(candidates):
                normalized = _normalize_rust_missing_module_create_path(str(path_match.group("path") or ""))
                if normalized and normalized not in seen:
                    seen.add(normalized)
                    paths.append(normalized)
    return tuple(paths)


def _rust_duplicate_module_candidate_paths_from_errors(errors: tuple[str, ...]) -> tuple[str, ...]:
    paths: list[str] = []
    seen: set[str] = set()
    for error in errors:
        raw_text = _ANSI_ESCAPE_RE.sub("", str(error or ""))
        if "E0761" not in raw_text or "found at both" not in raw_text:
            continue
        for path_match in _RUST_QUOTED_RS_PATH_RE.finditer(raw_text):
            normalized = _normalize_rust_missing_module_create_path(str(path_match.group("path") or ""))
            if normalized and normalized not in seen:
                seen.add(normalized)
                paths.append(normalized)
    return tuple(paths)


def _normalize_rust_missing_module_create_path(path: str) -> str:
    raw = str(path or "").strip().replace("\\", "/")
    normalized = raw
    while normalized.startswith("./"):
        normalized = normalized[2:]
    if not normalized or normalized.startswith("/") or not normalized.endswith(".rs"):
        return ""
    if re.match(r"^[A-Za-z]:/", raw):
        return ""
    raw_parts = normalized.split("/")
    parts = tuple(part for part in raw_parts if part)
    if not parts or len(parts) != len(raw_parts):
        return ""
    if any(part in {".", "..", "target", "build", "out"} for part in parts):
        return ""
    return normalized


def _post_execution_artifact_quality_errors(adapter: Any) -> tuple[str, ...]:
    candidates: list[Any] = [
        getattr(adapter, "artifact_quality_errors", None),
        getattr(adapter, "_artifact_quality_errors", None),
        getattr(adapter, "last_artifact_quality_errors", None),
        getattr(adapter, "_last_artifact_quality_errors", None),
    ]
    execution = getattr(adapter, "_execution", None)
    if execution is not None:
        candidates.extend(
            [
                getattr(execution, "artifact_quality_errors", None),
                getattr(execution, "_artifact_quality_errors", None),
                getattr(execution, "last_artifact_quality_errors", None),
                getattr(execution, "_last_artifact_quality_errors", None),
            ]
        )
    errors: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        if isinstance(candidate, str):
            items = (candidate,)
        elif isinstance(candidate, (list, tuple, set)):
            items = tuple(candidate)
        else:
            continue
        for item in items:
            text = str(item or "").strip()
            if not text or text in seen:
                continue
            seen.add(text)
            errors.append(text)
    return tuple(errors)


def _rust_post_execution_artifact_quality_errors(adapter: Any) -> tuple[str, ...]:
    return _post_execution_artifact_quality_errors(adapter)


def _collect_java_base_files(workspace: Path) -> dict[str, str]:
    base_files: dict[str, str] = {}
    for path in sorted(workspace.rglob("*.java")):
        if not path.is_file() or _is_generated_build_path(path):
            continue
        try:
            relative_path = path.relative_to(workspace).as_posix()
        except ValueError:
            continue
        try:
            base_files[relative_path] = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
    return base_files


def _collect_java_test_base_files(workspace: Path) -> dict[str, str]:
    base_files: dict[str, str] = {}
    for path in sorted(workspace.rglob("*.java")):
        if not path.is_file() or _is_generated_build_path(path):
            continue
        try:
            relative_path = path.relative_to(workspace).as_posix()
        except ValueError:
            continue
        if not _is_java_test_source_path(relative_path):
            continue
        try:
            base_files[relative_path] = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
    return base_files


def _is_java_test_source_path(path: str) -> bool:
    parts = tuple(part.lower() for part in str(path or "").replace("\\", "/").split("/") if part)
    if len(parts) >= 3 and parts[0] == "src" and parts[1] == "test":
        return True
    return "test" in parts or "tests" in parts


def _looks_like_cpp_workspace(workspace: Path) -> bool:
    return (workspace / "CMakeLists.txt").exists() or any(
        path.is_file() and path.suffix in _CPP_REPAIR_FILE_SUFFIXES for path in workspace.rglob("*")
    )


def _is_generated_build_path(path: Path) -> bool:
    return "build" in path.parts or "cmake-build" in path.parts


def _rust_typed_receipt_unbound_source_tools(
    runtime_executable_source_tools: frozenset[str],
) -> frozenset[str]:
    return frozenset(
        source_tool
        for source_tool in _RUST_TYPED_RECEIPT_CUTOVER_SOURCE_TOOLS
        if source_tool not in runtime_executable_source_tools
    )


def _rust_typed_receipt_remaining_source_tools(
    runtime_executable_source_tools: frozenset[str],
) -> list[str]:
    return sorted(_rust_typed_receipt_unbound_source_tools(runtime_executable_source_tools))


def _rust_typed_receipt_runtime_migrated_subcases(
    runtime_executable_source_tools: frozenset[str],
) -> list[str]:
    subcases: set[str] = set()
    for source_tool, source_tool_subcases in _RUST_TYPED_RECEIPT_CUTOVER_SUBCASES_BY_SOURCE_TOOL.items():
        if source_tool in runtime_executable_source_tools:
            subcases.update(source_tool_subcases)
    return sorted(subcases)


def _rust_typed_receipt_remaining_subcases(
    runtime_executable_source_tools: frozenset[str],
) -> list[str]:
    subcases: set[str] = set()
    for source_tool, source_tool_subcases in _RUST_TYPED_RECEIPT_CUTOVER_SUBCASES_BY_SOURCE_TOOL.items():
        if source_tool not in runtime_executable_source_tools:
            subcases.update(source_tool_subcases)
    return sorted(subcases)


def _record_to_tool_result(
    record: dict[str, Any],
    *,
    source_tool: str,
    default_action: str,
) -> dict[str, Any]:
    return _write_tool_result(_record_payload(record, source_tool=source_tool, default_action=default_action))


def _record_payload(record: dict[str, Any], *, source_tool: str, default_action: str) -> dict[str, Any]:
    file_path = str(record.get("file") or "")
    return {
        "ok": True,
        "source_tool": source_tool,
        "source_tools": [source_tool],
        "file": file_path,
        "action": str(record.get("action") or default_action),
        "operation": "modify",
        "applied_tool_name": "write_file",
        "receipt_authority": "non_authoritative_adapter_projection_record",
        "receipt_status": "pending_revalidation",
        "authoritative": False,
        "typed_receipt_path_available": False,
        "evidence_status": "missing_evidence",
        "evidence_missing": True,
        "repair_success_verdict": False,
        "verifier_evidence_required": True,
        "verifier_evidence_present": False,
        "repair_kernel": _adapter_projection_repair_kernel_payload(
            source_tool=source_tool,
            file_path=file_path,
        ),
    }


def _write_tool_result(result: dict[str, Any], *, tool_name: str = "write_file") -> dict[str, Any]:
    return {
        "tool": tool_name,
        "tool_name": tool_name,
        "success": bool(result.get("ok", True)),
        "result": result,
    }


def _normalize_resident_agi_repair_advisory_overlay(
    overlay: dict[str, Any] | None,
) -> dict[str, Any]:
    payload = overlay if isinstance(overlay, dict) else {}
    advisor_notes_raw = payload.get("advisor_notes")
    raw_advisor_notes = (
        [item for item in advisor_notes_raw if isinstance(item, dict)] if isinstance(advisor_notes_raw, list) else []
    )
    advisor_notes, validation_errors = _validate_resident_agi_advisor_notes(raw_advisor_notes)
    suggested_rule_count = sum(
        len(note.get("suggested_rules") or [])
        for note in advisor_notes
        if isinstance(note.get("suggested_rules"), list)
    )

    ready = str(payload.get("status") or "").strip() == "ready"
    eligible = bool(payload.get("eligible_for_director_injection"))
    advisory_only = bool(payload.get("advisory_only", True))
    authoritative = bool(payload.get("authoritative"))
    agi_execution_authority = bool(payload.get("agi_execution_authority"))
    active = ready and eligible and advisory_only and not authoritative and not agi_execution_authority
    return {
        "schema_version": "director.post_execution_resident_agi_advisory_overlay.v1",
        "source": payload.get("source") or "resident.autonomy.public.build_resident_agi_repair_advisory_overlay",
        "status": payload.get("status") or "not_provided",
        "supported": True,
        "active": active,
        "eligible_for_director_injection": eligible,
        "authoritative": False,
        "advisory_only": True,
        "writes_allowed": False,
        "agi_execution_authority": False,
        "advisor_note_count": len(advisor_notes),
        "suggested_rule_count": suggested_rule_count,
        "advisor_notes": advisor_notes if active else [],
        "validation_error_count": len(validation_errors),
        "validation_errors": validation_errors,
        "reason": payload.get("reason") or payload.get("error") or "",
        "director_runtime_contract": payload.get("director_runtime_contract") or "director.repair_advisory_policy.v1",
        "injection_policy": "director_runtime_advisory_only_no_writes_no_registration",
    }


def _runtime_advisor_notes_from_overlay(overlay: dict[str, Any]) -> RuntimeAdvisorNotes:
    if not bool(overlay.get("active")):
        return ()
    raw_notes = overlay.get("advisor_notes")
    if not isinstance(raw_notes, list):
        return ()
    notes: list[RepairAdvisoryV1] = []
    for raw_note in raw_notes:
        if not isinstance(raw_note, dict):
            continue
        suggested_rules = raw_note.get("suggested_rules")
        metadata = raw_note.get("metadata")
        with contextlib.suppress(TypeError, ValueError):
            notes.append(
                RepairAdvisoryV1(
                    advisor_source=str(raw_note.get("advisor_source") or raw_note.get("source") or "resident_agi"),
                    message=str(raw_note.get("message") or ""),
                    confidence=float(raw_note.get("confidence") or 0.0),
                    suggested_rules=tuple(item for item in suggested_rules if isinstance(item, dict))
                    if isinstance(suggested_rules, list)
                    else (),
                    metadata=dict(metadata) if isinstance(metadata, dict) else {},
                )
            )
    return tuple(notes)


def _validate_resident_agi_advisor_notes(advisor_notes: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[str]]:
    normalized_notes: list[dict[str, Any]] = []
    validation_errors: list[str] = []
    for index, note in enumerate(advisor_notes):
        suggested_rules = note.get("suggested_rules")
        result = validate_director_repair_advisory(
            QueryDirectorRepairAdvisoryValidationV1(
                advisor_source=str(note.get("advisor_source") or note.get("source") or "resident_agi"),
                message=str(note.get("message") or ""),
                confidence=float(note.get("confidence") or 0.0),
                suggested_rules=tuple(item for item in suggested_rules if isinstance(item, dict))
                if isinstance(suggested_rules, list)
                else (),
                metadata=dict(note.get("metadata") or {}),
            )
        )
        if result.ok and result.normalized_advisory is not None:
            normalized_notes.append(dict(result.normalized_advisory))
            continue
        errors = list(result.errors or ("advisory validation rejected note",))
        validation_errors.extend(f"advisor_notes[{index}]: {error}" for error in errors)
    return normalized_notes, validation_errors


def _build_scheduler_bridge_summary(
    tool_results: list[dict[str, Any]],
    *,
    repair_kernel: dict[str, Any],
    ordered_steps: tuple[DirectorRepairPostExecutionStepV1, ...],
    schedule_summary: dict[str, Any] | None = None,
    receipt_projections: list[dict[str, Any]] | None = None,
    resident_agi_repair_advisory_overlay: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payloads = [_result_payload(item) for item in tool_results]
    schedule_summary_payload = dict(schedule_summary or {})
    receipts = repair_kernel.get("receipts")
    receipt_payloads = receipts if isinstance(receipts, list) else []
    active_step_ids = _sorted_unique(str(payload.get("bridge_step_id") or "") for payload in payloads)
    agi_overlay = resident_agi_repair_advisory_overlay or {}
    migration_debt = dict(repair_kernel.get("repair_kernel_migration_debt") or {})
    rust_typed_receipt_cutover_evidence = dict(migration_debt.get("rust_typed_receipt_cutover_evidence") or {})
    schedule_receipt_projections = _callback_receipt_projections_from_schedule_result(receipt_projections or [])
    callback_receipt_projections = schedule_receipt_projections or _callback_receipt_projections_from_payloads(payloads)
    return {
        "schema_version": "director.post_execution_scheduler_bridge.v1",
        "mode": "adapter_projection_bridge",
        "target_scheduler": "director.runtime.repair_kernel.scheduler",
        "schedule_source": "director.runtime.public.query_director_repair_post_execution_schedule",
        "runner_binding_owner": "roles.adapters",
        "adapter_projection_bridge": True,
        "adapter_callback_bridge": False,
        "step_order": [step.to_dict() for step in ordered_steps],
        "active_step_ids": active_step_ids,
        "observed_max_round": max(
            _max_int(payloads, "round_number"),
            _max_int(callback_receipt_projections, "round_number"),
        ),
        "configured_max_rounds": max(
            _schedule_summary_int(schedule_summary_payload, "max_rounds"),
            _max_revalidation_int(payloads, "max_rounds"),
        ),
        "tool_result_count": len(tool_results),
        "source_tools": _sorted_unique(str(payload.get("source_tool") or "") for payload in payloads),
        "phases": _count_by_payload_key(payloads, "phase", default="post_execution"),
        "priorities": _count_by_payload_key(payloads, "priority", default="1"),
        "rounds": _count_by_payload_key(payloads, "round_number", default="0"),
        "evidence_statuses": _count_by_payload_key(payloads, "evidence_status", default="missing_evidence"),
        "receipt_count": len(receipt_payloads),
        "receipts_with_revalidation": sum(1 for receipt in receipt_payloads if receipt.get("revalidation_evidence")),
        "authoritative": bool(repair_kernel.get("authoritative")),
        "adapter_receipt_projection_count": len(callback_receipt_projections),
        "adapter_receipts_authoritative": False,
        "adapter_receipt_authority_values": _sorted_unique(
            _callback_receipt_authority_value(projection) for projection in callback_receipt_projections
        ),
        "adapter_receipts_with_revalidation": sum(
            1 for projection in callback_receipt_projections if _callback_projection_has_revalidation(projection)
        ),
        "callback_receipt_projection_count": len(callback_receipt_projections),
        "callback_receipts_authoritative": False,
        "callback_receipt_authority_values": _sorted_unique(
            _callback_receipt_authority_value(projection) for projection in callback_receipt_projections
        ),
        "callback_receipts_with_revalidation": sum(
            1 for projection in callback_receipt_projections if _callback_projection_has_revalidation(projection)
        ),
        "typed_receipt_path_available": False,
        "adapter_projection_claimed_typed_receipt_path_count": sum(
            1
            for projection in callback_receipt_projections
            if _callback_projection_claims_typed_receipt_path_available(projection)
        ),
        "callback_projection_claimed_typed_receipt_path_count": sum(
            1
            for projection in callback_receipt_projections
            if _callback_projection_claims_typed_receipt_path_available(projection)
        ),
        "migration_blocker": _CALLBACK_RECEIPT_MIGRATION_BLOCKER,
        "resident_agi_advisory_active": bool(agi_overlay.get("active")),
        "resident_agi_advisory_note_count": int(agi_overlay.get("advisor_note_count") or 0),
        "resident_agi_suggested_rule_count": int(agi_overlay.get("suggested_rule_count") or 0),
        "repair_kernel_migration_debt": migration_debt,
        "adapter_projection_debt": dict(migration_debt.get("adapter_projection_debt") or {}),
        **_rust_typed_receipt_cutover_projection_fields(rust_typed_receipt_cutover_evidence),
    }


def _callback_receipt_projections_from_schedule_result(
    receipt_projections: list[Any],
) -> list[dict[str, Any]]:
    projections: list[dict[str, Any]] = []
    for projection in receipt_projections:
        normalized = _callback_receipt_projection_to_dict(projection)
        if not normalized:
            continue
        normalized["authoritative"] = False
        normalized.setdefault("projection_source", "schedule_result.receipt_projections")
        normalized.setdefault("summary_only", False)
        normalized.setdefault("typed_receipt_path_available", False)
        normalized.setdefault("migration_blocker", _CALLBACK_RECEIPT_MIGRATION_BLOCKER)
        if "bridge_step_id" not in normalized and normalized.get("step_id"):
            normalized["bridge_step_id"] = normalized.get("step_id")
        if not _callback_receipt_authority_value(normalized):
            normalized["receipt_authority"] = "non_authoritative_adapter_projection"
        projections.append(normalized)
    return projections


def _callback_receipt_projection_to_dict(projection: Any) -> dict[str, Any]:
    to_dict = getattr(projection, "to_dict", None)
    if callable(to_dict):
        try:
            value = to_dict()
        except (AttributeError, TypeError, ValueError, RuntimeError) as exc:
            return {
                "projection_error": "projection_to_dict_failed",
                "projection_type": type(projection).__name__,
                "error": str(exc),
            }
        if isinstance(value, Mapping):
            return dict(value)
        return {
            "projection_error": "projection_to_dict_returned_non_mapping",
            "projection_type": type(projection).__name__,
            "value_type": type(value).__name__,
        }
    if isinstance(projection, Mapping):
        return dict(projection)
    return {
        "projection_error": "unsupported_projection_type",
        "projection_type": type(projection).__name__,
    }


def _callback_receipt_projections_from_payloads(payloads: list[dict[str, Any]]) -> list[dict[str, Any]]:
    projections: list[dict[str, Any]] = []
    for payload in payloads:
        explicit_projections = _explicit_callback_receipt_projections(payload)
        if explicit_projections:
            projections.extend(
                _normalize_callback_receipt_projection(
                    projection,
                    payload=payload,
                    source="payload.callback_receipt_projection",
                )
                for projection in explicit_projections
            )
            continue
        if _payload_has_callback_receipt_projection_annotation(payload):
            projections.append(_summary_only_callback_receipt_projection(payload))
    return projections


def _explicit_callback_receipt_projections(payload: dict[str, Any]) -> list[dict[str, Any]]:
    projections: list[dict[str, Any]] = []
    for key in ("callback_receipt_projection", "callback_receipt_projections"):
        raw_projection = payload.get(key)
        if isinstance(raw_projection, dict):
            projections.append(dict(raw_projection))
            continue
        if isinstance(raw_projection, list):
            projections.extend(dict(item) for item in raw_projection if isinstance(item, dict))
    return projections


def _normalize_callback_receipt_projection(
    projection: dict[str, Any],
    *,
    payload: dict[str, Any],
    source: str,
) -> dict[str, Any]:
    normalized = dict(projection)
    normalized["authoritative"] = False
    normalized.setdefault("projection_source", source)
    normalized.setdefault("summary_only", False)
    normalized.setdefault("source_tool", payload.get("source_tool"))
    normalized.setdefault("bridge_step_id", payload.get("bridge_step_id"))
    normalized.setdefault("typed_receipt_path_available", _payload_typed_receipt_path_available(payload))
    normalized.setdefault("migration_blocker", _payload_migration_blocker(payload))
    if not _callback_receipt_authority_value(normalized):
        normalized["receipt_authority"] = _payload_receipt_authority(payload) or "non_authoritative_adapter_projection"
    _attach_payload_revalidation_to_projection(normalized, payload)
    return normalized


def _summary_only_callback_receipt_projection(payload: dict[str, Any]) -> dict[str, Any]:
    projection = {
        "projection_source": "summary_only_payload_annotation",
        "summary_only": True,
        "source_tool": payload.get("source_tool"),
        "bridge_step_id": payload.get("bridge_step_id"),
        "authoritative": False,
        "receipt_authority": _payload_receipt_authority(payload) or "non_authoritative_adapter_projection",
        "typed_receipt_path_available": _payload_typed_receipt_path_available(payload),
        "migration_blocker": _payload_migration_blocker(payload),
    }
    _attach_payload_revalidation_to_projection(projection, payload)
    return projection


def _payload_has_callback_receipt_projection_annotation(payload: dict[str, Any]) -> bool:
    if any(
        bool(payload.get(key))
        for key in (
            "adapter_callback_bridge",
            "produces_tool_results_only",
            "callback_migration_envelope",
            "migration_callback_envelope",
            "convergence_scheduler_required",
        )
    ):
        return True
    if payload.get("typed_receipt_path") == "unavailable_in_callback_bridge":
        return True
    if payload.get("typed_receipt_path_available") is False and payload.get("preferred_typed_receipt_entrypoint"):
        return True
    revalidation = payload.get("revalidation")
    return isinstance(revalidation, dict) and any(
        bool(revalidation.get(key)) for key in ("callback_migration_envelope", "convergence_scheduler_required")
    )


def _attach_payload_revalidation_to_projection(
    projection: dict[str, Any],
    payload: dict[str, Any],
) -> None:
    revalidation = payload.get("revalidation")
    if "revalidation" not in projection and isinstance(revalidation, dict) and revalidation:
        projection["revalidation"] = dict(revalidation)
    revalidation_evidence = _payload_revalidation_evidence(payload)
    if "revalidation_evidence" not in projection and revalidation_evidence:
        projection["revalidation_evidence"] = revalidation_evidence


def _payload_revalidation_evidence(payload: dict[str, Any]) -> dict[str, Any]:
    evidence = payload.get("revalidation_evidence")
    if isinstance(evidence, dict) and evidence:
        return dict(evidence)
    repair_kernel = payload.get("repair_kernel")
    if isinstance(repair_kernel, dict):
        kernel_evidence = repair_kernel.get("revalidation_evidence")
        if isinstance(kernel_evidence, dict) and kernel_evidence:
            return dict(kernel_evidence)
    return {}


def _payload_receipt_authority(payload: dict[str, Any]) -> str:
    for source in (payload, payload.get("repair_kernel")):
        if not isinstance(source, dict):
            continue
        for key in ("receipt_authority", "receipt_authority_value", "authority", "authority_value"):
            value = str(source.get(key) or "").strip()
            if value:
                return value
    return ""


def _callback_receipt_authority_value(projection: dict[str, Any]) -> str:
    for key in ("receipt_authority", "receipt_authority_value", "authority", "authority_value"):
        value = str(projection.get(key) or "").strip()
        if value:
            return value
    return ""


def _callback_projection_has_revalidation(projection: dict[str, Any]) -> bool:
    if bool(projection.get("revalidation_evidence_present")):
        return True
    for key in ("revalidation", "revalidation_evidence"):
        value = projection.get(key)
        if isinstance(value, dict) and value:
            return True
        if isinstance(value, list) and value:
            return True
    return False


def _callback_projection_claims_typed_receipt_path_available(projection: dict[str, Any]) -> bool:
    if _bool_claim(projection.get("typed_receipt_path_available")):
        return True
    metadata = projection.get("metadata")
    if isinstance(metadata, dict):
        return _bool_claim(metadata.get("claimed_typed_receipt_path_available"))
    return False


def _payload_typed_receipt_path_available(payload: dict[str, Any]) -> bool:
    if "typed_receipt_path_available" in payload:
        return _bool_claim(payload.get("typed_receipt_path_available"))
    revalidation = payload.get("revalidation")
    if isinstance(revalidation, dict):
        return _bool_claim(revalidation.get("typed_receipt_path_available"))
    return False


def _payload_migration_blocker(payload: dict[str, Any]) -> str:
    blocker = str(payload.get("migration_blocker") or "").strip()
    return blocker or _CALLBACK_RECEIPT_MIGRATION_BLOCKER


def _source_tool_counts(source_tools: list[str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for source_tool in source_tools:
        key = str(source_tool or "").strip()
        if not key:
            continue
        counts[key] = counts.get(key, 0) + 1
    return dict(sorted(counts.items()))


def _build_rust_typed_receipt_cutover_evidence(
    *,
    remaining_source_tools: list[str],
    allowed_source_tools: list[str],
    blocked_source_tools: list[str],
    blocked_migrated_source_tools: list[str],
    remaining_adapter_subcases: list[str],
    runtime_migrated_subcases: list[str],
    blocked_subcases: list[str],
    blocked_migrated_subcases: list[str],
) -> dict[str, Any]:
    remaining = _sorted_unique(remaining_source_tools)
    allowed = _sorted_unique(allowed_source_tools)
    blocked = _sorted_unique(blocked_source_tools)
    blocked_migrated = _sorted_unique(blocked_migrated_source_tools)
    remaining_subcases = _sorted_unique(remaining_adapter_subcases)
    migrated_subcases = _sorted_unique(runtime_migrated_subcases)
    blocked_adapter_subcases = _sorted_unique(blocked_subcases)
    blocked_migrated_adapter_subcases = _sorted_unique(blocked_migrated_subcases)
    remaining_source_tool_blockers = _sorted_unique(f"remaining_source_tool:{source_tool}" for source_tool in remaining)
    remaining_adapter_subcase_blockers = _sorted_unique(
        f"remaining_adapter_subcase:{subcase}" for subcase in remaining_subcases
    )
    blocked_source_tool_blockers = _sorted_unique(f"blocked_source_tool:{source_tool}" for source_tool in blocked)
    blocked_migrated_source_tool_blockers = _sorted_unique(
        f"blocked_migrated_source_tool:{source_tool}" for source_tool in blocked_migrated
    )
    blocked_adapter_subcase_blockers = _sorted_unique(
        f"blocked_adapter_subcase:{subcase}" for subcase in blocked_adapter_subcases
    )
    blocked_migrated_adapter_subcase_blockers = _sorted_unique(
        f"blocked_migrated_subcase:{subcase}" for subcase in blocked_migrated_adapter_subcases
    )
    cutover_ready = not any(
        (
            remaining,
            blocked,
            blocked_migrated,
            remaining_subcases,
            blocked_adapter_subcases,
            blocked_migrated_adapter_subcases,
        )
    )
    authority_blockers = [] if cutover_ready else ["typed_receipt_cutover_not_authoritative"]
    blockers = _sorted_unique(
        [
            *authority_blockers,
            *remaining_source_tool_blockers,
            *remaining_adapter_subcase_blockers,
            *blocked_source_tool_blockers,
            *blocked_migrated_source_tool_blockers,
            *blocked_adapter_subcase_blockers,
            *blocked_migrated_adapter_subcase_blockers,
        ]
    )
    return {
        "schema_version": "director.rust_typed_receipt_cutover_evidence.v1",
        "typed_receipt_cutover_authoritative": cutover_ready,
        "typed_receipt_cutover_writes_authoritative": cutover_ready,
        "receipt_authority": "director.runtime.repair_kernel" if cutover_ready else "migration_debt_projection",
        "authority_boundary": (
            "runtime_typed_receipt_authoritative"
            if cutover_ready
            else "adapter_projection_only_not_runtime_repair_receipt"
        ),
        "cutover_ready": cutover_ready,
        "cutover_blockers": blockers,
        "remaining_source_tool_blockers": remaining_source_tool_blockers,
        "remaining_adapter_subcase_blockers": remaining_adapter_subcase_blockers,
        "blocked_source_tool_blockers": blocked_source_tool_blockers,
        "blocked_migrated_source_tool_blockers": blocked_migrated_source_tool_blockers,
        "blocked_adapter_subcase_blockers": blocked_adapter_subcase_blockers,
        "blocked_migrated_subcase_blockers": blocked_migrated_adapter_subcase_blockers,
        "remaining_source_tools": remaining,
        "remaining_source_tool_count": len(remaining),
        "remaining_source_tool_counts": _source_tool_counts(remaining),
        "remaining_source_tools_without_runtime_receipt": allowed,
        "remaining_source_tool_without_runtime_receipt_count": len(allowed),
        "remaining_source_tool_without_runtime_receipt_counts": _source_tool_counts(allowed),
        "blocked_source_tools": blocked,
        "blocked_source_tool_count": len(blocked),
        "blocked_source_tool_counts": _source_tool_counts(blocked),
        "blocked_migrated_source_tools": blocked_migrated,
        "blocked_migrated_source_tool_count": len(blocked_migrated),
        "blocked_migrated_source_tool_counts": _source_tool_counts(blocked_migrated),
        "remaining_adapter_subcases": remaining_subcases,
        "remaining_adapter_subcase_count": len(remaining_subcases),
        "remaining_adapter_subcase_counts": _source_tool_counts(remaining_subcases),
        "runtime_migrated_subcases": migrated_subcases,
        "runtime_migrated_subcase_count": len(migrated_subcases),
        "runtime_migrated_subcase_counts": _source_tool_counts(migrated_subcases),
        "blocked_subcases": blocked_adapter_subcases,
        "blocked_subcase_count": len(blocked_adapter_subcases),
        "blocked_subcase_counts": _source_tool_counts(blocked_adapter_subcases),
        "blocked_migrated_subcases": blocked_migrated_adapter_subcases,
        "blocked_migrated_subcase_count": len(blocked_migrated_adapter_subcases),
        "blocked_migrated_subcase_counts": _source_tool_counts(blocked_migrated_adapter_subcases),
    }


def _rust_typed_receipt_cutover_projection_fields(cutover_evidence: dict[str, Any]) -> dict[str, Any]:
    evidence = dict(cutover_evidence or {})
    cutover_authoritative = bool(evidence.get("typed_receipt_cutover_authoritative"))
    cutover_writes_authoritative = bool(evidence.get("typed_receipt_cutover_writes_authoritative"))
    cutover_ready = bool(evidence.get("cutover_ready"))
    return {
        "rust_typed_receipt_cutover_evidence": evidence,
        "rust_typed_receipt_cutover_authoritative": cutover_authoritative,
        "rust_typed_receipt_cutover_writes_authoritative": cutover_writes_authoritative,
        "rust_typed_receipt_cutover_ready": cutover_ready,
        "rust_typed_receipt_cutover_blockers": list(evidence.get("cutover_blockers") or []),
        "rust_typed_receipt_cutover_not_authoritative": not cutover_authoritative,
        "rust_typed_receipt_authority_boundary": str(evidence.get("authority_boundary") or ""),
        "rust_typed_receipt_remaining_source_tool_blockers": list(evidence.get("remaining_source_tool_blockers") or []),
        "rust_typed_receipt_remaining_subcase_blockers": list(evidence.get("remaining_adapter_subcase_blockers") or []),
        "rust_typed_receipt_blocked_source_tool_blockers": list(evidence.get("blocked_source_tool_blockers") or []),
        "rust_typed_receipt_blocked_migrated_source_tool_blockers": list(
            evidence.get("blocked_migrated_source_tool_blockers") or []
        ),
        "rust_typed_receipt_blocked_subcase_blockers": list(evidence.get("blocked_adapter_subcase_blockers") or []),
        "rust_typed_receipt_blocked_migrated_subcase_blockers": list(
            evidence.get("blocked_migrated_subcase_blockers") or []
        ),
        "rust_typed_receipt_remaining_source_tool_count": int(evidence.get("remaining_source_tool_count") or 0),
        "rust_typed_receipt_remaining_source_tool_counts": dict(evidence.get("remaining_source_tool_counts") or {}),
        "rust_typed_receipt_blocked_source_tool_count": int(evidence.get("blocked_source_tool_count") or 0),
        "rust_typed_receipt_blocked_source_tool_counts": dict(evidence.get("blocked_source_tool_counts") or {}),
        "rust_typed_receipt_blocked_migrated_source_tool_count": int(
            evidence.get("blocked_migrated_source_tool_count") or 0
        ),
        "rust_typed_receipt_blocked_migrated_source_tool_counts": dict(
            evidence.get("blocked_migrated_source_tool_counts") or {}
        ),
        "rust_typed_receipt_remaining_subcases": list(evidence.get("remaining_adapter_subcases") or []),
        "rust_typed_receipt_remaining_subcase_count": int(evidence.get("remaining_adapter_subcase_count") or 0),
        "rust_typed_receipt_remaining_subcase_counts": dict(evidence.get("remaining_adapter_subcase_counts") or {}),
        "rust_typed_receipt_runtime_migrated_subcases": list(evidence.get("runtime_migrated_subcases") or []),
        "rust_typed_receipt_runtime_migrated_subcase_count": int(evidence.get("runtime_migrated_subcase_count") or 0),
        "rust_typed_receipt_runtime_migrated_subcase_counts": dict(
            evidence.get("runtime_migrated_subcase_counts") or {}
        ),
        "rust_typed_receipt_blocked_subcases": list(evidence.get("blocked_subcases") or []),
        "rust_typed_receipt_blocked_subcase_count": int(evidence.get("blocked_subcase_count") or 0),
        "rust_typed_receipt_blocked_subcase_counts": dict(evidence.get("blocked_subcase_counts") or {}),
        "rust_typed_receipt_blocked_migrated_subcases": list(evidence.get("blocked_migrated_subcases") or []),
        "rust_typed_receipt_blocked_migrated_subcase_count": int(evidence.get("blocked_migrated_subcase_count") or 0),
        "rust_typed_receipt_blocked_migrated_subcase_counts": dict(
            evidence.get("blocked_migrated_subcase_counts") or {}
        ),
    }


def _bool_claim(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes"}
    return bool(value)


def _build_repair_kernel_migration_debt(
    tool_results: list[dict[str, Any]],
    *,
    ordered_steps: tuple[DirectorRepairPostExecutionStepV1, ...],
    convergence_verifier_present: bool,
) -> dict[str, Any]:
    payloads = [_result_payload(item) for item in tool_results]
    executable_source_tools = _runtime_executable_source_tools()
    remaining_source_tools = _rust_typed_receipt_remaining_source_tools(executable_source_tools)
    source_tools_without_runtime_receipt = sorted(_rust_typed_receipt_unbound_source_tools(executable_source_tools))
    remaining_subcases = _rust_typed_receipt_remaining_subcases(executable_source_tools)
    runtime_migrated_subcases = _rust_typed_receipt_runtime_migrated_subcases(executable_source_tools)
    rust_typed_receipt_blocked_source_tools = _sorted_unique(
        source_tool
        for payload in payloads
        for source_tool in _payload_list_values(payload, "rust_typed_receipt_blocked_source_tools")
    )
    rust_typed_receipt_blocked_migrated_source_tools = _sorted_unique(
        source_tool
        for payload in payloads
        for source_tool in _payload_list_values(payload, "rust_typed_receipt_blocked_migrated_source_tools")
    )
    rust_typed_receipt_blocked_subcases = _sorted_unique(
        subcase
        for payload in payloads
        for subcase in _payload_list_values(payload, "rust_typed_receipt_blocked_subcases")
    )
    rust_typed_receipt_blocked_migrated_subcases = _sorted_unique(
        subcase
        for payload in payloads
        for subcase in _payload_list_values(payload, "rust_typed_receipt_blocked_migrated_subcases")
    )
    rust_typed_receipt_cutover_evidence = _build_rust_typed_receipt_cutover_evidence(
        remaining_source_tools=remaining_source_tools,
        allowed_source_tools=source_tools_without_runtime_receipt,
        blocked_source_tools=rust_typed_receipt_blocked_source_tools,
        blocked_migrated_source_tools=rust_typed_receipt_blocked_migrated_source_tools,
        remaining_adapter_subcases=remaining_subcases,
        runtime_migrated_subcases=runtime_migrated_subcases,
        blocked_subcases=rust_typed_receipt_blocked_subcases,
        blocked_migrated_subcases=rust_typed_receipt_blocked_migrated_subcases,
    )
    payloads_by_step: dict[str, list[dict[str, Any]]] = {}
    for payload in payloads:
        step_id = str(payload.get("bridge_step_id") or "").strip()
        if step_id:
            payloads_by_step.setdefault(step_id, []).append(payload)

    public_schedule = query_director_repair_post_execution_schedule()
    runtime_ordered_step_ids = [step.step_id for step in public_schedule.items]
    step_entries = [
        _build_step_migration_debt(
            step,
            payloads=payloads_by_step.get(step.step_id, []),
            executable_source_tools=executable_source_tools,
            convergence_verifier_present=convergence_verifier_present,
        )
        for step in ordered_steps
    ]
    adapter_only_step_count = sum(1 for step in step_entries if step["adapter_only_source_tools"])
    missing_evidence_step_count = sum(1 for step in step_entries if "missing_verifier_evidence" in step["blockers"])
    cutover_ready_step_count = sum(1 for step in step_entries if bool(step["cutover_ready"]))
    return {
        "schema_version": "director.post_execution_repair_kernel_migration_debt.v1",
        "owner_cell": "roles.adapters",
        "runtime_schedule_source": public_schedule.source,
        "runtime_ordered_step_ids": runtime_ordered_step_ids,
        "runner_binding_owner": public_schedule.runner_binding_owner,
        "convergence_verifier_present": convergence_verifier_present,
        "rust_typed_receipt_remaining_source_tools": remaining_source_tools,
        "rust_typed_receipt_source_tools_without_runtime_receipt": source_tools_without_runtime_receipt,
        "rust_typed_receipt_remaining_subcases": remaining_subcases,
        "rust_typed_receipt_runtime_migrated_subcases": runtime_migrated_subcases,
        "rust_typed_receipt_blocked_source_tools": rust_typed_receipt_blocked_source_tools,
        "rust_typed_receipt_blocked_migrated_source_tools": rust_typed_receipt_blocked_migrated_source_tools,
        "rust_typed_receipt_blocked_subcases": rust_typed_receipt_blocked_subcases,
        "rust_typed_receipt_blocked_migrated_subcases": rust_typed_receipt_blocked_migrated_subcases,
        **_rust_typed_receipt_cutover_projection_fields(rust_typed_receipt_cutover_evidence),
        "step_count": len(step_entries),
        "steps": step_entries,
        "adapter_projection_debt": {
            "schema_version": "director.post_execution_adapter_projection_debt.v1",
            "adapter_projection_bridge": True,
            "adapter_callback_bridge": False,
            "produces_tool_results_only": True,
            "preferred_typed_receipt_entrypoint": "run_runtime_repair_convergence",
            "runtime_bound_step_count": sum(1 for step in step_entries if step["runtime_executable_source_tools"]),
            "adapter_only_step_count": adapter_only_step_count,
            "missing_verifier_evidence_step_count": missing_evidence_step_count,
            "cutover_ready_step_count": cutover_ready_step_count,
            "cutover_ready": cutover_ready_step_count == len(step_entries) and bool(step_entries),
            "rust_typed_receipt_remaining_source_tools": remaining_source_tools,
            "rust_typed_receipt_source_tools_without_runtime_receipt": source_tools_without_runtime_receipt,
            "rust_typed_receipt_remaining_subcases": remaining_subcases,
            "rust_typed_receipt_runtime_migrated_subcases": runtime_migrated_subcases,
            "rust_typed_receipt_blocked_source_tools": rust_typed_receipt_blocked_source_tools,
            "rust_typed_receipt_blocked_migrated_source_tools": rust_typed_receipt_blocked_migrated_source_tools,
            "rust_typed_receipt_blocked_subcases": rust_typed_receipt_blocked_subcases,
            "rust_typed_receipt_blocked_migrated_subcases": rust_typed_receipt_blocked_migrated_subcases,
            **_rust_typed_receipt_cutover_projection_fields(rust_typed_receipt_cutover_evidence),
        },
    }


def _build_step_migration_debt(
    step: DirectorRepairPostExecutionStepV1,
    *,
    payloads: list[dict[str, Any]],
    executable_source_tools: frozenset[str],
    convergence_verifier_present: bool,
) -> dict[str, Any]:
    actual_source_tools = _actual_source_tools_for_payloads(payloads)
    runtime_executable_source_tools = [
        source_tool for source_tool in actual_source_tools if source_tool in executable_source_tools
    ]
    adapter_only_source_tools = [
        source_tool for source_tool in actual_source_tools if source_tool not in executable_source_tools
    ]
    write_tool_evidence = any(_payload_has_write_tool_evidence(payload) for payload in payloads)
    verifier_evidence_required = bool(actual_source_tools and write_tool_evidence)
    verifier_evidence_present = any(_payload_has_verifier_evidence(payload) for payload in payloads)
    convergence_path_available = bool(runtime_executable_source_tools)
    rust_typed_receipt_blocked_source_tools = _sorted_unique(
        source_tool
        for payload in payloads
        for source_tool in _payload_list_values(payload, "rust_typed_receipt_blocked_source_tools")
    )
    rust_typed_receipt_blocked_migrated_source_tools = _sorted_unique(
        source_tool
        for payload in payloads
        for source_tool in _payload_list_values(payload, "rust_typed_receipt_blocked_migrated_source_tools")
    )
    rust_typed_receipt_blocked_subcases = _sorted_unique(
        subcase
        for payload in payloads
        for subcase in _payload_list_values(payload, "rust_typed_receipt_blocked_subcases")
    )
    rust_typed_receipt_blocked_migrated_subcases = _sorted_unique(
        subcase
        for payload in payloads
        for subcase in _payload_list_values(payload, "rust_typed_receipt_blocked_migrated_subcases")
    )
    remaining_source_tools = _rust_typed_receipt_remaining_source_tools(executable_source_tools)
    source_tools_without_runtime_receipt = sorted(_rust_typed_receipt_unbound_source_tools(executable_source_tools))
    remaining_subcases = _rust_typed_receipt_remaining_subcases(executable_source_tools)
    runtime_migrated_subcases = _rust_typed_receipt_runtime_migrated_subcases(executable_source_tools)
    rust_typed_receipt_cutover_evidence = _build_rust_typed_receipt_cutover_evidence(
        remaining_source_tools=remaining_source_tools,
        allowed_source_tools=source_tools_without_runtime_receipt,
        blocked_source_tools=rust_typed_receipt_blocked_source_tools,
        blocked_migrated_source_tools=rust_typed_receipt_blocked_migrated_source_tools,
        remaining_adapter_subcases=remaining_subcases,
        runtime_migrated_subcases=runtime_migrated_subcases,
        blocked_subcases=rust_typed_receipt_blocked_subcases,
        blocked_migrated_subcases=rust_typed_receipt_blocked_migrated_subcases,
    )
    blockers: list[str] = []
    if not payloads:
        blockers.append("no_tool_results_observed")
    if actual_source_tools and not convergence_path_available:
        blockers.append("convergence_path_unavailable")
    if adapter_only_source_tools:
        blockers.append("adapter_only_source_tools_present")
    if step.source_tool not in executable_source_tools and runtime_executable_source_tools:
        blockers.append("declared_step_source_tool_uses_aggregate_runner")
    if actual_source_tools and not write_tool_evidence:
        blockers.append("missing_write_tool_evidence")
    if verifier_evidence_required and not verifier_evidence_present:
        blockers.append("missing_verifier_evidence")
    if convergence_path_available and not convergence_verifier_present:
        blockers.append("convergence_verifier_not_provided")
    if any(_payload_has_non_authoritative_runtime_receipt(payload) for payload in payloads):
        blockers.append("non_authoritative_runtime_receipt_requires_revalidation")
    if any(_payload_is_adapter_projection_record(payload) for payload in payloads):
        blockers.append("adapter_projection_record_requires_revalidation")
    if rust_typed_receipt_blocked_source_tools:
        blockers.append(_RUST_TYPED_RECEIPT_SOURCE_TOOL_BLOCKER)
    blockers = _sorted_unique(blockers)
    return {
        "step_id": step.step_id,
        "language": step.language,
        "phase": step.phase,
        "priority": step.priority,
        "declared_source_tool": step.source_tool,
        "actual_source_tools": actual_source_tools,
        "runtime_executable_source_tools": runtime_executable_source_tools,
        "adapter_only_source_tools": adapter_only_source_tools,
        "rust_typed_receipt_remaining_source_tools": remaining_source_tools,
        "rust_typed_receipt_source_tools_without_runtime_receipt": source_tools_without_runtime_receipt,
        "rust_typed_receipt_remaining_subcases": remaining_subcases,
        "rust_typed_receipt_runtime_migrated_subcases": runtime_migrated_subcases,
        "rust_typed_receipt_blocked_source_tools": rust_typed_receipt_blocked_source_tools,
        "rust_typed_receipt_blocked_migrated_source_tools": rust_typed_receipt_blocked_migrated_source_tools,
        "rust_typed_receipt_blocked_subcases": rust_typed_receipt_blocked_subcases,
        "rust_typed_receipt_blocked_migrated_subcases": rust_typed_receipt_blocked_migrated_subcases,
        **_rust_typed_receipt_cutover_projection_fields(rust_typed_receipt_cutover_evidence),
        "write_tool_evidence": write_tool_evidence,
        "convergence_path_available": convergence_path_available,
        "convergence_verifier_present": convergence_verifier_present,
        "verifier_evidence_required": verifier_evidence_required,
        "verifier_evidence_present": verifier_evidence_present,
        "cutover_ready": bool(
            actual_source_tools
            and write_tool_evidence
            and convergence_path_available
            and verifier_evidence_present
            and not adapter_only_source_tools
            and not blockers
        ),
        "blockers": blockers,
    }


def _adapter_projection_repair_kernel_payload(*, source_tool: str, file_path: str) -> dict[str, Any]:
    return {
        "owner_cell": "roles.adapters.strategy_host",
        "status": "pending_revalidation",
        "authoritative": False,
        "requires_revalidation": True,
        "verifier_evidence_required": True,
        "verifier_evidence_present": False,
        "evidence_status": "missing_evidence",
        "evidence_missing": True,
        "evidence_missing_reason": "missing_revalidation_evidence",
        "repair_success_verdict": False,
        "source_tool": source_tool,
        "source_tools": [source_tool],
        "file": file_path,
        "files_changed": [file_path] if file_path else [],
        "receipt_authority": "non_authoritative_adapter_projection_record",
        "typed_receipt_path_available": False,
        "applied_tool_name": "write_file",
        "revalidation_evidence": {},
        "migration_debt": {
            "schema_version": "director.post_execution_adapter_projection_record_debt.v1",
            "adapter_only_source_tools": [source_tool],
            "runtime_executable_source_tools": [],
            "cutover_ready": False,
            "blockers": [
                "adapter_only_source_tools_present",
                "missing_verifier_evidence",
                "adapter_projection_record_requires_revalidation",
            ],
        },
    }


def _runtime_executable_source_tools() -> frozenset[str]:
    catalog = query_director_repair_strategy_catalog(QueryDirectorRepairStrategyCatalogV1(include_items=False))
    summary = dict(catalog.summary)
    source_tools = summary.get("executable_runtime_source_tools")
    if not isinstance(source_tools, list):
        return frozenset()
    return frozenset(str(source_tool) for source_tool in source_tools if str(source_tool or "").strip())


def _actual_source_tools_for_payloads(payloads: list[dict[str, Any]]) -> list[str]:
    source_tools: list[str] = []
    for payload in payloads:
        source_tools.append(str(payload.get("source_tool") or ""))
        payload_source_tools = payload.get("source_tools")
        if isinstance(payload_source_tools, list):
            source_tools.extend(str(item) for item in payload_source_tools)
    return _sorted_unique(source_tools)


def _payload_has_write_tool_evidence(payload: dict[str, Any]) -> bool:
    if not bool(payload.get("ok", True)):
        return False
    return bool(
        payload.get("file")
        and (
            payload.get("bytes_written") is not None
            or payload.get("before_hash")
            or payload.get("after_hash")
            or payload.get("operation")
        )
    )


def _payload_has_verifier_evidence(payload: dict[str, Any]) -> bool:
    repair_kernel = payload.get("repair_kernel")
    if isinstance(repair_kernel, dict):
        if str(repair_kernel.get("evidence_status") or "").strip() == "missing_evidence":
            return False
        revalidation_evidence = repair_kernel.get("revalidation_evidence")
        if isinstance(revalidation_evidence, dict) and revalidation_evidence:
            return str(revalidation_evidence.get("evidence_status") or "").strip() != "missing_evidence"
        receipts = repair_kernel.get("receipts")
        if isinstance(receipts, list) and any(
            isinstance(receipt, dict)
            and str(receipt.get("evidence_status") or "").strip() != "missing_evidence"
            and bool(receipt.get("revalidation_evidence"))
            for receipt in receipts
        ):
            return True
    return False


def _payload_has_non_authoritative_runtime_receipt(payload: dict[str, Any]) -> bool:
    repair_kernel = payload.get("repair_kernel")
    if not isinstance(repair_kernel, dict):
        return False
    if repair_kernel.get("owner_cell") != "director.runtime":
        return False
    return not bool(repair_kernel.get("authoritative")) or bool(repair_kernel.get("requires_revalidation"))


def _payload_is_adapter_projection_record(payload: dict[str, Any]) -> bool:
    repair_kernel = payload.get("repair_kernel")
    return isinstance(repair_kernel, dict) and repair_kernel.get("owner_cell") == "roles.adapters.strategy_host"


def _runner_for_post_execution_step(step: DirectorRepairPostExecutionStepV1) -> StepRunner:
    runner = _POST_EXECUTION_REPAIR_RUNNERS.get(step.step_id)
    if runner is None:
        raise RuntimeError(f"post-execution repair schedule has no runner binding: {step.step_id}")
    return runner


def _result_payload(tool_result: dict[str, Any]) -> dict[str, Any]:
    result = tool_result.get("result")
    return result if isinstance(result, dict) else {}


def _payload_list_values(payload: dict[str, Any], key: str) -> list[Any]:
    values = payload.get(key)
    return list(values) if isinstance(values, list) else []


def _count_by_payload_key(
    payloads: list[dict[str, Any]],
    key: str,
    *,
    default: str,
) -> dict[str, int]:
    counts: dict[str, int] = {}
    for payload in payloads:
        value = str(payload.get(key) if payload.get(key) is not None else default).strip() or default
        counts[value] = counts.get(value, 0) + 1
    return dict(sorted(counts.items()))


def _sorted_unique(values: Any) -> list[str]:
    return sorted({str(value).strip() for value in values if str(value or "").strip()})


def _max_int(payloads: list[dict[str, Any]], key: str) -> int:
    maximum = 0
    for payload in payloads:
        try:
            maximum = max(maximum, int(payload.get(key) or 0))
        except (TypeError, ValueError):
            continue
    return maximum


def _max_revalidation_int(payloads: list[dict[str, Any]], key: str) -> int:
    maximum = 0
    for payload in payloads:
        revalidation = payload.get("revalidation")
        if not isinstance(revalidation, dict):
            continue
        try:
            maximum = max(maximum, int(revalidation.get(key) or 0))
        except (TypeError, ValueError):
            continue
    return maximum


def _schedule_summary_int(schedule_summary: dict[str, Any], key: str) -> int:
    try:
        return max(0, int(schedule_summary.get(key) or 0))
    except (TypeError, ValueError):
        return 0
