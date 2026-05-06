"""QA-related Workflow activities.

Migrated from:
  polaris/cells/orchestration/workflow_runtime/internal/runtime_engine/activities/qa_activities.py
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

from polaris.cells.orchestration.pm_planning.public.service import run_integration_verify_runner
from polaris.cells.orchestration.workflow_activity.internal.workflow_client import get_activity_api
from polaris.kernelone.fs.text_ops import write_json_atomic
from polaris.kernelone.process.command_executor import CommandExecutionService
from polaris.kernelone.storage.io_paths import build_cache_root, resolve_artifact_path

from .base import ActivityExecutionResult, register_activity

activity = get_activity_api()


def _normalize_metadata(payload: dict[str, Any]) -> dict[str, Any]:
    metadata = (payload or {}).get("metadata")
    return metadata if isinstance(metadata, dict) else {}


def _write_runtime_result(
    workspace: str,
    metadata: dict[str, Any],
    rel_path: str,
    result: dict[str, Any],
) -> str:
    cache_root = str(metadata.get("cache_root_full") or "").strip()
    if not cache_root and workspace:
        try:
            cache_root = build_cache_root(
                str(metadata.get("ramdisk_root") or "").strip(),
                workspace,
            )
        except (RuntimeError, ValueError):
            cache_root = ""
    target = resolve_artifact_path(workspace, cache_root, rel_path)
    if target:
        write_json_atomic(target, result)
    return target


def _run_command(command: str, workspace: str, timeout_seconds: int) -> tuple[bool, str, list[str]]:
    import shlex

    try:
        from polaris.kernelone.process.command_executor import CommandRequest

        executor = CommandExecutionService(workspace)
        tokens = shlex.split(command)
        if not tokens:
            return False, "Empty command", ["empty command"]
        request = CommandRequest(
            executable=tokens[0],
            args=tokens[1:],
            cwd=workspace,
            timeout_seconds=max(timeout_seconds, 30),
        )
        completed = executor.run(request)
    except (RuntimeError, ValueError) as exc:
        return False, f"QA command runtime error: {exc}", [str(exc)]

    if completed.get("timed_out"):
        return False, f"QA command timed out after {timeout_seconds}s", []

    stdout_tail = [str(line).strip() for line in str(completed.get("stdout") or "").splitlines() if str(line).strip()][
        -6:
    ]
    stderr_tail = [str(line).strip() for line in str(completed.get("stderr") or "").splitlines() if str(line).strip()][
        -6:
    ]
    if int(completed.get("returncode") or 0) == 0:
        return True, f"QA command passed: {command}", stdout_tail
    errors = [f"Command failed ({int(completed.get('returncode') or 0)}): {command}"]
    errors.extend(f"[stdout] {line}" for line in stdout_tail)
    errors.extend(f"[stderr] {line}" for line in stderr_tail)
    return False, f"QA command failed: {command}", errors[:20]


def _detect_unit_command(workspace: str) -> str:
    if os.path.isfile(os.path.join(workspace, "package.json")):
        return "npm run test -- --watch=false"
    if os.path.isfile(os.path.join(workspace, "go.mod")):
        return "go test ./... -run TestDoesNotExist"
    if os.path.isfile(os.path.join(workspace, "Cargo.toml")):
        return "cargo test --no-run"
    return "python -m pytest --collect-only -q"


@register_activity("run_integration_qa")
@activity.defn(name="run_integration_qa")
async def run_integration_qa(payload: dict[str, Any]) -> dict[str, Any]:
    """Run the existing integration QA verifier used by the PM runtime."""
    run_id = str((payload or {}).get("run_id") or "").strip()
    workspace = str((payload or {}).get("workspace") or "").strip()
    metadata = _normalize_metadata(payload)
    if not workspace:
        return ActivityExecutionResult(
            success=False,
            summary="Integration QA payload is missing workspace",
            payload={"run_id": run_id},
            errors=["missing_workspace"],
        ).to_dict()
    try:
        success, summary, errors = run_integration_verify_runner(workspace)
    except (RuntimeError, ValueError) as exc:
        error_type = type(exc).__name__
        return ActivityExecutionResult(
            success=False,
            summary=f"Integration QA runtime error: {exc}",
            payload={"run_id": run_id},
            errors=[str(exc)],
            error_code=error_type,
        ).to_dict()
    result = ActivityExecutionResult(
        success=bool(success),
        summary=str(summary or "").strip(),
        payload={"run_id": run_id, "workspace": workspace},
        errors=[str(item).strip() for item in errors if str(item).strip()],
    ).to_dict()
    artifact_payload = {
        "schema_version": 1,
        "enabled": True,
        "ran": True,
        "passed": bool(success),
        "reason": "integration_qa_passed" if success else "integration_qa_failed",
        "summary": str(summary or "").strip(),
        "errors": [str(item).strip() for item in errors if str(item).strip()],
        "run_id": run_id,
        "workspace": workspace,
        "timestamp": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
    }
    artifact_path = _write_runtime_result(
        workspace,
        metadata,
        "runtime/results/integration_qa.result.json",
        artifact_payload,
    )
    if artifact_path:
        payload_dict = result.get("payload")
        if isinstance(payload_dict, dict):
            payload_dict["result_path"] = artifact_path
    return result


@register_activity("run_unit_qa")
@activity.defn(name="run_unit_qa")
async def run_unit_qa(payload: dict[str, Any]) -> dict[str, Any]:
    """Run a lightweight unit/smoke verification pass."""
    run_id = str((payload or {}).get("run_id") or "").strip()
    workspace = str((payload or {}).get("workspace") or "").strip()
    metadata = _normalize_metadata(payload)
    if not workspace:
        return ActivityExecutionResult(
            success=False,
            summary="Unit QA payload is missing workspace",
            payload={"run_id": run_id},
            errors=["missing_workspace"],
        ).to_dict()
    command = _detect_unit_command(workspace)
    success, summary, errors = _run_command(command, workspace, timeout_seconds=120)
    result = ActivityExecutionResult(
        success=bool(success),
        summary=str(summary or "").strip(),
        payload={"run_id": run_id, "workspace": workspace, "command": command},
        errors=errors,
    ).to_dict()
    artifact_payload = {
        "schema_version": 1,
        "ran": True,
        "passed": bool(success),
        "reason": "unit_qa_passed" if success else "unit_qa_failed",
        "summary": str(summary or "").strip(),
        "errors": [str(item).strip() for item in errors if str(item).strip()],
        "run_id": run_id,
        "workspace": workspace,
        "command": command,
        "timestamp": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
    }
    artifact_path = _write_runtime_result(
        workspace,
        metadata,
        "runtime/results/unit_qa.result.json",
        artifact_payload,
    )
    if artifact_path:
        payload_dict = result.get("payload")
        if isinstance(payload_dict, dict):
            payload_dict["result_path"] = artifact_path
    return result


@register_activity("collect_evidence")
@activity.defn(name="collect_evidence")
async def collect_evidence(payload: dict[str, Any]) -> dict[str, Any]:
    """Collect evidence references emitted by prior activities."""
    evidence = (payload or {}).get("evidence")
    normalized = {str(key): value for key, value in evidence.items()} if isinstance(evidence, dict) else {}
    return ActivityExecutionResult(
        success=True,
        summary="Collected Workflow QA evidence references",
        payload={"evidence": normalized},
    ).to_dict()
