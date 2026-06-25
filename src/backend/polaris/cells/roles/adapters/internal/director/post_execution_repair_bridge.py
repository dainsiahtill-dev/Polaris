"""Post-execution deterministic repair bridge for Director adapter.

This module is the migration-time boundary between legacy language-specific
repair functions and the Director runtime repair kernel receipt model.
"""

from __future__ import annotations

import contextlib
import hashlib
import shutil
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

from polaris.cells.director.runtime.public.service import (
    DirectorRepairPostExecutionStepV1,
    QueryDirectorRepairAdvisoryValidationV1,
    RepairAdvisoryV1,
    RunDirectorRepairCommandV1,
    run_director_post_execution_repair_schedule,
    run_director_repair,
    validate_director_repair_advisory,
)

from .execution_tools import DirectorToolExecutor
from .repair_profile_projection import project_repair_kernel_summary

StepRunner = Callable[[Any, Path, str], list[dict[str, Any]]]
RuntimeAdvisorNotes = tuple[RepairAdvisoryV1, ...]

_CPP_REPAIR_FILE_SUFFIXES = (".c", ".cc", ".cpp", ".cxx", ".h", ".hh", ".hpp", ".hxx")
_RUST_SHADOW_COPY_IGNORES = frozenset({".git", ".venv", "__pycache__", "node_modules", "target"})
_POST_EXECUTION_REPAIR_MAX_ROUNDS = 3


_POST_EXECUTION_REPAIR_RUNNERS: dict[str, StepRunner] = {
    "go.module_import": lambda adapter, workspace, task_id: _run_go_post_repairs(adapter, task_id=task_id),
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
            )
        if step.step_id == "go.module_import":
            return _run_go_post_repairs(
                adapter,
                task_id=task_id,
                advisor_notes=runtime_advisor_notes,
            )
        if step.step_id == "java.post_execution":
            return _run_java_post_repairs(
                adapter,
                workspace,
                task_id=task_id,
                advisor_notes=runtime_advisor_notes,
            )
        return runner(adapter, workspace, task_id)

    tool_results, ordered_steps = run_director_post_execution_repair_schedule(
        runner_step_ids=tuple(_POST_EXECUTION_REPAIR_RUNNERS),
        runner=_run_step,
        max_rounds=_POST_EXECUTION_REPAIR_MAX_ROUNDS,
    )
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
    scheduler_bridge = _build_scheduler_bridge_summary(
        tool_results,
        repair_kernel=repair_kernel,
        ordered_steps=ordered_steps,
        resident_agi_repair_advisory_overlay=agi_advisory_overlay,
    )
    return tool_results, {
        "schema_version": "director.post_execution_repair_kernel.v1",
        "repair_kernel": repair_kernel,
        "scheduler_bridge": scheduler_bridge,
        "resident_agi_repair_advisory_overlay": agi_advisory_overlay,
    }


def run_cpp_post_repairs_as_tool_results(
    workspace: str | Path,
    *,
    adapter: Any | None = None,
    task_id: str = "director-cpp-post-repair",
    advisor_notes: RuntimeAdvisorNotes = (),
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
    )
    tool_results.extend(
        _run_cpp_standard_include_runtime_repair(
            adapter,
            workspace_path,
            task_id=task_id,
            advisor_notes=advisor_notes,
        )
    )
    tool_results.extend(
        _run_cpp_placeholder_declaration_runtime_repair(
            adapter,
            workspace_path,
            task_id=task_id,
            advisor_notes=advisor_notes,
        )
    )
    tool_results.extend(
        _run_cpp_struct_getter_field_access_runtime_repair(
            adapter,
            workspace_path,
            task_id=task_id,
            advisor_notes=advisor_notes,
        )
    )
    tool_results.extend(
        _run_cpp_missing_private_members_runtime_repair(
            adapter,
            workspace_path,
            task_id=task_id,
            advisor_notes=advisor_notes,
        )
    )
    tool_results.extend(
        _record_to_tool_result(
            record,
            source_tool="deterministic_cpp_post_repair",
            default_action="cpp_post_repair",
        )
        for record in _run_remaining_cpp_legacy_post_repairs(workspace_path)
    )
    return tool_results


def _run_cpp_include_path_runtime_repair(
    adapter: Any | None,
    workspace: Path,
    *,
    task_id: str,
    advisor_notes: RuntimeAdvisorNotes = (),
) -> list[dict[str, Any]]:
    return _run_cpp_runtime_repair(
        adapter,
        workspace,
        task_id=task_id,
        source_tool="deterministic_cpp_include_path_repair",
        advisor_notes=advisor_notes,
    )


def _run_cpp_standard_include_runtime_repair(
    adapter: Any | None,
    workspace: Path,
    *,
    task_id: str,
    advisor_notes: RuntimeAdvisorNotes = (),
) -> list[dict[str, Any]]:
    return _run_cpp_runtime_repair(
        adapter,
        workspace,
        task_id=task_id,
        source_tool="deterministic_cpp_standard_include_repair",
        advisor_notes=advisor_notes,
    )


def _run_cpp_placeholder_declaration_runtime_repair(
    adapter: Any | None,
    workspace: Path,
    *,
    task_id: str,
    advisor_notes: RuntimeAdvisorNotes = (),
) -> list[dict[str, Any]]:
    return _run_cpp_runtime_repair(
        adapter,
        workspace,
        task_id=task_id,
        source_tool="deterministic_cpp_placeholder_declaration_repair",
        advisor_notes=advisor_notes,
    )


def _run_cpp_struct_getter_field_access_runtime_repair(
    adapter: Any | None,
    workspace: Path,
    *,
    task_id: str,
    advisor_notes: RuntimeAdvisorNotes = (),
) -> list[dict[str, Any]]:
    return _run_cpp_runtime_repair(
        adapter,
        workspace,
        task_id=task_id,
        source_tool="deterministic_cpp_struct_getter_field_access_repair",
        advisor_notes=advisor_notes,
    )


def _run_cpp_missing_private_members_runtime_repair(
    adapter: Any | None,
    workspace: Path,
    *,
    task_id: str,
    advisor_notes: RuntimeAdvisorNotes = (),
) -> list[dict[str, Any]]:
    return _run_cpp_runtime_repair(
        adapter,
        workspace,
        task_id=task_id,
        source_tool="deterministic_cpp_missing_private_members_repair",
        advisor_notes=advisor_notes,
    )


def _run_cpp_runtime_repair(
    adapter: Any | None,
    workspace: Path,
    *,
    task_id: str,
    source_tool: str,
    advisor_notes: RuntimeAdvisorNotes = (),
) -> list[dict[str, Any]]:
    workspace_path = workspace.resolve()
    base_files = _collect_cpp_base_files(workspace_path)
    if not base_files:
        return []

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


def _run_remaining_cpp_legacy_post_repairs(workspace: Path) -> list[dict[str, str]]:
    from .deterministic_repairs.cpp_repairs import repair_cpp_failing_smoke_translation_units

    repairs: list[dict[str, str]] = []
    repairs.extend(repair_cpp_failing_smoke_translation_units(workspace))
    return repairs


def _run_go_post_repairs(
    adapter: Any,
    *,
    task_id: str,
    advisor_notes: RuntimeAdvisorNotes = (),
) -> list[dict[str, Any]]:
    from .deterministic_repairs.generic_repairs import _apply_deterministic_go_module_import_repair

    return list(
        _apply_deterministic_go_module_import_repair(
            adapter,
            task_id=task_id,
            advisor_notes=advisor_notes,
        )
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
    from .deterministic_repairs.rust_repairs import run_all_rust_post_repairs

    with tempfile.TemporaryDirectory(prefix="polaris-rust-post-") as shadow_root:
        shadow_workspace = Path(shadow_root) / "workspace"
        shutil.copytree(
            workspace_path,
            shadow_workspace,
            ignore=_rust_shadow_copy_ignore,
        )
        before_snapshot = _snapshot_rust_shadow_text_files(shadow_workspace)
        records = list(run_all_rust_post_repairs(shadow_workspace))
        after_snapshot = _snapshot_rust_shadow_text_files(shadow_workspace)

    changed_paths = sorted(path for path, content in after_snapshot.items() if before_snapshot.get(path) != content)
    deleted_paths = sorted(path for path in before_snapshot if path not in after_snapshot)
    if not changed_paths and not deleted_paths:
        return []

    message_bus = getattr(getattr(adapter, "_execution", None), "_message_bus", None)
    executor = DirectorToolExecutor(
        str(workspace_path),
        message_bus=message_bus,
        worker_id="director",
    )

    records_by_file: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        record_file = str(record.get("file") or "").strip()
        if record_file:
            records_by_file.setdefault(record_file, []).append(record)

    tool_results: list[dict[str, Any]] = []
    for relative_path in changed_paths:
        content = after_snapshot[relative_path]
        applied_tool_name, write_result = _apply_rust_shadow_change_with_director_tool(
            executor,
            relative_path,
            before_content=before_snapshot.get(relative_path),
            after_content=content,
            task_id=task_id,
        )
        if bool(write_result.get("ok")):
            progress_update = getattr(adapter, "_update_task_progress", None)
            if callable(progress_update):
                with contextlib.suppress(OSError, RuntimeError, TypeError, ValueError):
                    progress_update(task_id, "executing", current_file=relative_path)
        tool_results.append(
            _rust_record_to_tool_result(
                _primary_rust_shadow_record(records_by_file.get(relative_path), relative_path),
                write_result=write_result,
                shadow_metadata={
                    "applied_tool_name": applied_tool_name,
                    "before_content": before_snapshot.get(relative_path, ""),
                    "after_content": content,
                    "record_count": len(records_by_file.get(relative_path, [])),
                    "source_tools": sorted(
                        {
                            str(record.get("source_tool") or "deterministic_rust_post_repair")
                            for record in records_by_file.get(relative_path, [])
                        }
                    ),
                },
            )
        )

    for relative_path in deleted_paths:
        tool_results.append(
            _rust_shadow_delete_blocked_tool_result(
                _primary_rust_shadow_record(records_by_file.get(relative_path), relative_path),
                relative_path,
            )
        )
    return tool_results


def _run_java_post_repairs(
    adapter: Any,
    workspace: Path,
    *,
    task_id: str,
    advisor_notes: RuntimeAdvisorNotes = (),
) -> list[dict[str, Any]]:
    if not any(workspace.rglob("*.java")):
        return []
    from .deterministic_repairs.java_repairs import repair_java_test_dependencies

    tool_results = _run_java_accessor_alias_runtime_repair(
        adapter,
        workspace,
        task_id=task_id,
        advisor_notes=advisor_notes,
    )
    tool_results.extend(
        _record_to_tool_result(
            record,
            source_tool="deterministic_java_post_repair",
            default_action="java_post_repair",
        )
        for record in repair_java_test_dependencies(workspace)
    )
    return tool_results


def _run_java_accessor_alias_runtime_repair(
    adapter: Any,
    workspace: Path,
    *,
    task_id: str,
    advisor_notes: RuntimeAdvisorNotes = (),
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
                        "broadcast_ok": bool(write_result.get("broadcast_ok")),
                        "director_policy": write_result.get("director_policy"),
                        "repair_kernel": {
                            "owner_cell": "director.runtime",
                            "receipt_id": receipt.receipt_id,
                            "plan_id": receipt.plan_id,
                            "status": receipt.status,
                            "authoritative": receipt.authoritative,
                            "advisor_notes": [note.to_dict() for note in receipt.advisor_notes],
                            "before_hashes": dict(receipt.before_hashes),
                            "after_hashes": dict(receipt.after_hashes),
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


def _sha256_text(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _rust_shadow_delete_blocked_tool_result(record: dict[str, Any], relative_path: str) -> dict[str, Any]:
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
            "legacy_shadow_workspace": True,
            "legacy_shadow_delete_blocked": True,
        }
    )
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
        result["revalidation_scope"] = "legacy_shadow_workspace"
    if shadow_metadata is not None:
        before_content = str(shadow_metadata.get("before_content") or "")
        after_content = str(shadow_metadata.get("after_content") or "")
        result["before_hash"] = _sha256_text(before_content)
        result["after_hash"] = _sha256_text(after_content)
        result["legacy_shadow_workspace"] = True
        result["legacy_shadow_applied_via_director_tools"] = write_result is not None
        result["legacy_shadow_record_count"] = int(shadow_metadata.get("record_count") or 0)
        applied_tool_name = str(shadow_metadata.get("applied_tool_name") or applied_tool_name)
        source_tools = shadow_metadata.get("source_tools")
        if isinstance(source_tools, list) and source_tools:
            result["legacy_shadow_source_tools"] = [str(source_tool) for source_tool in source_tools]
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
    return _write_tool_result(result, tool_name=applied_tool_name)


def _record_to_tool_result(
    record: dict[str, Any],
    *,
    source_tool: str,
    default_action: str,
) -> dict[str, Any]:
    return _write_tool_result(_record_payload(record, source_tool=source_tool, default_action=default_action))


def _record_payload(record: dict[str, Any], *, source_tool: str, default_action: str) -> dict[str, Any]:
    return {
        "ok": True,
        "source_tool": source_tool,
        "file": str(record.get("file") or ""),
        "action": str(record.get("action") or default_action),
        "operation": "modify",
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
    resident_agi_repair_advisory_overlay: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payloads = [_result_payload(item) for item in tool_results]
    receipts = repair_kernel.get("receipts")
    receipt_payloads = receipts if isinstance(receipts, list) else []
    active_step_ids = _sorted_unique(str(payload.get("bridge_step_id") or "") for payload in payloads)
    agi_overlay = resident_agi_repair_advisory_overlay or {}
    return {
        "schema_version": "director.post_execution_scheduler_bridge.v1",
        "mode": "legacy_callback_bridge",
        "target_scheduler": "director.runtime.repair_kernel.scheduler",
        "schedule_source": "director.runtime.public.query_director_repair_post_execution_schedule",
        "runner_binding_owner": "roles.adapters",
        "step_order": [step.to_dict() for step in ordered_steps],
        "active_step_ids": active_step_ids,
        "observed_max_round": _max_int(payloads, "round_number"),
        "configured_max_rounds": _max_revalidation_int(payloads, "max_rounds"),
        "tool_result_count": len(tool_results),
        "source_tools": _sorted_unique(str(payload.get("source_tool") or "") for payload in payloads),
        "phases": _count_by_payload_key(payloads, "phase", default="post_execution"),
        "priorities": _count_by_payload_key(payloads, "priority", default="1"),
        "rounds": _count_by_payload_key(payloads, "round_number", default="0"),
        "receipt_count": len(receipt_payloads),
        "receipts_with_revalidation": sum(1 for receipt in receipt_payloads if receipt.get("revalidation_evidence")),
        "authoritative": bool(repair_kernel.get("authoritative")),
        "resident_agi_advisory_active": bool(agi_overlay.get("active")),
        "resident_agi_advisory_note_count": int(agi_overlay.get("advisor_note_count") or 0),
        "resident_agi_suggested_rule_count": int(agi_overlay.get("suggested_rule_count") or 0),
    }


def _runner_for_post_execution_step(step: DirectorRepairPostExecutionStepV1) -> StepRunner:
    runner = _POST_EXECUTION_REPAIR_RUNNERS.get(step.step_id)
    if runner is None:
        raise RuntimeError(f"post-execution repair schedule has no runner binding: {step.step_id}")
    return runner


def _result_payload(tool_result: dict[str, Any]) -> dict[str, Any]:
    result = tool_result.get("result")
    return result if isinstance(result, dict) else {}


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
