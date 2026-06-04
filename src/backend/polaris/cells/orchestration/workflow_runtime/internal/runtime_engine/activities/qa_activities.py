"""QA-related Workflow activities."""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

from polaris.cells.orchestration.pm_planning.public.service import (
    detect_integration_verify_command,
    run_integration_verify_runner,
)
from polaris.cells.orchestration.workflow_runtime.internal.workflow_client import get_activity_api
from polaris.kernelone.fs.text_ops import write_json_atomic
from polaris.kernelone.process.command_executor import CommandExecutionService
from polaris.kernelone.storage.io_paths import build_cache_root, resolve_artifact_path

from .base import ActivityExecutionResult, register_activity

activity = get_activity_api()


def _normalize_metadata(payload: dict[str, Any]) -> dict[str, Any]:
    metadata = (payload or {}).get("metadata")
    return metadata if isinstance(metadata, dict) else {}


def _flag_enabled(*sources: dict[str, Any], key: str) -> bool:
    for source in sources:
        value = source.get(key)
        if isinstance(value, bool):
            return value
        token = str(value or "").strip().lower()
        if token in {"1", "true", "yes", "on"}:
            return True
        if token in {"0", "false", "no", "off"}:
            return False
    return False


def _context_snapshot_evidence(snapshot: Any) -> dict[str, Any]:
    """Extract compact, stable Context OS evidence from a resolved snapshot."""
    if snapshot is None:
        return {}
    context_os_summary = getattr(snapshot, "context_os_summary", {})
    source_refs = getattr(snapshot, "source_refs", ())
    return {
        "workspace": str(getattr(snapshot, "workspace", "") or "").strip(),
        "role": str(getattr(snapshot, "role", "") or "").strip(),
        "run_id": str(getattr(snapshot, "run_id", "") or "").strip(),
        "session_id": str(getattr(snapshot, "session_id", "") or "").strip(),
        "mode": str(getattr(snapshot, "mode", "") or "").strip(),
        "token_usage_estimate": int(getattr(snapshot, "token_usage_estimate", 0) or 0),
        "source_refs": [str(item).strip() for item in source_refs if str(item).strip()],
        "context_os_summary": dict(context_os_summary) if isinstance(context_os_summary, dict) else {},
    }


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
        # Parse command and execute directly
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
        return detect_integration_verify_command(workspace)
    if os.path.isfile(os.path.join(workspace, "go.mod")):
        return "go test ./... -run TestDoesNotExist"
    if os.path.isfile(os.path.join(workspace, "Cargo.toml")):
        return "cargo test --no-run"
    return "python -m pytest --collect-only -q"


@register_activity("record_qa_cognitive_receipt")
@activity.defn(name="record_qa_cognitive_receipt")
async def record_qa_cognitive_receipt(payload: dict[str, Any]) -> dict[str, Any]:
    """Record Cognitive Runtime evidence for the final QA gate."""

    run_id = str((payload or {}).get("run_id") or "").strip()
    workspace = str((payload or {}).get("workspace") or "").strip()
    metadata = _normalize_metadata(payload)
    status = str((payload or {}).get("status") or "").strip() or "unknown"
    reason = str((payload or {}).get("reason") or "").strip()
    summary = str((payload or {}).get("summary") or "").strip()
    unit_payload = (payload or {}).get("unit")
    integration_payload = (payload or {}).get("integration")
    evidence_refs = [str(item).strip() for item in (payload or {}).get("evidence_refs") or () if str(item).strip()]
    errors = [str(item).strip() for item in (payload or {}).get("errors") or () if str(item).strip()]
    if not workspace:
        return ActivityExecutionResult(
            success=False,
            summary="QA Cognitive Runtime receipt requires workspace",
            payload={"run_id": run_id},
            errors=["missing_workspace"],
            error_code="qa_cognitive_runtime_missing_workspace",
        ).to_dict()

    required = _flag_enabled(metadata, key="cognitive_runtime_required")
    context_os_expected = _flag_enabled(metadata, key="context_os_expected")
    evidence: dict[str, Any] = {
        "ok": False,
        "required": required,
        "receipt_type": "qa_verification",
        "status": status,
        "context_os_expected": context_os_expected,
    }
    context_os_evidence: dict[str, Any] = {
        "ok": False,
        "required": context_os_expected,
        "skipped": not context_os_expected,
        "reason": "" if context_os_expected else "context_os_not_expected",
    }
    try:
        from polaris.cells.factory.cognitive_runtime.public import (
            RecordRuntimeReceiptCommandV1,
            ResolveContextCommandV1,
            get_cognitive_runtime_public_service,
        )

        session_id = (
            str(
                metadata.get("qa_session_id")
                or metadata.get("role_session_id")
                or metadata.get("session_id")
                or f"qa-{run_id or 'adhoc'}"
            ).strip()
            or None
        )
        service = get_cognitive_runtime_public_service()
        try:
            if context_os_expected:
                context_result = service.resolve_context(
                    ResolveContextCommandV1(
                        workspace=workspace,
                        role="qa",
                        query=summary or reason or "workflow runtime final QA verification",
                        step=0,
                        run_id=run_id or "qa-verification",
                        mode="workflow_runtime_qa_verification",
                        session_id=session_id,
                        sources_enabled=("runtime", "events", "contracts"),
                        policy={
                            "source": "workflow_runtime.qa_activities.record_qa_cognitive_receipt",
                            "context_os_required": True,
                            "status": status,
                            "reason": reason,
                        },
                    )
                )
                if not bool(getattr(context_result, "ok", False)):
                    context_os_evidence["error_message"] = (
                        str(getattr(context_result, "error_message", "") or "").strip()
                        or str(getattr(context_result, "error_code", "") or "").strip()
                        or "context_os_resolve_failed"
                    )
                    evidence["context_os"] = context_os_evidence
                    if required:
                        return ActivityExecutionResult(
                            success=False,
                            summary=f"QA Context OS resolve failed: {context_os_evidence['error_message']}",
                            payload={"cognitive_runtime_receipt": evidence},
                            errors=[context_os_evidence["error_message"]],
                            error_code="qa_context_os_resolve_failed",
                        ).to_dict()
                else:
                    context_os_evidence = {
                        "ok": True,
                        "required": True,
                        "skipped": False,
                        "snapshot": _context_snapshot_evidence(getattr(context_result, "snapshot", None)),
                    }
            evidence["context_os"] = context_os_evidence
            result = service.record_runtime_receipt(
                RecordRuntimeReceiptCommandV1(
                    workspace=workspace,
                    receipt_type="qa_verification",
                    session_id=session_id,
                    run_id=run_id or None,
                    trace_refs=tuple(evidence_refs),
                    payload={
                        "source": "workflow_runtime.qa_activities.record_qa_cognitive_receipt",
                        "role": "qa",
                        "status": status,
                        "reason": reason,
                        "summary": summary,
                        "unit": unit_payload if isinstance(unit_payload, dict) else {},
                        "integration": integration_payload if isinstance(integration_payload, dict) else {},
                        "errors": errors,
                        "evidence_refs": evidence_refs,
                        "context_os_expected": context_os_expected,
                        "context_os": context_os_evidence,
                    },
                    turn_envelope={
                        "role": "qa",
                        "session_id": session_id,
                        "run_id": run_id,
                        "task_id": "qa::verification",
                    },
                )
            )
        finally:
            service.close()
    except (RuntimeError, ValueError, ImportError) as exc:
        evidence["error_message"] = str(exc)
        if required:
            return ActivityExecutionResult(
                success=False,
                summary=f"QA Cognitive Runtime receipt failed: {exc}",
                payload={"cognitive_runtime_receipt": evidence},
                errors=[str(exc)],
                error_code="qa_cognitive_runtime_receipt_failed",
            ).to_dict()
        return ActivityExecutionResult(
            success=True,
            summary="QA Cognitive Runtime receipt skipped after optional failure",
            payload={"cognitive_runtime_receipt": evidence},
            errors=[str(exc)],
        ).to_dict()

    if not bool(getattr(result, "ok", False)):
        error_message = str(getattr(result, "error_message", "") or "").strip()
        error_code = str(getattr(result, "error_code", "") or "").strip()
        evidence["error_message"] = error_message or error_code
        if required:
            return ActivityExecutionResult(
                success=False,
                summary=evidence["error_message"] or "QA Cognitive Runtime receipt failed",
                payload={"cognitive_runtime_receipt": evidence},
                errors=[evidence["error_message"] or "qa_cognitive_runtime_receipt_failed"],
                error_code="qa_cognitive_runtime_receipt_failed",
            ).to_dict()
        return ActivityExecutionResult(
            success=True,
            summary="QA Cognitive Runtime receipt returned non-ok in optional mode",
            payload={"cognitive_runtime_receipt": evidence},
            errors=[evidence["error_message"] or "qa_cognitive_runtime_receipt_failed"],
        ).to_dict()

    receipt = getattr(result, "receipt", None)
    receipt_id = str(getattr(receipt, "receipt_id", "") or "").strip()
    if required and not receipt_id:
        return ActivityExecutionResult(
            success=False,
            summary="QA Cognitive Runtime receipt did not return a receipt_id",
            payload={"cognitive_runtime_receipt": evidence},
            errors=["qa_cognitive_runtime_receipt_missing_id"],
            error_code="qa_cognitive_runtime_receipt_missing_id",
        ).to_dict()
    evidence["ok"] = True
    if receipt_id:
        evidence["receipt_id"] = receipt_id
    return ActivityExecutionResult(
        success=True,
        summary="QA Cognitive Runtime receipt recorded",
        payload={"cognitive_runtime_receipt": evidence},
    ).to_dict()


@register_activity("run_integration_qa")
@activity.defn(name="run_integration_qa")
async def run_integration_qa(payload: dict[str, Any]) -> dict[str, Any]:
    """Run the existing integration QA verifier used by the PM runtime."""
    run_id = str((payload or {}).get("run_id") or "").strip()
    workspace = str((payload or {}).get("workspace") or "").strip()
    metadata = _normalize_metadata(payload)
    # execution_mode is threaded through workflow_input.metadata from pm_workflow
    execution_mode = str(metadata.get("execution_mode") or "workflow").lower()
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
        # 保留异常类型信息，便于调试
        error_type = type(exc).__name__
        return ActivityExecutionResult(
            success=False,
            summary=f"Integration QA runtime error: {exc}",
            payload={"run_id": run_id},
            errors=[str(exc)],
            error_code=error_type,  # 传递异常类型作为错误码
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
        "execution_mode": execution_mode,
        "qa_path": "qa_workflow",
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
    # execution_mode is threaded through workflow_input.metadata from pm_workflow
    execution_mode = str(metadata.get("execution_mode") or "workflow").lower()
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
        "execution_mode": execution_mode,
        "qa_path": "qa_workflow",
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
