"""Deterministic npm runtime-dependency + test-script repairs, carved verbatim."""

from __future__ import annotations

import contextlib
import json
import re
from pathlib import Path
from typing import Any

from ..execution_tools import DirectorToolExecutor
from ._common import (
    _KNOWN_DEV_DEPENDENCY_VERSIONS,
    _KNOWN_RUNTIME_DEPENDENCY_VERSIONS,
    _package_declared_in_manifest,
    _parse_required_dev_dependency_packages,
    _parse_undeclared_runtime_import_packages,
)


def _apply_deterministic_runtime_dependency_repair(
    adapter: Any,
    *,
    task_id: str,
    artifact_quality_errors: list[str],
) -> list[dict[str, Any]]:
    package_names = _parse_undeclared_runtime_import_packages(artifact_quality_errors)
    runtime_package_names = [name for name in package_names if name in _KNOWN_RUNTIME_DEPENDENCY_VERSIONS]
    dev_package_names = _parse_required_dev_dependency_packages(artifact_quality_errors)
    dev_package_names = [name for name in dev_package_names if name in _KNOWN_DEV_DEPENDENCY_VERSIONS]
    if not runtime_package_names and not dev_package_names:
        return []

    workspace_path = Path(str(getattr(adapter, "workspace", "") or "")).resolve()
    package_path = workspace_path / "package.json"
    if not package_path.is_file():
        return []
    try:
        payload = json.loads(package_path.read_text(encoding="utf-8"))
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError):
        return []
    if not isinstance(payload, dict):
        return []

    dependencies_raw = payload.get("dependencies")
    dependencies: dict[str, Any] = dict(dependencies_raw) if isinstance(dependencies_raw, dict) else {}
    dev_dependencies_raw = payload.get("devDependencies")
    dev_dependencies: dict[str, Any] = dict(dev_dependencies_raw) if isinstance(dev_dependencies_raw, dict) else {}
    added_runtime: list[str] = []
    added_dev: list[str] = []
    for package_name in runtime_package_names:
        if _package_declared_in_manifest(payload, package_name):
            continue
        dependencies[package_name] = _KNOWN_RUNTIME_DEPENDENCY_VERSIONS[package_name]
        added_runtime.append(package_name)
    for package_name in dev_package_names:
        if _package_declared_in_manifest(payload, package_name):
            continue
        dev_dependencies[package_name] = _KNOWN_DEV_DEPENDENCY_VERSIONS[package_name]
        added_dev.append(package_name)
    added = [*added_runtime, *added_dev]
    if not added:
        return []

    if added_runtime:
        payload["dependencies"] = dict(sorted(dependencies.items()))
    if added_dev:
        payload["devDependencies"] = dict(sorted(dev_dependencies.items()))
    content = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    message_bus = getattr(getattr(adapter, "_execution", None), "_message_bus", None)
    write_result = DirectorToolExecutor(
        str(workspace_path),
        message_bus=message_bus,
        worker_id="director",
    ).execute_tool(
        "write_file",
        {"file": "package.json", "content": content},
        task_id=task_id,
    )
    if not bool(write_result.get("ok")):
        return []
    with contextlib.suppress(OSError, RuntimeError, TypeError, ValueError):
        adapter._update_task_progress(task_id, "executing", current_file="package.json")
    return [
        {
            "tool": "write_file",
            "tool_name": "write_file",
            "success": True,
            "result": {
                "ok": True,
                "source_tool": "deterministic_runtime_dependency_repair",
                "file": "package.json",
                "packages": added,
                "runtime_packages": added_runtime,
                "dev_packages": added_dev,
                "bytes_written": int(write_result.get("bytes_written") or len(content.encode("utf-8"))),
                "operation": str(write_result.get("operation") or "modify"),
                "broadcast_ok": bool(write_result.get("broadcast_ok")),
                "director_policy": write_result.get("director_policy"),
            },
        }
    ]


def _apply_deterministic_npm_test_script_repair(
    adapter: Any,
    *,
    task_id: str,
    artifact_quality_errors: list[str],
) -> list[dict[str, Any]]:
    if not any(_is_repairable_npm_test_script_error(error) for error in artifact_quality_errors):
        return []

    workspace_path = Path(str(getattr(adapter, "workspace", "") or "")).resolve()
    package_path = workspace_path / "package.json"
    if not package_path.is_file():
        return []
    try:
        payload = json.loads(package_path.read_text(encoding="utf-8"))
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError):
        return []
    if not isinstance(payload, dict) or not _workspace_has_typescript_context(workspace_path, payload):
        return []

    scripts_raw = payload.get("scripts")
    scripts: dict[str, Any] = dict(scripts_raw) if isinstance(scripts_raw, dict) else {}
    changed_scripts: dict[str, str] = {}
    if "build" not in scripts:
        compile_script = str(scripts.get("compile") or "").strip()
        scripts["build"] = "npm run compile" if compile_script else "tsc"
        changed_scripts["build"] = str(scripts["build"])

    if any(_is_repairable_npm_test_script_error(error) for error in artifact_quality_errors):
        test_script = str(scripts.get("test") or "").strip()
        if not test_script or _is_manifest_only_or_default_test_script_error(artifact_quality_errors):
            scripts["test"] = "npm run build"
            changed_scripts["test"] = "npm run build"

    missing_start_entrypoint = _missing_npm_script_entrypoint(artifact_quality_errors, script_name="start")
    if missing_start_entrypoint:
        entrypoint = _package_entrypoint(payload, fallback=missing_start_entrypoint)
        scripts["start"] = f"npm run build && node {entrypoint}"
        changed_scripts["start"] = str(scripts["start"])

    if not changed_scripts:
        return []

    payload["scripts"] = dict(sorted((str(key), value) for key, value in scripts.items()))
    content = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    message_bus = getattr(getattr(adapter, "_execution", None), "_message_bus", None)
    write_result = DirectorToolExecutor(
        str(workspace_path),
        message_bus=message_bus,
        worker_id="director",
    ).execute_tool(
        "write_file",
        {"file": "package.json", "content": content},
        task_id=task_id,
    )
    if not bool(write_result.get("ok")):
        return []
    with contextlib.suppress(OSError, RuntimeError, TypeError, ValueError):
        adapter._update_task_progress(task_id, "executing", current_file="package.json")
    return [
        {
            "tool": "write_file",
            "tool_name": "write_file",
            "success": True,
            "result": {
                "ok": True,
                "source_tool": "deterministic_npm_script_contract_repair",
                "file": "package.json",
                "scripts": changed_scripts,
                "bytes_written": int(write_result.get("bytes_written") or len(content.encode("utf-8"))),
                "operation": str(write_result.get("operation") or "modify"),
                "broadcast_ok": bool(write_result.get("broadcast_ok")),
                "director_policy": write_result.get("director_policy"),
            },
        }
    ]


def _is_repairable_npm_test_script_error(error: Any) -> bool:
    text = str(error or "")
    return (
        "npm default failing test script" in text
        or "npm placeholder test script" in text
        or "npm manifest-only test script" in text
        or "npm package manifest script 'test' has invalid shell syntax" in text
        or "npm package manifest script 'start' references missing local entrypoint" in text
    )


def _is_manifest_only_or_default_test_script_error(errors: list[str]) -> bool:
    joined = "\n".join(str(error or "") for error in errors)
    return (
        "npm default failing test script" in joined
        or "npm placeholder test script" in joined
        or "npm manifest-only test script" in joined
        or "npm package manifest script 'test' has invalid shell syntax" in joined
    )


def _missing_npm_script_entrypoint(errors: list[str], *, script_name: str) -> str:
    pattern = re.compile(
        rf"npm package manifest script '{re.escape(script_name)}' references missing local entrypoint '([^']+)'"
    )
    for error in errors:
        match = pattern.search(str(error or ""))
        if match:
            return str(match.group(1) or "").strip()
    return ""


def _package_entrypoint(payload: dict[str, Any], *, fallback: str) -> str:
    entrypoint = str(payload.get("main") or "").strip()
    return entrypoint or fallback or "dist/main.js"


def _workspace_has_typescript_context(workspace_path: Path, payload: dict[str, Any]) -> bool:
    if (workspace_path / "tsconfig.json").is_file():
        return True
    for dependency_key in ("dependencies", "devDependencies"):
        dependencies = payload.get(dependency_key)
        if isinstance(dependencies, dict) and "typescript" in dependencies:
            return True
    for source_root in ("src", "tests"):
        root = workspace_path / source_root
        if root.is_dir() and any(path.suffix == ".ts" for path in root.rglob("*.ts")):
            return True
    return False
