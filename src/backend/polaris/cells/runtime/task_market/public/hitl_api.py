"""Human-in-the-Loop API for task market."""

from __future__ import annotations

from typing import Any

from polaris.cells.runtime.task_market.internal.human_review import (
    ESCALATION_CHAIN,
    RESOLUTION_ACTIONS,
    get_next_escalation_role,
)
from polaris.cells.runtime.task_market.public.contracts import (
    QueryPendingHumanReviewsV1,
    RequestHumanReviewCommandV1,
    ResolveHumanReviewCommandV1,
)
from polaris.cells.runtime.task_market.public.service import get_task_market_service


def list_pending_reviews(workspace: str, limit: int = 100) -> list[dict[str, Any]]:
    """List all WAITING_HUMAN review requests.

    Args:
        workspace: The workspace identifier.
        limit: Maximum number of entries to return (default 100).

    Returns:
        A list of pending review request records.
    """
    svc = get_task_market_service()
    return list(
        svc.query_pending_human_reviews(
            QueryPendingHumanReviewsV1(
                workspace=workspace,
                limit=limit,
            )
        )
    )


def resolve_review(
    workspace: str,
    task_id: str,
    resolution: str,
    resolved_by: str = "human",
) -> dict[str, Any]:
    """Resolve a human review request.

    Args:
        workspace: The workspace identifier.
        task_id: The task to resolve.
        resolution: One of ``RESOLUTION_ACTIONS`` (e.g. "requeue_design",
            "requeue_exec", "force_resolve", "close_as_invalid", "shadow_continue").
        resolved_by: Identifier for who resolved it (default "human").

    Returns:
        A result dict with ``ok`` flag and either success or an error reason.
    """
    normalized = str(resolution or "").strip().lower()
    if normalized not in RESOLUTION_ACTIONS:
        return {
            "ok": False,
            "task_id": task_id,
            "reason": f"Invalid resolution: {resolution!r}. Must be one of: {', '.join(sorted(RESOLUTION_ACTIONS))}",
        }

    try:
        svc = get_task_market_service()
        result = svc.resolve_human_review(
            ResolveHumanReviewCommandV1(
                workspace=workspace,
                task_id=task_id,
                resolution=normalized,
                resolved_by=resolved_by,
            )
        )
        return {
            "ok": bool(result.ok),
            "task_id": task_id,
            "resolution": normalized,
            "status": result.status,
            "stage": result.stage,
        }
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "task_id": task_id, "reason": str(exc)}


def escalate_to_council(
    workspace: str,
    task_id: str,
    trace_id: str = "",
    reason: str = "Tri-Council escalation",
) -> dict[str, Any]:
    """Escalate a task to Tri-Council.

    Args:
        workspace: The workspace identifier.
        task_id: The task to escalate.
        trace_id: Optional trace identifier.
        reason: Reason for escalation (default "Tri-Council escalation").

    Returns:
        A result dict with ``ok`` flag and either success or an error reason.
    """
    try:
        svc = get_task_market_service()
        result = svc.request_human_review(
            RequestHumanReviewCommandV1(
                workspace=workspace,
                task_id=task_id,
                trace_id=trace_id,
                reason=reason,
                escalation_policy="tri_council",
                requested_by="hitl_api",
            )
        )
        return {
            "ok": bool(result.ok),
            "task_id": task_id,
            "escalation": "tri_council",
            "status": result.status,
            "stage": result.stage,
        }
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "task_id": task_id, "reason": str(exc)}


def advance_council_role(workspace: str, task_id: str, escalated_by: str = "system") -> dict[str, Any]:
    """Advance an existing Tri-Council review request to the next role."""
    try:
        svc = get_task_market_service()
        result = svc.advance_human_review_escalation(
            workspace=workspace,
            task_id=task_id,
            escalated_by=escalated_by,
        )
        return dict(result)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "task_id": task_id, "reason": str(exc)}


def get_escalation_chain() -> list[str]:
    """Return the Tri-Council escalation chain.

    Returns:
        A list of role names in escalation order.
    """
    return list(ESCALATION_CHAIN)


def get_next_role(current_role: str) -> str | None:
    """Get next escalation role after ``current_role``.

    Args:
        current_role: The current role in the escalation chain.

    Returns:
        The next role in the chain, or None if ``current_role`` is the last.
    """
    return get_next_escalation_role(current_role)


__all__ = [
    "advance_council_role",
    "escalate_to_council",
    "get_escalation_chain",
    "get_next_role",
    "list_pending_reviews",
    "resolve_review",
]
