"""Language-specific post-execution repair runners for the Director adapter bridge."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from polaris.cells.runtime.task_runtime.public import TaskRuntimeExecutionAttemptIdentityV1

from ._constants import (
    _GO_POST_EXECUTION_RUNTIME_SOURCE_TOOLS,
    _RUST_LIB_ROOT_FACADE_SOURCE_TOOL,
    _RUST_MISSING_FIELDS_SOURCE_TOOL,
    ConvergenceVerifier,
    RuntimeAdvisorNotes,
)
from ._helpers import (
    _collect_cpp_base_files,
    _collect_go_base_files,
    _collect_java_base_files,
    _collect_rust_base_files,
    _looks_like_cpp_workspace,
    _policy_gated_adapter_missing_tool_result,
    _post_execution_artifact_quality_errors,
    _rust_declared_binary_paths,
    _rust_duplicate_module_candidate_paths_from_errors,
    _rust_missing_module_candidate_paths_from_errors,
    _rust_post_execution_artifact_quality_errors,
)


def _invoke_run_runtime_repair_with_director_tools(*args: Any, **kwargs: Any) -> list[dict[str, Any]]:
    """Dispatch via package namespace so monkeypatches on the package bind."""

    import polaris.cells.roles.adapters.internal.director.post_execution_repair_bridge as _pkg

    return _pkg.run_runtime_repair_with_director_tools(*args, **kwargs)


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
    return _invoke_run_runtime_repair_with_director_tools(
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
    return _invoke_run_runtime_repair_with_director_tools(
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
    return _invoke_run_runtime_repair_with_director_tools(
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
    return _invoke_run_runtime_repair_with_director_tools(
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
    return _invoke_run_runtime_repair_with_director_tools(
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
    return _invoke_run_runtime_repair_with_director_tools(
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
    return _invoke_run_runtime_repair_with_director_tools(
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
    return _invoke_run_runtime_repair_with_director_tools(
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
    return _invoke_run_runtime_repair_with_director_tools(
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
    return _invoke_run_runtime_repair_with_director_tools(
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
    return _invoke_run_runtime_repair_with_director_tools(
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
    return _invoke_run_runtime_repair_with_director_tools(
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
    return _invoke_run_runtime_repair_with_director_tools(
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
    return _invoke_run_runtime_repair_with_director_tools(
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
    return _invoke_run_runtime_repair_with_director_tools(
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
    return _invoke_run_runtime_repair_with_director_tools(
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
    return _invoke_run_runtime_repair_with_director_tools(
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
    return _invoke_run_runtime_repair_with_director_tools(
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
    return _invoke_run_runtime_repair_with_director_tools(
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
