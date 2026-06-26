"""Post-execution deterministic repair bridge for Director adapter.

This module is the migration-time boundary between legacy language-specific
repair functions and the Director runtime repair kernel receipt model.
"""

from __future__ import annotations

import contextlib
import hashlib
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
    RunDirectorRepairCommandV1,
    query_director_repair_post_execution_schedule,
    query_director_repair_strategy_catalog,
    run_director_post_execution_repair_schedule_result,
    run_director_repair,
    validate_director_repair_advisory,
)

from .deterministic_repairs._runtime_bridge import run_runtime_repair_with_director_tools
from .execution_tools import DirectorToolExecutor
from .repair_profile_projection import project_repair_kernel_summary

StepRunner = Callable[[Any, Path, str], list[dict[str, Any]]]
RuntimeAdvisorNotes = tuple[RepairAdvisoryV1, ...]
ConvergenceVerifier = Callable[[Any], Any]

_CPP_REPAIR_FILE_SUFFIXES = (".c", ".cc", ".cpp", ".cxx", ".h", ".hh", ".hpp", ".hxx")
_RUST_SHADOW_COPY_IGNORES = frozenset({".git", ".venv", "__pycache__", "node_modules", "target"})
_POST_EXECUTION_REPAIR_MAX_ROUNDS = 3
_CALLBACK_RECEIPT_MIGRATION_BLOCKER = "callback runners still return tool_results instead of RepairReceipt"
_RUST_LEGACY_AGGREGATE_REMAINING_SOURCE_TOOLS = frozenset(
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
_RUST_LEGACY_AGGREGATE_SUBCASES_BY_SOURCE_TOOL: Mapping[str, frozenset[str]] = {
    _RUST_MISSING_FIELDS_SOURCE_TOOL: frozenset({_RUST_MISSING_FIELDS_FIELD_DECLARATION_SUBCASE}),
    _RUST_LIB_ROOT_FACADE_SOURCE_TOOL: frozenset(
        {
            _RUST_LIB_ROOT_FACADE_EXPORT_OR_MODULE_DECLARATION_SUBCASE,
            _RUST_LIB_ROOT_FACADE_PATH_REWRITE_SUBCASE,
        }
    ),
}
_RUST_LEGACY_AGGREGATE_SOURCE_TOOL_BLOCKER = "legacy_aggregate_shadow_replay_source_tool_not_remaining"
_GO_POST_EXECUTION_RUNTIME_SOURCE_TOOLS = (
    "deterministic_go_bare_import_string_repair",
    "deterministic_go_nested_import_repair",
    "deterministic_go_module_import_repair",
    "deterministic_go_bare_import_repair",
    "deterministic_go_subpath_repair",
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
) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    """Run post-execution language repairs and return normalized tool results."""

    workspace = Path(str(getattr(adapter, "workspace", "") or ""))
    agi_advisory_overlay = _normalize_resident_agi_repair_advisory_overlay(
        resident_agi_repair_advisory_overlay,
    )
    runtime_advisor_notes = _runtime_advisor_notes_from_overlay(agi_advisory_overlay)

    def _run_step(step: DirectorRepairPostExecutionStepV1) -> list[dict[str, Any]]:
        runner = _runner_for_post_execution_step(step)
        if step.step_id == "cpp.post_execution":
            return run_cpp_post_repairs_as_tool_results(
                workspace,
                adapter=adapter,
                task_id=task_id,
                advisor_notes=runtime_advisor_notes,
                convergence_verifier=convergence_verifier,
            )
        if step.step_id == "go.module_import":
            return _run_go_post_repairs(
                adapter,
                task_id=task_id,
                advisor_notes=runtime_advisor_notes,
                convergence_verifier=convergence_verifier,
            )
        if step.step_id == "java.post_execution":
            return _run_java_post_repairs(
                adapter,
                workspace,
                task_id=task_id,
                advisor_notes=runtime_advisor_notes,
                convergence_verifier=convergence_verifier,
            )
        return runner(adapter, workspace, task_id)

    schedule_result = run_director_post_execution_repair_schedule_result(
        runner_step_ids=tuple(_POST_EXECUTION_REPAIR_RUNNERS),
        runner=_run_step,
        max_rounds=_POST_EXECUTION_REPAIR_MAX_ROUNDS,
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
    repair_kernel["legacy_callback_debt"] = migration_debt["legacy_callback_debt"]
    rust_legacy_aggregate_cutover_evidence = dict(
        migration_debt.get("legacy_aggregate_cutover_readiness_evidence") or {}
    )
    repair_kernel.update(_legacy_aggregate_cutover_projection_fields(rust_legacy_aggregate_cutover_evidence))
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
        "legacy_callback_debt": migration_debt["legacy_callback_debt"],
        "legacy_aggregate_cutover_readiness_evidence": rust_legacy_aggregate_cutover_evidence,
        "resident_agi_repair_advisory_overlay": agi_advisory_overlay,
    }


def run_cpp_post_repairs_as_tool_results(
    workspace: str | Path,
    *,
    adapter: Any | None = None,
    task_id: str = "director-cpp-post-repair",
    advisor_notes: RuntimeAdvisorNotes = (),
    convergence_verifier: ConvergenceVerifier | None = None,
) -> list[dict[str, Any]]:
    """Run C++ post repairs and normalize them as write-tool results."""

    workspace_path = Path(workspace)
    if not _looks_like_cpp_workspace(workspace_path):
        return []

    tool_results = _run_cpp_include_path_runtime_repair(
        adapter,
        workspace_path,
        task_id=task_id,
        advisor_notes=advisor_notes,
        convergence_verifier=convergence_verifier,
    )
    tool_results.extend(
        _run_cpp_standard_include_runtime_repair(
            adapter,
            workspace_path,
            task_id=task_id,
            advisor_notes=advisor_notes,
            convergence_verifier=convergence_verifier,
        )
    )
    tool_results.extend(
        _run_cpp_placeholder_declaration_runtime_repair(
            adapter,
            workspace_path,
            task_id=task_id,
            advisor_notes=advisor_notes,
            convergence_verifier=convergence_verifier,
        )
    )
    tool_results.extend(
        _run_cpp_struct_getter_field_access_runtime_repair(
            adapter,
            workspace_path,
            task_id=task_id,
            advisor_notes=advisor_notes,
            convergence_verifier=convergence_verifier,
        )
    )
    tool_results.extend(
        _run_cpp_missing_private_members_runtime_repair(
            adapter,
            workspace_path,
            task_id=task_id,
            advisor_notes=advisor_notes,
            convergence_verifier=convergence_verifier,
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
) -> list[dict[str, Any]]:
    return _run_cpp_runtime_repair(
        adapter,
        workspace,
        task_id=task_id,
        source_tool="deterministic_cpp_include_path_repair",
        advisor_notes=advisor_notes,
        convergence_verifier=convergence_verifier,
    )


def _run_cpp_standard_include_runtime_repair(
    adapter: Any | None,
    workspace: Path,
    *,
    task_id: str,
    advisor_notes: RuntimeAdvisorNotes = (),
    convergence_verifier: ConvergenceVerifier | None = None,
) -> list[dict[str, Any]]:
    return _run_cpp_runtime_repair(
        adapter,
        workspace,
        task_id=task_id,
        source_tool="deterministic_cpp_standard_include_repair",
        advisor_notes=advisor_notes,
        convergence_verifier=convergence_verifier,
    )


def _run_cpp_placeholder_declaration_runtime_repair(
    adapter: Any | None,
    workspace: Path,
    *,
    task_id: str,
    advisor_notes: RuntimeAdvisorNotes = (),
    convergence_verifier: ConvergenceVerifier | None = None,
) -> list[dict[str, Any]]:
    return _run_cpp_runtime_repair(
        adapter,
        workspace,
        task_id=task_id,
        source_tool="deterministic_cpp_placeholder_declaration_repair",
        advisor_notes=advisor_notes,
        convergence_verifier=convergence_verifier,
    )


def _run_cpp_struct_getter_field_access_runtime_repair(
    adapter: Any | None,
    workspace: Path,
    *,
    task_id: str,
    advisor_notes: RuntimeAdvisorNotes = (),
    convergence_verifier: ConvergenceVerifier | None = None,
) -> list[dict[str, Any]]:
    return _run_cpp_runtime_repair(
        adapter,
        workspace,
        task_id=task_id,
        source_tool="deterministic_cpp_struct_getter_field_access_repair",
        advisor_notes=advisor_notes,
        convergence_verifier=convergence_verifier,
    )


def _run_cpp_missing_private_members_runtime_repair(
    adapter: Any | None,
    workspace: Path,
    *,
    task_id: str,
    advisor_notes: RuntimeAdvisorNotes = (),
    convergence_verifier: ConvergenceVerifier | None = None,
) -> list[dict[str, Any]]:
    return _run_cpp_runtime_repair(
        adapter,
        workspace,
        task_id=task_id,
        source_tool="deterministic_cpp_missing_private_members_repair",
        advisor_notes=advisor_notes,
        convergence_verifier=convergence_verifier,
    )


def _run_cpp_runtime_repair(
    adapter: Any | None,
    workspace: Path,
    *,
    task_id: str,
    source_tool: str,
    advisor_notes: RuntimeAdvisorNotes = (),
    convergence_verifier: ConvergenceVerifier | None = None,
) -> list[dict[str, Any]]:
    workspace_path = workspace.resolve()
    base_files = _collect_cpp_base_files(workspace_path)
    if not base_files:
        return []

    if convergence_verifier is not None and adapter is not None:
        return run_runtime_repair_with_director_tools(
            adapter,
            workspace_path=workspace_path,
            task_id=task_id,
            source_tool=source_tool,
            executor_factory=DirectorToolExecutor,
            base_files=base_files,
            allowed_paths=tuple(base_files.keys()),
            advisor_notes=advisor_notes,
            use_editor=False,
            convergence_verifier=convergence_verifier,
            max_rounds=_POST_EXECUTION_REPAIR_MAX_ROUNDS,
        )

    write_results: dict[str, dict[str, Any]] = {}
    if adapter is not None:
        message_bus = getattr(getattr(adapter, "_execution", None), "_message_bus", None)
        executor = DirectorToolExecutor(
            str(workspace_path),
            message_bus=message_bus,
            worker_id="director",
        )

        def writer(path: str, content: str) -> dict[str, Any]:
            write_result = executor.execute_tool(
                "write_file",
                {"file": path, "content": content},
                task_id=task_id,
            )
            write_results[path] = dict(write_result)
            if bool(write_result.get("ok")):
                with contextlib.suppress(OSError, RuntimeError, TypeError, ValueError):
                    adapter._update_task_progress(task_id, "executing", current_file=path)
            return dict(write_result)

    else:

        def writer(path: str, content: str) -> dict[str, Any]:
            write_result = _direct_runtime_writer(workspace_path, path, content)
            write_results[path] = dict(write_result)
            return dict(write_result)

    canonical_result = run_director_repair(
        RunDirectorRepairCommandV1(
            task_id=task_id,
            workspace=str(workspace_path),
            source_tool=source_tool,
            base_files=base_files,
            allowed_paths=tuple(base_files.keys()),
            advisor_notes=advisor_notes,
        ),
        writer=writer,
    )
    if canonical_result.ok:
        return _canonical_repair_result_to_tool_results(
            canonical_result,
            write_results=write_results,
            workspace=workspace_path,
        )
    return _canonical_repair_failure_to_tool_results(
        canonical_result,
        source_tool=source_tool,
    )


def _run_go_post_repairs(
    adapter: Any,
    *,
    task_id: str,
    advisor_notes: RuntimeAdvisorNotes = (),
    convergence_verifier: ConvergenceVerifier | None = None,
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for source_tool in _GO_POST_EXECUTION_RUNTIME_SOURCE_TOOLS:
        runtime_results = _run_go_runtime_repair(
            adapter,
            task_id=task_id,
            source_tool=source_tool,
            advisor_notes=advisor_notes,
            convergence_verifier=convergence_verifier,
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
        executor_factory=DirectorToolExecutor,
        base_files=base_files,
        allowed_paths=tuple(base_files.keys()),
        advisor_notes=advisor_notes,
        use_editor=True,
        convergence_verifier=convergence_verifier,
        max_rounds=_POST_EXECUTION_REPAIR_MAX_ROUNDS,
    )


def _run_rust_post_repairs(
    adapter: Any,
    workspace: Path,
    *,
    task_id: str,
) -> list[dict[str, Any]]:
    workspace_path = workspace.resolve()
    if not (workspace_path / "Cargo.toml").is_file():
        return []

    tool_results = _run_rust_crate_import_rewrite_runtime_repair(
        adapter,
        workspace_path,
        task_id=task_id,
    )
    tool_results.extend(
        _run_rust_method_self_signature_runtime_repair(
            adapter,
            workspace_path,
            task_id=task_id,
        )
    )
    tool_results.extend(
        _run_rust_wrong_crate_path_runtime_repair(
            adapter,
            workspace_path,
            task_id=task_id,
        )
    )
    tool_results.extend(
        _run_rust_incompatible_copy_derive_runtime_repair(
            adapter,
            workspace_path,
            task_id=task_id,
        )
    )
    tool_results.extend(
        _run_rust_missing_trait_derive_runtime_repair(
            adapter,
            workspace_path,
            task_id=task_id,
        )
    )
    tool_results.extend(
        _run_rust_unused_import_runtime_repair(
            adapter,
            workspace_path,
            task_id=task_id,
        )
    )
    tool_results.extend(
        _run_rust_unresolved_pub_use_runtime_repair(
            adapter,
            workspace_path,
            task_id=task_id,
        )
    )
    tool_results.extend(
        _run_rust_trait_import_runtime_repair(
            adapter,
            workspace_path,
            task_id=task_id,
        )
    )
    tool_results.extend(
        _run_rust_line_suggestion_runtime_repair(
            adapter,
            workspace_path,
            task_id=task_id,
        )
    )
    tool_results.extend(
        _run_rust_field_rename_suggestion_runtime_repair(
            adapter,
            workspace_path,
            task_id=task_id,
        )
    )
    tool_results.extend(
        _run_rust_missing_binary_entrypoint_runtime_repair(
            adapter,
            workspace_path,
            task_id=task_id,
        )
    )
    tool_results.extend(
        _run_rust_missing_module_file_runtime_repair(
            adapter,
            workspace_path,
            task_id=task_id,
        )
    )
    tool_results.extend(
        _run_rust_duplicate_module_file_runtime_repair(
            adapter,
            workspace_path,
            task_id=task_id,
        )
    )
    tool_results.extend(
        _run_rust_missing_fields_runtime_repair(
            adapter,
            workspace_path,
            task_id=task_id,
        )
    )
    tool_results.extend(
        _run_rust_lib_root_facade_runtime_repair(
            adapter,
            workspace_path,
            task_id=task_id,
        )
    )
    return tool_results


def _run_rust_dependency_repair(
    adapter: Any,
    *,
    task_id: str,
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
        executor_factory=DirectorToolExecutor,
        base_files=base_files,
        artifact_quality_errors=_rust_post_execution_artifact_quality_errors(adapter),
        allowed_paths=tuple(base_files.keys()),
        use_editor=False,
    )


def _run_rust_crate_import_rewrite_runtime_repair(
    adapter: Any,
    workspace: Path,
    *,
    task_id: str,
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
        executor_factory=DirectorToolExecutor,
        base_files=base_files,
        artifact_quality_errors=artifact_quality_errors,
        allowed_paths=tuple(base_files.keys()),
        use_editor=True,
    )


def _run_rust_method_self_signature_runtime_repair(
    adapter: Any,
    workspace: Path,
    *,
    task_id: str,
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
        executor_factory=DirectorToolExecutor,
        base_files=base_files,
        artifact_quality_errors=artifact_quality_errors,
        allowed_paths=tuple(base_files.keys()),
        use_editor=True,
    )


def _run_rust_wrong_crate_path_runtime_repair(
    adapter: Any,
    workspace: Path,
    *,
    task_id: str,
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
        executor_factory=DirectorToolExecutor,
        base_files=base_files,
        artifact_quality_errors=artifact_quality_errors,
        allowed_paths=tuple(base_files.keys()),
        use_editor=True,
    )


def _run_rust_incompatible_copy_derive_runtime_repair(
    adapter: Any,
    workspace: Path,
    *,
    task_id: str,
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
        executor_factory=DirectorToolExecutor,
        base_files=base_files,
        artifact_quality_errors=artifact_quality_errors,
        allowed_paths=tuple(base_files.keys()),
        use_editor=True,
    )


def _run_rust_missing_trait_derive_runtime_repair(
    adapter: Any,
    workspace: Path,
    *,
    task_id: str,
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
        executor_factory=DirectorToolExecutor,
        base_files=base_files,
        artifact_quality_errors=artifact_quality_errors,
        allowed_paths=tuple(base_files.keys()),
        use_editor=True,
    )


def _run_rust_unused_import_runtime_repair(
    adapter: Any,
    workspace: Path,
    *,
    task_id: str,
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
        executor_factory=DirectorToolExecutor,
        base_files=base_files,
        artifact_quality_errors=artifact_quality_errors,
        allowed_paths=tuple(base_files.keys()),
        use_editor=True,
    )


def _run_rust_unresolved_pub_use_runtime_repair(
    adapter: Any,
    workspace: Path,
    *,
    task_id: str,
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
        executor_factory=DirectorToolExecutor,
        base_files=base_files,
        artifact_quality_errors=artifact_quality_errors,
        allowed_paths=tuple(base_files.keys()),
        use_editor=True,
    )


def _run_rust_trait_import_runtime_repair(
    adapter: Any,
    workspace: Path,
    *,
    task_id: str,
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
        executor_factory=DirectorToolExecutor,
        base_files=base_files,
        artifact_quality_errors=artifact_quality_errors,
        allowed_paths=tuple(base_files.keys()),
        use_editor=True,
    )


def _run_rust_line_suggestion_runtime_repair(
    adapter: Any,
    workspace: Path,
    *,
    task_id: str,
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
        executor_factory=DirectorToolExecutor,
        base_files=base_files,
        artifact_quality_errors=artifact_quality_errors,
        allowed_paths=tuple(base_files.keys()),
        use_editor=True,
    )


def _run_rust_field_rename_suggestion_runtime_repair(
    adapter: Any,
    workspace: Path,
    *,
    task_id: str,
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
        executor_factory=DirectorToolExecutor,
        base_files=base_files,
        artifact_quality_errors=artifact_quality_errors,
        allowed_paths=tuple(base_files.keys()),
        use_editor=True,
    )


def _run_rust_missing_binary_entrypoint_runtime_repair(
    adapter: Any,
    workspace: Path,
    *,
    task_id: str,
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
        executor_factory=DirectorToolExecutor,
        base_files=base_files,
        artifact_quality_errors=_rust_post_execution_artifact_quality_errors(adapter),
        allowed_paths=allowed_paths,
        use_editor=False,
    )


def _run_rust_missing_module_file_runtime_repair(
    adapter: Any,
    workspace: Path,
    *,
    task_id: str,
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
        executor_factory=DirectorToolExecutor,
        base_files=base_files,
        artifact_quality_errors=artifact_quality_errors,
        allowed_paths=allowed_paths,
        use_editor=False,
    )


def _run_rust_duplicate_module_file_runtime_repair(
    adapter: Any,
    workspace: Path,
    *,
    task_id: str,
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
        executor_factory=DirectorToolExecutor,
        base_files=base_files,
        artifact_quality_errors=artifact_quality_errors,
        allowed_paths=allowed_paths,
        use_editor=False,
    )


def _run_rust_missing_fields_runtime_repair(
    adapter: Any,
    workspace: Path,
    *,
    task_id: str,
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
        executor_factory=DirectorToolExecutor,
        base_files=base_files,
        artifact_quality_errors=artifact_quality_errors,
        allowed_paths=tuple(base_files.keys()),
        use_editor=True,
    )


def _run_rust_lib_root_facade_runtime_repair(
    adapter: Any,
    workspace: Path,
    *,
    task_id: str,
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
        executor_factory=DirectorToolExecutor,
        base_files=base_files,
        artifact_quality_errors=artifact_quality_errors,
        allowed_paths=tuple(base_files.keys()),
        use_editor=True,
    )


def _run_java_post_repairs(
    adapter: Any,
    workspace: Path,
    *,
    task_id: str,
    advisor_notes: RuntimeAdvisorNotes = (),
    convergence_verifier: ConvergenceVerifier | None = None,
) -> list[dict[str, Any]]:
    if not any(workspace.rglob("*.java")):
        return []

    tool_results = _run_java_accessor_alias_runtime_repair(
        adapter,
        workspace,
        task_id=task_id,
        advisor_notes=advisor_notes,
        convergence_verifier=convergence_verifier,
    )
    tool_results.extend(
        _run_java_test_dependency_runtime_repair(
            adapter,
            workspace,
            task_id=task_id,
            advisor_notes=advisor_notes,
            convergence_verifier=convergence_verifier,
        )
    )
    return tool_results


def _run_java_accessor_alias_runtime_repair(
    adapter: Any,
    workspace: Path,
    *,
    task_id: str,
    advisor_notes: RuntimeAdvisorNotes = (),
    convergence_verifier: ConvergenceVerifier | None = None,
) -> list[dict[str, Any]]:
    workspace_path = workspace.resolve()
    base_files: dict[str, str] = {}
    for java_file in sorted((workspace_path / "src" / "main" / "java").rglob("*.java")):
        try:
            relative_path = java_file.relative_to(workspace_path).as_posix()
        except ValueError:
            continue
        try:
            base_files[relative_path] = java_file.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
    if not base_files:
        return []

    if convergence_verifier is not None:
        return run_runtime_repair_with_director_tools(
            adapter,
            workspace_path=workspace_path,
            task_id=task_id,
            source_tool="deterministic_java_accessor_alias_repair",
            executor_factory=DirectorToolExecutor,
            base_files=base_files,
            allowed_paths=tuple(base_files.keys()),
            advisor_notes=advisor_notes,
            use_editor=False,
            convergence_verifier=convergence_verifier,
            max_rounds=_POST_EXECUTION_REPAIR_MAX_ROUNDS,
        )

    message_bus = getattr(getattr(adapter, "_execution", None), "_message_bus", None)
    executor = DirectorToolExecutor(
        str(workspace_path),
        message_bus=message_bus,
        worker_id="director",
    )
    write_results: dict[str, dict[str, Any]] = {}

    def _policy_gated_writer(path: str, content: str) -> dict[str, Any]:
        write_result = executor.execute_tool(
            "write_file",
            {"file": path, "content": content},
            task_id=task_id,
        )
        write_results[path] = dict(write_result)
        if bool(write_result.get("ok")):
            with contextlib.suppress(OSError, RuntimeError, TypeError, ValueError):
                adapter._update_task_progress(task_id, "executing", current_file=path)
        return dict(write_result)

    canonical_result = run_director_repair(
        RunDirectorRepairCommandV1(
            task_id=task_id,
            workspace=str(workspace_path),
            source_tool="deterministic_java_accessor_alias_repair",
            base_files=base_files,
            allowed_paths=tuple(base_files.keys()),
            advisor_notes=advisor_notes,
        ),
        writer=_policy_gated_writer,
    )
    if canonical_result.ok:
        return _canonical_repair_result_to_tool_results(
            canonical_result,
            write_results=write_results,
            workspace=workspace_path,
        )
    return _canonical_repair_failure_to_tool_results(
        canonical_result,
        source_tool="deterministic_java_accessor_alias_repair",
    )


def _run_java_test_dependency_runtime_repair(
    adapter: Any,
    workspace: Path,
    *,
    task_id: str,
    advisor_notes: RuntimeAdvisorNotes = (),
    convergence_verifier: ConvergenceVerifier | None = None,
) -> list[dict[str, Any]]:
    workspace_path = workspace.resolve()
    base_files = _collect_java_test_base_files(workspace_path)
    if not base_files:
        return []

    source_tool = "deterministic_java_test_dependency_repair"
    if convergence_verifier is not None:
        return run_runtime_repair_with_director_tools(
            adapter,
            workspace_path=workspace_path,
            task_id=task_id,
            source_tool=source_tool,
            executor_factory=DirectorToolExecutor,
            base_files=base_files,
            allowed_paths=tuple(base_files.keys()),
            advisor_notes=advisor_notes,
            use_editor=False,
            convergence_verifier=convergence_verifier,
            max_rounds=_POST_EXECUTION_REPAIR_MAX_ROUNDS,
        )

    message_bus = getattr(getattr(adapter, "_execution", None), "_message_bus", None)
    executor = DirectorToolExecutor(
        str(workspace_path),
        message_bus=message_bus,
        worker_id="director",
    )
    write_results: dict[str, dict[str, Any]] = {}

    def _policy_gated_writer(path: str, content: str) -> dict[str, Any]:
        write_result = executor.execute_tool(
            "write_file",
            {"file": path, "content": content},
            task_id=task_id,
        )
        write_results[path] = dict(write_result)
        if bool(write_result.get("ok")):
            with contextlib.suppress(OSError, RuntimeError, TypeError, ValueError):
                adapter._update_task_progress(task_id, "executing", current_file=path)
        return dict(write_result)

    canonical_result = run_director_repair(
        RunDirectorRepairCommandV1(
            task_id=task_id,
            workspace=str(workspace_path),
            source_tool=source_tool,
            base_files=base_files,
            allowed_paths=tuple(base_files.keys()),
            advisor_notes=advisor_notes,
        ),
        writer=_policy_gated_writer,
    )
    if canonical_result.ok:
        return _canonical_repair_result_to_tool_results(
            canonical_result,
            write_results=write_results,
            workspace=workspace_path,
        )
    return _canonical_repair_failure_to_tool_results(
        canonical_result,
        source_tool=source_tool,
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
        if not path.is_file() or any(part in _RUST_SHADOW_COPY_IGNORES for part in path.parts):
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


def _rust_post_execution_artifact_quality_errors(adapter: Any) -> tuple[str, ...]:
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


def _direct_runtime_writer(workspace: Path, path: str, content: str) -> dict[str, Any]:
    try:
        target = (workspace / path).resolve()
        target.relative_to(workspace)
    except (OSError, ValueError):
        return {
            "ok": False,
            "file": path,
            "error": "repair target path escaped workspace",
        }
    try:
        target.write_text(content, encoding="utf-8")
    except OSError as exc:
        return {
            "ok": False,
            "file": path,
            "error": str(exc),
        }
    return {
        "ok": True,
        "file": path,
        "bytes_written": len(content.encode("utf-8")),
        "operation": "modify",
    }


def _looks_like_cpp_workspace(workspace: Path) -> bool:
    return (workspace / "CMakeLists.txt").exists() or any(
        path.is_file() and path.suffix in _CPP_REPAIR_FILE_SUFFIXES for path in workspace.rglob("*")
    )


def _is_generated_build_path(path: Path) -> bool:
    return "build" in path.parts or "cmake-build" in path.parts


def _rust_shadow_copy_ignore(src: str, names: list[str]) -> set[str]:
    ignored: set[str] = set()
    src_path = Path(src)
    for name in names:
        candidate = src_path / name
        if name in _RUST_SHADOW_COPY_IGNORES or candidate.is_symlink():
            ignored.add(name)
    return ignored


def _snapshot_rust_shadow_text_files(workspace: Path) -> dict[str, str]:
    files: dict[str, str] = {}
    for path in sorted(workspace.rglob("*")):
        if not path.is_file():
            continue
        try:
            relative_path = path.relative_to(workspace)
        except ValueError:
            continue
        if any(part in _RUST_SHADOW_COPY_IGNORES for part in relative_path.parts):
            continue
        if path.name != "Cargo.toml" and path.suffix != ".rs":
            continue
        try:
            files[relative_path.as_posix()] = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
    return files


def _apply_rust_shadow_change_with_director_tool(
    executor: DirectorToolExecutor,
    relative_path: str,
    *,
    before_content: str | None,
    after_content: str,
    task_id: str,
) -> tuple[str, dict[str, Any]]:
    replacement = None
    if before_content is not None and relative_path.endswith(".rs"):
        replacement = _minimal_unique_text_replacement(before_content, after_content)
    if replacement is not None:
        search, replace = replacement
        edit_result = executor.execute_tool(
            "edit_file",
            {"file": relative_path, "search": search, "replace": replace},
            task_id=task_id,
        )
        return "edit_file", dict(edit_result)

    write_result = executor.execute_tool(
        "write_file",
        {"file": relative_path, "content": after_content},
        task_id=task_id,
    )
    return "write_file", dict(write_result)


def _minimal_unique_text_replacement(before_content: str, after_content: str) -> tuple[str, str] | None:
    if before_content == after_content or before_content == "":
        return None

    prefix_length = 0
    max_prefix = min(len(before_content), len(after_content))
    while prefix_length < max_prefix and before_content[prefix_length] == after_content[prefix_length]:
        prefix_length += 1

    before_end = len(before_content)
    after_end = len(after_content)
    while (
        before_end > prefix_length
        and after_end > prefix_length
        and before_content[before_end - 1] == after_content[after_end - 1]
    ):
        before_end -= 1
        after_end -= 1

    start = prefix_length
    end = before_end
    if start == end:
        if start > 0:
            start -= 1
        elif end < len(before_content):
            end += 1
        else:
            return None

    while before_content.count(before_content[start:end]) != 1 and (start > 0 or end < len(before_content)):
        if start > 0:
            start -= 1
        if before_content.count(before_content[start:end]) == 1:
            break
        if end < len(before_content):
            end += 1

    search = before_content[start:end]
    if not search or before_content.count(search) != 1:
        return None

    left_context = prefix_length - start
    right_context = end - before_end
    replace_start = prefix_length - left_context
    replace_end = after_end + right_context
    return search, after_content[replace_start:replace_end]


def _primary_rust_shadow_record(records: list[dict[str, Any]] | None, relative_path: str) -> dict[str, Any]:
    if records:
        record = dict(records[0])
        record.setdefault("file", relative_path)
        return record
    return {
        "file": relative_path,
        "source_tool": "deterministic_rust_post_repair",
        "action": "legacy_shadow_diff_apply",
    }


def _rust_shadow_record_source_tool(record: dict[str, Any]) -> str:
    return str(record.get("source_tool") or "deterministic_rust_post_repair").strip()


def _rust_legacy_aggregate_allowed_source_tools(
    runtime_executable_source_tools: frozenset[str],
) -> frozenset[str]:
    return frozenset(
        source_tool
        for source_tool in _RUST_LEGACY_AGGREGATE_REMAINING_SOURCE_TOOLS
        if source_tool not in runtime_executable_source_tools
    )


def _rust_legacy_aggregate_remaining_source_tools(
    runtime_executable_source_tools: frozenset[str],
) -> list[str]:
    return sorted(_rust_legacy_aggregate_allowed_source_tools(runtime_executable_source_tools))


def _rust_legacy_aggregate_runtime_migrated_subcases(
    runtime_executable_source_tools: frozenset[str],
) -> list[str]:
    subcases: set[str] = set()
    for source_tool, source_tool_subcases in _RUST_LEGACY_AGGREGATE_SUBCASES_BY_SOURCE_TOOL.items():
        if source_tool in runtime_executable_source_tools:
            subcases.update(source_tool_subcases)
    return sorted(subcases)


def _rust_legacy_aggregate_remaining_subcases(
    runtime_executable_source_tools: frozenset[str],
) -> list[str]:
    subcases: set[str] = set()
    for source_tool, source_tool_subcases in _RUST_LEGACY_AGGREGATE_SUBCASES_BY_SOURCE_TOOL.items():
        if source_tool not in runtime_executable_source_tools:
            subcases.update(source_tool_subcases)
    return sorted(subcases)


def _rust_shadow_record_subcases(record: dict[str, Any]) -> list[str]:
    source_tool = _rust_shadow_record_source_tool(record)
    if source_tool == _RUST_MISSING_FIELDS_SOURCE_TOOL:
        return [_RUST_MISSING_FIELDS_FIELD_DECLARATION_SUBCASE]
    if source_tool != _RUST_LIB_ROOT_FACADE_SOURCE_TOOL:
        return []

    subcases: list[str] = []
    path_rewrites = record.get("path_rewrites")
    if isinstance(path_rewrites, list) and path_rewrites:
        subcases.append(_RUST_LIB_ROOT_FACADE_PATH_REWRITE_SUBCASE)
    module_exports = record.get("module_exports")
    if isinstance(module_exports, list) and module_exports:
        subcases.append(_RUST_LIB_ROOT_FACADE_EXPORT_OR_MODULE_DECLARATION_SUBCASE)
    return _sorted_unique(subcases)


def _rust_legacy_aggregate_shadow_replay_guard(
    *,
    records: list[dict[str, Any]],
    records_by_file: dict[str, list[dict[str, Any]]],
    shadow_paths: list[str],
    runtime_executable_source_tools: frozenset[str],
) -> dict[str, list[str]]:
    allowed_source_tools = _rust_legacy_aggregate_allowed_source_tools(runtime_executable_source_tools)
    remaining_subcases = set(_rust_legacy_aggregate_remaining_subcases(runtime_executable_source_tools))
    runtime_migrated_subcases = set(_rust_legacy_aggregate_runtime_migrated_subcases(runtime_executable_source_tools))
    blocked_source_tools: list[str] = []
    blocked_migrated_source_tools: list[str] = []
    blocked_subcases: list[str] = []
    blocked_migrated_subcases: list[str] = []

    for record in records:
        source_tool = _rust_shadow_record_source_tool(record)
        if source_tool in allowed_source_tools:
            continue
        record_subcases = _rust_shadow_record_subcases(record)
        if record_subcases and all(subcase in remaining_subcases for subcase in record_subcases):
            continue
        blocked_source_tools.append(source_tool)
        if source_tool in runtime_executable_source_tools:
            blocked_migrated_source_tools.append(source_tool)
        for subcase in record_subcases:
            if subcase in runtime_migrated_subcases:
                blocked_migrated_subcases.append(subcase)
            else:
                blocked_subcases.append(subcase)

    if shadow_paths:
        for relative_path in shadow_paths:
            path_records = records_by_file.get(relative_path)
            if not path_records:
                blocked_source_tools.append("deterministic_rust_post_repair")
    return {
        "blocked_source_tools": _sorted_unique(blocked_source_tools),
        "blocked_migrated_source_tools": _sorted_unique(blocked_migrated_source_tools),
        "blocked_subcases": _sorted_unique(blocked_subcases),
        "blocked_migrated_subcases": _sorted_unique(blocked_migrated_subcases),
    }


def _rust_legacy_aggregate_source_tool_blocked_result(
    *,
    relative_paths: list[str],
    source_tools: list[str],
    blocked_migrated_source_tools: list[str],
    blocked_subcases: list[str],
    blocked_migrated_subcases: list[str],
    runtime_executable_source_tools: frozenset[str],
) -> dict[str, Any]:
    normalized_source_tools = _sorted_unique(source_tools)
    runtime_migrated_source_tools = [
        source_tool for source_tool in normalized_source_tools if source_tool in runtime_executable_source_tools
    ]
    allowed_source_tools = sorted(_rust_legacy_aggregate_allowed_source_tools(runtime_executable_source_tools))
    remaining_source_tools = _rust_legacy_aggregate_remaining_source_tools(runtime_executable_source_tools)
    remaining_subcases = _rust_legacy_aggregate_remaining_subcases(runtime_executable_source_tools)
    runtime_migrated_subcases = _rust_legacy_aggregate_runtime_migrated_subcases(runtime_executable_source_tools)
    cutover_evidence = _build_rust_legacy_aggregate_cutover_evidence(
        remaining_source_tools=remaining_source_tools,
        allowed_source_tools=allowed_source_tools,
        blocked_source_tools=normalized_source_tools,
        blocked_migrated_source_tools=blocked_migrated_source_tools or runtime_migrated_source_tools,
        remaining_legacy_subcases=remaining_subcases,
        runtime_migrated_subcases=runtime_migrated_subcases,
        blocked_subcases=blocked_subcases,
        blocked_migrated_subcases=blocked_migrated_subcases,
    )
    primary_source_tool = normalized_source_tools[0] if normalized_source_tools else "deterministic_rust_post_repair"
    primary_path = relative_paths[0] if len(relative_paths) == 1 else ""
    result = _record_payload(
        {
            "file": primary_path,
            "source_tool": primary_source_tool,
            "action": "legacy_shadow_replay_blocked",
        },
        source_tool=primary_source_tool,
        default_action="legacy_shadow_replay_blocked",
    )
    result.update(
        {
            "ok": False,
            "blocked": True,
            "file": primary_path,
            "files_changed": list(relative_paths),
            "operation": "legacy_shadow_replay_guard",
            "requested_tool_name": "legacy_shadow_replay",
            "applied_tool_name": "blocked_legacy_shadow_replay",
            "error": "Rust legacy aggregate shadow replay emitted source_tool outside the remaining allowlist.",
            "source_tools": normalized_source_tools,
            "legacy_shadow_source_tools": normalized_source_tools,
            "legacy_shadow_workspace": True,
            "legacy_shadow_replay": True,
            "legacy_shadow_applied_via_director_tools": False,
            "receipt_authority": "non_authoritative_shadow_replay_projection",
            "receipt_status": "blocked",
            "evidence_status": "failed_evidence",
            "evidence_failed": True,
            "evidence_missing": False,
            "repair_success_verdict": False,
            "evidence_failure_reason": _RUST_LEGACY_AGGREGATE_SOURCE_TOOL_BLOCKER,
            "verifier_evidence_required": True,
            "verifier_evidence_present": False,
            "runtime_authoritative_plan": False,
            "typed_receipt_path_available": False,
            "migration_blocker": _RUST_LEGACY_AGGREGATE_SOURCE_TOOL_BLOCKER,
            "legacy_aggregate_remaining_source_tools": remaining_source_tools,
            "legacy_aggregate_shadow_replay_allowed_source_tools": allowed_source_tools,
            "legacy_aggregate_blocked_source_tools": normalized_source_tools,
            "legacy_aggregate_blocked_migrated_source_tools": blocked_migrated_source_tools
            or runtime_migrated_source_tools,
            "remaining_legacy_subcases": remaining_subcases,
            "runtime_migrated_subcases": runtime_migrated_subcases,
            "legacy_aggregate_remaining_legacy_subcases": remaining_subcases,
            "legacy_aggregate_runtime_migrated_subcases": runtime_migrated_subcases,
            "legacy_aggregate_blocked_subcases": _sorted_unique(blocked_subcases),
            "legacy_aggregate_blocked_migrated_subcases": _sorted_unique(blocked_migrated_subcases),
            **_legacy_aggregate_cutover_projection_fields(cutover_evidence),
        }
    )
    repair_kernel = result.get("repair_kernel")
    if isinstance(repair_kernel, dict):
        repair_kernel.update(
            {
                "status": "blocked",
                "blocked": True,
                "authoritative": False,
                "runtime_authoritative_plan": False,
                "requires_revalidation": True,
                "receipt_authority": "non_authoritative_shadow_replay_projection",
                "receipt_status": "blocked",
                "evidence_status": "failed_evidence",
                "evidence_failed": True,
                "evidence_missing": False,
                "repair_success_verdict": False,
                "source_tools": normalized_source_tools,
                "files_changed": list(relative_paths),
                "applied_tool_name": "blocked_legacy_shadow_replay",
                "legacy_shadow_workspace": True,
                "legacy_shadow_replay": True,
                "legacy_shadow_applied_via_director_tools": False,
                "migration_blocker": _RUST_LEGACY_AGGREGATE_SOURCE_TOOL_BLOCKER,
                "evidence_failure_reason": _RUST_LEGACY_AGGREGATE_SOURCE_TOOL_BLOCKER,
                "legacy_aggregate_remaining_source_tools": remaining_source_tools,
                "legacy_aggregate_shadow_replay_allowed_source_tools": allowed_source_tools,
                "legacy_aggregate_blocked_source_tools": normalized_source_tools,
                "legacy_aggregate_blocked_migrated_source_tools": blocked_migrated_source_tools
                or runtime_migrated_source_tools,
                "remaining_legacy_subcases": remaining_subcases,
                "runtime_migrated_subcases": runtime_migrated_subcases,
                "legacy_aggregate_remaining_legacy_subcases": remaining_subcases,
                "legacy_aggregate_runtime_migrated_subcases": runtime_migrated_subcases,
                "legacy_aggregate_blocked_subcases": _sorted_unique(blocked_subcases),
                "legacy_aggregate_blocked_migrated_subcases": _sorted_unique(blocked_migrated_subcases),
                **_legacy_aggregate_cutover_projection_fields(cutover_evidence),
            }
        )
    return _write_tool_result(result, tool_name="write_file")


def _sha256_text(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _normalized_source_tools(
    record: dict[str, Any],
    *,
    source_tools: list[str] | None,
    fallback: str,
) -> list[str]:
    values = [str(record.get("source_tool") or fallback)]
    if isinstance(source_tools, list):
        values.extend(str(source_tool) for source_tool in source_tools)
    return sorted({value.strip() for value in values if value.strip()})


def _legacy_revalidation_evidence_status(
    evidence: Mapping[str, Any] | None,
    *,
    write_ok: bool,
    blocked: bool,
) -> str:
    if blocked or not write_ok:
        return "failed_evidence"
    payload = dict(evidence or {})
    if not payload:
        return "missing_evidence"
    metadata = payload.get("metadata")
    metadata_dict = metadata if isinstance(metadata, dict) else {}
    failure_reason = str(metadata_dict.get("revalidation_failure_reason") or "").strip()
    if failure_reason in {
        "invalid_revalidation_evidence_type",
        "missing_revalidation_evidence",
        "missing_revalidation_exit_code",
        "revalidator_exception",
    }:
        return "missing_evidence"
    command = payload.get("command")
    has_command = isinstance(command, (list, tuple)) and any(str(item or "").strip() for item in command)
    exit_code = _optional_int(payload.get("exit_code"))
    if not has_command or exit_code is None:
        return "missing_evidence"
    errors_after = _optional_int(payload.get("errors_after"))
    if exit_code != 0 or (errors_after is not None and errors_after > 0):
        return "failed_evidence"
    if payload.get("residual_diagnostic_ids") or payload.get("residual_artifact_quality_errors"):
        return "failed_evidence"
    return "resolved_evidence"


def _legacy_evidence_missing_reason(evidence: Mapping[str, Any] | None) -> str:
    payload = dict(evidence or {})
    if not payload:
        return "missing_revalidation_evidence"
    metadata = payload.get("metadata")
    metadata_dict = metadata if isinstance(metadata, dict) else {}
    failure_reason = str(metadata_dict.get("revalidation_failure_reason") or "").strip()
    if failure_reason:
        return failure_reason
    command = payload.get("command")
    if not isinstance(command, (list, tuple)) or not any(str(item or "").strip() for item in command):
        return "missing_revalidation_command"
    if _optional_int(payload.get("exit_code")) is None:
        return "missing_revalidation_exit_code"
    return "missing_revalidation_evidence"


def _legacy_receipt_status_for_evidence(evidence_status: str, *, blocked: bool = False) -> str:
    if blocked:
        return "blocked"
    if evidence_status == "resolved_evidence":
        return "applied"
    if evidence_status == "failed_evidence":
        return "failed_revalidation"
    return "pending_revalidation"


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _apply_shadow_replay_repair_kernel_projection(
    result: dict[str, Any],
    *,
    relative_path: str,
    source_tools: list[str],
    applied_tool_name: str,
    evidence_status: str,
    before_hash: Any,
    after_hash: Any,
    revalidation_evidence: dict[str, Any],
    evidence_failure_reason: Any = None,
    blocked: bool = False,
) -> None:
    repair_kernel = result.get("repair_kernel")
    if not isinstance(repair_kernel, dict):
        return
    before_hash_text = str(before_hash or "").strip()
    after_hash_text = str(after_hash or "").strip()
    receipt_status = _legacy_receipt_status_for_evidence(evidence_status, blocked=blocked)
    repair_kernel.update(
        {
            "receipt_authority": "non_authoritative_shadow_replay_projection",
            "status": receipt_status,
            "authoritative": False,
            "runtime_authoritative_plan": False,
            "requires_revalidation": True,
            "verifier_evidence_required": True,
            "verifier_evidence_present": evidence_status != "missing_evidence",
            "evidence_status": evidence_status,
            "evidence_missing": evidence_status == "missing_evidence",
            "evidence_failed": evidence_status == "failed_evidence",
            "evidence_resolved": evidence_status == "resolved_evidence",
            "repair_success_verdict": evidence_status == "resolved_evidence" and not blocked,
            "applied_tool_name": applied_tool_name,
            "source_tools": list(source_tools),
            "files_changed": [relative_path] if relative_path else [],
            "legacy_shadow_workspace": True,
            "legacy_shadow_replay": True,
            "legacy_shadow_applied_via_director_tools": bool(result.get("legacy_shadow_applied_via_director_tools")),
            "legacy_shadow_before_exists": bool(result.get("legacy_shadow_before_exists", True)),
            "legacy_shadow_after_exists": bool(result.get("legacy_shadow_after_exists", not blocked)),
            "typed_receipt_path_available": False,
            "migration_blocker": _CALLBACK_RECEIPT_MIGRATION_BLOCKER,
            "revalidation_evidence": dict(revalidation_evidence),
        }
    )
    if before_hash_text:
        repair_kernel["before_hashes"] = {relative_path: before_hash_text} if relative_path else {}
    if after_hash_text:
        repair_kernel["after_hashes"] = {relative_path: after_hash_text} if relative_path else {}
    if blocked:
        repair_kernel["blocked"] = True
        repair_kernel["legacy_shadow_delete_blocked"] = True
    if evidence_status == "missing_evidence":
        reason = str(result.get("evidence_missing_reason") or "missing_revalidation_evidence")
        repair_kernel["evidence_missing_reason"] = reason
    if evidence_failure_reason:
        repair_kernel["evidence_failure_reason"] = str(evidence_failure_reason)
    remaining_source_tools = result.get("legacy_aggregate_remaining_source_tools")
    if isinstance(remaining_source_tools, list):
        repair_kernel["legacy_aggregate_remaining_source_tools"] = list(remaining_source_tools)
    allowed_source_tools = result.get("legacy_aggregate_shadow_replay_allowed_source_tools")
    if isinstance(allowed_source_tools, list):
        repair_kernel["legacy_aggregate_shadow_replay_allowed_source_tools"] = list(allowed_source_tools)
    remaining_legacy_subcases = result.get("remaining_legacy_subcases")
    if isinstance(remaining_legacy_subcases, list):
        repair_kernel["remaining_legacy_subcases"] = list(remaining_legacy_subcases)
        repair_kernel["legacy_aggregate_remaining_legacy_subcases"] = list(remaining_legacy_subcases)
    runtime_migrated_subcases = result.get("runtime_migrated_subcases")
    if isinstance(runtime_migrated_subcases, list):
        repair_kernel["runtime_migrated_subcases"] = list(runtime_migrated_subcases)
        repair_kernel["legacy_aggregate_runtime_migrated_subcases"] = list(runtime_migrated_subcases)
    blocked_subcases = result.get("legacy_aggregate_blocked_subcases")
    if isinstance(blocked_subcases, list):
        repair_kernel["legacy_aggregate_blocked_subcases"] = list(blocked_subcases)
    blocked_migrated_subcases = result.get("legacy_aggregate_blocked_migrated_subcases")
    if isinstance(blocked_migrated_subcases, list):
        repair_kernel["legacy_aggregate_blocked_migrated_subcases"] = list(blocked_migrated_subcases)
    cutover_evidence = result.get("legacy_aggregate_cutover_readiness_evidence")
    if isinstance(cutover_evidence, dict):
        repair_kernel.update(_legacy_aggregate_cutover_projection_fields(cutover_evidence))


def _rust_shadow_delete_blocked_tool_result(
    record: dict[str, Any],
    relative_path: str,
    *,
    before_content: str | None = None,
    source_tools: list[str] | None = None,
    runtime_executable_source_tools: frozenset[str] | None = None,
) -> dict[str, Any]:
    runtime_source_tools = runtime_executable_source_tools or frozenset()
    remaining_source_tools = _rust_legacy_aggregate_remaining_source_tools(runtime_source_tools)
    allowed_source_tools = sorted(_rust_legacy_aggregate_allowed_source_tools(runtime_source_tools))
    normalized_source_tools = _normalized_source_tools(
        record,
        source_tools=source_tools,
        fallback="deterministic_rust_post_repair",
    )
    remaining_subcases = _rust_legacy_aggregate_remaining_subcases(runtime_source_tools)
    runtime_migrated_subcases = _rust_legacy_aggregate_runtime_migrated_subcases(runtime_source_tools)
    cutover_evidence = _build_rust_legacy_aggregate_cutover_evidence(
        remaining_source_tools=remaining_source_tools,
        allowed_source_tools=allowed_source_tools,
        blocked_source_tools=[],
        blocked_migrated_source_tools=[],
        remaining_legacy_subcases=remaining_subcases,
        runtime_migrated_subcases=runtime_migrated_subcases,
        blocked_subcases=[],
        blocked_migrated_subcases=[],
    )
    before_hash = _sha256_text(before_content) if before_content is not None else ""
    result = _record_payload(
        record,
        source_tool=str(record.get("source_tool") or "deterministic_rust_post_repair"),
        default_action=str(record.get("symbols") or record.get("action") or "rust_post_repair"),
    )
    result.update(
        {
            "ok": False,
            "file": relative_path,
            "operation": "delete",
            "blocked": True,
            "error": "Rust shadow repair requested a deletion, but DirectorToolExecutor has no delete_file tool.",
            "requested_tool_name": "delete_file",
            "applied_tool_name": "blocked_delete_file",
            "source_tools": normalized_source_tools,
            "receipt_authority": "non_authoritative_shadow_replay_projection",
            "receipt_status": "blocked",
            "authoritative": False,
            "runtime_authoritative_plan": False,
            "typed_receipt_path_available": False,
            "evidence_status": "failed_evidence",
            "evidence_failed": True,
            "repair_success_verdict": False,
            "evidence_failure_reason": "shadow_replay_delete_blocked",
            "verifier_evidence_required": True,
            "verifier_evidence_present": False,
            "legacy_shadow_workspace": True,
            "legacy_shadow_replay": True,
            "legacy_shadow_before_exists": before_content is not None,
            "legacy_shadow_after_exists": False,
            "legacy_shadow_delete_blocked": True,
            "legacy_aggregate_remaining_source_tools": remaining_source_tools,
            "legacy_aggregate_shadow_replay_allowed_source_tools": allowed_source_tools,
            "remaining_legacy_subcases": remaining_subcases,
            "runtime_migrated_subcases": runtime_migrated_subcases,
            "legacy_aggregate_remaining_legacy_subcases": remaining_subcases,
            "legacy_aggregate_runtime_migrated_subcases": runtime_migrated_subcases,
            **_legacy_aggregate_cutover_projection_fields(cutover_evidence),
        }
    )
    if before_hash:
        result["before_hash"] = before_hash
        result["after_hash"] = "file_absent"
    _apply_shadow_replay_repair_kernel_projection(
        result,
        relative_path=relative_path,
        source_tools=normalized_source_tools,
        applied_tool_name="blocked_delete_file",
        evidence_status="failed_evidence",
        before_hash=result.get("before_hash"),
        after_hash=result.get("after_hash"),
        revalidation_evidence={},
        evidence_failure_reason="shadow_replay_delete_blocked",
        blocked=True,
    )
    repair_kernel = result.get("repair_kernel")
    if isinstance(repair_kernel, dict):
        repair_kernel["legacy_aggregate_remaining_source_tools"] = remaining_source_tools
        repair_kernel["legacy_aggregate_shadow_replay_allowed_source_tools"] = allowed_source_tools
        repair_kernel["remaining_legacy_subcases"] = remaining_subcases
        repair_kernel["runtime_migrated_subcases"] = runtime_migrated_subcases
        repair_kernel["legacy_aggregate_remaining_legacy_subcases"] = remaining_subcases
        repair_kernel["legacy_aggregate_runtime_migrated_subcases"] = runtime_migrated_subcases
        repair_kernel.update(_legacy_aggregate_cutover_projection_fields(cutover_evidence))
    return _write_tool_result(result)


def _rust_record_to_tool_result(
    record: dict[str, Any],
    *,
    write_result: dict[str, Any] | None = None,
    shadow_metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    applied_tool_name = "write_file"
    result = _record_payload(
        record,
        source_tool=str(record.get("source_tool") or "deterministic_rust_post_repair"),
        default_action=str(record.get("symbols") or "rust_post_repair"),
    )
    if record.get("phase"):
        result["phase"] = record.get("phase")
    if record.get("priority") is not None:
        result["priority"] = record.get("priority")
    if record.get("round_number") is not None:
        result["round_number"] = record.get("round_number")
    revalidation = record.get("revalidation")
    if isinstance(revalidation, dict) and revalidation:
        result["revalidation"] = revalidation
        result["revalidation_evidence"] = dict(revalidation)
        result["revalidation_scope"] = "legacy_shadow_workspace"
    if shadow_metadata is not None:
        before_content = str(shadow_metadata.get("before_content") or "")
        after_content = str(shadow_metadata.get("after_content") or "")
        result["before_hash"] = _sha256_text(before_content)
        result["after_hash"] = _sha256_text(after_content)
        result["legacy_shadow_workspace"] = True
        result["legacy_shadow_replay"] = True
        result["legacy_shadow_applied_via_director_tools"] = write_result is not None
        result["legacy_shadow_before_exists"] = bool(shadow_metadata.get("before_exists", True))
        result["legacy_shadow_after_exists"] = bool(shadow_metadata.get("after_exists", True))
        result["legacy_shadow_record_count"] = int(shadow_metadata.get("record_count") or 0)
        applied_tool_name = str(shadow_metadata.get("applied_tool_name") or applied_tool_name)
        result["applied_tool_name"] = applied_tool_name
        result["receipt_authority"] = "non_authoritative_shadow_replay_projection"
        result["authoritative"] = False
        result["runtime_authoritative_plan"] = False
        result["typed_receipt_path_available"] = False
        result["migration_blocker"] = _CALLBACK_RECEIPT_MIGRATION_BLOCKER
        result["legacy_aggregate_remaining_source_tools"] = list(
            shadow_metadata.get(
                "legacy_aggregate_remaining_source_tools",
                sorted(_RUST_LEGACY_AGGREGATE_REMAINING_SOURCE_TOOLS),
            )
        )
        result["legacy_aggregate_shadow_replay_allowed_source_tools"] = list(
            shadow_metadata.get(
                "legacy_aggregate_shadow_replay_allowed_source_tools",
                sorted(_RUST_LEGACY_AGGREGATE_REMAINING_SOURCE_TOOLS),
            )
        )
        result["remaining_legacy_subcases"] = list(shadow_metadata.get("remaining_legacy_subcases", []))
        result["runtime_migrated_subcases"] = list(shadow_metadata.get("runtime_migrated_subcases", []))
        result["legacy_aggregate_remaining_legacy_subcases"] = list(result["remaining_legacy_subcases"])
        result["legacy_aggregate_runtime_migrated_subcases"] = list(result["runtime_migrated_subcases"])
        source_tools = shadow_metadata.get("source_tools")
        normalized_source_tools = _normalized_source_tools(
            record,
            source_tools=source_tools if isinstance(source_tools, list) else None,
            fallback="deterministic_rust_post_repair",
        )
        result["source_tools"] = normalized_source_tools
        result["legacy_shadow_source_tools"] = normalized_source_tools
        cutover_evidence = _build_rust_legacy_aggregate_cutover_evidence(
            remaining_source_tools=list(result.get("legacy_aggregate_remaining_source_tools") or []),
            allowed_source_tools=list(result.get("legacy_aggregate_shadow_replay_allowed_source_tools") or []),
            blocked_source_tools=[],
            blocked_migrated_source_tools=[],
            remaining_legacy_subcases=list(result.get("remaining_legacy_subcases") or []),
            runtime_migrated_subcases=list(result.get("runtime_migrated_subcases") or []),
            blocked_subcases=[],
            blocked_migrated_subcases=[],
        )
        result.update(_legacy_aggregate_cutover_projection_fields(cutover_evidence))
    if write_result is not None:
        ok = bool(write_result.get("ok"))
        result["ok"] = ok
        result["file"] = str(write_result.get("file") or result.get("file") or "")
        for key in (
            "bytes_written",
            "operation",
            "broadcast_ok",
            "director_policy",
            "replacements",
            "normalized_patch_like_write",
            "json_config_repaired",
        ):
            if key in write_result:
                result[key] = write_result.get(key)
        if "operation" not in write_result:
            result["operation"] = applied_tool_name
        if not ok:
            result["error"] = str(write_result.get("error") or "Director write_file failed")
            if write_result.get("blocked"):
                result["blocked"] = True
    if shadow_metadata is not None:
        revalidation_evidence = dict(result.get("revalidation_evidence") or {})
        evidence_status = _legacy_revalidation_evidence_status(
            revalidation_evidence,
            write_ok=bool(result.get("ok", True)),
            blocked=bool(result.get("blocked")),
        )
        result["evidence_status"] = evidence_status
        result["receipt_status"] = _legacy_receipt_status_for_evidence(
            evidence_status,
            blocked=bool(result.get("blocked")),
        )
        result["evidence_missing"] = evidence_status == "missing_evidence"
        result["evidence_failed"] = evidence_status == "failed_evidence"
        result["evidence_resolved"] = evidence_status == "resolved_evidence"
        result["repair_success_verdict"] = evidence_status == "resolved_evidence" and bool(result.get("ok", True))
        result["verifier_evidence_required"] = True
        result["verifier_evidence_present"] = evidence_status != "missing_evidence"
        if evidence_status == "missing_evidence":
            result["evidence_missing_reason"] = _legacy_evidence_missing_reason(revalidation_evidence)
        elif evidence_status == "failed_evidence":
            result["evidence_failure_reason"] = "legacy_shadow_revalidation_failed"
        _apply_shadow_replay_repair_kernel_projection(
            result,
            relative_path=str(result.get("file") or ""),
            source_tools=list(result.get("source_tools") or []),
            applied_tool_name=applied_tool_name,
            evidence_status=evidence_status,
            before_hash=result.get("before_hash"),
            after_hash=result.get("after_hash"),
            revalidation_evidence=revalidation_evidence,
            evidence_failure_reason=result.get("evidence_failure_reason"),
            blocked=bool(result.get("blocked")),
        )
    return _write_tool_result(result, tool_name=applied_tool_name)


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
        "receipt_authority": "non_authoritative_legacy_callback_record",
        "receipt_status": "pending_revalidation",
        "authoritative": False,
        "typed_receipt_path_available": False,
        "evidence_status": "missing_evidence",
        "evidence_missing": True,
        "repair_success_verdict": False,
        "verifier_evidence_required": True,
        "verifier_evidence_present": False,
        "repair_kernel": _legacy_callback_repair_kernel_payload(
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
    legacy_aggregate_cutover_evidence = dict(migration_debt.get("legacy_aggregate_cutover_readiness_evidence") or {})
    schedule_receipt_projections = _callback_receipt_projections_from_schedule_result(receipt_projections or [])
    callback_receipt_projections = schedule_receipt_projections or _callback_receipt_projections_from_payloads(payloads)
    return {
        "schema_version": "director.post_execution_scheduler_bridge.v1",
        "mode": "legacy_callback_bridge",
        "target_scheduler": "director.runtime.repair_kernel.scheduler",
        "schedule_source": "director.runtime.public.query_director_repair_post_execution_schedule",
        "runner_binding_owner": "roles.adapters",
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
        "callback_receipt_projection_count": len(callback_receipt_projections),
        "callback_receipts_authoritative": False,
        "callback_receipt_authority_values": _sorted_unique(
            _callback_receipt_authority_value(projection) for projection in callback_receipt_projections
        ),
        "callback_receipts_with_revalidation": sum(
            1 for projection in callback_receipt_projections if _callback_projection_has_revalidation(projection)
        ),
        "typed_receipt_path_available": False,
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
        "legacy_callback_debt": dict(migration_debt.get("legacy_callback_debt") or {}),
        **_legacy_aggregate_cutover_projection_fields(legacy_aggregate_cutover_evidence),
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
            normalized["receipt_authority"] = "non_authoritative_callback_projection"
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
        normalized["receipt_authority"] = (
            _payload_receipt_authority(payload) or "non_authoritative_callback_receipt_projection"
        )
    _attach_payload_revalidation_to_projection(normalized, payload)
    return normalized


def _summary_only_callback_receipt_projection(payload: dict[str, Any]) -> dict[str, Any]:
    projection = {
        "projection_source": "summary_only_payload_annotation",
        "summary_only": True,
        "source_tool": payload.get("source_tool"),
        "bridge_step_id": payload.get("bridge_step_id"),
        "authoritative": False,
        "receipt_authority": _payload_receipt_authority(payload) or "non_authoritative_callback_tool_result_projection",
        "typed_receipt_path_available": _payload_typed_receipt_path_available(payload),
        "migration_blocker": _payload_migration_blocker(payload),
    }
    _attach_payload_revalidation_to_projection(projection, payload)
    return projection


def _payload_has_callback_receipt_projection_annotation(payload: dict[str, Any]) -> bool:
    if any(
        bool(payload.get(key))
        for key in (
            "legacy_callback_bridge",
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


def _build_rust_legacy_aggregate_cutover_evidence(
    *,
    remaining_source_tools: list[str],
    allowed_source_tools: list[str],
    blocked_source_tools: list[str],
    blocked_migrated_source_tools: list[str],
    remaining_legacy_subcases: list[str],
    runtime_migrated_subcases: list[str],
    blocked_subcases: list[str],
    blocked_migrated_subcases: list[str],
) -> dict[str, Any]:
    remaining = _sorted_unique(remaining_source_tools)
    allowed = _sorted_unique(allowed_source_tools)
    blocked = _sorted_unique(blocked_source_tools)
    blocked_migrated = _sorted_unique(blocked_migrated_source_tools)
    remaining_subcases = _sorted_unique(remaining_legacy_subcases)
    migrated_subcases = _sorted_unique(runtime_migrated_subcases)
    blocked_legacy_subcases = _sorted_unique(blocked_subcases)
    blocked_migrated_legacy_subcases = _sorted_unique(blocked_migrated_subcases)
    remaining_source_tool_blockers = _sorted_unique(f"remaining_source_tool:{source_tool}" for source_tool in remaining)
    remaining_legacy_subcase_blockers = _sorted_unique(
        f"remaining_legacy_subcase:{subcase}" for subcase in remaining_subcases
    )
    blocked_source_tool_blockers = _sorted_unique(f"blocked_source_tool:{source_tool}" for source_tool in blocked)
    blocked_migrated_source_tool_blockers = _sorted_unique(
        f"blocked_migrated_source_tool:{source_tool}" for source_tool in blocked_migrated
    )
    blocked_legacy_subcase_blockers = _sorted_unique(
        f"blocked_legacy_subcase:{subcase}" for subcase in blocked_legacy_subcases
    )
    blocked_migrated_legacy_subcase_blockers = _sorted_unique(
        f"blocked_migrated_subcase:{subcase}" for subcase in blocked_migrated_legacy_subcases
    )
    blockers = _sorted_unique(
        [
            "legacy_shadow_replay_non_authoritative",
            *remaining_source_tool_blockers,
            *remaining_legacy_subcase_blockers,
            *blocked_source_tool_blockers,
            *blocked_migrated_source_tool_blockers,
            *blocked_legacy_subcase_blockers,
            *blocked_migrated_legacy_subcase_blockers,
        ]
    )
    return {
        "schema_version": "director.rust_legacy_aggregate_cutover_readiness_evidence.v1",
        "shadow_replay_non_authoritative": True,
        "shadow_replay_authoritative": False,
        "shadow_replay_writes_authoritative": False,
        "shadow_replay_receipt_authority": "non_authoritative_shadow_replay_projection",
        "shadow_replay_authority_boundary": "legacy_shadow_replay_projection_only_not_runtime_receipt",
        "cutover_ready": False,
        "cutover_blockers": blockers,
        "remaining_source_tool_blockers": remaining_source_tool_blockers,
        "remaining_legacy_subcase_blockers": remaining_legacy_subcase_blockers,
        "blocked_source_tool_blockers": blocked_source_tool_blockers,
        "blocked_migrated_source_tool_blockers": blocked_migrated_source_tool_blockers,
        "blocked_legacy_subcase_blockers": blocked_legacy_subcase_blockers,
        "blocked_migrated_subcase_blockers": blocked_migrated_legacy_subcase_blockers,
        "remaining_source_tools": remaining,
        "remaining_source_tool_count": len(remaining),
        "remaining_source_tool_counts": _source_tool_counts(remaining),
        "shadow_replay_allowed_source_tools": allowed,
        "shadow_replay_allowed_source_tool_count": len(allowed),
        "shadow_replay_allowed_source_tool_counts": _source_tool_counts(allowed),
        "blocked_source_tools": blocked,
        "blocked_source_tool_count": len(blocked),
        "blocked_source_tool_counts": _source_tool_counts(blocked),
        "blocked_migrated_source_tools": blocked_migrated,
        "blocked_migrated_source_tool_count": len(blocked_migrated),
        "blocked_migrated_source_tool_counts": _source_tool_counts(blocked_migrated),
        "remaining_legacy_subcases": remaining_subcases,
        "remaining_legacy_subcase_count": len(remaining_subcases),
        "remaining_legacy_subcase_counts": _source_tool_counts(remaining_subcases),
        "runtime_migrated_subcases": migrated_subcases,
        "runtime_migrated_subcase_count": len(migrated_subcases),
        "runtime_migrated_subcase_counts": _source_tool_counts(migrated_subcases),
        "blocked_subcases": blocked_legacy_subcases,
        "blocked_subcase_count": len(blocked_legacy_subcases),
        "blocked_subcase_counts": _source_tool_counts(blocked_legacy_subcases),
        "blocked_migrated_subcases": blocked_migrated_legacy_subcases,
        "blocked_migrated_subcase_count": len(blocked_migrated_legacy_subcases),
        "blocked_migrated_subcase_counts": _source_tool_counts(blocked_migrated_legacy_subcases),
    }


def _legacy_aggregate_cutover_projection_fields(cutover_evidence: dict[str, Any]) -> dict[str, Any]:
    evidence = dict(cutover_evidence or {})
    return {
        "legacy_aggregate_cutover_readiness_evidence": evidence,
        "legacy_aggregate_shadow_replay_authoritative": False,
        "legacy_aggregate_shadow_replay_writes_authoritative": False,
        "legacy_aggregate_cutover_ready": False,
        "legacy_aggregate_cutover_blockers": list(evidence.get("cutover_blockers") or []),
        "legacy_aggregate_shadow_replay_non_authoritative": True,
        "legacy_aggregate_shadow_replay_authority_boundary": str(
            evidence.get("shadow_replay_authority_boundary") or ""
        ),
        "legacy_aggregate_remaining_source_tool_blockers": list(evidence.get("remaining_source_tool_blockers") or []),
        "legacy_aggregate_remaining_legacy_subcase_blockers": list(
            evidence.get("remaining_legacy_subcase_blockers") or []
        ),
        "legacy_aggregate_blocked_source_tool_blockers": list(evidence.get("blocked_source_tool_blockers") or []),
        "legacy_aggregate_blocked_migrated_source_tool_blockers": list(
            evidence.get("blocked_migrated_source_tool_blockers") or []
        ),
        "legacy_aggregate_blocked_legacy_subcase_blockers": list(evidence.get("blocked_legacy_subcase_blockers") or []),
        "legacy_aggregate_blocked_migrated_subcase_blockers": list(
            evidence.get("blocked_migrated_subcase_blockers") or []
        ),
        "legacy_aggregate_remaining_source_tool_count": int(evidence.get("remaining_source_tool_count") or 0),
        "legacy_aggregate_remaining_source_tool_counts": dict(evidence.get("remaining_source_tool_counts") or {}),
        "legacy_aggregate_blocked_source_tool_count": int(evidence.get("blocked_source_tool_count") or 0),
        "legacy_aggregate_blocked_source_tool_counts": dict(evidence.get("blocked_source_tool_counts") or {}),
        "legacy_aggregate_blocked_migrated_source_tool_count": int(
            evidence.get("blocked_migrated_source_tool_count") or 0
        ),
        "legacy_aggregate_blocked_migrated_source_tool_counts": dict(
            evidence.get("blocked_migrated_source_tool_counts") or {}
        ),
        "legacy_aggregate_remaining_legacy_subcases": list(evidence.get("remaining_legacy_subcases") or []),
        "legacy_aggregate_remaining_legacy_subcase_count": int(evidence.get("remaining_legacy_subcase_count") or 0),
        "legacy_aggregate_remaining_legacy_subcase_counts": dict(evidence.get("remaining_legacy_subcase_counts") or {}),
        "legacy_aggregate_runtime_migrated_subcases": list(evidence.get("runtime_migrated_subcases") or []),
        "legacy_aggregate_runtime_migrated_subcase_count": int(evidence.get("runtime_migrated_subcase_count") or 0),
        "legacy_aggregate_runtime_migrated_subcase_counts": dict(evidence.get("runtime_migrated_subcase_counts") or {}),
        "legacy_aggregate_blocked_subcases": list(evidence.get("blocked_subcases") or []),
        "legacy_aggregate_blocked_subcase_count": int(evidence.get("blocked_subcase_count") or 0),
        "legacy_aggregate_blocked_subcase_counts": dict(evidence.get("blocked_subcase_counts") or {}),
        "legacy_aggregate_blocked_migrated_subcases": list(evidence.get("blocked_migrated_subcases") or []),
        "legacy_aggregate_blocked_migrated_subcase_count": int(evidence.get("blocked_migrated_subcase_count") or 0),
        "legacy_aggregate_blocked_migrated_subcase_counts": dict(evidence.get("blocked_migrated_subcase_counts") or {}),
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
    remaining_source_tools = _rust_legacy_aggregate_remaining_source_tools(executable_source_tools)
    allowed_legacy_aggregate_source_tools = sorted(_rust_legacy_aggregate_allowed_source_tools(executable_source_tools))
    remaining_legacy_subcases = _rust_legacy_aggregate_remaining_subcases(executable_source_tools)
    runtime_migrated_subcases = _rust_legacy_aggregate_runtime_migrated_subcases(executable_source_tools)
    blocked_legacy_aggregate_source_tools = _sorted_unique(
        source_tool
        for payload in payloads
        for source_tool in _payload_list_values(payload, "legacy_aggregate_blocked_source_tools")
    )
    blocked_migrated_legacy_aggregate_source_tools = _sorted_unique(
        source_tool
        for payload in payloads
        for source_tool in _payload_list_values(payload, "legacy_aggregate_blocked_migrated_source_tools")
    )
    blocked_legacy_aggregate_subcases = _sorted_unique(
        subcase
        for payload in payloads
        for subcase in _payload_list_values(payload, "legacy_aggregate_blocked_subcases")
    )
    blocked_migrated_legacy_aggregate_subcases = _sorted_unique(
        subcase
        for payload in payloads
        for subcase in _payload_list_values(payload, "legacy_aggregate_blocked_migrated_subcases")
    )
    legacy_aggregate_cutover_evidence = _build_rust_legacy_aggregate_cutover_evidence(
        remaining_source_tools=remaining_source_tools,
        allowed_source_tools=allowed_legacy_aggregate_source_tools,
        blocked_source_tools=blocked_legacy_aggregate_source_tools,
        blocked_migrated_source_tools=blocked_migrated_legacy_aggregate_source_tools,
        remaining_legacy_subcases=remaining_legacy_subcases,
        runtime_migrated_subcases=runtime_migrated_subcases,
        blocked_subcases=blocked_legacy_aggregate_subcases,
        blocked_migrated_subcases=blocked_migrated_legacy_aggregate_subcases,
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
    legacy_step_count = sum(1 for step in step_entries if step["legacy_only_source_tools"])
    missing_evidence_step_count = sum(1 for step in step_entries if "missing_verifier_evidence" in step["blockers"])
    cutover_ready_step_count = sum(1 for step in step_entries if bool(step["cutover_ready"]))
    return {
        "schema_version": "director.post_execution_repair_kernel_migration_debt.v1",
        "owner_cell": "roles.adapters",
        "runtime_schedule_source": public_schedule.source,
        "runtime_ordered_step_ids": runtime_ordered_step_ids,
        "runner_binding_owner": public_schedule.runner_binding_owner,
        "convergence_verifier_present": convergence_verifier_present,
        "legacy_aggregate_remaining_source_tools": remaining_source_tools,
        "legacy_aggregate_shadow_replay_allowed_source_tools": allowed_legacy_aggregate_source_tools,
        "remaining_legacy_subcases": remaining_legacy_subcases,
        "runtime_migrated_subcases": runtime_migrated_subcases,
        "legacy_aggregate_remaining_legacy_subcases": remaining_legacy_subcases,
        "legacy_aggregate_runtime_migrated_subcases": runtime_migrated_subcases,
        "legacy_aggregate_blocked_source_tools": blocked_legacy_aggregate_source_tools,
        "legacy_aggregate_blocked_migrated_source_tools": blocked_migrated_legacy_aggregate_source_tools,
        "legacy_aggregate_blocked_subcases": blocked_legacy_aggregate_subcases,
        "legacy_aggregate_blocked_migrated_subcases": blocked_migrated_legacy_aggregate_subcases,
        **_legacy_aggregate_cutover_projection_fields(legacy_aggregate_cutover_evidence),
        "step_count": len(step_entries),
        "steps": step_entries,
        "legacy_callback_debt": {
            "schema_version": "director.post_execution_legacy_callback_debt.v1",
            "legacy_callback_bridge": True,
            "produces_tool_results_only": True,
            "preferred_typed_receipt_entrypoint": "run_runtime_repair_convergence",
            "runtime_bound_step_count": sum(1 for step in step_entries if step["runtime_executable_source_tools"]),
            "legacy_only_step_count": legacy_step_count,
            "missing_verifier_evidence_step_count": missing_evidence_step_count,
            "cutover_ready_step_count": cutover_ready_step_count,
            "cutover_ready": cutover_ready_step_count == len(step_entries) and bool(step_entries),
            "legacy_aggregate_remaining_source_tools": remaining_source_tools,
            "legacy_aggregate_shadow_replay_allowed_source_tools": allowed_legacy_aggregate_source_tools,
            "remaining_legacy_subcases": remaining_legacy_subcases,
            "runtime_migrated_subcases": runtime_migrated_subcases,
            "legacy_aggregate_remaining_legacy_subcases": remaining_legacy_subcases,
            "legacy_aggregate_runtime_migrated_subcases": runtime_migrated_subcases,
            "legacy_aggregate_blocked_source_tools": blocked_legacy_aggregate_source_tools,
            "legacy_aggregate_blocked_migrated_source_tools": blocked_migrated_legacy_aggregate_source_tools,
            "legacy_aggregate_blocked_subcases": blocked_legacy_aggregate_subcases,
            "legacy_aggregate_blocked_migrated_subcases": blocked_migrated_legacy_aggregate_subcases,
            **_legacy_aggregate_cutover_projection_fields(legacy_aggregate_cutover_evidence),
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
    legacy_only_source_tools = [
        source_tool for source_tool in actual_source_tools if source_tool not in executable_source_tools
    ]
    write_tool_evidence = any(_payload_has_write_tool_evidence(payload) for payload in payloads)
    verifier_evidence_required = bool(actual_source_tools and write_tool_evidence)
    verifier_evidence_present = any(_payload_has_verifier_evidence(payload) for payload in payloads)
    convergence_path_available = bool(runtime_executable_source_tools)
    legacy_aggregate_blocked_source_tools = _sorted_unique(
        source_tool
        for payload in payloads
        for source_tool in _payload_list_values(payload, "legacy_aggregate_blocked_source_tools")
    )
    legacy_aggregate_blocked_migrated_source_tools = _sorted_unique(
        source_tool
        for payload in payloads
        for source_tool in _payload_list_values(payload, "legacy_aggregate_blocked_migrated_source_tools")
    )
    legacy_aggregate_blocked_subcases = _sorted_unique(
        subcase
        for payload in payloads
        for subcase in _payload_list_values(payload, "legacy_aggregate_blocked_subcases")
    )
    legacy_aggregate_blocked_migrated_subcases = _sorted_unique(
        subcase
        for payload in payloads
        for subcase in _payload_list_values(payload, "legacy_aggregate_blocked_migrated_subcases")
    )
    remaining_source_tools = _rust_legacy_aggregate_remaining_source_tools(executable_source_tools)
    allowed_legacy_aggregate_source_tools = sorted(_rust_legacy_aggregate_allowed_source_tools(executable_source_tools))
    remaining_legacy_subcases = _rust_legacy_aggregate_remaining_subcases(executable_source_tools)
    runtime_migrated_subcases = _rust_legacy_aggregate_runtime_migrated_subcases(executable_source_tools)
    legacy_aggregate_cutover_evidence = _build_rust_legacy_aggregate_cutover_evidence(
        remaining_source_tools=remaining_source_tools,
        allowed_source_tools=allowed_legacy_aggregate_source_tools,
        blocked_source_tools=legacy_aggregate_blocked_source_tools,
        blocked_migrated_source_tools=legacy_aggregate_blocked_migrated_source_tools,
        remaining_legacy_subcases=remaining_legacy_subcases,
        runtime_migrated_subcases=runtime_migrated_subcases,
        blocked_subcases=legacy_aggregate_blocked_subcases,
        blocked_migrated_subcases=legacy_aggregate_blocked_migrated_subcases,
    )
    blockers: list[str] = []
    if not payloads:
        blockers.append("no_tool_results_observed")
    if actual_source_tools and not convergence_path_available:
        blockers.append("convergence_path_unavailable")
    if legacy_only_source_tools:
        blockers.append("legacy_only_source_tools_present")
    if step.source_tool not in executable_source_tools and runtime_executable_source_tools:
        blockers.append("declared_step_source_tool_is_legacy_aggregate")
    if actual_source_tools and not write_tool_evidence:
        blockers.append("missing_write_tool_evidence")
    if verifier_evidence_required and not verifier_evidence_present:
        blockers.append("missing_verifier_evidence")
    if convergence_path_available and not convergence_verifier_present:
        blockers.append("convergence_verifier_not_provided")
    if any(_payload_has_non_authoritative_runtime_receipt(payload) for payload in payloads):
        blockers.append("non_authoritative_runtime_receipt_requires_revalidation")
    if any(_payload_is_legacy_callback_record(payload) for payload in payloads):
        blockers.append("legacy_callback_record_projection")
    if legacy_aggregate_blocked_source_tools:
        blockers.append(_RUST_LEGACY_AGGREGATE_SOURCE_TOOL_BLOCKER)
    blockers = _sorted_unique(blockers)
    return {
        "step_id": step.step_id,
        "language": step.language,
        "phase": step.phase,
        "priority": step.priority,
        "declared_source_tool": step.source_tool,
        "actual_source_tools": actual_source_tools,
        "runtime_executable_source_tools": runtime_executable_source_tools,
        "legacy_only_source_tools": legacy_only_source_tools,
        "legacy_aggregate_remaining_source_tools": remaining_source_tools,
        "legacy_aggregate_shadow_replay_allowed_source_tools": allowed_legacy_aggregate_source_tools,
        "remaining_legacy_subcases": remaining_legacy_subcases,
        "runtime_migrated_subcases": runtime_migrated_subcases,
        "legacy_aggregate_remaining_legacy_subcases": remaining_legacy_subcases,
        "legacy_aggregate_runtime_migrated_subcases": runtime_migrated_subcases,
        "legacy_aggregate_blocked_source_tools": legacy_aggregate_blocked_source_tools,
        "legacy_aggregate_blocked_migrated_source_tools": legacy_aggregate_blocked_migrated_source_tools,
        "legacy_aggregate_blocked_subcases": legacy_aggregate_blocked_subcases,
        "legacy_aggregate_blocked_migrated_subcases": legacy_aggregate_blocked_migrated_subcases,
        **_legacy_aggregate_cutover_projection_fields(legacy_aggregate_cutover_evidence),
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
            and not legacy_only_source_tools
            and not blockers
        ),
        "blockers": blockers,
    }


def _legacy_callback_repair_kernel_payload(*, source_tool: str, file_path: str) -> dict[str, Any]:
    return {
        "owner_cell": "roles.adapters.legacy_strategy_host",
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
        "receipt_authority": "non_authoritative_legacy_callback_record",
        "typed_receipt_path_available": False,
        "applied_tool_name": "write_file",
        "revalidation_evidence": {},
        "migration_debt": {
            "schema_version": "director.post_execution_legacy_callback_record_debt.v1",
            "legacy_only_source_tools": [source_tool],
            "runtime_executable_source_tools": [],
            "cutover_ready": False,
            "blockers": [
                "legacy_only_source_tools_present",
                "missing_verifier_evidence",
                "legacy_callback_record_projection",
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
        legacy_shadow_source_tools = payload.get("legacy_shadow_source_tools")
        if isinstance(legacy_shadow_source_tools, list):
            source_tools.extend(str(item) for item in legacy_shadow_source_tools)
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


def _payload_is_legacy_callback_record(payload: dict[str, Any]) -> bool:
    repair_kernel = payload.get("repair_kernel")
    return isinstance(repair_kernel, dict) and repair_kernel.get("owner_cell") == "roles.adapters.legacy_strategy_host"


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
