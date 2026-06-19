"""Engine/Workflow dispatch result builders.

Pure payload/result builders extracted verbatim from
``dispatch_pipeline.py``. The orchestration entry points
(``run_engine_dispatch`` / ``run_dispatch_pipeline``) and the
workflow-submit singletons stay canonical for test monkeypatchability."""

from __future__ import annotations

from typing import Any


def _nop_update_role_status(role: str, *, status: str, running: bool, detail: str) -> None:
    """No-op fallback when no callback is provided."""
    pass


def _chief_engineer_preflight_block_reason(result: Any) -> str:
    """Return a non-empty reason when CE preflight must block dispatch."""
    if result is None:
        return "chief_engineer_preflight_missing"
    if not isinstance(result, dict):
        return "chief_engineer_preflight_invalid_result"

    explicit_reason = str(result.get("reason") or "").strip()
    if bool(result.get("hard_failure")):
        return explicit_reason or "chief_engineer_preflight_hard_failure"

    status = str(result.get("status") or "").strip().lower()
    if status in {"failed", "failure", "error", "blocked"}:
        return explicit_reason or f"chief_engineer_preflight_{status}"

    if result.get("ok") is False:
        return explicit_reason or "chief_engineer_preflight_not_ok"
    if result.get("success") is False:
        return explicit_reason or "chief_engineer_preflight_unsuccessful"
    if result.get("ran") is False:
        return explicit_reason or "chief_engineer_preflight_not_run"

    return ""


def _build_chief_engineer_blocked_director_result(
    *,
    run_id: str,
    task_count: int,
    reason: str,
    chief_engineer_result: Any,
) -> dict[str, Any]:
    """Build a Director-compatible blocked result for failed CE preflight."""
    summary = ""
    if isinstance(chief_engineer_result, dict):
        summary = str(chief_engineer_result.get("summary") or "").strip()
    if not summary:
        summary = "ChiefEngineer preflight blocked dispatch"

    return {
        "run_id": run_id,
        "status": "blocked",
        "mode": "chief_engineer_preflight",
        "summary": summary,
        "successes": 0,
        "failures": 0,
        "blocked": int(task_count or 0),
        "total": int(task_count or 0),
        "hard_failure": True,
        "dispatch_blocked": True,
        "dispatch_anomaly": "chief_engineer_preflight_failed",
        "preflight_reason": reason,
    }


def _build_workflow_input(
    workflow_input_type: Any,
    *,
    workspace_full: str,
    run_id: str,
    iteration: int,
    tasks: list[dict[str, Any]],
) -> Any:
    """Build the workflow submission input object."""
    return workflow_input_type(
        workspace=workspace_full,
        run_id=run_id,
        precomputed_payload={"tasks": tasks},
        metadata={"iteration": int(iteration or 0)},
    )


def _build_director_workflow_result(
    *,
    run_id: str,
    task_count: int,
    workflow_result: Any,
) -> dict[str, Any]:
    """Normalize workflow submission outcome into Director result payload."""
    submitted = bool(getattr(workflow_result, "submitted", False))
    status = str(getattr(workflow_result, "status", "") or "").strip()
    error_text = str(getattr(workflow_result, "error", "") or "").strip()
    details = getattr(workflow_result, "details", {})
    normalized_details = details if isinstance(details, dict) else {}

    if not submitted:
        return {
            "run_id": run_id,
            "status": status or "failed",
            "mode": "workflow",
            "workflow_id": str(getattr(workflow_result, "workflow_id", "") or "").strip(),
            "workflow_run_id": str(getattr(workflow_result, "workflow_run_id", "") or "").strip(),
            "summary": str(error_text or status).strip(),
            "error": error_text,
            "details": normalized_details,
            "successes": 0,
            "total": task_count,
        }

    return {
        "run_id": run_id,
        "status": "queued",
        "mode": "workflow",
        "workflow_id": str(getattr(workflow_result, "workflow_id", "") or "").strip(),
        "workflow_run_id": str(getattr(workflow_result, "workflow_run_id", "") or "").strip(),
        "summary": "Director workflow scheduled in Workflow",
        "error": error_text,
        "details": normalized_details,
        "successes": task_count,
        "total": task_count,
    }
