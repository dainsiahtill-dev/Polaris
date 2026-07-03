"""Materialization-quality callback runner ports for the Director adapter.

This module owns adapter callback execution only: collecting scoped base files,
binding Director tool execution, and invoking runtime source tools selected by
the Director runtime schedule. It does not project authoritative repair summary
or receipt evidence.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from contextlib import suppress
from pathlib import Path
from typing import Any

from polaris.cells.director.runtime.public.service import (
    QueryDirectorRepairCoverageV1,
    QueryDirectorRepairMaterializationAllowedPathsV1,
    QueryDirectorRepairMaterializationPlanProbeV1,
    QueryDirectorRepairMaterializationQualityScheduleV1,
    query_director_repair_coverage,
    query_director_repair_materialization_allowed_paths,
    query_director_repair_materialization_plan_probe,
    query_director_repair_materialization_quality_schedule,
)

from .execution_tools import DirectorToolExecutor
from .runtime_repair_tool_adapter import run_runtime_repair_with_director_tools

_MATERIALIZATION_QUALITY_REPAIR_RUNNERS = {
    "materialization.hygiene_scaffold": "_run_materialization_hygiene_scaffold",
    "materialization.typescript_scaffold": "_run_materialization_typescript_scaffold",
    "materialization.typescript_compiler": "_run_materialization_typescript_compiler",
    "materialization.html_entrypoint": "_run_materialization_html_entrypoint",
    "materialization.node_manifest": "_run_materialization_node_manifest",
    "materialization.rust_compiler": "_run_materialization_rust_compiler",
    "materialization.target_runtime": "_run_materialization_target_runtime",
    "materialization.python_import": "_run_materialization_python_import",
    "materialization.go_import": "_run_materialization_go_import",
}

_MATERIALIZATION_TARGET_RUNTIME_SUFFIXES = (
    ".ts",
    ".tsx",
    ".js",
    ".jsx",
    ".mjs",
    ".cjs",
    ".json",
    ".html",
    ".htm",
)


def has_materialization_quality_runtime_repair_coverage(artifact_quality_errors: list[str]) -> bool:
    """Return true when runtime coverage maps diagnostics to this port's executable schedule."""

    if not artifact_quality_errors:
        return False
    with suppress(RuntimeError, TypeError, ValueError):
        coverage = _project_coverage_preaudit(artifact_quality_errors)
        return _coverage_has_materialization_runtime_source_tool(
            coverage,
            materialization_source_tools=_materialization_runtime_coverage_source_tools(),
        )
    return False


def _materialization_runtime_source_tools_for_step(step_id: str) -> tuple[str, ...]:
    schedule = query_director_repair_materialization_quality_schedule(
        QueryDirectorRepairMaterializationQualityScheduleV1(include_items=True)
    )
    for step in schedule.items:
        if step.step_id == step_id:
            return step.runtime_source_tools
    return ()


def _materialization_runtime_coverage_source_tools() -> frozenset[str]:
    schedule = query_director_repair_materialization_quality_schedule(
        QueryDirectorRepairMaterializationQualityScheduleV1(include_items=True)
    )
    return frozenset(source_tool for step in schedule.items for source_tool in step.runtime_source_tools)


def _coverage_has_materialization_runtime_source_tool(
    coverage: Mapping[str, Any],
    *,
    materialization_source_tools: frozenset[str],
) -> bool:
    items = coverage.get("items") if isinstance(coverage, Mapping) else None
    if not isinstance(items, list | tuple):
        return False
    for item in items:
        if not isinstance(item, Mapping):
            continue
        if not bool(item.get("executable_runtime_plan_matched")):
            continue
        source_tools = item.get("matched_source_tools")
        if not isinstance(source_tools, list | tuple):
            continue
        if any(str(source_tool or "") in materialization_source_tools for source_tool in source_tools):
            return True
    return False


def _run_materialization_quality_repair_step(
    step_id: str,
    adapter: Any,
    *,
    task: dict[str, Any],
    task_id: str,
    artifact_quality_errors: list[str],
    advisor_notes: tuple[Any, ...] = (),
    convergence_verifier: Callable[[Any], Any] | None = None,
) -> list[dict[str, Any]]:
    if step_id == "materialization.hygiene_scaffold":
        return _run_materialization_hygiene_scaffold(
            adapter,
            task=task,
            task_id=task_id,
            artifact_quality_errors=artifact_quality_errors,
            convergence_verifier=convergence_verifier,
        )
    if step_id == "materialization.typescript_scaffold":
        return _run_materialization_typescript_scaffold(
            adapter,
            task=task,
            task_id=task_id,
            artifact_quality_errors=artifact_quality_errors,
            convergence_verifier=convergence_verifier,
        )
    if step_id == "materialization.typescript_compiler":
        return _run_materialization_typescript_compiler(
            adapter,
            task=task,
            task_id=task_id,
            artifact_quality_errors=artifact_quality_errors,
            convergence_verifier=convergence_verifier,
        )
    if step_id == "materialization.html_entrypoint":
        return _run_materialization_html_entrypoint(
            adapter,
            task=task,
            task_id=task_id,
            artifact_quality_errors=artifact_quality_errors,
            convergence_verifier=convergence_verifier,
        )
    if step_id == "materialization.node_manifest":
        return _run_materialization_node_manifest(
            adapter,
            task=task,
            task_id=task_id,
            artifact_quality_errors=artifact_quality_errors,
            convergence_verifier=convergence_verifier,
        )
    if step_id == "materialization.rust_compiler":
        return _run_materialization_rust_compiler(
            adapter,
            task=task,
            task_id=task_id,
            artifact_quality_errors=artifact_quality_errors,
            convergence_verifier=convergence_verifier,
        )
    if step_id == "materialization.target_runtime":
        return _run_materialization_target_runtime(
            adapter,
            task=task,
            task_id=task_id,
            artifact_quality_errors=artifact_quality_errors,
            convergence_verifier=convergence_verifier,
        )
    if step_id == "materialization.python_import":
        return _run_materialization_python_import(
            adapter,
            task=task,
            task_id=task_id,
            artifact_quality_errors=artifact_quality_errors,
            convergence_verifier=convergence_verifier,
        )
    if step_id == "materialization.go_import":
        return _run_materialization_go_import(
            adapter,
            task=task,
            task_id=task_id,
            artifact_quality_errors=artifact_quality_errors,
            advisor_notes=advisor_notes,
            convergence_verifier=convergence_verifier,
        )
    raise RuntimeError(f"materialization quality repair step has no runtime runner: {step_id}")


def _run_materialization_hygiene_scaffold(
    adapter: Any,
    *,
    task: dict[str, Any],
    task_id: str,
    artifact_quality_errors: list[str],
    convergence_verifier: Callable[[Any], Any] | None = None,
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for source_tool in _materialization_runtime_source_tools_for_step("materialization.hygiene_scaffold"):
        workspace_path = Path(str(getattr(adapter, "workspace", "") or "")).resolve()
        base_files = _collect_materialization_hygiene_base_files(
            workspace_path,
            task=task,
            artifact_quality_errors=artifact_quality_errors,
            source_tool=source_tool,
        )
        if not base_files:
            continue
        results.extend(
            run_runtime_repair_with_director_tools(
                adapter,
                workspace_path=workspace_path,
                task_id=task_id,
                source_tool=source_tool,
                executor_factory=DirectorToolExecutor,
                base_files=base_files,
                artifact_quality_errors=artifact_quality_errors,
                allowed_paths=tuple(base_files.keys()),
                use_editor=True,
                convergence_verifier=convergence_verifier,
            )
        )
    return results


def _run_materialization_typescript_scaffold(
    adapter: Any,
    *,
    task: dict[str, Any],
    task_id: str,
    artifact_quality_errors: list[str],
    convergence_verifier: Callable[[Any], Any] | None = None,
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    workspace_path = Path(str(getattr(adapter, "workspace", "") or "")).resolve()
    for source_tool in _materialization_runtime_source_tools_for_step("materialization.typescript_scaffold"):
        base_files = _collect_materialization_runtime_base_files(
            workspace_path,
            artifact_quality_errors=artifact_quality_errors,
            source_tool=source_tool,
            allowed_suffixes=(".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".json"),
            collect_unmatched_diagnostic_paths=True,
            task=task,
        )
        if not base_files:
            continue
        results.extend(
            run_runtime_repair_with_director_tools(
                adapter,
                workspace_path=workspace_path,
                task_id=task_id,
                source_tool=source_tool,
                executor_factory=DirectorToolExecutor,
                base_files=base_files,
                artifact_quality_errors=artifact_quality_errors,
                allowed_paths=tuple(base_files.keys()),
                use_editor=True,
                convergence_verifier=convergence_verifier,
            )
        )
    return results


def _run_materialization_typescript_compiler(
    adapter: Any,
    *,
    task: dict[str, Any],
    task_id: str,
    artifact_quality_errors: list[str],
    convergence_verifier: Callable[[Any], Any] | None = None,
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    source_tools = _materialization_plannable_runtime_source_tools(
        adapter,
        task=task,
        artifact_quality_errors=artifact_quality_errors,
        materialization_step_id="materialization.typescript_compiler",
        allowed_suffixes=(".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".html", ".json"),
        caller="materialization_typescript_compiler",
    )
    for source_tool in source_tools:
        results.extend(
            _run_materialization_typescript_runtime_repair(
                adapter,
                task=task,
                task_id=task_id,
                artifact_quality_errors=artifact_quality_errors,
                source_tool=source_tool,
                convergence_verifier=convergence_verifier,
            )
        )
    return results


def _materialization_plannable_runtime_source_tools(
    adapter: Any,
    *,
    task: Mapping[str, Any] | None,
    artifact_quality_errors: Sequence[str] | None,
    candidate_source_tools: Sequence[str] = (),
    materialization_step_id: str | None = None,
    allowed_suffixes: Sequence[str],
    caller: str,
) -> tuple[str, ...]:
    """Return only runtime source tools whose coverage can produce concrete patches."""

    runtime_source_tools = tuple(candidate_source_tools) or _materialization_runtime_source_tools_for_step(
        str(materialization_step_id or "")
    )
    if not artifact_quality_errors or not runtime_source_tools:
        return ()
    workspace_path = Path(str(getattr(adapter, "workspace", "") or "")).resolve()
    base_files = _collect_materialization_runtime_base_files(
        workspace_path,
        artifact_quality_errors=[str(item) for item in artifact_quality_errors],
        source_tool=str(runtime_source_tools[0]),
        allowed_suffixes=tuple(allowed_suffixes),
        collect_unmatched_diagnostic_paths=True,
        task=task,
    )
    if not base_files:
        return ()
    return _materialization_plannable_runtime_source_tools_from_base_files(
        artifact_quality_errors=artifact_quality_errors,
        candidate_source_tools=tuple(candidate_source_tools),
        materialization_step_id=materialization_step_id,
        base_files=base_files,
        caller=caller,
    )


def _materialization_plannable_runtime_source_tools_from_base_files(
    *,
    artifact_quality_errors: Sequence[str] | None,
    candidate_source_tools: Sequence[str] = (),
    materialization_step_id: str | None = None,
    base_files: Mapping[str, str],
    caller: str,
) -> tuple[str, ...]:
    """Return source tools proven by runtime plan-probe, not coverage alone."""

    if not candidate_source_tools and not materialization_step_id:
        return ()
    if not base_files:
        return ()
    errors = tuple(str(item) for item in artifact_quality_errors or () if str(item or "").strip())
    if not errors:
        return tuple(str(item) for item in candidate_source_tools) or _materialization_runtime_source_tools_for_step(
            str(materialization_step_id or "")
        )
    plan_probe = query_director_repair_materialization_plan_probe(
        QueryDirectorRepairMaterializationPlanProbeV1(
            artifact_quality_errors=errors,
            base_files=dict(base_files),
            source_tools=tuple(str(item) for item in candidate_source_tools),
            step_id=materialization_step_id,
            fallback_to_step_source_tools=materialization_step_id is not None,
            mode="shadow",
            metadata={
                "caller": caller,
                "read_only_plan_probe": True,
                "coverage_is_not_planning": True,
            },
        )
    )
    return plan_probe.plannable_source_tools


def _run_materialization_html_entrypoint(
    adapter: Any,
    *,
    task: dict[str, Any],
    task_id: str,
    artifact_quality_errors: list[str],
    convergence_verifier: Callable[[Any], Any] | None = None,
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for source_tool in _materialization_runtime_source_tools_for_step("materialization.html_entrypoint"):
        results.extend(
            _run_materialization_typescript_runtime_repair(
                adapter,
                task=task,
                task_id=task_id,
                artifact_quality_errors=artifact_quality_errors,
                source_tool=source_tool,
                convergence_verifier=convergence_verifier,
            )
        )
    return results


def _run_materialization_typescript_runtime_repair(
    adapter: Any,
    *,
    task: Mapping[str, Any] | None = None,
    task_id: str,
    artifact_quality_errors: list[str],
    source_tool: str,
    collect_unmatched_diagnostic_paths: bool = False,
    convergence_verifier: Callable[[Any], Any] | None = None,
) -> list[dict[str, Any]]:
    workspace_path = Path(str(getattr(adapter, "workspace", "") or "")).resolve()
    base_files = _collect_materialization_runtime_base_files(
        workspace_path,
        artifact_quality_errors=artifact_quality_errors,
        source_tool=source_tool,
        allowed_suffixes=(".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".html", ".json"),
        collect_unmatched_diagnostic_paths=collect_unmatched_diagnostic_paths,
        task=task,
    )
    if not base_files or not artifact_quality_errors:
        return []
    return run_runtime_repair_with_director_tools(
        adapter,
        workspace_path=workspace_path,
        task_id=task_id,
        source_tool=source_tool,
        executor_factory=DirectorToolExecutor,
        base_files=base_files,
        artifact_quality_errors=artifact_quality_errors,
        allowed_paths=tuple(base_files.keys()),
        use_editor=True,
        convergence_verifier=convergence_verifier,
    )


def _run_materialization_node_manifest(
    adapter: Any,
    *,
    task: dict[str, Any],
    task_id: str,
    artifact_quality_errors: list[str],
    convergence_verifier: Callable[[Any], Any] | None = None,
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    workspace_path = Path(str(getattr(adapter, "workspace", "") or "")).resolve()
    for source_tool in _materialization_runtime_source_tools_for_step("materialization.node_manifest"):
        base_files = _collect_materialization_node_manifest_base_files(
            workspace_path,
            task=task,
            artifact_quality_errors=artifact_quality_errors,
            source_tool=source_tool,
        )
        if not base_files:
            continue
        results.extend(
            run_runtime_repair_with_director_tools(
                adapter,
                workspace_path=workspace_path,
                task_id=task_id,
                source_tool=source_tool,
                executor_factory=DirectorToolExecutor,
                base_files=base_files,
                artifact_quality_errors=artifact_quality_errors,
                allowed_paths=tuple(base_files.keys()),
                use_editor=True,
                convergence_verifier=convergence_verifier,
            )
        )
    return results


def _run_materialization_rust_compiler(
    adapter: Any,
    *,
    task: Mapping[str, Any] | None = None,
    task_id: str,
    artifact_quality_errors: list[str],
    convergence_verifier: Callable[[Any], Any] | None = None,
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    del task
    workspace_path = Path(str(getattr(adapter, "workspace", "") or "")).resolve()
    base_files = _collect_materialization_rust_base_files(workspace_path)
    source_tools = _materialization_plannable_runtime_source_tools_from_base_files(
        artifact_quality_errors=artifact_quality_errors,
        materialization_step_id="materialization.rust_compiler",
        base_files=base_files,
        caller="materialization_rust_compiler",
    )
    for source_tool in source_tools:
        results.extend(
            _run_materialization_rust_runtime_repair(
                adapter,
                task_id=task_id,
                artifact_quality_errors=artifact_quality_errors,
                source_tool=source_tool,
                convergence_verifier=convergence_verifier,
            )
        )
    return results


def _collect_materialization_runtime_base_files(
    workspace_path: Path,
    *,
    artifact_quality_errors: list[str],
    source_tool: str,
    allowed_suffixes: tuple[str, ...],
    collect_unmatched_diagnostic_paths: bool = False,
    task: Mapping[str, Any] | None = None,
) -> dict[str, str]:
    if not workspace_path.is_dir():
        return {}
    paths: list[str] = []
    source_tool_matched = False
    with suppress(RuntimeError, TypeError, ValueError):
        coverage = _project_coverage_preaudit(artifact_quality_errors)
        for item in coverage.get("items") or ():
            if not isinstance(item, Mapping):
                continue
            matched_source_tools = item.get("matched_source_tools")
            matched = isinstance(matched_source_tools, list | tuple) and source_tool in {
                str(tool or "") for tool in matched_source_tools
            }
            if not matched and not collect_unmatched_diagnostic_paths:
                continue
            source_tool_matched = source_tool_matched or matched
            diagnostic = item.get("diagnostic")
            if not isinstance(diagnostic, Mapping):
                continue
            path = str(diagnostic.get("path") or "").strip().replace("\\", "/")
            if path and path.endswith(allowed_suffixes):
                paths.append(path)
    if not source_tool_matched and not collect_unmatched_diagnostic_paths:
        return {}
    paths.extend(_materialization_task_candidate_paths(task, allowed_suffixes=allowed_suffixes))
    if ".json" in allowed_suffixes:
        for config_name in ("package.json", "tsconfig.json", "tsconfig.app.json", "tsconfig.node.json"):
            paths.append(config_name)
        for tsconfig_path in sorted(workspace_path.glob("tsconfig.*.json")):
            with suppress(ValueError):
                paths.append(tsconfig_path.relative_to(workspace_path).as_posix())

    base_files: dict[str, str] = {}
    for relative_path in dict.fromkeys(paths):
        full_path = (workspace_path / relative_path).resolve()
        try:
            full_path.relative_to(workspace_path)
        except ValueError:
            continue
        if not full_path.is_file():
            continue
        with suppress(OSError, UnicodeDecodeError):
            base_files[relative_path] = full_path.read_text(encoding="utf-8")
    return base_files


def _collect_materialization_hygiene_base_files(
    workspace_path: Path,
    *,
    task: Mapping[str, Any] | None,
    artifact_quality_errors: list[str],
    source_tool: str,
) -> dict[str, str]:
    if not workspace_path.is_dir():
        return {}
    if source_tool == "deterministic_scaffold_marker_cleanup" and not _task_allows_materialization_scaffold_cleanup(
        task
    ):
        return {}
    return _collect_materialization_runtime_base_files(
        workspace_path,
        artifact_quality_errors=artifact_quality_errors,
        source_tool=source_tool,
        allowed_suffixes=(".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".py", ".go", ".html", ".css", ".json"),
        collect_unmatched_diagnostic_paths=True,
        task=task,
    )


def _collect_materialization_node_manifest_base_files(
    workspace_path: Path,
    *,
    task: Mapping[str, Any] | None,
    artifact_quality_errors: list[str],
    source_tool: str,
) -> dict[str, str]:
    base_files = _collect_materialization_runtime_base_files(
        workspace_path,
        artifact_quality_errors=artifact_quality_errors,
        source_tool=source_tool,
        allowed_suffixes=(".json", ".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx"),
        collect_unmatched_diagnostic_paths=True,
        task=task,
    )
    for relative_path in ("package.json", "scripts/test.mjs", "scripts/test.js", "scripts/test.cjs"):
        _add_existing_materialization_base_file(base_files, workspace_path, relative_path)
    return base_files


def _collect_materialization_target_runtime_base_files(
    workspace_path: Path,
    *,
    task: Mapping[str, Any] | None,
    artifact_quality_errors: list[str],
    source_tool: str,
) -> dict[str, str]:
    base_files = _collect_materialization_runtime_base_files(
        workspace_path,
        artifact_quality_errors=artifact_quality_errors,
        source_tool=source_tool,
        allowed_suffixes=(".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".json", ".html", ".htm"),
        collect_unmatched_diagnostic_paths=True,
        task=task,
    )
    if source_tool in _materialization_runtime_source_tools_for_step("materialization.target_runtime"):
        _add_bounded_workspace_materialization_base_files(
            base_files,
            workspace_path,
            allowed_suffixes=(".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".json", ".html", ".htm"),
            max_files=512,
        )
    return base_files


def _materialization_allowed_paths_from_runtime_public_plan(
    *,
    source_tool: str,
    base_files: Mapping[str, str],
    artifact_quality_errors: list[str],
) -> tuple[str, ...]:
    result = query_director_repair_materialization_allowed_paths(
        QueryDirectorRepairMaterializationAllowedPathsV1(
            source_tool=source_tool,
            base_files=base_files,
            artifact_quality_errors=tuple(artifact_quality_errors),
            mode="shadow",
            metadata={"adapter_bridge": "materialization_quality_runtime_ports"},
        )
    )
    return result.allowed_paths


def _add_bounded_workspace_materialization_base_files(
    base_files: dict[str, str],
    workspace_path: Path,
    *,
    allowed_suffixes: tuple[str, ...],
    max_files: int,
) -> None:
    if not workspace_path.is_dir():
        return
    ignored_parts = {"node_modules", "dist", "build", "coverage", ".git", ".venv", "venv", "__pycache__"}
    for candidate in sorted(workspace_path.rglob("*")):
        if len(base_files) >= max_files:
            return
        if not candidate.is_file() or not candidate.name.lower().endswith(allowed_suffixes):
            continue
        try:
            relative = candidate.relative_to(workspace_path).as_posix()
        except ValueError:
            continue
        if any(part in ignored_parts for part in relative.split("/")):
            continue
        _add_existing_materialization_base_file(base_files, workspace_path, relative)


def _add_existing_materialization_base_file(
    base_files: dict[str, str],
    workspace_path: Path,
    relative_path: str,
) -> None:
    normalized = str(relative_path or "").strip().replace("\\", "/")
    if not normalized or normalized in base_files:
        return
    full_path = (workspace_path / normalized).resolve()
    try:
        full_path.relative_to(workspace_path)
    except ValueError:
        return
    if not full_path.is_file():
        return
    with suppress(OSError, UnicodeDecodeError):
        base_files[normalized] = full_path.read_text(encoding="utf-8")


def _task_allows_materialization_scaffold_cleanup(task: Mapping[str, Any] | None) -> bool:
    if not isinstance(task, Mapping):
        return False
    metadata_raw = task.get("metadata")
    metadata = metadata_raw if isinstance(metadata_raw, Mapping) else {}
    if str(metadata.get("autofix_reason") or "").strip() == "deterministic_scaffold_residue_cleanup":
        return True
    task_text = _materialization_task_text_blob(task).lower()
    return "scaffold" in task_text and "residue" in task_text and "audit-seed" in task_text


def _materialization_task_text_blob(value: Any) -> str:
    if isinstance(value, Mapping):
        return "\n".join(_materialization_task_text_blob(item) for item in value.values())
    if isinstance(value, list | tuple | set):
        return "\n".join(_materialization_task_text_blob(item) for item in value)
    return str(value or "")


def _materialization_task_candidate_paths(
    task: Mapping[str, Any] | None,
    *,
    allowed_suffixes: tuple[str, ...],
) -> tuple[str, ...]:
    if not isinstance(task, Mapping):
        return ()
    raw_candidates: list[Any] = []
    for key in ("target_files", "files", "paths"):
        value = task.get(key)
        if isinstance(value, str):
            raw_candidates.append(value)
        elif isinstance(value, list | tuple | set):
            raw_candidates.extend(value)
    metadata = task.get("metadata")
    if isinstance(metadata, Mapping):
        for key in ("target_files", "files", "paths"):
            value = metadata.get(key)
            if isinstance(value, str):
                raw_candidates.append(value)
            elif isinstance(value, list | tuple | set):
                raw_candidates.extend(value)
    paths: list[str] = []
    for candidate in raw_candidates:
        path = str(candidate or "").strip().replace("\\", "/")
        if path and path.endswith(allowed_suffixes):
            paths.append(path)
    return tuple(dict.fromkeys(paths))


def _materialization_allowed_paths_in_current_task_scope(
    runtime_allowed_paths: Sequence[str],
    *,
    task: Mapping[str, Any] | None,
    allowed_suffixes: tuple[str, ...],
) -> tuple[str, ...]:
    """Constrain runtime repair writes to the current task's materialization scope."""

    task_allowed_paths = _materialization_task_candidate_paths(task, allowed_suffixes=allowed_suffixes)
    runtime_allowed = tuple(dict.fromkeys(str(path or "").strip().replace("\\", "/") for path in runtime_allowed_paths))
    runtime_allowed = tuple(path for path in runtime_allowed if path)
    if not task_allowed_paths:
        return runtime_allowed
    task_allowed = set(task_allowed_paths)
    return tuple(path for path in runtime_allowed if path in task_allowed)


def _run_materialization_rust_runtime_repair(
    adapter: Any,
    *,
    task_id: str,
    artifact_quality_errors: list[str],
    source_tool: str,
    convergence_verifier: Callable[[Any], Any] | None = None,
) -> list[dict[str, Any]]:
    from .execution_tools import DirectorToolExecutor
    from .runtime_repair_tool_adapter import run_runtime_repair_with_director_tools

    workspace_path = Path(str(getattr(adapter, "workspace", "") or "")).resolve()
    base_files = _collect_materialization_rust_base_files(workspace_path)
    if not base_files or not artifact_quality_errors:
        return []
    return run_runtime_repair_with_director_tools(
        adapter,
        workspace_path=workspace_path,
        task_id=task_id,
        source_tool=source_tool,
        executor_factory=DirectorToolExecutor,
        base_files=base_files,
        artifact_quality_errors=artifact_quality_errors,
        allowed_paths=tuple(base_files.keys()),
        use_editor=True,
        convergence_verifier=convergence_verifier,
    )


def _collect_materialization_rust_base_files(workspace_path: Path) -> dict[str, str]:
    if not workspace_path.is_dir():
        return {}
    base_files: dict[str, str] = {}
    cargo_path = workspace_path / "Cargo.toml"
    if cargo_path.is_file():
        with suppress(OSError, UnicodeDecodeError):
            base_files["Cargo.toml"] = cargo_path.read_text(encoding="utf-8")
    for rust_file in sorted(workspace_path.rglob("*.rs")):
        try:
            relative = rust_file.relative_to(workspace_path).as_posix()
        except ValueError:
            continue
        if "target" in relative.split("/"):
            continue
        try:
            base_files[relative] = rust_file.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
    return base_files


def _run_materialization_target_runtime(
    adapter: Any,
    *,
    task: dict[str, Any],
    task_id: str,
    artifact_quality_errors: list[str],
    convergence_verifier: Callable[[Any], Any] | None = None,
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    workspace_path = Path(str(getattr(adapter, "workspace", "") or "")).resolve()
    for source_tool in _materialization_runtime_source_tools_for_step("materialization.target_runtime"):
        base_files = _collect_materialization_target_runtime_base_files(
            workspace_path,
            task=task,
            artifact_quality_errors=artifact_quality_errors,
            source_tool=source_tool,
        )
        if not base_files:
            continue
        runtime_allowed_paths = _materialization_allowed_paths_from_runtime_public_plan(
            source_tool=source_tool,
            base_files=base_files,
            artifact_quality_errors=artifact_quality_errors,
        )
        allowed_paths = _materialization_allowed_paths_in_current_task_scope(
            runtime_allowed_paths,
            task=task,
            allowed_suffixes=_MATERIALIZATION_TARGET_RUNTIME_SUFFIXES,
        )
        if not allowed_paths:
            continue
        results.extend(
            run_runtime_repair_with_director_tools(
                adapter,
                workspace_path=workspace_path,
                task_id=task_id,
                source_tool=source_tool,
                executor_factory=DirectorToolExecutor,
                base_files=base_files,
                artifact_quality_errors=artifact_quality_errors,
                allowed_paths=allowed_paths,
                use_editor=True,
                convergence_verifier=convergence_verifier,
            )
        )
    return results


def _run_materialization_python_import(
    adapter: Any,
    *,
    task: dict[str, Any],
    task_id: str,
    artifact_quality_errors: list[str],
    convergence_verifier: Callable[[Any], Any] | None = None,
) -> list[dict[str, Any]]:
    workspace = Path(getattr(adapter, "workspace", "") or "")
    if not workspace.is_dir() or not artifact_quality_errors:
        return []
    workspace_path = workspace.resolve()
    if not any(workspace_path.rglob("*.py")):
        return []
    results: list[dict[str, Any]] = []
    base_files = _collect_materialization_python_base_files(workspace_path)
    source_tools = _materialization_plannable_runtime_source_tools_from_base_files(
        artifact_quality_errors=artifact_quality_errors,
        materialization_step_id="materialization.python_import",
        base_files=base_files,
        caller="materialization_python_import",
    )

    for source_tool in source_tools:
        runtime_results = run_runtime_repair_with_director_tools(
            adapter,
            workspace_path=workspace_path,
            task_id=task_id,
            source_tool=source_tool,
            executor_factory=DirectorToolExecutor,
            base_files=base_files,
            artifact_quality_errors=artifact_quality_errors,
            allowed_paths=tuple(base_files.keys()),
            use_editor=True,
            convergence_verifier=convergence_verifier,
        )
        if any(not bool(item.get("success", False)) for item in runtime_results):
            return [*results, *runtime_results]
        results.extend(runtime_results)
    return results


def _collect_materialization_python_base_files(workspace_path: Path) -> dict[str, str]:
    if not workspace_path.is_dir():
        return {}
    base_files: dict[str, str] = {}
    for python_file in sorted(workspace_path.rglob("*.py")):
        with suppress(ValueError):
            parts = python_file.relative_to(workspace_path).parts
            if any(part in {"__pycache__", ".venv", "venv"} for part in parts):
                continue
            relative_path = python_file.relative_to(workspace_path).as_posix()
            with suppress(OSError, UnicodeDecodeError):
                base_files[relative_path] = python_file.read_text(encoding="utf-8")
    return base_files


def _run_materialization_go_import(
    adapter: Any,
    *,
    task: Mapping[str, Any] | None = None,
    task_id: str,
    artifact_quality_errors: list[str],
    advisor_notes: tuple[Any, ...] = (),
    convergence_verifier: Callable[[Any], Any] | None = None,
) -> list[dict[str, Any]]:
    return _run_materialization_go_import_repairs(
        adapter,
        task=task,
        task_id=task_id,
        artifact_quality_errors=artifact_quality_errors,
        advisor_notes=advisor_notes,
        convergence_verifier=convergence_verifier,
    )


def _run_materialization_go_import_repairs(
    adapter: Any,
    *,
    task: Mapping[str, Any] | None = None,
    task_id: str,
    artifact_quality_errors: list[str] | tuple[str, ...] = (),
    advisor_notes: tuple[Any, ...] = (),
    convergence_verifier: Callable[[Any], Any] | None = None,
) -> list[dict[str, Any]]:
    """Run materialization Go import repairs from the runtime-owned schedule bridge."""

    workspace = Path(getattr(adapter, "workspace", "") or "")
    if not workspace.is_dir():
        return []
    workspace_path = workspace.resolve()
    if not any(workspace_path.rglob("*.go")):
        return []
    results: list[dict[str, Any]] = []
    del task
    base_files = _collect_materialization_go_base_files(workspace_path)
    if not any(path.endswith(".go") for path in base_files):
        return results
    source_tools = _materialization_plannable_runtime_source_tools_from_base_files(
        artifact_quality_errors=artifact_quality_errors,
        materialization_step_id="materialization.go_import",
        base_files=base_files,
        caller="materialization_go_import",
    )

    for source_tool in source_tools:
        runtime_results = run_runtime_repair_with_director_tools(
            adapter,
            workspace_path=workspace_path,
            task_id=task_id,
            source_tool=source_tool,
            executor_factory=DirectorToolExecutor,
            base_files=base_files,
            allowed_paths=tuple(base_files.keys()),
            artifact_quality_errors=artifact_quality_errors,
            advisor_notes=advisor_notes,
            use_editor=True,
            convergence_verifier=convergence_verifier,
        )
        if any(not bool(item.get("success", False)) for item in runtime_results):
            return [*results, *runtime_results]
        results.extend(runtime_results)

    return results


def _collect_materialization_go_base_files(workspace_path: Path) -> dict[str, str]:
    base_files: dict[str, str] = {}
    go_mod = workspace_path / "go.mod"
    if go_mod.is_file():
        with suppress(OSError, UnicodeDecodeError):
            base_files["go.mod"] = go_mod.read_text(encoding="utf-8")
    for go_file in sorted(workspace_path.rglob("*.go")):
        if not go_file.is_file() or go_file.name.endswith("_test.go"):
            continue
        with suppress(ValueError):
            relative_path = go_file.relative_to(workspace_path).as_posix()
            with suppress(OSError, UnicodeDecodeError):
                base_files[relative_path] = go_file.read_text(encoding="utf-8")
    return base_files


def _project_coverage_preaudit(artifact_quality_errors: list[str]) -> dict[str, Any]:
    """Project read-only rule coverage before any bridge runner writes."""

    return query_director_repair_coverage(
        QueryDirectorRepairCoverageV1(
            artifact_quality_errors=tuple(str(item) for item in artifact_quality_errors),
        )
    ).to_dict()


def _project_materialization_plan_probe_preaudit(
    adapter: Any,
    *,
    task: Mapping[str, Any] | None,
    artifact_quality_errors: list[str],
    coverage_preaudit: Mapping[str, Any],
) -> dict[str, Any]:
    """Project read-only coverage-vs-planning evidence for runtime ports."""

    del coverage_preaudit
    if not artifact_quality_errors:
        return {
            "schema_version": "director.materialization_quality_plan_probe_preaudit.v1",
            "status": "already_clean",
            "source": "roles.adapters.materialization_quality_runtime_ports",
            "runtime_public_entrypoint": "query_director_repair_materialization_plan_probe",
            "read_only": True,
            "candidate_source_tools": [],
            "base_file_count": 0,
        }
    workspace_path = Path(str(getattr(adapter, "workspace", "") or "")).resolve()
    materialization_source_tools = tuple(sorted(_materialization_runtime_coverage_source_tools()))
    base_files = _collect_materialization_runtime_base_files(
        workspace_path,
        artifact_quality_errors=artifact_quality_errors,
        source_tool=next(iter(materialization_source_tools), ""),
        allowed_suffixes=(
            ".ts",
            ".tsx",
            ".js",
            ".jsx",
            ".mjs",
            ".cjs",
            ".json",
            ".html",
            ".htm",
            ".css",
            ".py",
            ".go",
            ".rs",
            ".toml",
            ".java",
            ".cpp",
            ".cc",
            ".cxx",
            ".hpp",
            ".h",
        ),
        collect_unmatched_diagnostic_paths=True,
        task=task,
    )
    plan_probe = query_director_repair_materialization_plan_probe(
        QueryDirectorRepairMaterializationPlanProbeV1(
            artifact_quality_errors=tuple(str(item) for item in artifact_quality_errors),
            base_files=base_files,
            source_tools=materialization_source_tools,
            mode="shadow",
            metadata={
                "caller": "materialization_quality_runtime_ports",
                "read_only_plan_probe": True,
            },
        )
    ).to_dict()
    repair_plan_probe = dict(plan_probe.get("runtime_plan_probe") or {})
    covered_unplannable_source_tools = list(
        plan_probe.get("covered_unplannable_source_tools")
        or repair_plan_probe.get("covered_unplannable_source_tools")
        or ()
    )
    covered_unplannable_diagnostics = list(
        plan_probe.get("covered_unplannable_diagnostics")
        or repair_plan_probe.get("covered_unplannable_diagnostics")
        or ()
    )
    covered_unplannable_diagnostic_count = int(
        plan_probe.get("covered_unplannable_diagnostic_count")
        or repair_plan_probe.get("covered_unplannable_diagnostic_count")
        or len(covered_unplannable_diagnostics)
        or 0
    )
    return {
        **plan_probe,
        "schema_version": "director.materialization_quality_plan_probe_preaudit.v1",
        "source": "roles.adapters.materialization_quality_runtime_ports",
        "runtime_public_entrypoint": "query_director_repair_materialization_plan_probe",
        "read_only": True,
        "candidate_source_tools": list(plan_probe.get("candidate_source_tools") or ()),
        "covered_unplannable_source_tools": covered_unplannable_source_tools,
        "covered_unplannable_diagnostic_count": covered_unplannable_diagnostic_count,
        "covered_unplannable_diagnostics": covered_unplannable_diagnostics,
        "base_file_count": len(base_files),
        "runtime_plan_probe": plan_probe,
    }
