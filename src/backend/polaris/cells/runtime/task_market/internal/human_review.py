"""Human Review / HITL manager for ``runtime.task_market``."""

from __future__ import annotations

import os
import threading
from typing import TYPE_CHECKING, Any

from .errors import TaskMarketError
from .models import now_iso

if TYPE_CHECKING:
    from .store import TaskMarketStore


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Legitimate human-resolution actions (as defined in the blueprint).
RESOLUTION_ACTIONS: frozenset[str] = frozenset(
    {
        "requeue_design",
        "requeue_exec",
        "force_resolve",
        "close_as_invalid",
        "shadow_continue",
    }
)

# Tri-Council escalation chain.
ESCALATION_CHAIN: tuple[str, ...] = (
    "director",
    "chief_engineer",
    "pm",
    "architect",
    "human",
)

# Stage reached after human resolution, keyed by resolution action.
RESOLUTION_TO_STAGE: dict[str, str] = {
    "requeue_design": "pending_design",
    "requeue_exec": "pending_exec",
    "force_resolve": "resolved",
    "close_as_invalid": "rejected",
    "shadow_continue": "",  # Resumes from current stage — no change.
}

# Default auto-escalation timeout in seconds.
_DEFAULT_ESCALATION_TIMEOUT_SECONDS = 3600


def _read_escalation_timeout() -> int:
    """Read escalation timeout from env, default 3600s (1 hour)."""
    raw = str(os.environ.get("POLARIS_TASK_MARKET_ESCALATION_TIMEOUT_SECONDS", "") or "").strip()
    if not raw:
        return _DEFAULT_ESCALATION_TIMEOUT_SECONDS
    try:
        return max(60, int(raw))
    except (TypeError, ValueError):
        return _DEFAULT_ESCALATION_TIMEOUT_SECONDS


def _compute_escalation_deadline(created_at_iso: str) -> str:
    """Compute the escalation deadline as an ISO timestamp."""
    timeout_seconds = _read_escalation_timeout()
    from datetime import datetime, timedelta, timezone

    try:
        created = datetime.fromisoformat(created_at_iso)
    except (TypeError, ValueError):
        created = datetime.now(timezone.utc)
    deadline = created + timedelta(seconds=timeout_seconds)
    return deadline.isoformat()


# ---------------------------------------------------------------------------
# HumanReviewManager
# ---------------------------------------------------------------------------


class HumanReviewManager:
    """Manages WAITING_HUMAN tasks and human-in-the-loop resolution.

    When a task enters the ``waiting_human`` stage, a review request is
    created.  A human (or automated approval system) then calls
    ``resolve_review`` with one of the ``RESOLUTION_ACTIONS``.
    """

    def __init__(self, store: TaskMarketStore) -> None:
        self._store = store
        self._lock = threading.Lock()

    # ---- Mutation -----------------------------------------------------------

    def create_review_request(
        self,
        task_id: str,
        trace_id: str,
        workspace: str,
        reason: str,
        escalation_policy: str = "tri_council",
        requested_by: str = "system",
    ) -> dict[str, Any]:
        """Mark a task as WAITING_HUMAN and create a review request.

        Returns the created review request record.
        """
        with self._lock:
            items = self._store.load_items()
            item = items.get(task_id)

            if item is None:
                raise TaskMarketError(
                    f"Task {task_id} not found",
                    code="task_not_found",
                    details={"task_id": task_id},
                )

            existing_pending = self._find_pending_review(workspace=workspace, task_id=task_id)
            if item.status == "waiting_human" and existing_pending:
                return existing_pending

            previous_stage = item.stage
            previous_status = item.status
            item.stage = "waiting_human"
            item.status = "waiting_human"
            item.lease_token = ""
            item.lease_expires_at = 0.0
            item.claimed_by = ""
            item.claimed_role = ""
            item.metadata = dict(item.metadata)
            item.metadata["waiting_human_requested_at"] = now_iso()
            item.metadata["waiting_human_reason"] = str(reason or "").strip()
            item.metadata["waiting_human_requested_by"] = str(requested_by or "system").strip()
            item.metadata["waiting_human_snapshot"] = {
                "previous_stage": previous_stage,
                "previous_status": previous_status,
            }
            item.version += 1
            item.updated_at = now_iso()

            review_record: dict[str, Any] = {
                "task_id": task_id,
                "trace_id": str(trace_id or item.trace_id).strip(),
                "workspace": workspace,
                "reason": str(reason or "").strip(),
                "escalation_policy": str(escalation_policy or "tri_council").strip().lower(),
                "requested_by": str(requested_by or "system").strip(),
                "escalation_chain": list(ESCALATION_CHAIN),
                "current_role": "director",
                "next_role": get_next_escalation_role("director") or "",
                "status": "waiting",
                "created_at": now_iso(),
                "resolved_at": "",
                "resolution": "",
                "resolved_by": "",
                "resolution_note": "",
                "last_escalated_at": now_iso(),
                "escalation_deadline": _compute_escalation_deadline(now_iso()),
            }

            # Persist the request.
            self._save_review_request(review_record)
            items[item.task_id] = item
            self._store.save_items(items)

            return review_record

    def resolve_review(
        self,
        task_id: str,
        resolution: str,
        resolved_by: str = "human",
        note: str = "",
        *,
        workspace: str = "",
    ) -> dict[str, Any]:
        """Resolve a waiting human review.

        Args:
            task_id: The task being resolved.
            resolution: One of ``RESOLUTION_ACTIONS``.
            resolved_by: Identifier for who resolved it.
            note: Optional resolution note.
            workspace: Required for authority verification.

        Returns:
            The updated review request record.

        Raises:
            ValueError: if ``resolution`` is not a valid action.
            TaskMarketError: if the task is not in ``waiting_human`` state or
                the resolver lacks authority.
        """
        resolution = str(resolution or "").strip().lower()
        if resolution not in RESOLUTION_ACTIONS:
            raise ValueError(f"resolution must be one of: {sorted(RESOLUTION_ACTIONS)}; got {resolution!r}")

        with self._lock:
            items = self._store.load_items()
            item = items.get(task_id)

            if item is None:
                raise TaskMarketError(
                    f"Task {task_id} not found",
                    code="task_not_found",
                    details={"task_id": task_id},
                )

            if item.status != "waiting_human":
                raise TaskMarketError(
                    f"Task {task_id} is not in waiting_human state (status={item.status})",
                    code="not_waiting_human",
                    details={"task_id": task_id, "status": item.status},
                )

            # Authority verification: check resolved_by role against current_role.
            ws_token = str(workspace or item.workspace or "").strip()
            if ws_token:
                self._verify_resolve_authority(ws_token, task_id, resolved_by)

            target_stage = RESOLUTION_TO_STAGE.get(resolution, "")
            waiting_snapshot = item.metadata.get("waiting_human_snapshot", {})
            if not isinstance(waiting_snapshot, dict):
                waiting_snapshot = {}
            previous_stage = str(waiting_snapshot.get("previous_stage") or "").strip().lower()
            previous_status = str(waiting_snapshot.get("previous_status") or "").strip().lower()

            # shadow_continue: keep current stage, just clear waiting_human.
            if resolution == "shadow_continue":
                if previous_stage:
                    item.stage = previous_stage
                if previous_status:
                    item.status = previous_status
                elif item.stage:
                    item.status = item.stage
            elif target_stage:
                item.stage = target_stage
                item.status = target_stage
            else:
                # force_resolve / close_as_invalid handled above via target_stage.
                pass

            item.metadata = dict(item.metadata)
            item.metadata["waiting_human_resolved_at"] = now_iso()
            item.metadata["waiting_human_resolution"] = resolution
            item.metadata["waiting_human_resolved_by"] = str(resolved_by or "human").strip()
            item.metadata["waiting_human_resolution_note"] = str(note or "").strip()
            item.metadata.pop("waiting_human_snapshot", None)
            item.version += 1
            item.updated_at = now_iso()
            items[item.task_id] = item
            self._store.save_items(items)

            # Update review record.
            review_record = self._find_pending_review(workspace=item.workspace, task_id=task_id)
            if not review_record:
                review_record = {
                    "task_id": task_id,
                    "trace_id": item.trace_id,
                    "workspace": item.workspace,
                    "reason": "",
                    "escalation_policy": "tri_council",
                    "status": "waiting",
                    "created_at": now_iso(),
                }
            review_record["status"] = "resolved"
            review_record["resolution"] = resolution
            review_record["resolved_by"] = str(resolved_by or "human").strip()
            review_record["resolved_at"] = now_iso()
            review_record["resolution_note"] = str(note or "").strip()
            review_record["final_stage"] = item.stage
            review_record["final_status"] = item.status
            self._save_review_request(review_record)

            return review_record

    def escalate_to_tri_council(
        self,
        task_id: str,
        trace_id: str,
        workspace: str,
        reason: str,
    ) -> dict[str, Any]:
        """Escalate a task to the Tri-Council review path.

        This is a convenience wrapper that calls ``create_review_request``
        with ``escalation_policy="tri_council"``.
        """
        return self.create_review_request(
            task_id=task_id,
            trace_id=trace_id,
            workspace=workspace,
            reason=str(reason or "").strip(),
            escalation_policy="tri_council",
            requested_by="tri_council",
        )

    def advance_escalation_role(self, workspace: str, task_id: str) -> dict[str, Any]:
        """Advance pending review to next Tri-Council role.

        Returns:
            Updated review record.
        """
        with self._lock:
            review = self._find_pending_review(workspace=workspace, task_id=task_id)
            if not review:
                raise TaskMarketError(
                    f"Pending review not found for task {task_id}",
                    code="review_not_found",
                    details={"task_id": task_id},
                )
            current_role = str(review.get("current_role") or "director").strip().lower() or "director"
            next_role = get_next_escalation_role(current_role)
            if next_role is None:
                raise TaskMarketError(
                    f"Review already at terminal escalation role for task {task_id}",
                    code="escalation_terminal",
                    details={"task_id": task_id, "current_role": current_role},
                )
            review["current_role"] = next_role
            review["next_role"] = get_next_escalation_role(next_role) or ""
            review["last_escalated_at"] = now_iso()
            self._save_review_request(review)
            return review

    # ---- Query -------------------------------------------------------------

    def sweep_escalation_timeouts(self, workspace: str) -> dict[str, Any]:
        """Auto-escalate reviews whose escalation_deadline has passed.

        Returns a summary of escalated reviews.
        """
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc)
        escalated: list[str] = []
        terminal: list[str] = []

        pending = self.load_pending_reviews(workspace, limit=10_000)
        for review in pending:
            deadline_str = str(review.get("escalation_deadline") or "").strip()
            if not deadline_str:
                continue
            try:
                deadline = datetime.fromisoformat(deadline_str)
            except (TypeError, ValueError):
                continue

            if deadline.tzinfo is None:
                deadline = deadline.replace(tzinfo=timezone.utc)

            if now >= deadline:
                task_id = str(review.get("task_id") or "").strip()
                if not task_id:
                    continue
                try:
                    self.advance_escalation_role(workspace=workspace, task_id=task_id)
                    # Recompute deadline for the new role.
                    new_review = self._find_pending_review(workspace=workspace, task_id=task_id)
                    if new_review:
                        new_review["escalation_deadline"] = _compute_escalation_deadline(now_iso())
                        self._save_review_request(new_review)
                    escalated.append(task_id)
                except TaskMarketError:
                    terminal.append(task_id)

        return {
            "workspace": workspace,
            "escalated": escalated,
            "escalated_count": len(escalated),
            "terminal_count": len(terminal),
        }

    def _verify_resolve_authority(
        self,
        workspace: str,
        task_id: str,
        resolved_by: str,
    ) -> None:
        """Verify that the resolver has authority to resolve this review.

        Authority rules:
        - If ``resolved_by`` starts with a known role prefix (e.g. "director:bot-1"),
          the extracted role must match the review's ``current_role``.
        - The terminal role "human" is always allowed.
        - If no role can be extracted, resolution is allowed (backward compat).
        """
        review = self._find_pending_review(workspace=workspace, task_id=task_id)
        if not review:
            return  # No pending review — allow resolution.

        current_role = str(review.get("current_role") or "director").strip().lower()
        resolved_by_token = str(resolved_by or "human").strip().lower()

        # Extract role from resolved_by (format: "role:id" or just "role").
        resolver_role = resolved_by_token.split(":")[0] if resolved_by_token else ""

        # Terminal "human" role is always allowed.
        if resolver_role == "human" or resolved_by_token == "human":
            return

        # If we can extract a role and it doesn't match current_role, reject.
        known_roles = {r.lower() for r in ESCALATION_CHAIN}
        if resolver_role in known_roles and resolver_role != current_role:
            raise TaskMarketError(
                f"Resolver role '{resolver_role}' does not match current authority role '{current_role}'",
                code="unauthorized_role",
                details={
                    "task_id": task_id,
                    "resolver_role": resolver_role,
                    "current_role": current_role,
                    "resolved_by": resolved_by,
                },
            )

    def load_pending_reviews(
        self,
        workspace: str,
        *,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Return all unresolved human-review requests for a workspace."""
        return self._store.load_human_review_requests(workspace=workspace, limit=limit)

    # ---- Helpers -----------------------------------------------------------

    def _human_review_file_path(self, workspace: str) -> str:
        # Backward-compatible no-op retained for callers that may still import it.
        return ""

    def _save_review_request(self, record: dict[str, Any]) -> None:
        """Upsert a review request through the configured task-market store."""
        self._store.upsert_human_review_request(record)

    def _find_pending_review(self, *, workspace: str, task_id: str) -> dict[str, Any]:
        pending = self._store.load_human_review_requests(workspace=workspace, limit=10_000)
        for entry in pending:
            if str(entry.get("task_id") or "").strip() == task_id:
                return dict(entry)
        return {}


# ---------------------------------------------------------------------------
# Escalation helpers
# ---------------------------------------------------------------------------


def get_next_escalation_role(current_role: str) -> str | None:
    """Return the next role in the Tri-Council escalation chain, or None."""
    try:
        idx = list(ESCALATION_CHAIN).index(current_role)
        return ESCALATION_CHAIN[idx + 1] if idx + 1 < len(ESCALATION_CHAIN) else None
    except (ValueError, IndexError):
        return None


__all__ = [
    "ESCALATION_CHAIN",
    "RESOLUTION_ACTIONS",
    "RESOLUTION_TO_STAGE",
    "HumanReviewManager",
    "get_next_escalation_role",
]
