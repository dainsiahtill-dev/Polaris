"""Workflow compatibility client backed by self-hosted workflow runtime."""

from __future__ import annotations

import asyncio
import contextlib
import time
from dataclasses import dataclass, field
from typing import Any

from polaris.cells.orchestration.workflow_runtime.internal.config import WorkflowConfig
from polaris.cells.orchestration.workflow_runtime.internal.embedded_api import (
    get_activity_api as _get_local_activity_api,
    get_workflow_api as _get_local_workflow_api,
)
from polaris.cells.orchestration.workflow_runtime.internal.models import PMWorkflowInput
from polaris.cells.orchestration.workflow_runtime.internal.runtime_backend_adapter import (
    describe_workflow_sync as _describe_workflow_sync,
    get_adapter,
    query_workflow_sync as _query_workflow_sync,
    start_adapter as _start_adapter_async,
    stop_adapter as _stop_adapter_async,
)

__all__ = [
    "WorkflowSubmissionResult",
    "WorkflowUnavailableError",
    "cancel_workflow_sync",
    "describe_workflow_sync",
    "get_activity_api",
    "get_workflow_api",
    "query_workflow_sync",
    "start_adapter",
    "stop_adapter",
    "submit_pm_workflow_sync",
    "wait_for_workflow_completion_sync",
]


class WorkflowUnavailableError(RuntimeError):
    """Raised when runtime APIs are not available in the current environment."""


@dataclass(frozen=True)
class WorkflowSubmissionResult:
    """Result for scheduling a workflow."""

    submitted: bool
    status: str
    workflow_id: str = ""
    workflow_run_id: str = ""
    error: str = ""
    details: dict[str, Any] = field(default_factory=dict)


def _run_sync(coro: Any) -> Any:
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    if loop.is_running():
        raise RuntimeError("Sync workflow client API cannot run inside an active event loop.")
    return loop.run_until_complete(coro)


def _normalize_workflow_input(value: PMWorkflowInput | dict[str, Any]) -> PMWorkflowInput:
    if isinstance(value, PMWorkflowInput):
        return value
    if not isinstance(value, dict):
        raise TypeError("workflow_input must be PMWorkflowInput or dict")
    return PMWorkflowInput(
        workspace=str(value.get("workspace") or "").strip(),
        run_id=str(value.get("run_id") or "").strip(),
        precomputed_payload=(
            value.get("precomputed_payload")  # type: ignore[arg-type]
            if isinstance(value.get("precomputed_payload"), dict)
            else {}
        ),
        metadata=value.get("metadata") if isinstance(value.get("metadata"), dict) else {},  # type: ignore[arg-type]
    )


def get_workflow_api() -> Any:
    """Return workflow API."""
    return _get_local_workflow_api()


def get_activity_api() -> Any:
    """Return activity API."""
    return _get_local_activity_api()


async def _wait_for_adapter_workflow_completion(
    adapter: Any,
    workflow_id: str,
    *,
    timeout_seconds: float | None,
    poll_interval_seconds: float,
) -> dict[str, Any]:
    deadline = None
    if timeout_seconds is not None:
        try:
            timeout_value = float(timeout_seconds)
        except (RuntimeError, ValueError):
            timeout_value = 0.0
        if timeout_value > 0:
            deadline = time.monotonic() + timeout_value

    interval = max(0.2, float(poll_interval_seconds or 1.0))
    terminal = {"completed", "failed", "cancelled", "canceled", "terminated", "timed_out"}
    normalized_id = str(workflow_id or "").strip()

    while True:
        snapshot = await adapter.describe_workflow(normalized_id)
        payload = snapshot if isinstance(snapshot, dict) else {}
        status = str(payload.get("status") or "").strip().lower()
        if status in terminal:
            return {
                "ok": status == "completed",
                "workflow_id": str(payload.get("workflow_id") or normalized_id).strip(),
                "status": status,
                "result": payload.get("result") if isinstance(payload.get("result"), dict) else {},
                "error": str((payload.get("result") or {}).get("error") or payload.get("error") or "").strip()
                if isinstance(payload.get("result"), dict)
                else str(payload.get("error") or "").strip(),
                "details": payload,
            }
        if deadline is not None and time.monotonic() >= deadline:
            with contextlib.suppress(RuntimeError, ValueError):
                await adapter.cancel_workflow(normalized_id, reason="workflow_wait_timeout")
            return {
                "ok": False,
                "workflow_id": normalized_id,
                "status": "timed_out",
                "error": "workflow_wait_timeout",
            }
        await asyncio.sleep(interval)


async def _submit_pm_workflow_async(
    workflow_input: PMWorkflowInput,
    *,
    wait_until_complete: bool = False,
    timeout_seconds: float | None = None,
    poll_interval_seconds: float = 1.0,
) -> WorkflowSubmissionResult:
    adapter = await get_adapter()
    if not adapter._running:
        await adapter.start()
    payload = {
        "workspace": workflow_input.workspace,
        "run_id": workflow_input.run_id,
        "precomputed_payload": (
            workflow_input.precomputed_payload if isinstance(workflow_input.precomputed_payload, dict) else {}
        ),
        "metadata": (workflow_input.metadata if isinstance(workflow_input.metadata, dict) else {}),
    }
    result = await adapter.submit_workflow(
        workflow_name="pm_workflow",
        workflow_id=workflow_input.workflow_id,
        payload=payload,
    )
    status = result.status
    error = str(result.error or "").strip()
    details = result.result if isinstance(result.result, dict) else {}
    if wait_until_complete and str(status or "").strip().lower() in {"started", "running"} and not error:
        wait_payload = await _wait_for_adapter_workflow_completion(
            adapter,
            result.workflow_id,
            timeout_seconds=timeout_seconds,
            poll_interval_seconds=poll_interval_seconds,
        )
        status = str(wait_payload.get("status") or status or "").strip()
        error = str(wait_payload.get("error") or "").strip()
        final_result = wait_payload.get("result") if isinstance(wait_payload.get("result"), dict) else {}
        details = {
            "submission": details,
            "final": wait_payload,
            "result": final_result,
        }
    return WorkflowSubmissionResult(
        submitted=result.status in {"started", "running", "completed"},
        status=status,
        workflow_id=result.workflow_id,
        workflow_run_id=result.run_id,
        error=error,
        details=details,
    )


def submit_pm_workflow_sync(
    workflow_input: PMWorkflowInput | dict[str, Any],
    config: WorkflowConfig | None = None,
    *,
    wait_until_complete: bool = False,
    timeout_seconds: float | None = None,
    poll_interval_seconds: float = 1.0,
) -> WorkflowSubmissionResult:
    """Submit PM workflow through self-hosted runtime with Workflow-compatible result."""
    runtime_config = config or WorkflowConfig.from_env()
    if not bool(runtime_config.enabled):
        return WorkflowSubmissionResult(
            submitted=False,
            status="disabled",
            error="workflow_runtime_disabled",
        )
    try:
        normalized = _normalize_workflow_input(workflow_input)
    except (RuntimeError, ValueError) as exc:
        return WorkflowSubmissionResult(
            submitted=False,
            status="invalid_request",
            error=str(exc),
        )
    if not normalized.workspace or not normalized.run_id:
        return WorkflowSubmissionResult(
            submitted=False,
            status="invalid_request",
            error="workspace and run_id are required",
        )
    try:
        return _run_sync(
            _submit_pm_workflow_async(
                normalized,
                wait_until_complete=wait_until_complete,
                timeout_seconds=timeout_seconds,
                poll_interval_seconds=poll_interval_seconds,
            )
        )
    except (RuntimeError, ValueError) as exc:
        return WorkflowSubmissionResult(
            submitted=False,
            status="failed",
            workflow_id=normalized.workflow_id,
            error=str(exc),
        )


def wait_for_workflow_completion_sync(
    workflow_id: str,
    *,
    timeout_seconds: float | None = None,
    poll_interval_seconds: float = 1.0,
    config: WorkflowConfig | None = None,
) -> dict[str, Any]:
    """Wait for workflow to reach a terminal status.

    Returns the final describe payload (or a timeout/error payload).
    """
    normalized_id = str(workflow_id or "").strip()
    if not normalized_id:
        return {
            "ok": False,
            "workflow_id": normalized_id,
            "error": "workflow_id_required",
        }

    runtime_config = config or WorkflowConfig.from_env()
    if not bool(runtime_config.enabled):
        return {
            "ok": False,
            "workflow_id": normalized_id,
            "error": "workflow_runtime_disabled",
        }

    deadline = None
    if timeout_seconds is not None:
        try:
            timeout_value = float(timeout_seconds)
        except (RuntimeError, ValueError):
            timeout_value = 0.0
        if timeout_value > 0:
            deadline = time.monotonic() + timeout_value

    interval = max(0.2, float(poll_interval_seconds or 1.0))
    terminal = {"completed", "failed", "cancelled", "canceled", "terminated", "timed_out"}

    while True:
        snapshot = describe_workflow_sync(normalized_id, config=runtime_config)
        if not isinstance(snapshot, dict):
            return {
                "ok": False,
                "workflow_id": normalized_id,
                "error": "describe_workflow_failed",
            }
        if not bool(snapshot.get("ok", True)):
            return snapshot
        status = str(snapshot.get("status") or "").strip().lower()
        if status in terminal:
            return snapshot
        if deadline is not None and time.monotonic() >= deadline:
            return {
                "ok": False,
                "workflow_id": normalized_id,
                "status": status or "running",
                "error": "workflow_wait_timeout",
            }
        time.sleep(interval)


def describe_workflow_sync(
    workflow_id: str,
    config: WorkflowConfig | None = None,
) -> dict[str, Any]:
    """Describe workflow with compatibility payload."""
    runtime_config = config or WorkflowConfig.from_env()
    if not bool(runtime_config.enabled):
        return {
            "ok": False,
            "workflow_id": str(workflow_id or "").strip(),
            "error": "workflow_runtime_disabled",
        }
    return _describe_workflow_sync(workflow_id, config=runtime_config)


def query_workflow_sync(
    workflow_id: str,
    query_name: str,
    *args: Any,
    config: WorkflowConfig | None = None,
) -> dict[str, Any]:
    """Query workflow with compatibility payload."""
    runtime_config = config or WorkflowConfig.from_env()
    if not bool(runtime_config.enabled):
        return {
            "ok": False,
            "workflow_id": str(workflow_id or "").strip(),
            "query_name": str(query_name or "").strip(),
            "error": "workflow_runtime_disabled",
        }
    return _query_workflow_sync(workflow_id, query_name, *args, config=runtime_config)


async def _cancel_workflow_async(
    workflow_id: str,
    reason: str = "",
) -> dict[str, Any]:
    adapter = await get_adapter()
    if not adapter._running:
        await adapter.start()
    return await adapter.cancel_workflow(workflow_id, reason)


def cancel_workflow_sync(
    workflow_id: str,
    *,
    reason: str = "",
    config: WorkflowConfig | None = None,
) -> dict[str, Any]:
    """Cancel a workflow with a compatibility payload."""
    normalized_id = str(workflow_id or "").strip()
    runtime_config = config or WorkflowConfig.from_env()
    if not normalized_id:
        return {
            "ok": False,
            "workflow_id": normalized_id,
            "error": "workflow_id_required",
        }
    if not bool(runtime_config.enabled):
        return {
            "ok": False,
            "workflow_id": normalized_id,
            "error": "workflow_runtime_disabled",
        }
    try:
        payload = _run_sync(_cancel_workflow_async(normalized_id, str(reason or "").strip()))
    except (RuntimeError, ValueError) as exc:
        return {
            "ok": False,
            "workflow_id": normalized_id,
            "error": str(exc),
        }
    if not isinstance(payload, dict):
        return {
            "ok": False,
            "workflow_id": normalized_id,
            "error": "cancel_workflow_failed",
        }
    if str(payload.get("error") or "").strip():
        return {
            "ok": False,
            "workflow_id": normalized_id,
            "error": str(payload.get("error")).strip(),
        }
    return {
        "ok": bool(payload.get("cancelled")),
        "workflow_id": str(payload.get("workflow_id") or normalized_id).strip(),
        "cancelled": bool(payload.get("cancelled")),
        "reason": str(reason or "").strip(),
    }


def start_adapter() -> None:
    """Start global adapter for compatibility workflows."""
    _run_sync(_start_adapter_async())


def stop_adapter() -> None:
    """Stop global adapter for compatibility workflows."""
    _run_sync(_stop_adapter_async())
