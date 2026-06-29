"""Runtime-owned deterministic repair bridge for ``execute_method``.

This module keeps ``execute_method.py`` behind the Director Runtime public
repair boundary. File-mutating deterministic repairs must execute through
``run_runtime_repair_with_director_tools``; verifier-style helpers remain here
only as non-repair smoke checks until they move to a verifier cell.
"""

from __future__ import annotations

import contextlib
import os
import re
import subprocess
import sys
from functools import lru_cache
from pathlib import Path
from typing import Any

from polaris.cells.director.runtime.public.service import query_director_repair_strategy_catalog

from .execution_tools import DirectorToolExecutor
from .repair_profile_projection import project_repair_kernel_summary
from .runtime_repair_tool_adapter import run_runtime_repair_with_director_tools
from .task_scope_paths import _extract_task_path_candidates, _normalize_declared_task_path

_LEGACY_DETERMINISTIC_REPAIR_COMPAT_PREFIXES = ("_apply_deterministic_", "repair_")
# Migration-only surface for old ``execute_method`` imports. Production calls
# must use the explicit run_* wrappers below or director.runtime public service.
_LEGACY_EXECUTE_METHOD_REPAIR_HELPER_ALLOWLIST: frozenset[str] = frozenset()
_RUNTIME_EXECUTABLE_REPAIR_SOURCE_TOOL_FALLBACKS = frozenset(
    {
        "deterministic_cpp_include_path_repair",
        "deterministic_cpp_missing_private_members_repair",
        "deterministic_cpp_placeholder_declaration_repair",
        "deterministic_cpp_standard_include_repair",
        "deterministic_cpp_struct_getter_field_access_repair",
        "deterministic_go_bare_import_string_repair",
        "deterministic_java_accessor_alias_repair",
        "deterministic_patch_residue_cleanup",
        "deterministic_typescript_duplicate_object_property_repair",
        "deterministic_typescript_enum_member_separator_repair",
        "deterministic_typescript_nullable_canvas_context_repair",
        "deterministic_typescript_return_object_semicolon_repair",
    }
)
_RUNTIME_REPAIR_SCAN_EXCLUDED_DIRS = frozenset(
    {
        ".git",
        ".hg",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        "__pycache__",
        "build",
        "dist",
        "node_modules",
        "target",
        "venv",
        ".venv",
    }
)
_RUNTIME_TEXT_REPAIR_SUFFIXES = frozenset(
    {
        ".cjs",
        ".css",
        ".go",
        ".html",
        ".js",
        ".jsx",
        ".json",
        ".mjs",
        ".py",
        ".ts",
        ".tsx",
    }
)
_PYTHON_REPAIR_SUFFIXES = frozenset({".py"})
_TYPESCRIPT_REPAIR_SUFFIXES = frozenset({".ts", ".tsx"})
_DECLARED_TARGET_REPAIR_SUFFIXES = frozenset(
    {
        ".cjs",
        ".css",
        ".go",
        ".html",
        ".js",
        ".jsx",
        ".json",
        ".mjs",
        ".py",
        ".ts",
        ".tsx",
    }
)
_PYTHON_MAIN_BLOCK_RE = re.compile(
    r'^\s*if\s+__name__\s*==\s*["\']__main__["\']\s*:',
    re.MULTILINE,
)
_PYTHON_RUNTIME_SMOKE_TIMEOUT_SECONDS = 5.0


def _legacy_execute_method_helper_source_tool(name: str) -> str:
    if name.startswith("_apply_"):
        return name[len("_apply_") :]
    if name.startswith("repair_"):
        return name
    return ""


@lru_cache(maxsize=1)
def _runtime_executable_repair_source_tools() -> frozenset[str]:
    source_tools = set(_RUNTIME_EXECUTABLE_REPAIR_SOURCE_TOOL_FALLBACKS)
    try:
        catalog = query_director_repair_strategy_catalog()
        summary = catalog.summary if isinstance(catalog.summary, dict) else {}
        for source_tool in summary.get("executable_runtime_source_tools") or ():
            normalized = str(source_tool or "").strip()
            if normalized:
                source_tools.add(normalized)
    except (OSError, RuntimeError, TypeError, ValueError):
        pass
    return frozenset(source_tools)


def get_legacy_execute_method_repair_helper(name: str) -> Any:
    """Return an allowlisted migration helper for old execute_method imports."""

    source_tool = _legacy_execute_method_helper_source_tool(name)
    if source_tool in _runtime_executable_repair_source_tools():
        raise AttributeError(f"{name} is owned by director.runtime; use director.runtime.public")
    raise AttributeError(f"{name} is not an allowlisted execute_method legacy repair helper")


def _adapter_workspace_path(adapter: Any) -> Path | None:
    workspace = Path(str(getattr(adapter, "workspace", "") or "")).resolve()
    if not workspace.exists() or not workspace.is_dir():
        return None
    return workspace


def _adapter_artifact_quality_errors(adapter: Any) -> tuple[str, ...]:
    errors = getattr(adapter, "artifact_quality_errors", ())
    if errors is None:
        return ()
    if isinstance(errors, str):
        return (errors,)
    try:
        return tuple(str(item) for item in errors if str(item or "").strip())
    except TypeError:
        return ()


def _dedupe_posix_paths(paths: list[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    result: list[str] = []
    for path in paths:
        token = str(path or "").strip().replace("\\", "/")
        if not token or token in seen:
            continue
        seen.add(token)
        result.append(token)
    return tuple(result)


def _task_declared_paths(task: dict[str, Any], *, workspace_name: str) -> tuple[str, ...]:
    paths: list[str] = []
    for candidate in _extract_task_path_candidates(task):
        normalized = _normalize_declared_task_path(candidate, workspace_name=workspace_name)
        if normalized:
            paths.append(normalized)
    return _dedupe_posix_paths(paths)


def _task_allows_scaffold_marker_cleanup(task: dict[str, Any]) -> bool:
    metadata_raw = task.get("metadata")
    metadata = metadata_raw if isinstance(metadata_raw, dict) else {}
    if str(metadata.get("autofix_reason") or "").strip() == "deterministic_scaffold_residue_cleanup":
        return True
    task_text = _task_text_blob(task).lower()
    return "scaffold" in task_text and "residue" in task_text and "audit-seed" in task_text


def _task_text_blob(value: Any) -> str:
    if isinstance(value, dict):
        return "\n".join(_task_text_blob(item) for item in value.values())
    if isinstance(value, list | tuple | set):
        return "\n".join(_task_text_blob(item) for item in value)
    return str(value or "")


def _runtime_base_files_from_paths(
    workspace_path: Path,
    paths: tuple[str, ...],
    *,
    suffixes: frozenset[str],
    extra_paths: tuple[str, ...] = (),
) -> dict[str, str]:
    base_files: dict[str, str] = {}
    for raw_path in (*paths, *extra_paths):
        normalized = str(raw_path or "").strip().replace("\\", "/")
        if not normalized:
            continue
        target = (workspace_path / normalized).resolve()
        try:
            target.relative_to(workspace_path)
        except ValueError:
            continue
        if not target.is_file() or target.suffix.lower() not in suffixes:
            continue
        try:
            base_files[normalized] = target.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
    return base_files


def _runtime_base_files_by_scan(
    workspace_path: Path,
    *,
    suffixes: frozenset[str],
    max_files: int = 400,
) -> dict[str, str]:
    base_files: dict[str, str] = {}
    for target in sorted(workspace_path.rglob("*")):
        if len(base_files) >= max_files:
            break
        if any(part in _RUNTIME_REPAIR_SCAN_EXCLUDED_DIRS for part in target.relative_to(workspace_path).parts):
            continue
        if not target.is_file() or target.suffix.lower() not in suffixes:
            continue
        rel_path = target.relative_to(workspace_path).as_posix()
        try:
            base_files[rel_path] = target.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
    return base_files


def _runtime_base_files_for_task(
    workspace_path: Path,
    task: dict[str, Any],
    *,
    suffixes: frozenset[str],
    extra_paths: tuple[str, ...] = (),
    scan_when_unscoped: bool = False,
) -> tuple[dict[str, str], tuple[str, ...]]:
    workspace_name = workspace_path.name
    declared_paths = _task_declared_paths(task, workspace_name=workspace_name)
    base_files = _runtime_base_files_from_paths(
        workspace_path,
        declared_paths,
        suffixes=suffixes,
        extra_paths=extra_paths,
    )
    if not base_files and scan_when_unscoped:
        base_files = _runtime_base_files_by_scan(workspace_path, suffixes=suffixes)
    elif scan_when_unscoped:
        scoped_dirs = tuple(
            path.rstrip("/") + "/"
            for path in declared_paths
            if path and not Path(path).suffix and (workspace_path / path).is_dir()
        )
        if scoped_dirs:
            for path, content in _runtime_base_files_by_scan(workspace_path, suffixes=suffixes).items():
                if path.startswith(scoped_dirs):
                    base_files.setdefault(path, content)
    allowed_paths = _dedupe_posix_paths([*base_files.keys(), *declared_paths, *extra_paths])
    return base_files, allowed_paths


def _missing_declared_target_errors(
    workspace_path: Path,
    task: dict[str, Any],
    *,
    workspace_name: str,
) -> tuple[str, ...]:
    errors: list[str] = []
    for path in _task_declared_paths(task, workspace_name=workspace_name):
        target = (workspace_path / path).resolve()
        try:
            target.relative_to(workspace_path)
        except ValueError:
            continue
        if not target.exists():
            errors.append(f"declared target file missing {path} is missing")
    return tuple(errors)


def _runtime_repair_tool_results(
    adapter: Any,
    *,
    task: dict[str, Any],
    task_id: str,
    source_tool: str,
    suffixes: frozenset[str],
    artifact_quality_errors: tuple[str, ...] = (),
    extra_paths: tuple[str, ...] = (),
    scan_when_unscoped: bool = False,
    use_editor: bool = True,
) -> list[dict[str, Any]]:
    workspace_path = _adapter_workspace_path(adapter)
    if workspace_path is None:
        return []
    base_files, allowed_paths = _runtime_base_files_for_task(
        workspace_path,
        task,
        suffixes=suffixes,
        extra_paths=extra_paths,
        scan_when_unscoped=scan_when_unscoped,
    )
    if not base_files:
        return []
    return run_runtime_repair_with_director_tools(
        adapter,
        workspace_path=workspace_path,
        task_id=task_id,
        source_tool=source_tool,
        executor_factory=DirectorToolExecutor,
        base_files=base_files,
        artifact_quality_errors=artifact_quality_errors,
        allowed_paths=allowed_paths,
        use_editor=use_editor,
    )


def _runtime_repair_summary(
    *,
    stage: str,
    tool_results: list[dict[str, Any]],
    artifact_quality_errors: tuple[str, ...],
) -> dict[str, Any]:
    return {
        "stage": stage,
        "success": bool(tool_results),
        "repair_kernel": project_repair_kernel_summary(
            stage=stage,
            tool_results=tool_results,
            artifact_quality_errors=artifact_quality_errors,
        ),
        "repair_kernel_owner": "director.runtime",
        "adapter_strategy_host_used": False,
    }


def run_scaffold_marker_cleanup(
    adapter: Any,
    *,
    task: dict[str, Any],
    task_id: str,
) -> list[dict[str, Any]]:
    if not _task_allows_scaffold_marker_cleanup(task):
        return []
    return _runtime_repair_tool_results(
        adapter,
        task=task,
        task_id=task_id,
        source_tool="deterministic_scaffold_marker_cleanup",
        suffixes=_RUNTIME_TEXT_REPAIR_SUFFIXES,
    )


def run_node_test_script_contract_repair(
    adapter: Any,
    *,
    task: dict[str, Any],
    task_id: str,
) -> list[dict[str, Any]]:
    artifact_quality_errors = (
        *_adapter_artifact_quality_errors(adapter),
        "Artifact quality scan failed: over-strict generated node test contract in scripts/test.mjs",
    )
    return _runtime_repair_tool_results(
        adapter,
        task=task,
        task_id=task_id,
        source_tool="deterministic_node_test_script_contract_repair",
        suffixes=frozenset({".mjs"}),
        artifact_quality_errors=artifact_quality_errors,
        extra_paths=("scripts/test.mjs",),
        use_editor=False,
    )


def run_patch_residue_cleanup(
    adapter: Any,
    *,
    task: dict[str, Any],
    task_id: str,
) -> list[dict[str, Any]]:
    workspace_path = Path(str(getattr(adapter, "workspace", "") or "")).resolve()
    if not workspace_path.exists() or not workspace_path.is_dir():
        return []
    workspace_name = workspace_path.name
    base_files: dict[str, str] = {}
    for candidate in _extract_task_path_candidates(task):
        normalized = _normalize_declared_task_path(candidate, workspace_name=workspace_name)
        if not normalized:
            continue
        target_path = (workspace_path / normalized).resolve()
        try:
            target_path.relative_to(workspace_path)
        except ValueError:
            continue
        if not target_path.is_file() or target_path.suffix.lower() not in {
            ".ts",
            ".tsx",
            ".js",
            ".jsx",
            ".mjs",
            ".cjs",
        }:
            continue
        try:
            text = target_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        base_files[normalized] = text
    if not base_files:
        return []

    return run_runtime_repair_with_director_tools(
        adapter,
        workspace_path=workspace_path,
        task_id=task_id,
        source_tool="deterministic_patch_residue_cleanup",
        executor_factory=DirectorToolExecutor,
        base_files=base_files,
    )


def run_typescript_reexport_repair(
    adapter: Any,
    *,
    task: dict[str, Any],
    task_id: str,
) -> list[dict[str, Any]]:
    return _runtime_repair_tool_results(
        adapter,
        task=task,
        task_id=task_id,
        source_tool="deterministic_typescript_reexport_repair",
        suffixes=_TYPESCRIPT_REPAIR_SUFFIXES,
        artifact_quality_errors=_adapter_artifact_quality_errors(adapter),
        scan_when_unscoped=True,
    )


def run_python_unittest_missing_target_repair(
    adapter: Any,
    *,
    task: dict[str, Any],
    task_id: str,
) -> list[dict[str, Any]]:
    return _runtime_repair_tool_results(
        adapter,
        task=task,
        task_id=task_id,
        source_tool="deterministic_python_unittest_missing_target_repair",
        suffixes=_PYTHON_REPAIR_SUFFIXES,
        artifact_quality_errors=_adapter_artifact_quality_errors(adapter),
        scan_when_unscoped=True,
        use_editor=False,
    )


def run_pre_materialization_declared_target_repairs(
    adapter: Any,
    *,
    task: dict[str, Any],
    task_id: str,
    workspace_name: str,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    workspace_path = _adapter_workspace_path(adapter)
    if workspace_path is None:
        tool_results: list[dict[str, Any]] = []
        return tool_results, _runtime_repair_summary(
            stage="pre_materialization_declared_target_repair",
            tool_results=tool_results,
            artifact_quality_errors=(),
        )
    artifact_quality_errors = _missing_declared_target_errors(
        workspace_path,
        task,
        workspace_name=workspace_name,
    )
    tool_results = _runtime_repair_tool_results(
        adapter,
        task=task,
        task_id=task_id,
        source_tool="deterministic_pre_materialization_declared_target_repair",
        suffixes=_DECLARED_TARGET_REPAIR_SUFFIXES,
        artifact_quality_errors=artifact_quality_errors,
        scan_when_unscoped=True,
        use_editor=False,
    )
    return tool_results, _runtime_repair_summary(
        stage="pre_materialization_declared_target_repair",
        tool_results=tool_results,
        artifact_quality_errors=artifact_quality_errors,
    )


def run_declared_target_contract_repairs(
    adapter: Any,
    *,
    task: dict[str, Any],
    task_id: str,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    workspace_path = _adapter_workspace_path(adapter)
    if workspace_path is None:
        tool_results: list[dict[str, Any]] = []
        return tool_results, _runtime_repair_summary(
            stage="declared_target_contract_repair",
            tool_results=tool_results,
            artifact_quality_errors=(),
        )
    workspace_name = workspace_path.name
    artifact_quality_errors = (
        *_adapter_artifact_quality_errors(adapter),
        *_missing_declared_target_errors(workspace_path, task, workspace_name=workspace_name),
    )
    tool_results = _runtime_repair_tool_results(
        adapter,
        task=task,
        task_id=task_id,
        source_tool="deterministic_declared_target_contract_repair",
        suffixes=_DECLARED_TARGET_REPAIR_SUFFIXES,
        artifact_quality_errors=artifact_quality_errors,
        scan_when_unscoped=True,
        use_editor=False,
    )
    return tool_results, _runtime_repair_summary(
        stage="declared_target_contract_repair",
        tool_results=tool_results,
        artifact_quality_errors=artifact_quality_errors,
    )


def run_python_static_smoke(
    adapter: Any,
    *,
    all_affected_files: list[str],
) -> list[str]:
    workspace_path = Path(str(getattr(adapter, "workspace", "") or "")).resolve()
    if not workspace_path.exists() or not workspace_path.is_dir():
        return []

    errors: list[str] = []
    for rel in all_affected_files:
        if not isinstance(rel, str) or not rel.endswith(".py"):
            continue
        candidate = (workspace_path / rel).resolve()
        try:
            candidate.relative_to(workspace_path)
        except ValueError:
            continue
        if not candidate.is_file():
            continue
        try:
            proc = subprocess.run(
                [sys.executable, "-m", "py_compile", str(candidate)],
                cwd=str(workspace_path),
                capture_output=True,
                text=True,
                timeout=10.0,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            errors.append(
                f"Artifact quality scan failed: python static smoke could not "
                f"check {rel!r}: {type(exc).__name__}: {exc}"
            )
            continue
        if proc.returncode != 0:
            stderr = (proc.stderr or proc.stdout or "").strip()
            tail = "\n".join(line for line in stderr.splitlines()[-6:] if line)
            errors.append(
                f"Artifact quality scan failed: python static smoke found syntax error in {rel!r}; tail:\n{tail}"
            )
    return errors


def run_python_runtime_smoke(
    adapter: Any,
    *,
    task_id: str,
    all_affected_files: list[str],
    timeout_seconds: float | None = None,
) -> list[str]:
    del task_id
    bounded_timeout = _PYTHON_RUNTIME_SMOKE_TIMEOUT_SECONDS if timeout_seconds is None else float(timeout_seconds)
    workspace_path = Path(str(getattr(adapter, "workspace", "") or "")).resolve()
    if not workspace_path.exists() or not workspace_path.is_dir():
        return []

    errors: list[str] = []
    for rel in all_affected_files:
        if not isinstance(rel, str) or not rel.endswith(".py"):
            continue
        candidate = (workspace_path / rel).resolve()
        try:
            candidate.relative_to(workspace_path)
        except ValueError:
            continue
        if not candidate.is_file():
            continue
        try:
            text = candidate.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if not _PYTHON_MAIN_BLOCK_RE.search(text):
            continue
        env = os.environ.copy()
        current_pythonpath = str(env.get("PYTHONPATH") or "").strip()
        env["PYTHONPATH"] = (
            str(workspace_path)
            if not current_pythonpath
            else os.pathsep.join([str(workspace_path), current_pythonpath])
        )
        proc = subprocess.Popen(
            [sys.executable, str(candidate)],
            cwd=str(workspace_path),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
        )
        try:
            stdout, stderr = proc.communicate(timeout=max(0.5, bounded_timeout))
            returncode = proc.returncode
        except subprocess.TimeoutExpired:
            if proc.poll() is None:
                try:
                    proc.kill()
                finally:
                    with contextlib.suppress(OSError):
                        proc.wait(timeout=2.0)
                continue
            stdout = proc.stdout.read() if proc.stdout else ""
            stderr = proc.stderr.read() if proc.stderr else ""
            tail = "\n".join(line for line in (stderr or "").strip().splitlines()[-8:] if line)
            errors.append(
                f"Artifact quality scan failed: python runtime smoke timed out for {rel!r} "
                f"after {bounded_timeout}s; tail:\n{tail}"
            )
            continue
        except (OSError, ValueError) as exc:
            errors.append(
                f"Artifact quality scan failed: python runtime smoke could not launch "
                f"{rel!r}: {type(exc).__name__}: {exc}"
            )
            continue

        if returncode == 0:
            continue
        stderr_tail = (stderr or stdout or "").strip().splitlines()
        tail = "\n".join(line for line in stderr_tail[-8:] if line)
        if returncode < 0:
            errors.append(
                f"Artifact quality scan failed: python runtime smoke was killed for {rel!r} "
                f"(returncode={returncode}, signal={-returncode}); tail:\n{tail}"
            )
        else:
            errors.append(
                f"Artifact quality scan failed: python runtime smoke crashed for {rel!r} "
                f"(returncode={returncode}); tail:\n{tail}"
            )
    errors.extend(
        _run_python_unittest_discover_smoke(
            adapter,
            all_affected_files=all_affected_files,
            timeout_seconds=bounded_timeout,
        )
    )
    return errors


def _run_python_unittest_discover_smoke(
    adapter: Any,
    *,
    all_affected_files: list[str],
    timeout_seconds: float,
) -> list[str]:
    workspace_path = Path(str(getattr(adapter, "workspace", "") or "")).resolve()
    if not workspace_path.exists() or not workspace_path.is_dir():
        return []

    touched_test_files = [
        _normalize_declared_task_path(str(item or ""))
        for item in all_affected_files
        if _looks_like_python_unittest_test_path(str(item or ""))
    ]
    if not touched_test_files:
        return []
    tests_dir = workspace_path / "tests"
    if not tests_dir.is_dir():
        return []
    try:
        has_discoverable_tests = any(path.is_file() for path in tests_dir.rglob("test_*.py"))
    except (OSError, RuntimeError):
        return []
    if not has_discoverable_tests:
        return []

    env = os.environ.copy()
    current_pythonpath = str(env.get("PYTHONPATH") or "").strip()
    env["PYTHONPATH"] = (
        str(workspace_path) if not current_pythonpath else os.pathsep.join([str(workspace_path), current_pythonpath])
    )
    command = [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-p", "test_*.py", "-v"]
    try:
        completed = subprocess.run(
            command,
            cwd=str(workspace_path),
            capture_output=True,
            text=True,
            env=env,
            timeout=max(1.0, float(timeout_seconds)),
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        output = "\n".join(str(part or "").strip() for part in (exc.stdout, exc.stderr) if part)
        tail = "\n".join(line for line in output.splitlines()[-40:] if line)
        return [
            "Artifact quality scan failed: workspace validation command timed out "
            "(python -m unittest discover -s tests -p test_*.py -v); "
            f"touched_tests={touched_test_files[:6]}; tail:\n{tail}"
        ]
    except (OSError, ValueError) as exc:
        return [
            "Artifact quality scan failed: workspace validation command could not launch "
            "(python -m unittest discover -s tests -p test_*.py -v): "
            f"{type(exc).__name__}: {exc}"
        ]

    output = (completed.stderr or completed.stdout or "").strip()
    if completed.returncode == 0 or _unittest_discover_only_found_no_tests(output):
        return []
    tail = "\n".join(line for line in output.splitlines()[-80:] if line)
    return [
        "Artifact quality scan failed: workspace validation command failed "
        "(python -m unittest discover -s tests -p test_*.py -v); "
        f"touched_tests={touched_test_files[:6]}; tail:\n{tail}"
    ]


def _looks_like_python_unittest_test_path(rel_path: str) -> bool:
    normalized = _normalize_declared_task_path(rel_path)
    name = Path(normalized).name
    return normalized.endswith(".py") and (
        name.startswith("test_") or name.endswith("_test.py") or "/tests/" in normalized
    )


def _unittest_discover_only_found_no_tests(output: str) -> bool:
    token = str(output or "").lower()
    return "ran 0 tests" in token and "no tests ran" in token and "traceback" not in token
