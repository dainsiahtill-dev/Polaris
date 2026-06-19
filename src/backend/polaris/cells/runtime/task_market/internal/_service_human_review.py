"""HITL / Tri-Council human-review flow for the task-market service facade.

``HumanReviewMixin`` owns request / resolve / escalation-advance, the pending
queue query, the escalation-timeout sweep, and the internal no-lock escalation
helper. Bodies are moved verbatim from the original ``service.py``.
"""

from __future__ import annotations

import time
from typing import Any

from polaris.cells.runtime.task_market.public.contracts import (
    HumanReviewResultV1,
    QueryPendingHumanReviewsV1,
    RequestHumanReviewCommandV1,
    ResolveHumanReviewCommandV1,
    TaskMarketError,
)

from ._service_base import ServiceBaseMixin
from .human_review import RESOLUTION_TO_STAGE, HumanReviewManager, get_next_escalation_role
from .models import now_iso

__all__ = ["HumanReviewMixin"]


class HumanReviewMixin(ServiceBaseMixin):
    """WAITING_HUMAN / HITL / Tri-Council management."""

    def request_human_review(self, command: RequestHumanReviewCommandV1) -> HumanReviewResultV1:
        t0 = time.monotonic()
        with self._workspace_lock(command.workspace):
            store = self._get_store(command.workspace)
            items = store.load_items()
            item = self._require_item(items, command.task_id)
            previous_status = item.status
            previous_stage = item.stage
            next_role = get_next_escalation_role("director") or ""
            transition = {
                "task_id": item.task_id,
                "from_status": previous_status,
                "to_status": "waiting_human",
                "event_type": "human_review_requested",
                "worker_id": command.requested_by,
                "lease_token": "",
                "version": int(item.version) + 1,
                "metadata": {
                    "trace_id": item.trace_id,
                    "reason": command.reason,
                    "from_stage": previous_stage,
                    "to_stage": "waiting_human",
                    "escalation_policy": command.escalation_policy,
                },
            }
            outbox = self._build_outbox_record(
                workspace=command.workspace,
                event_type="task_market.human_review_requested",
                run_id=item.run_id,
                task_id=item.task_id,
                payload={
                    "trace_id": item.trace_id,
                    "reason": command.reason,
                    "from_stage": previous_stage,
                    "to_stage": "waiting_human",
                    "requested_by": command.requested_by,
                    "escalation_policy": command.escalation_policy,
                    "next_role": next_role,
                },
            )

            review = HumanReviewManager(store).create_review_request(
                task_id=command.task_id,
                trace_id=command.trace_id or item.trace_id,
                workspace=command.workspace,
                reason=command.reason,
                escalation_policy=command.escalation_policy,
                requested_by=command.requested_by,
                transitions=[transition],
                outbox_records=[outbox],
            )

            items = store.load_items()
            item = self._require_item(items, command.task_id)
            self._observe(
                "human_review_request", (time.monotonic() - t0) * 1000.0, task_id=item.task_id, trace_id=item.trace_id
            )
            self._maybe_emit_webhook(
                workspace=command.workspace,
                run_id=item.run_id,
                task_id=item.task_id,
                action="requested",
                callback_url=command.callback_url,
                current_role=review.get("current_role", "director"),
                review=review,
            )
            return HumanReviewResultV1(
                ok=True,
                task_id=item.task_id,
                stage=item.stage,
                status=item.status,
                reason=command.reason,
            )

    def resolve_human_review(self, command: ResolveHumanReviewCommandV1) -> HumanReviewResultV1:
        t0 = time.monotonic()
        with self._workspace_lock(command.workspace):
            store = self._get_store(command.workspace)
            items = store.load_items()
            item = self._require_item(items, command.task_id)
            previous_status = item.status
            previous_stage = item.stage
            resolution = str(command.resolution or "").strip().lower()
            target_stage = RESOLUTION_TO_STAGE.get(resolution, "")
            waiting_snapshot = item.metadata.get("waiting_human_snapshot", {})
            if not isinstance(waiting_snapshot, dict):
                waiting_snapshot = {}
            if resolution == "shadow_continue":
                planned_stage = str(waiting_snapshot.get("previous_stage") or previous_stage).strip().lower()
                planned_status = str(waiting_snapshot.get("previous_status") or planned_stage).strip().lower()
            elif target_stage:
                planned_stage = target_stage
                planned_status = target_stage
            else:
                planned_stage = item.stage
                planned_status = item.status

            transition = {
                "task_id": item.task_id,
                "from_status": previous_status,
                "to_status": planned_status,
                "event_type": "human_review_resolved",
                "worker_id": command.resolved_by,
                "lease_token": "",
                "version": int(item.version) + 1,
                "metadata": {
                    "trace_id": item.trace_id,
                    "resolution": command.resolution,
                    "note": command.note,
                    "from_stage": previous_stage,
                    "to_stage": planned_stage,
                },
            }
            outbox = self._build_outbox_record(
                workspace=command.workspace,
                event_type="task_market.human_review_resolved",
                run_id=item.run_id,
                task_id=item.task_id,
                payload={
                    "trace_id": item.trace_id,
                    "resolution": command.resolution,
                    "resolved_by": command.resolved_by,
                    "from_stage": previous_stage,
                    "to_stage": planned_stage,
                    "final_status": planned_status,
                },
            )

            review = HumanReviewManager(store).resolve_review(
                task_id=command.task_id,
                resolution=command.resolution,
                resolved_by=command.resolved_by,
                note=command.note,
                workspace=command.workspace,
                transitions=[transition],
                outbox_records=[outbox],
            )

            items = store.load_items()
            item = self._require_item(items, command.task_id)
            self._observe(
                "human_review_resolve", (time.monotonic() - t0) * 1000.0, task_id=item.task_id, trace_id=item.trace_id
            )
            self._maybe_emit_webhook(
                workspace=command.workspace,
                run_id=item.run_id,
                task_id=item.task_id,
                action="resolved",
                callback_url=command.callback_url,
                current_role=review.get("current_role", ""),
                review=review,
            )
            return HumanReviewResultV1(
                ok=True,
                task_id=item.task_id,
                stage=item.stage,
                status=item.status,
                resolution=command.resolution,
                reason=command.note,
            )

    def advance_human_review_escalation(
        self,
        *,
        workspace: str,
        task_id: str,
        escalated_by: str,
    ) -> dict[str, Any]:
        workspace_token = str(workspace or "").strip()
        if not workspace_token:
            raise TaskMarketError("workspace is required", code="workspace_required")
        if not str(task_id or "").strip():
            raise TaskMarketError("task_id is required", code="task_id_required")
        escalated_by_token = str(escalated_by or "").strip() or "system"

        with self._workspace_lock(workspace_token):
            store = self._get_store(workspace_token)
            items = store.load_items()
            item = self._require_item(items, task_id)
            previous_version = int(item.version)
            review = HumanReviewManager(store).advance_escalation_role(workspace=workspace_token, task_id=task_id)
            item.metadata = dict(item.metadata)
            item.metadata["human_review_current_role"] = review.get("current_role", "")
            item.metadata["human_review_next_role"] = review.get("next_role", "")
            item.metadata["human_review_last_escalated_by"] = escalated_by_token
            item.metadata["human_review_last_escalated_at"] = now_iso()
            item.version += 1
            item.updated_at = now_iso()
            items[item.task_id] = item
            transition = {
                "task_id": item.task_id,
                "from_status": item.status,
                "to_status": item.status,
                "event_type": "human_review_escalated",
                "worker_id": escalated_by_token,
                "lease_token": "",
                "version": item.version,
                "metadata": {
                    "trace_id": item.trace_id,
                    "current_role": review.get("current_role", ""),
                    "next_role": review.get("next_role", ""),
                },
            }
            outbox = self._build_outbox_record(
                workspace=workspace_token,
                event_type="task_market.human_review_escalated",
                run_id=item.run_id,
                task_id=item.task_id,
                payload={
                    "trace_id": item.trace_id,
                    "escalated_by": escalated_by_token,
                    "current_role": review.get("current_role", ""),
                    "next_role": review.get("next_role", ""),
                },
            )
            self._atomic_save_changed_items(
                store=store,
                items=items,
                transitions=[transition],
                outbox_records=[outbox],
                expected_versions={item.task_id: previous_version},
            )
            return {
                "ok": True,
                "task_id": task_id,
                "current_role": review.get("current_role", ""),
                "next_role": review.get("next_role", ""),
            }

    def query_pending_human_reviews(
        self,
        query: QueryPendingHumanReviewsV1,
    ) -> tuple[dict[str, Any], ...]:
        with self._workspace_lock(query.workspace):
            store = self._get_store(query.workspace)
            rows = HumanReviewManager(store).load_pending_reviews(
                workspace=query.workspace,
                limit=query.limit,
            )
            return tuple(dict(row) for row in rows)

    # ---- Escalation Timeout Sweep ------------------------------------------

    def sweep_escalation_timeouts(self, workspace: str) -> dict[str, Any]:
        """Auto-escalate HITL reviews whose escalation_deadline has passed.

        Delegates to ``HumanReviewManager.sweep_escalation_timeouts``.
        """
        workspace_token = str(workspace or "").strip()
        if not workspace_token:
            raise TaskMarketError("workspace is required", code="workspace_required")
        with self._workspace_lock(workspace_token):
            store = self._get_store(workspace_token)
            return HumanReviewManager(store).sweep_escalation_timeouts(workspace_token)

    def _escalate_to_human_review_no_lock(
        self,
        *,
        workspace: str,
        store: Any,
        task_id: str,
        reason: str,
        requested_by: str,
    ) -> dict[str, Any]:
        items = store.load_items()
        item = self._require_item(items, task_id)
        previous_version = item.version
        previous_status = item.status
        previous_stage = item.stage
        next_role = get_next_escalation_role("director") or ""
        transition = {
            "task_id": item.task_id,
            "from_status": previous_status,
            "to_status": "waiting_human",
            "event_type": "human_review_requested",
            "worker_id": requested_by,
            "lease_token": "",
            "version": int(item.version) + 1,
            "metadata": {
                "trace_id": item.trace_id,
                "reason": reason,
                "from_stage": previous_stage,
                "to_stage": "waiting_human",
                "escalation_policy": "tri_council",
                "next_role": next_role,
            },
        }
        outbox = self._build_outbox_record(
            workspace=workspace,
            event_type="task_market.human_review_requested",
            run_id=item.run_id,
            task_id=item.task_id,
            payload={
                "trace_id": item.trace_id,
                "reason": reason,
                "requested_by": requested_by,
                "from_stage": previous_stage,
                "to_stage": "waiting_human",
                "escalation_policy": "tri_council",
                "next_role": next_role,
            },
        )

        review = HumanReviewManager(store).create_review_request(
            task_id=task_id,
            trace_id=item.trace_id,
            workspace=workspace,
            reason=reason,
            escalation_policy="tri_council",
            requested_by=requested_by,
            transitions=[transition],
            outbox_records=[outbox],
        )

        items = store.load_items()
        item = self._require_item(items, task_id)
        if item.version == previous_version and item.status == previous_status and item.stage == previous_stage:
            return {"item": item, "review": review}
        return {"item": item, "review": review}
