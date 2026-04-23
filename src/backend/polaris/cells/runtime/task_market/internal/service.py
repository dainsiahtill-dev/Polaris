"""Service implementation for ``runtime.task_market`` — delegates to internal modules."""

from __future__ import annotations

import logging
import os
import threading
import time
import uuid
from collections import Counter
from datetime import datetime, timezone
from typing import Any

from polaris.cells.events.fact_stream.public.contracts import AppendFactEventCommandV1
from polaris.cells.events.fact_stream.public.service import append_fact_event
from polaris.cells.runtime.task_market.public.contracts import (
    AcknowledgeTaskStageCommandV1,
    ChangeOrderResultV1,
    ClaimTaskWorkItemCommandV1,
    FailTaskStageCommandV1,
    HumanReviewResultV1,
    MoveTaskToDeadLetterCommandV1,
    PlanRevisionResultV1,
    PublishTaskWorkItemCommandV1,
    QueryChangeOrdersV1,
    QueryPendingHumanReviewsV1,
    QueryPlanRevisionsV1,
    QueryTaskMarketStatusV1,
    RegisterPlanRevisionCommandV1,
    RenewTaskLeaseCommandV1,
    RequestHumanReviewCommandV1,
    RequeueTaskCommandV1,
    ResolveHumanReviewCommandV1,
    SubmitChangeOrderCommandV1,
    TaskLeaseRenewResultV1,
    TaskMarketError,
    TaskMarketStatusResultV1,
    TaskWorkItemResultV1,
)

from .consumer_loop import ConsumerLoopManager
from .dlq import DLQManager
from .errors import (
    StaleLeaseTokenError,
    TaskNotClaimableError,
    TaskNotFoundError,
)
from .fsm import PRIORITY_WEIGHT, get_fsm
from .human_review import HumanReviewManager, get_next_escalation_role
from .lease_manager import LeaseManager
from .metrics import get_task_market_metrics
from .models import (
    TERMINAL_STATUSES,
    TaskWorkItemRecord,
    now_epoch,
    now_iso,
)
from .reconciler import TaskReconciliationLoop
from .saga import CompensationAction, SagaCompensator
from .store import get_store
from .tracing import get_task_market_tracer

logger = logging.getLogger(__name__)
_IN_PROGRESS_STATUSES = {"in_design", "in_execution", "in_qa"}
_EXECUTION_STATUS_SET = {"pending_exec", "in_execution"}
_QA_STATUS_SET = {"pending_qa", "in_qa"}
_DESIGN_STATUS_SET = {"pending_design", "in_design"}


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class TaskMarketService:
    """Task market service with lease-aware stage transitions.

    This implementation delegates to focused internal modules:
    - ``LeaseManager`` — lease lifecycle (grant / renew / validate)
    - ``DLQManager`` — dead-letter queue management
    - ``HumanReviewManager`` — WAITING_HUMAN / HITL management
    - ``TaskStageFSM`` — state transition validation

    The store backend (JSON or SQLite) is selected via
    ``KERNELONE_TASK_MARKET_STORE`` or the ``get_store()`` factory.
    """

    def __init__(self) -> None:
        self._workspace_locks: dict[str, threading.Lock] = {}
        self._workspace_locks_guard = threading.Lock()
        self._reconciliation_loops: dict[str, TaskReconciliationLoop] = {}
        self._reconciliation_loops_guard = threading.Lock()
        self._consumer_loop_managers: dict[str, ConsumerLoopManager] = {}
        self._consumer_loop_managers_guard = threading.Lock()
        self._auto_reconciliation_enabled = self._read_bool_env(
            "KERNELONE_TASK_MARKET_ENABLE_RECONCILIATION_LOOP",
            default=False,
        )
        self._auto_reconciliation_interval_seconds = self._read_float_env(
            "KERNELONE_TASK_MARKET_RECONCILIATION_INTERVAL_SECONDS",
            default=30.0,
            min_value=1.0,
        )
        self._fsm = get_fsm()
        self._metrics = get_task_market_metrics()
        self._tracer = get_task_market_tracer()

    def _workspace_lock(self, workspace: str) -> threading.Lock:
        workspace_token = str(workspace or "").strip()
        if not workspace_token:
            raise TaskMarketError("workspace is required", code="workspace_required")
        with self._workspace_locks_guard:
            lock = self._workspace_locks.get(workspace_token)
            if lock is None:
                lock = threading.Lock()
                self._workspace_locks[workspace_token] = lock
            return lock

    @staticmethod
    def _read_bool_env(name: str, *, default: bool) -> bool:
        raw = os.getenv(name)
        if raw is None:
            return default
        normalized = str(raw).strip().lower()
        if not normalized:
            return default
        return normalized in {"1", "true", "yes", "on"}

    @staticmethod
    def _read_float_env(name: str, *, default: float, min_value: float) -> float:
        raw = os.getenv(name)
        if raw is None:
            return max(min_value, float(default))
        try:
            parsed = float(str(raw).strip())
        except (TypeError, ValueError):
            return max(min_value, float(default))
        return max(min_value, parsed)

    def start_reconciliation_loop(self, workspace: str, *, interval_seconds: float | None = None) -> bool:
        workspace_token = str(workspace or "").strip()
        if not workspace_token:
            raise TaskMarketError("workspace is required", code="workspace_required")

        interval = (
            self._auto_reconciliation_interval_seconds
            if interval_seconds is None
            else max(1.0, float(interval_seconds))
        )
        with self._reconciliation_loops_guard:
            loop = self._reconciliation_loops.get(workspace_token)
            if loop is not None:
                loop.start()
                return False
            loop = TaskReconciliationLoop(
                service=self,
                workspace=workspace_token,
                interval_seconds=interval,
            )
            loop.start()
            self._reconciliation_loops[workspace_token] = loop
            return True

    def stop_reconciliation_loop(self, workspace: str) -> bool:
        workspace_token = str(workspace or "").strip()
        if not workspace_token:
            raise TaskMarketError("workspace is required", code="workspace_required")
        with self._reconciliation_loops_guard:
            loop = self._reconciliation_loops.pop(workspace_token, None)
        if loop is None:
            return False
        loop.stop()
        return True

    def stop_all_reconciliation_loops(self) -> int:
        with self._reconciliation_loops_guard:
            entries = tuple(self._reconciliation_loops.items())
            self._reconciliation_loops.clear()
        for _, loop in entries:
            loop.stop()
        return len(entries)

    def _maybe_start_reconciliation_loop(self, workspace: str) -> None:
        if not self._auto_reconciliation_enabled:
            return
        try:
            self.start_reconciliation_loop(workspace)
        except TaskMarketError:
            return

    # ---- Store access -------------------------------------------------------

    def _get_store(self, workspace: str):
        """Return the appropriate store backend (lazy)."""
        return get_store(workspace)

    # ---- Publish ------------------------------------------------------------

    def publish_work_item(self, command: PublishTaskWorkItemCommandV1) -> TaskWorkItemResultV1:
        t0 = time.monotonic()
        self._maybe_start_reconciliation_loop(command.workspace)
        with self._workspace_lock(command.workspace):
            store = self._get_store(command.workspace)
            items = store.load_items()
            item = items.get(command.task_id)

            if item is None:
                item = TaskWorkItemRecord(
                    task_id=command.task_id,
                    trace_id=command.trace_id,
                    run_id=command.run_id,
                    workspace=command.workspace,
                    stage=command.stage,
                    status=command.stage,
                    priority=command.priority,
                    plan_id=command.plan_id,
                    plan_revision_id=command.plan_revision_id,
                    root_task_id=command.root_task_id or command.task_id,
                    parent_task_id=command.parent_task_id,
                    is_leaf=command.is_leaf,
                    depends_on=list(command.depends_on),
                    requirement_digest=command.requirement_digest,
                    constraint_digest=command.constraint_digest,
                    summary_ref=command.summary_ref,
                    superseded_by_revision=command.superseded_by_revision,
                    change_policy=command.change_policy,
                    compensation_group_id=command.compensation_group_id,
                    payload=dict(command.payload),
                    metadata=dict(command.metadata),
                    version=1,
                    attempts=0,
                    max_attempts=max(1, int(command.max_attempts)),
                )
            else:
                item.trace_id = command.trace_id
                item.run_id = command.run_id
                item.workspace = command.workspace
                item.stage = command.stage
                item.status = command.stage
                item.priority = command.priority
                item.plan_id = command.plan_id
                item.plan_revision_id = command.plan_revision_id
                item.root_task_id = command.root_task_id or command.task_id
                item.parent_task_id = command.parent_task_id
                item.is_leaf = command.is_leaf
                item.depends_on = list(command.depends_on)
                item.requirement_digest = command.requirement_digest
                item.constraint_digest = command.constraint_digest
                item.summary_ref = command.summary_ref
                item.superseded_by_revision = command.superseded_by_revision
                item.change_policy = command.change_policy
                item.compensation_group_id = command.compensation_group_id
                item.payload = dict(command.payload)
                item.metadata = dict(command.metadata)
                item.max_attempts = max(1, int(command.max_attempts))
                item.lease_token = ""
                item.lease_expires_at = 0.0
                item.claimed_by = ""
                item.claimed_role = ""
                item.version += 1
                item.updated_at = now_iso()

            items[item.task_id] = item

            # Collect transition and outbox records for atomic write.
            transition = {
                "task_id": item.task_id,
                "from_status": "",
                "to_status": item.status,
                "event_type": "published",
                "worker_id": "",
                "lease_token": "",
                "version": item.version,
                "metadata": {
                    "trace_id": item.trace_id,
                    "stage": item.stage,
                    "priority": item.priority,
                    "source_role": command.source_role,
                    "plan_id": item.plan_id,
                    "plan_revision_id": item.plan_revision_id,
                    "root_task_id": item.root_task_id,
                    "parent_task_id": item.parent_task_id,
                },
            }

            outbox_record = self._build_outbox_record(
                workspace=command.workspace,
                event_type="task_market.work_item_published",
                run_id=command.run_id,
                task_id=command.task_id,
                payload={
                    "trace_id": command.trace_id,
                    "stage": item.stage,
                    "status": item.status,
                    "priority": item.priority,
                    "source_role": command.source_role,
                    "plan_id": item.plan_id,
                    "plan_revision_id": item.plan_revision_id,
                    "root_task_id": item.root_task_id,
                    "parent_task_id": item.parent_task_id,
                },
            )

            store.save_items_and_outbox_atomic(
                items=items,
                transitions=[transition],
                outbox_records=[outbox_record],
            )

            self._observe(
                "publish",
                (time.monotonic() - t0) * 1000.0,
                stage=command.stage,
                task_id=command.task_id,
                trace_id=command.trace_id,
            )
            return self._result_from_item(item, reason="published")

    def claim_work_item(self, command: ClaimTaskWorkItemCommandV1) -> TaskWorkItemResultV1:
        t0 = time.monotonic()
        with self._workspace_lock(command.workspace):
            store = self._get_store(command.workspace)
            items = store.load_items()

            # Select a candidate.
            selected = self._select_claim_candidate(
                items=items,
                stage=command.stage,
                task_id_filter=command.task_id,
                at_epoch=now_epoch(),
            )

            if selected is None:
                return TaskWorkItemResultV1(
                    ok=False,
                    task_id=str(command.task_id or ""),
                    stage=command.stage,
                    status=command.stage,
                    version=0,
                    reason="no_claimable_work_item",
                )

            # Check retry exhaustion.
            if selected.attempts >= selected.max_attempts:
                dlq = DLQManager(store)
                dlq.move_to_dead_letter(
                    item=selected,
                    reason="retry_exhausted_on_claim",
                    error_code="retry_exhausted",
                    metadata={"worker_id": command.worker_id, "worker_role": command.worker_role},
                )
                transition = {
                    "task_id": selected.task_id,
                    "from_status": selected.status,
                    "to_status": "dead_letter",
                    "event_type": "dead_lettered",
                    "worker_id": command.worker_id,
                    "lease_token": "",
                    "version": selected.version,
                    "metadata": {
                        "trace_id": selected.trace_id,
                        "reason": "retry_exhausted_on_claim",
                        "attempts": selected.attempts,
                    },
                }
                outbox = self._build_outbox_record(
                    workspace=command.workspace,
                    event_type="task_market.work_item_dead_lettered",
                    run_id=selected.run_id,
                    task_id=selected.task_id,
                    payload={
                        "trace_id": selected.trace_id,
                        "reason": "retry_exhausted_on_claim",
                        "attempts": selected.attempts,
                    },
                )
                store.save_items_and_outbox_atomic(
                    items=items,
                    transitions=[transition],
                    outbox_records=[outbox],
                )
                return self._result_from_item(selected, ok=False, reason="retry_exhausted_on_claim")

            # Grant lease via LeaseManager.
            lm = LeaseManager(store)
            from_status = selected.status
            try:
                lease_token, expires_at = lm.grant_lease(
                    item=selected,
                    worker_id=command.worker_id,
                    worker_role=command.worker_role,
                    visibility_timeout_seconds=command.visibility_timeout_seconds,
                )
            except TaskNotClaimableError as exc:
                return TaskWorkItemResultV1(
                    ok=False,
                    task_id=selected.task_id,
                    stage=selected.stage,
                    status=selected.status,
                    version=selected.version,
                    reason=f"lease_error: {exc}",
                )

            items[selected.task_id] = selected
            transition = {
                "task_id": selected.task_id,
                "from_status": from_status,
                "to_status": selected.status,
                "event_type": "claimed",
                "worker_id": command.worker_id,
                "lease_token": lease_token,
                "version": selected.version,
                "metadata": {
                    "trace_id": selected.trace_id,
                    "stage": selected.stage,
                    "worker_role": command.worker_role,
                    "lease_expires_at": expires_at,
                },
            }
            outbox = self._build_outbox_record(
                workspace=command.workspace,
                event_type="task_market.lease_granted",
                run_id=selected.run_id,
                task_id=selected.task_id,
                payload={
                    "trace_id": selected.trace_id,
                    "stage": selected.stage,
                    "status": selected.status,
                    "worker_id": command.worker_id,
                    "worker_role": command.worker_role,
                    "lease_token": lease_token,
                    "lease_expires_at": expires_at,
                },
            )
            store.save_items_and_outbox_atomic(
                items=items,
                transitions=[transition],
                outbox_records=[outbox],
            )

            self._observe("claim", (time.monotonic() - t0) * 1000.0, stage=command.stage, task_id=selected.task_id)
            return self._result_from_item(selected, lease_token=lease_token, reason="claimed")

    def renew_task_lease(self, command: RenewTaskLeaseCommandV1) -> TaskLeaseRenewResultV1:
        t0 = time.monotonic()
        with self._workspace_lock(command.workspace):
            store = self._get_store(command.workspace)
            items = store.load_items()
            item = items.get(command.task_id)

            if item is None:
                return TaskLeaseRenewResultV1(
                    ok=False,
                    task_id=command.task_id,
                    lease_token=command.lease_token,
                    lease_expires_at="",
                    version=0,
                    reason="task_not_found",
                )

            lm = LeaseManager(store)
            try:
                ok, expires_at = lm.renew_lease(
                    item=item,
                    lease_token=command.lease_token,
                    visibility_timeout_seconds=command.visibility_timeout_seconds,
                )
            except StaleLeaseTokenError:
                return TaskLeaseRenewResultV1(
                    ok=False,
                    task_id=item.task_id,
                    lease_token=command.lease_token,
                    lease_expires_at="",
                    version=item.version,
                    reason="lease_token_mismatch",
                )

            if ok:
                items[item.task_id] = item
                outbox = self._build_outbox_record(
                    workspace=command.workspace,
                    event_type="task_market.lease_renewed",
                    run_id=item.run_id,
                    task_id=item.task_id,
                    payload={
                        "trace_id": item.trace_id,
                        "status": item.status,
                        "lease_expires_at": expires_at,
                    },
                )
                store.save_items_and_outbox_atomic(
                    items=items,
                    transitions=[],
                    outbox_records=[outbox],
                )

            self._observe("renew_lease", (time.monotonic() - t0) * 1000.0, task_id=command.task_id)
            return TaskLeaseRenewResultV1(
                ok=ok,
                task_id=item.task_id,
                lease_token=item.lease_token,
                lease_expires_at=datetime.fromtimestamp(expires_at, tz=timezone.utc).isoformat(),
                version=item.version,
                reason="lease_renewed" if ok else "lease_token_mismatch",
            )

    # ---- Acknowledge --------------------------------------------------------

    def acknowledge_task_stage(self, command: AcknowledgeTaskStageCommandV1) -> TaskWorkItemResultV1:
        t0 = time.monotonic()
        with self._workspace_lock(command.workspace):
            store = self._get_store(command.workspace)
            items = store.load_items()
            item = self._require_item(items, command.task_id)

            # Validate lease token.
            lm = LeaseManager(store)
            lm.validate_token(item, command.lease_token)

            previous_status = item.status

            # Determine next status.
            if command.next_stage is not None:
                item.stage = command.next_stage
                item.status = command.next_stage
            else:
                terminal_status = str(command.terminal_status or "resolved").strip().lower()
                if terminal_status not in TERMINAL_STATUSES:
                    raise TaskMarketError(
                        f"Unsupported terminal status: {terminal_status}",
                        code="unsupported_terminal_status",
                        details={"task_id": item.task_id, "status": terminal_status},
                    )
                item.status = terminal_status

            item.metadata = dict(item.metadata)
            item.metadata["last_summary"] = command.summary
            item.metadata["last_ack_metadata"] = dict(command.metadata)

            # Merge ack metadata into item.payload so downstream consumers (Director, QA)
            # can access fields generated by upstream workers (CE sets blueprint_id, guardrails, etc.).
            item.payload = {**dict(item.payload), **dict(command.metadata)}

            # Clear lease.
            lm.clear_lease(item)
            item.version += 1
            item.updated_at = now_iso()

            items[item.task_id] = item

            transition = {
                "task_id": item.task_id,
                "from_status": previous_status,
                "to_status": item.status,
                "event_type": "acknowledged",
                "worker_id": item.claimed_by or "",
                "lease_token": command.lease_token,
                "version": item.version,
                "metadata": {
                    "trace_id": item.trace_id,
                    "from_status": previous_status,
                    "to_status": item.status,
                    "next_stage": command.next_stage or "",
                    "summary": command.summary,
                },
            }

            outbox = self._build_outbox_record(
                workspace=command.workspace,
                event_type="task_market.stage_acknowledged",
                run_id=item.run_id,
                task_id=item.task_id,
                payload={
                    "trace_id": item.trace_id,
                    "from_status": previous_status,
                    "to_status": item.status,
                    "next_stage": command.next_stage or "",
                },
            )

            store.save_items_and_outbox_atomic(
                items=items,
                transitions=[transition],
                outbox_records=[outbox],
            )

            if command.next_stage == "waiting_human":
                escalation = self._escalate_to_human_review_no_lock(
                    workspace=command.workspace,
                    store=store,
                    task_id=item.task_id,
                    reason=str(command.summary or "qa_waiting_human").strip() or "qa_waiting_human",
                    requested_by="acknowledge_task_stage",
                )
                item = escalation["item"]

            self._observe(
                "acknowledge",
                (time.monotonic() - t0) * 1000.0,
                stage=item.stage,
                task_id=item.task_id,
                trace_id=item.trace_id,
            )
            return self._result_from_item(item, reason="acknowledged")

    # ---- Fail --------------------------------------------------------------

    def fail_task_stage(self, command: FailTaskStageCommandV1) -> TaskWorkItemResultV1:
        t0 = time.monotonic()
        with self._workspace_lock(command.workspace):
            store = self._get_store(command.workspace)
            items = store.load_items()
            item = self._require_item(items, command.task_id)

            lm = LeaseManager(store)
            lm.validate_token(item, command.lease_token)

            previous_status = item.status

            item.last_error = {
                "error_code": command.error_code,
                "error_message": command.error_message,
                "metadata": dict(command.metadata),
                "occurred_at": now_iso(),
            }

            # Determine disposition.
            move_to_dead_letter = bool(command.to_dead_letter) or item.attempts >= item.max_attempts

            if move_to_dead_letter:
                dlq = DLQManager(store)
                dlq.move_to_dead_letter(
                    item=item,
                    reason=command.error_message,
                    error_code=command.error_code,
                    metadata=dict(command.metadata),
                )
                reason = "dead_lettered"
                event_type = "task_market.work_item_dead_lettered"
                lm.clear_lease(item)
            elif command.requeue_stage:
                item.stage = command.requeue_stage
                item.status = command.requeue_stage
                lm.clear_lease(item)
                item.version += 1
                item.updated_at = now_iso()
                reason = "requeued"
                event_type = "task_market.stage_requeued"
            else:
                item.status = "rejected"
                lm.clear_lease(item)
                item.version += 1
                item.updated_at = now_iso()
                reason = "rejected"
                event_type = "task_market.stage_rejected"

            items[item.task_id] = item

            transition = {
                "task_id": item.task_id,
                "from_status": previous_status,
                "to_status": item.status,
                "event_type": event_type,
                "worker_id": item.claimed_by or "",
                "lease_token": command.lease_token,
                "version": item.version,
                "metadata": {
                    "trace_id": item.trace_id,
                    "error_code": command.error_code,
                    "error_message": command.error_message,
                },
            }

            outbox = self._build_outbox_record(
                workspace=command.workspace,
                event_type=event_type,
                run_id=item.run_id,
                task_id=item.task_id,
                payload={
                    "trace_id": item.trace_id,
                    "status": item.status,
                    "stage": item.stage,
                    "error_code": command.error_code,
                    "error_message": command.error_message,
                },
            )

            store.save_items_and_outbox_atomic(
                items=items,
                transitions=[transition],
                outbox_records=[outbox],
            )

            # Saga compensation only on terminal failure paths (not requeue).
            if command.requeue_stage is None:
                task_compensation_summary = self._compensate_task_no_lock(
                    workspace=command.workspace,
                    store=store,
                    items=items,
                    item=item,
                    reason=f"task_failed:{command.error_code}",
                    initiator="fail_task_stage",
                )
                item.metadata = dict(item.metadata)
                item.metadata["saga_task_compensation"] = task_compensation_summary

                parent_compensation_summary: dict[str, Any] | None = None
                if not item.is_leaf:
                    parent_compensation_summary = self._compensate_children_for_parent_failure(
                        workspace=command.workspace,
                        store=store,
                        items=items,
                        parent_task_id=item.task_id,
                        reason=f"parent_failed:{command.error_code}",
                    )
                    item.metadata["saga_child_compensation"] = parent_compensation_summary

                item.version += 1
                item.updated_at = now_iso()
                items[item.task_id] = item

                saga_transition = {
                    "task_id": item.task_id,
                    "from_status": item.status,
                    "to_status": item.status,
                    "event_type": "saga_failure_compensation",
                    "worker_id": "fail_task_stage",
                    "lease_token": "",
                    "version": item.version,
                    "metadata": {
                        "trace_id": item.trace_id,
                        "task_compensated": bool(task_compensation_summary.get("executed", False)),
                        "children_compensated": (
                            int(parent_compensation_summary.get("child_count", 0))
                            if isinstance(parent_compensation_summary, dict)
                            else 0
                        ),
                    },
                }

                saga_outbox = self._build_outbox_record(
                    workspace=command.workspace,
                    event_type="task_market.saga_failure_compensation",
                    run_id=item.run_id,
                    task_id=item.task_id,
                    payload={
                        "trace_id": item.trace_id,
                        "task_compensated": bool(task_compensation_summary.get("executed", False)),
                        "children_compensated": (
                            int(parent_compensation_summary.get("child_count", 0))
                            if isinstance(parent_compensation_summary, dict)
                            else 0
                        ),
                    },
                )

                store.save_items_and_outbox_atomic(
                    items=items,
                    transitions=[saga_transition],
                    outbox_records=[saga_outbox],
                )

            # Route to HITL/Tri-Council when failure is terminal or requires manual handling.
            should_escalate = False
            escalate_reason = ""
            if command.requeue_stage is None:
                should_escalate = bool(dict(command.metadata).get("escalate_to_human_review", False))
                escalate_reason = f"task_failed:{command.error_code}"
                task_summary_raw = item.metadata.get("saga_task_compensation")
                if isinstance(task_summary_raw, dict) and bool(
                    task_summary_raw.get("requires_manual_intervention", False)
                ):
                    should_escalate = True
                    escalate_reason = "saga_manual_intervention_required"
                child_summary_raw = item.metadata.get("saga_child_compensation")
                if isinstance(child_summary_raw, dict) and bool(
                    child_summary_raw.get("requires_manual_intervention", False)
                ):
                    should_escalate = True
                    escalate_reason = "child_saga_manual_intervention_required"

            if should_escalate and item.status != "waiting_human":
                escalation = self._escalate_to_human_review_no_lock(
                    workspace=command.workspace,
                    store=store,
                    task_id=item.task_id,
                    reason=escalate_reason,
                    requested_by="fail_task_stage",
                )
                item = escalation["item"]
                items = store.load_items()

            self._observe(
                "fail", (time.monotonic() - t0) * 1000.0, stage=item.stage, task_id=item.task_id, trace_id=item.trace_id
            )
            return self._result_from_item(item, reason=reason)

    # ---- Requeue -----------------------------------------------------------

    def requeue_task(self, command: RequeueTaskCommandV1) -> TaskWorkItemResultV1:
        t0 = time.monotonic()
        with self._workspace_lock(command.workspace):
            store = self._get_store(command.workspace)
            items = store.load_items()
            item = self._require_item(items, command.task_id)

            previous_status = item.status
            item.stage = command.target_stage
            item.status = command.target_stage
            lm = LeaseManager(store)
            lm.clear_lease(item)
            item.metadata = dict(item.metadata)
            item.metadata["requeue_reason"] = command.reason
            item.metadata["requeue_metadata"] = dict(command.metadata)
            item.metadata["requeued_at"] = now_iso()
            item.version += 1
            item.updated_at = now_iso()

            items[item.task_id] = item

            transition = {
                "task_id": item.task_id,
                "from_status": previous_status,
                "to_status": item.status,
                "event_type": "requeued",
                "worker_id": "",
                "lease_token": "",
                "version": item.version,
                "metadata": {
                    "trace_id": item.trace_id,
                    "target_stage": command.target_stage,
                    "reason": command.reason,
                },
            }

            outbox = self._build_outbox_record(
                workspace=command.workspace,
                event_type="task_market.work_item_requeued",
                run_id=item.run_id,
                task_id=item.task_id,
                payload={
                    "trace_id": item.trace_id,
                    "target_stage": command.target_stage,
                    "reason": command.reason,
                },
            )

            store.save_items_and_outbox_atomic(
                items=items,
                transitions=[transition],
                outbox_records=[outbox],
            )

            self._observe("requeue", (time.monotonic() - t0) * 1000.0, stage=command.target_stage, task_id=item.task_id)
            return self._result_from_item(item, reason="requeued")

    # ---- Dead Letter --------------------------------------------------------

    def move_task_to_dead_letter(self, command: MoveTaskToDeadLetterCommandV1) -> TaskWorkItemResultV1:
        t0 = time.monotonic()
        with self._workspace_lock(command.workspace):
            store = self._get_store(command.workspace)
            items = store.load_items()
            item = self._require_item(items, command.task_id)

            previous_status = item.status
            dlq = DLQManager(store)
            dlq.move_to_dead_letter(
                item=item,
                reason=command.reason,
                error_code=str(command.error_code or "").strip(),
                metadata=dict(command.metadata),
            )
            items[item.task_id] = item

            transition = {
                "task_id": item.task_id,
                "from_status": previous_status,
                "to_status": "dead_letter",
                "event_type": "dead_lettered",
                "worker_id": "",
                "lease_token": "",
                "version": item.version,
                "metadata": {
                    "trace_id": item.trace_id,
                    "reason": command.reason,
                    "error_code": command.error_code or "",
                },
            }

            outbox = self._build_outbox_record(
                workspace=command.workspace,
                event_type="task_market.work_item_dead_lettered",
                run_id=item.run_id,
                task_id=item.task_id,
                payload={
                    "trace_id": item.trace_id,
                    "reason": command.reason,
                    "error_code": command.error_code or "",
                },
            )

            store.save_items_and_outbox_atomic(
                items=items,
                transitions=[transition],
                outbox_records=[outbox],
            )

            self._observe("dead_letter", (time.monotonic() - t0) * 1000.0, task_id=item.task_id)
            return self._result_from_item(item, reason="dead_lettered")

    # ---- HITL / Human Review -----------------------------------------------

    def request_human_review(self, command: RequestHumanReviewCommandV1) -> HumanReviewResultV1:
        t0 = time.monotonic()
        with self._workspace_lock(command.workspace):
            store = self._get_store(command.workspace)
            items = store.load_items()
            item = self._require_item(items, command.task_id)
            previous_status = item.status
            previous_stage = item.stage

            review = HumanReviewManager(store).create_review_request(
                task_id=command.task_id,
                trace_id=command.trace_id or item.trace_id,
                workspace=command.workspace,
                reason=command.reason,
                escalation_policy=command.escalation_policy,
                requested_by=command.requested_by,
            )

            items = store.load_items()
            item = self._require_item(items, command.task_id)
            store.append_transition(
                task_id=item.task_id,
                from_status=previous_status,
                to_status=item.status,
                event_type="human_review_requested",
                worker_id=command.requested_by,
                lease_token="",
                version=item.version,
                metadata={
                    "trace_id": item.trace_id,
                    "reason": command.reason,
                    "from_stage": previous_stage,
                    "to_stage": item.stage,
                    "escalation_policy": review.get("escalation_policy", "tri_council"),
                },
            )
            self._emit_fact(
                workspace=command.workspace,
                event_type="task_market.human_review_requested",
                run_id=item.run_id,
                task_id=item.task_id,
                payload={
                    "trace_id": item.trace_id,
                    "reason": command.reason,
                    "from_stage": previous_stage,
                    "to_stage": item.stage,
                    "requested_by": command.requested_by,
                    "escalation_policy": review.get("escalation_policy", "tri_council"),
                    "next_role": review.get("next_role", ""),
                },
            )
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

            review = HumanReviewManager(store).resolve_review(
                task_id=command.task_id,
                resolution=command.resolution,
                resolved_by=command.resolved_by,
                note=command.note,
                workspace=command.workspace,
            )

            items = store.load_items()
            item = self._require_item(items, command.task_id)
            store.append_transition(
                task_id=item.task_id,
                from_status=previous_status,
                to_status=item.status,
                event_type="human_review_resolved",
                worker_id=command.resolved_by,
                lease_token="",
                version=item.version,
                metadata={
                    "trace_id": item.trace_id,
                    "resolution": command.resolution,
                    "note": command.note,
                    "from_stage": previous_stage,
                    "to_stage": item.stage,
                },
            )
            self._emit_fact(
                workspace=command.workspace,
                event_type="task_market.human_review_resolved",
                run_id=item.run_id,
                task_id=item.task_id,
                payload={
                    "trace_id": item.trace_id,
                    "resolution": command.resolution,
                    "resolved_by": command.resolved_by,
                    "from_stage": previous_stage,
                    "to_stage": item.stage,
                    "final_status": review.get("final_status", item.status),
                },
            )
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
            store.save_items_and_outbox_atomic(
                items=items,
                transitions=[transition],
                outbox_records=[outbox],
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

    # ---- Outbox Relay -------------------------------------------------------

    def relay_outbox_messages(self, workspace: str, *, limit: int = 200) -> dict[str, Any]:
        t0 = time.monotonic()
        workspace_token = str(workspace or "").strip()
        if not workspace_token:
            raise TaskMarketError("workspace is required", code="workspace_required")
        max_limit = max(1, int(limit))
        with self._workspace_lock(workspace_token):
            store = self._get_store(workspace_token)
            rows = store.load_outbox_messages(
                workspace_token,
                statuses=("pending", "failed"),
                limit=max_limit,
            )

            sent = 0
            failed = 0
            sent_outbox_ids: list[str] = []
            failed_outbox_ids: list[str] = []
            for row in rows:
                outbox_id = str(row.get("outbox_id") or "").strip()
                if not outbox_id:
                    continue
                payload_raw = row.get("payload")
                payload = dict(payload_raw) if isinstance(payload_raw, dict) else {}
                try:
                    append_fact_event(
                        AppendFactEventCommandV1(
                            workspace=workspace_token,
                            stream=str(row.get("stream") or "task_market.events").strip() or "task_market.events",
                            event_type=str(row.get("event_type") or "").strip(),
                            source=str(row.get("source") or "runtime.task_market").strip() or "runtime.task_market",
                            run_id=str(row.get("run_id") or "").strip(),
                            task_id=str(row.get("task_id") or "").strip(),
                            payload=payload,
                        )
                    )
                    store.mark_outbox_message_sent(
                        workspace_token,
                        outbox_id,
                        delivered_at=now_iso(),
                    )
                    sent += 1
                    sent_outbox_ids.append(outbox_id)
                except (OSError, RuntimeError, ValueError) as exc:
                    store.mark_outbox_message_failed(
                        workspace_token,
                        outbox_id,
                        error_message=str(exc),
                        failed_at=now_iso(),
                    )
                    failed += 1
                    failed_outbox_ids.append(outbox_id)

            self._metrics.record_outbox_relay(sent=sent, failed=failed)
            self._observe("outbox_relay", (time.monotonic() - t0) * 1000.0)
            return {
                "workspace": workspace_token,
                "scanned": len(rows),
                "sent": sent,
                "failed": failed,
                "sent_outbox_ids": tuple(sent_outbox_ids),
                "failed_outbox_ids": tuple(failed_outbox_ids),
            }

    # ---- Query --------------------------------------------------------------

    def query_status(self, query: QueryTaskMarketStatusV1) -> TaskMarketStatusResultV1:
        with self._workspace_lock(query.workspace):
            store = self._get_store(query.workspace)
            items = store.load_items()
            rows: list[dict[str, Any]] = []
            counts: dict[str, int] = {}

            for item in items.values():
                counts[item.status] = counts.get(item.status, 0) + 1
                if query.stage and item.stage != query.stage:
                    continue
                if query.status and item.status != query.status:
                    continue
                payload = item.to_dict()
                if not query.include_payload:
                    payload["payload"] = {}
                rows.append(payload)

            rows.sort(
                key=lambda entry: (
                    PRIORITY_WEIGHT.get(str(entry.get("priority") or "medium").lower(), 1),
                    str(entry.get("updated_at") or ""),
                ),
                reverse=True,
            )
            limited = tuple(rows[: query.limit])

            return TaskMarketStatusResultV1(
                workspace=query.workspace,
                total=len(rows),
                counts=counts,
                items=limited,
            )

    # ---- Revision / Change Order -------------------------------------------

    def register_plan_revision(self, command: RegisterPlanRevisionCommandV1) -> PlanRevisionResultV1:
        t0 = time.monotonic()
        with self._workspace_lock(command.workspace):
            store = self._get_store(command.workspace)
            record: dict[str, object] = {
                "workspace": command.workspace,
                "plan_id": command.plan_id,
                "plan_revision_id": command.plan_revision_id,
                "parent_revision_id": command.parent_revision_id,
                "source_role": command.source_role,
                "requirement_digest": command.requirement_digest,
                "constraint_digest": command.constraint_digest,
                "metadata": dict(command.metadata),
                "created_at": now_iso(),
            }
            store.upsert_plan_revision(record)
            self._emit_fact(
                workspace=command.workspace,
                event_type="task_market.plan_revision_registered",
                run_id=command.plan_revision_id,
                task_id=command.plan_id,
                payload={
                    "plan_id": command.plan_id,
                    "plan_revision_id": command.plan_revision_id,
                    "parent_revision_id": command.parent_revision_id,
                    "source_role": command.source_role,
                },
            )
            self._observe("revision_register", (time.monotonic() - t0) * 1000.0, task_id=command.plan_id)
            return PlanRevisionResultV1(
                ok=True,
                workspace=command.workspace,
                plan_id=command.plan_id,
                plan_revision_id=command.plan_revision_id,
                parent_revision_id=command.parent_revision_id,
                reason="registered",
            )

    def query_plan_revisions(self, query: QueryPlanRevisionsV1) -> tuple[dict[str, Any], ...]:
        with self._workspace_lock(query.workspace):
            store = self._get_store(query.workspace)
            rows = store.load_plan_revisions(
                query.workspace,
                plan_id=query.plan_id,
                limit=query.limit,
            )
            return tuple(rows)

    def submit_change_order(self, command: SubmitChangeOrderCommandV1) -> ChangeOrderResultV1:
        t0 = time.monotonic()
        with self._workspace_lock(command.workspace):
            store = self._get_store(command.workspace)
            items = store.load_items()
            impacted_ids = set(command.affected_task_ids)
            candidates = [
                item
                for item in items.values()
                if item.plan_id == command.plan_id
                and item.plan_revision_id == command.from_revision_id
                and (not impacted_ids or item.task_id in impacted_ids)
            ]

            impact_counts: dict[str, int] = {}
            affected_task_ids: list[str] = []
            current_time = now_iso()
            change_transitions: list[dict[str, Any]] = []

            for item in candidates:
                impact = self._apply_change_order_impact(
                    item=item,
                    to_revision_id=command.to_revision_id,
                    change_type=command.change_type,
                    current_time=current_time,
                )
                impact_counts[impact] = impact_counts.get(impact, 0) + 1
                if impact != "unaffected":
                    affected_task_ids.append(item.task_id)
                    items[item.task_id] = item
                    change_transitions.append(
                        {
                            "task_id": item.task_id,
                            "from_status": item.status,
                            "to_status": item.status,
                            "event_type": "change_order_applied",
                            "worker_id": command.source_role,
                            "lease_token": "",
                            "version": item.version,
                            "metadata": {
                                "change_type": command.change_type,
                                "from_revision_id": command.from_revision_id,
                                "to_revision_id": command.to_revision_id,
                                "impact": impact,
                            },
                        }
                    )

            outbox = self._build_outbox_record(
                workspace=command.workspace,
                event_type="task_market.change_order_submitted",
                run_id=command.to_revision_id,
                task_id=command.plan_id,
                payload={
                    "plan_id": command.plan_id,
                    "from_revision_id": command.from_revision_id,
                    "to_revision_id": command.to_revision_id,
                    "change_type": command.change_type,
                    "impacted_total": len(affected_task_ids),
                    "impact_counts": impact_counts,
                },
            )

            store.save_items_and_outbox_atomic(
                items=items,
                transitions=change_transitions,
                outbox_records=[outbox],
            )

            # Ensure target revision exists in registry.
            store.upsert_plan_revision(
                {
                    "workspace": command.workspace,
                    "plan_id": command.plan_id,
                    "plan_revision_id": command.to_revision_id,
                    "parent_revision_id": command.from_revision_id,
                    "source_role": command.source_role,
                    "requirement_digest": "",
                    "constraint_digest": "",
                    "metadata": {"registered_via": "change_order"},
                    "created_at": current_time,
                }
            )

            change_order_record: dict[str, object] = {
                "workspace": command.workspace,
                "plan_id": command.plan_id,
                "from_revision_id": command.from_revision_id,
                "to_revision_id": command.to_revision_id,
                "change_type": command.change_type,
                "source_role": command.source_role,
                "summary": command.summary,
                "trace_id": command.trace_id,
                "affected_task_ids": affected_task_ids,
                "impact_counts": impact_counts,
                "metadata": dict(command.metadata),
                "created_at": current_time,
            }
            store.append_change_order(change_order_record)

            self._observe("change_order", (time.monotonic() - t0) * 1000.0, task_id=command.plan_id)
            return ChangeOrderResultV1(
                ok=True,
                workspace=command.workspace,
                plan_id=command.plan_id,
                from_revision_id=command.from_revision_id,
                to_revision_id=command.to_revision_id,
                change_type=command.change_type,
                impacted_total=len(affected_task_ids),
                impact_counts=impact_counts,
                affected_task_ids=tuple(affected_task_ids),
                reason="change_order_applied",
            )

    def query_change_orders(self, query: QueryChangeOrdersV1) -> tuple[dict[str, Any], ...]:
        with self._workspace_lock(query.workspace):
            store = self._get_store(query.workspace)
            rows = store.load_change_orders(
                query.workspace,
                plan_id=query.plan_id,
                limit=query.limit,
            )
            return tuple(rows)

    # ---- Saga Compensation -------------------------------------------------

    def register_compensation_action(
        self,
        *,
        workspace: str,
        task_id: str,
        lease_token: str,
        action: dict[str, Any],
    ) -> dict[str, Any]:
        workspace_token = str(workspace or "").strip()
        if not workspace_token:
            raise TaskMarketError("workspace is required", code="workspace_required")

        with self._workspace_lock(workspace_token):
            store = self._get_store(workspace_token)
            items = store.load_items()
            item = self._require_item(items, task_id)
            LeaseManager(store).validate_token(item, lease_token)
            action_model = CompensationAction.from_mapping(action)
            metadata = dict(item.metadata)
            state = SagaCompensator().register_action(metadata, action_model)
            item.metadata = metadata
            item.version += 1
            item.updated_at = now_iso()
            items[item.task_id] = item

            transition = {
                "task_id": item.task_id,
                "from_status": item.status,
                "to_status": item.status,
                "event_type": "saga_action_registered",
                "worker_id": item.claimed_by or "",
                "lease_token": item.lease_token,
                "version": item.version,
                "metadata": {
                    "trace_id": item.trace_id,
                    "action_type": action_model.action_type,
                    "target": action_model.target,
                },
            }
            outbox = self._build_outbox_record(
                workspace=workspace_token,
                event_type="task_market.saga_action_registered",
                run_id=item.run_id,
                task_id=item.task_id,
                payload={
                    "trace_id": item.trace_id,
                    "action_type": action_model.action_type,
                    "target": action_model.target,
                },
            )
            store.save_items_and_outbox_atomic(
                items=items,
                transitions=[transition],
                outbox_records=[outbox],
            )
            return {
                "ok": True,
                "task_id": item.task_id,
                "registered_actions": len(state.get("actions", [])),
            }

    def commit_compensation_actions(
        self,
        *,
        workspace: str,
        task_id: str,
        lease_token: str,
    ) -> dict[str, Any]:
        workspace_token = str(workspace or "").strip()
        if not workspace_token:
            raise TaskMarketError("workspace is required", code="workspace_required")

        with self._workspace_lock(workspace_token):
            store = self._get_store(workspace_token)
            items = store.load_items()
            item = self._require_item(items, task_id)
            LeaseManager(store).validate_token(item, lease_token)
            metadata = dict(item.metadata)
            state = SagaCompensator().commit(metadata)
            item.metadata = metadata
            item.version += 1
            item.updated_at = now_iso()
            items[item.task_id] = item

            transition = {
                "task_id": item.task_id,
                "from_status": item.status,
                "to_status": item.status,
                "event_type": "saga_committed",
                "worker_id": item.claimed_by or "",
                "lease_token": item.lease_token,
                "version": item.version,
                "metadata": {
                    "trace_id": item.trace_id,
                    "actions": len(state.get("actions", [])),
                    "committed": bool(state.get("committed", False)),
                },
            }
            outbox = self._build_outbox_record(
                workspace=workspace_token,
                event_type="task_market.saga_committed",
                run_id=item.run_id,
                task_id=item.task_id,
                payload={
                    "trace_id": item.trace_id,
                    "actions": len(state.get("actions", [])),
                },
            )
            store.save_items_and_outbox_atomic(
                items=items,
                transitions=[transition],
                outbox_records=[outbox],
            )
            return {
                "ok": True,
                "task_id": item.task_id,
                "committed": bool(state.get("committed", False)),
                "actions": len(state.get("actions", [])),
            }

    def compensate_task(
        self,
        *,
        workspace: str,
        task_id: str,
        reason: str,
        initiator: str = "manual",
    ) -> dict[str, Any]:
        workspace_token = str(workspace or "").strip()
        if not workspace_token:
            raise TaskMarketError("workspace is required", code="workspace_required")

        with self._workspace_lock(workspace_token):
            store = self._get_store(workspace_token)
            items = store.load_items()
            item = self._require_item(items, task_id)
            summary = self._compensate_task_no_lock(
                workspace=workspace_token,
                store=store,
                items=items,
                item=item,
                reason=reason,
                initiator=initiator,
            )
            store.save_items(items)
            return summary

    # ---- Reconciliation ----------------------------------------------------

    def reconcile_parent_statuses(self, workspace: str, *, limit: int = 5000) -> dict[str, Any]:
        """Reconcile parent task status with current child aggregate state.

        This method is designed for event-driven race recovery (late/out-of-order
        messages) and can be called by a periodic loop.
        """
        t0 = time.monotonic()
        workspace_token = str(workspace or "").strip()
        if not workspace_token:
            raise TaskMarketError("workspace is required", code="workspace_required")

        with self._workspace_lock(workspace_token):
            store = self._get_store(workspace_token)
            items = store.load_items()
            parent_items = [item for item in items.values() if not item.is_leaf][: max(0, int(limit))]
            children_by_parent: dict[str, list[TaskWorkItemRecord]] = {}
            for candidate in items.values():
                parent_task_id = str(candidate.parent_task_id or "").strip()
                if not parent_task_id:
                    continue
                children_by_parent.setdefault(parent_task_id, []).append(candidate)

            updated_parent_ids: list[str] = []
            reconciliation_transitions: list[dict[str, Any]] = []
            reconciliation_outbox: list[dict[str, Any]] = []
            scanned = 0
            for parent in parent_items:
                scanned += 1
                children = children_by_parent.get(parent.task_id, [])
                if not children:
                    continue

                expected_status, expected_stage = self._compute_expected_parent_state(children)
                if parent.status == expected_status and (not expected_stage or parent.stage == expected_stage):
                    continue

                previous_status = parent.status
                previous_stage = parent.stage
                parent.status = expected_status
                if expected_stage:
                    parent.stage = expected_stage
                LeaseManager(store).clear_lease(parent)
                parent.version += 1
                parent.updated_at = now_iso()
                parent.metadata = dict(parent.metadata)
                child_status_counts = dict(Counter(child.status for child in children))
                parent.metadata["reconciled_from_children_at"] = parent.updated_at
                parent.metadata["reconciled_child_status_counts"] = child_status_counts
                parent.metadata["reconciled_expected_status"] = expected_status
                if expected_stage:
                    parent.metadata["reconciled_expected_stage"] = expected_stage

                items[parent.task_id] = parent
                updated_parent_ids.append(parent.task_id)

                reconciliation_transitions.append(
                    {
                        "task_id": parent.task_id,
                        "from_status": previous_status,
                        "to_status": parent.status,
                        "event_type": "reconciled",
                        "worker_id": "task_reconciler",
                        "lease_token": "",
                        "version": parent.version,
                        "metadata": {
                            "trace_id": parent.trace_id,
                            "from_stage": previous_stage,
                            "to_stage": parent.stage,
                            "child_status_counts": child_status_counts,
                        },
                    }
                )
                reconciliation_outbox.append(
                    self._build_outbox_record(
                        workspace=workspace_token,
                        event_type="task_market.parent_reconciled",
                        run_id=parent.run_id,
                        task_id=parent.task_id,
                        payload={
                            "trace_id": parent.trace_id,
                            "from_status": previous_status,
                            "to_status": parent.status,
                            "from_stage": previous_stage,
                            "to_stage": parent.stage,
                            "child_status_counts": child_status_counts,
                        },
                    )
                )

            if updated_parent_ids:
                store.save_items_and_outbox_atomic(
                    items=items,
                    transitions=reconciliation_transitions,
                    outbox_records=reconciliation_outbox,
                )

            self._observe("reconcile", (time.monotonic() - t0) * 1000.0)
            return {
                "workspace": workspace_token,
                "scanned": scanned,
                "updated": len(updated_parent_ids),
                "updated_parent_ids": tuple(updated_parent_ids),
            }

    # ---- Consumer Loops (Durable Pull-Consumer) ----------------------------

    def start_consumer_loops(
        self,
        workspace: str,
        *,
        poll_interval: float | None = None,
        consumer_types: dict[str, type] | None = None,
    ) -> bool:
        """Start durable consumer daemon threads for a workspace.

        Spawns CE, Director, QA consumer threads and an outbox relay thread.
        Returns ``True`` if started, ``False`` if already running.
        """
        from .consumer_loop import ConsumerLoopManager

        workspace_token = str(workspace or "").strip()
        if not workspace_token:
            raise TaskMarketError("workspace is required", code="workspace_required")

        with self._consumer_loop_managers_guard:
            existing = self._consumer_loop_managers.get(workspace_token)
            if existing is not None and existing.is_running():
                return False
            manager = ConsumerLoopManager(
                workspace_token,
                poll_interval=poll_interval,
            )
            manager.start(consumer_types=consumer_types, service=self)
            self._consumer_loop_managers[workspace_token] = manager
            return True

    def stop_consumer_loops(self, workspace: str) -> bool:
        """Stop durable consumer daemon threads for a workspace.

        Returns ``True`` if a running manager was stopped, ``False`` otherwise.
        """
        workspace_token = str(workspace or "").strip()
        if not workspace_token:
            raise TaskMarketError("workspace is required", code="workspace_required")

        with self._consumer_loop_managers_guard:
            manager = self._consumer_loop_managers.pop(workspace_token, None)
        if manager is None:
            return False
        manager.stop()
        return True

    def stop_all_consumer_loops(self) -> int:
        """Stop all running consumer loop managers. Returns count stopped."""
        with self._consumer_loop_managers_guard:
            entries = tuple(self._consumer_loop_managers.items())
            self._consumer_loop_managers.clear()
        for _, manager in entries:
            manager.stop()
        return len(entries)

    def query_consumer_loop_status(self, workspace: str) -> dict[str, Any]:
        """Return consumer loop status for a workspace."""
        workspace_token = str(workspace or "").strip()
        if not workspace_token:
            raise TaskMarketError("workspace is required", code="workspace_required")

        with self._consumer_loop_managers_guard:
            manager = self._consumer_loop_managers.get(workspace_token)
        if manager is None:
            return {
                "workspace": workspace_token,
                "started": False,
                "is_running": False,
                "roles": {},
                "outbox_relay_running": False,
            }
        return manager.status()

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

    # ---- Drift-Driven Requeue ---------------------------------------------

    def requeue_drifted_items(self, workspace: str) -> dict[str, Any]:
        """Detect revision drift and auto-requeue drifted items to pending_design.

        Called by the reconciliation loop to converge item revision state with
        the latest registered plan revision.
        """
        t0 = time.monotonic()
        workspace_token = str(workspace or "").strip()
        if not workspace_token:
            raise TaskMarketError("workspace is required", code="workspace_required")

        with self._workspace_lock(workspace_token):
            store = self._get_store(workspace_token)
            items = store.load_items()

            # Build plan_id -> items mapping.
            items_by_plan: dict[str, list[TaskWorkItemRecord]] = {}
            for item in items.values():
                plan_key = str(item.plan_id or "").strip()
                if not plan_key:
                    continue
                items_by_plan.setdefault(plan_key, []).append(item)

            # Load latest revision per plan.
            latest_revision_by_plan: dict[str, str] = {}
            for plan_key in items_by_plan:
                revisions = store.load_plan_revisions(
                    workspace_token,
                    plan_id=plan_key,
                    limit=1,
                )
                if revisions:
                    rev_id = str(revisions[0].get("plan_revision_id") or "").strip()
                    if rev_id:
                        latest_revision_by_plan[plan_key] = rev_id

            # Identify and requeue drifted items.
            requeued_ids: list[str] = []
            requeue_transitions: list[dict[str, Any]] = []
            requeue_outbox: list[dict[str, Any]] = []

            for plan_key, plan_items in items_by_plan.items():
                latest = latest_revision_by_plan.get(plan_key, "")
                if not latest:
                    continue
                for item in plan_items:
                    if item.status in TERMINAL_STATUSES:
                        continue
                    if not item.plan_revision_id or item.plan_revision_id == latest:
                        continue
                    if item.status == "dead_letter":
                        continue

                    previous_status = item.status
                    previous_stage = item.stage

                    # Requeue to pending_design.
                    item.stage = "pending_design"
                    item.status = "pending_design"
                    LeaseManager(store).clear_lease(item)
                    item.metadata = dict(item.metadata)
                    item.metadata["drift_requeue_reason"] = "revision_drift"
                    item.metadata["drift_requeue_from_revision"] = item.plan_revision_id
                    item.metadata["drift_requeue_to_revision"] = latest
                    item.metadata["drift_requeued_at"] = now_iso()
                    item.plan_revision_id = latest
                    item.version += 1
                    item.updated_at = now_iso()

                    items[item.task_id] = item
                    requeued_ids.append(item.task_id)

                    requeue_transitions.append(
                        {
                            "task_id": item.task_id,
                            "from_status": previous_status,
                            "to_status": item.status,
                            "event_type": "revision_drift_requeued",
                            "worker_id": "drift_reconciler",
                            "lease_token": "",
                            "version": item.version,
                            "metadata": {
                                "trace_id": item.trace_id,
                                "from_stage": previous_stage,
                                "to_stage": item.stage,
                                "from_revision": item.metadata["drift_requeue_from_revision"],
                                "to_revision": latest,
                            },
                        }
                    )
                    requeue_outbox.append(
                        self._build_outbox_record(
                            workspace=workspace_token,
                            event_type="task_market.revision_drift_requeued",
                            run_id=item.run_id,
                            task_id=item.task_id,
                            payload={
                                "trace_id": item.trace_id,
                                "from_status": previous_status,
                                "to_status": item.status,
                                "from_revision": item.metadata["drift_requeue_from_revision"],
                                "to_revision": latest,
                            },
                        )
                    )

            if requeued_ids:
                store.save_items_and_outbox_atomic(
                    items=items,
                    transitions=requeue_transitions,
                    outbox_records=requeue_outbox,
                )

            self._observe("drift_requeue", (time.monotonic() - t0) * 1000.0)
            return {
                "workspace": workspace_token,
                "requeued_count": len(requeued_ids),
                "requeued_ids": tuple(requeued_ids),
            }

    # ---- Revision Drift Detection -----------------------------------------

    def detect_revision_drift(
        self,
        workspace: str,
        *,
        plan_id: str = "",
    ) -> dict[str, Any]:
        """Detect work items whose plan_revision_id lags behind the latest revision.

        Returns a summary with drifted item details and latest revision per plan.
        """
        t0 = time.monotonic()
        workspace_token = str(workspace or "").strip()
        if not workspace_token:
            raise TaskMarketError("workspace is required", code="workspace_required")

        with self._workspace_lock(workspace_token):
            store = self._get_store(workspace_token)
            items = store.load_items()

            # Build plan_id -> items mapping.
            items_by_plan: dict[str, list[TaskWorkItemRecord]] = {}
            for item in items.values():
                plan_key = str(item.plan_id or "").strip()
                if not plan_key:
                    continue
                if plan_id and plan_key != plan_id:
                    continue
                items_by_plan.setdefault(plan_key, []).append(item)

            # Load latest revision per plan.
            latest_revision_by_plan: dict[str, str] = {}
            for plan_key in items_by_plan:
                revisions = store.load_plan_revisions(
                    workspace_token,
                    plan_id=plan_key,
                    limit=1,
                )
                if revisions:
                    rev_id = str(revisions[0].get("plan_revision_id") or "").strip()
                    if rev_id:
                        latest_revision_by_plan[plan_key] = rev_id

            # Detect drift.
            drifted_items: list[dict[str, Any]] = []
            for plan_key, plan_items in items_by_plan.items():
                latest = latest_revision_by_plan.get(plan_key, "")
                if not latest:
                    continue
                for item in plan_items:
                    if item.status in TERMINAL_STATUSES:
                        continue
                    if item.plan_revision_id and item.plan_revision_id != latest:
                        drifted_items.append(
                            {
                                "task_id": item.task_id,
                                "plan_id": item.plan_id,
                                "current_revision": item.plan_revision_id,
                                "latest_revision": latest,
                                "status": item.status,
                                "stage": item.stage,
                            }
                        )

            self._observe("revision_drift", (time.monotonic() - t0) * 1000.0)
            return {
                "workspace": workspace_token,
                "plan_id_filter": plan_id,
                "drifted_count": len(drifted_items),
                "drifted_items": tuple(drifted_items),
                "latest_revision_by_plan": latest_revision_by_plan,
            }

    # ---- Read-Only Impact Analyzer -----------------------------------------

    def analyze_change_order_impact(
        self,
        workspace: str,
        *,
        plan_id: str,
        from_revision_id: str,
        to_revision_id: str,
        change_type: str = "scope_change",
        affected_task_ids: tuple[str, ...] = (),
    ) -> dict[str, Any]:
        """Preview the impact of a change order without mutating any state.

        This is a read-only version of the logic in ``submit_change_order``
        and ``_apply_change_order_impact``.
        """
        t0 = time.monotonic()
        workspace_token = str(workspace or "").strip()
        if not workspace_token:
            raise TaskMarketError("workspace is required", code="workspace_required")

        with self._workspace_lock(workspace_token):
            store = self._get_store(workspace_token)
            items = store.load_items()
            impacted_ids = set(affected_task_ids)

            candidates = [
                item
                for item in items.values()
                if item.plan_id == plan_id
                and item.plan_revision_id == from_revision_id
                and (not impacted_ids or item.task_id in impacted_ids)
            ]

            impact_counts: dict[str, int] = {}
            preview_items: list[dict[str, Any]] = []
            for item in candidates:
                impact = self._classify_impact(item.status)
                impact_counts[impact] = impact_counts.get(impact, 0) + 1
                preview_items.append(
                    {
                        "task_id": item.task_id,
                        "status": item.status,
                        "stage": item.stage,
                        "impact": impact,
                    }
                )

            self._observe("impact_analyze", (time.monotonic() - t0) * 1000.0)
            return {
                "workspace": workspace_token,
                "plan_id": plan_id,
                "from_revision_id": from_revision_id,
                "to_revision_id": to_revision_id,
                "change_type": change_type,
                "candidates_total": len(candidates),
                "impact_counts": impact_counts,
                "preview_items": tuple(preview_items),
            }

    # ---- DAG Validator (Cycle Detection) -----------------------------------

    def validate_dependency_dag(
        self,
        workspace: str,
        *,
        plan_id: str = "",
    ) -> dict[str, Any]:
        """Validate the depends_on graph for cycles and orphan references.

        Uses DFS with white/gray/black coloring to detect cycles.
        """
        t0 = time.monotonic()
        workspace_token = str(workspace or "").strip()
        if not workspace_token:
            raise TaskMarketError("workspace is required", code="workspace_required")

        with self._workspace_lock(workspace_token):
            store = self._get_store(workspace_token)
            items = store.load_items()

            if plan_id:
                items = {tid: item for tid, item in items.items() if item.plan_id == plan_id}

            # Build adjacency: task_id -> depends_on task_ids
            all_ids = set(items.keys())
            adjacency: dict[str, list[str]] = {}
            for item in items.values():
                adjacency[item.task_id] = [dep for dep in item.depends_on if dep]

            # Detect orphan references (depends_on points to non-existent task).
            orphan_depends_on: list[str] = [dep for deps in adjacency.values() for dep in deps if dep not in all_ids]

            # DFS cycle detection.
            _white, _gray, _black = 0, 1, 2
            color: dict[str, int] = dict.fromkeys(all_ids, _white)
            cycles: list[list[str]] = []

            def dfs(node: str, path: list[str]) -> None:
                color[node] = _gray
                path.append(node)
                for neighbor in adjacency.get(node, []):
                    if neighbor not in all_ids:
                        continue  # orphan, skip
                    if color[neighbor] == _gray:
                        # Found cycle — extract it.
                        cycle_start = path.index(neighbor)
                        cycles.append(list(path[cycle_start:]))
                    elif color[neighbor] == _white:
                        dfs(neighbor, path)
                path.pop()
                color[node] = _black

            for tid in all_ids:
                if color[tid] == _white:
                    dfs(tid, [])

            is_valid = len(cycles) == 0 and len(orphan_depends_on) == 0

            self._observe("dag_validate", (time.monotonic() - t0) * 1000.0)
            return {
                "workspace": workspace_token,
                "plan_id_filter": plan_id,
                "valid": is_valid,
                "cycle_count": len(cycles),
                "cycles": tuple(tuple(c) for c in cycles),
                "orphan_depends_on": tuple(orphan_depends_on),
                "total_nodes": len(all_ids),
                "total_edges": sum(len(deps) for deps in adjacency.values()),
            }

    # ---- Helpers -----------------------------------------------------------

    def _observe(
        self,
        operation: str,
        duration_ms: float,
        *,
        stage: str = "",
        ok: bool = True,
        task_id: str = "",
        trace_id: str = "",
    ) -> None:
        """Record operation metrics, structured logging, and OTel span."""
        self._metrics.record_operation(operation, duration_ms, stage=stage, ok=ok)
        logger.info(
            "task_market %s: task_id=%s stage=%s trace_id=%s ok=%s duration_ms=%.1f",
            operation,
            task_id,
            stage,
            trace_id,
            ok,
            duration_ms,
        )
        # OTel span — records operation as a span event on the current span
        # (if a parent span exists) or as a standalone span.
        if self._tracer.enabled:
            with self._tracer.start_span(
                f"task_market.{operation}",
                {
                    "task_id": task_id,
                    "stage": stage,
                    "trace_id": trace_id,
                    "ok": str(ok),
                    "duration_ms": duration_ms,
                },
            ):
                pass  # Span is opened and immediately closed — records the event.

    def _require_item(self, items: dict[str, TaskWorkItemRecord], task_id: str) -> TaskWorkItemRecord:
        item = items.get(str(task_id or "").strip())
        if item is None:
            raise TaskNotFoundError(
                f"Task not found: {task_id}",
                task_id=task_id,
            )
        return item

    @staticmethod
    def _classify_impact(status: str) -> str:
        """Classify the impact of a change order on a task based on its current status (read-only)."""
        if status == "resolved":
            return "needs_revalidation"
        if status in _IN_PROGRESS_STATUSES:
            return "cancel_requested"
        if status in {"pending_design", "pending_exec", "pending_qa"}:
            return "superseded"
        if status == "waiting_human":
            return "retained_waiting_human"
        return "unaffected"

    def _apply_change_order_impact(
        self,
        *,
        item: TaskWorkItemRecord,
        to_revision_id: str,
        change_type: str,
        current_time: str,
    ) -> str:
        item.metadata = dict(item.metadata)
        item.metadata["change_order_type"] = change_type
        item.metadata["change_order_applied_at"] = current_time
        item.metadata["change_order_to_revision"] = to_revision_id

        impact = self._classify_impact(item.status)

        if impact == "needs_revalidation":
            item.metadata["change_order_state"] = impact
            item.version += 1
            item.updated_at = current_time
            return impact
        if impact == "cancel_requested":
            item.superseded_by_revision = to_revision_id
            item.metadata["change_order_state"] = impact
            item.version += 1
            item.updated_at = current_time
            return impact
        if impact == "superseded":
            item.superseded_by_revision = to_revision_id
            item.metadata["change_order_state"] = impact
            item.version += 1
            item.updated_at = current_time
            return impact
        if impact == "retained_waiting_human":
            item.metadata["change_order_state"] = impact
            item.version += 1
            item.updated_at = current_time
            return impact
        return "unaffected"

    def _compute_expected_parent_state(
        self,
        children: list[TaskWorkItemRecord],
    ) -> tuple[str, str]:
        statuses = {child.status for child in children}

        if statuses and statuses <= {"resolved"}:
            return "resolved", ""
        if "dead_letter" in statuses:
            return "dead_letter", ""
        if "rejected" in statuses:
            return "rejected", ""
        if "waiting_human" in statuses:
            return "waiting_human", "waiting_human"
        if statuses & _QA_STATUS_SET:
            return "in_qa", "pending_qa"
        if statuses & _EXECUTION_STATUS_SET:
            return "in_execution", "pending_exec"
        if statuses & _DESIGN_STATUS_SET:
            return "in_design", "pending_design"

        # Fallback for unexpected custom statuses.
        return "pending_design", "pending_design"

    def _compensate_task_no_lock(
        self,
        *,
        workspace: str,
        store: Any,
        items: dict[str, TaskWorkItemRecord],
        item: TaskWorkItemRecord,
        reason: str,
        initiator: str,
    ) -> dict[str, Any]:
        metadata = dict(item.metadata)
        summary = SagaCompensator().compensate(
            item_metadata=metadata,
            workspace=workspace,
            reason=reason,
            initiator=initiator,
        )
        item.metadata = metadata
        if not bool(summary.get("changed", False)):
            return {
                "task_id": item.task_id,
                **summary,
            }

        item.version += 1
        item.updated_at = now_iso()
        items[item.task_id] = item
        store.append_transition(
            task_id=item.task_id,
            from_status=item.status,
            to_status=item.status,
            event_type="saga_compensated",
            worker_id=initiator,
            lease_token=item.lease_token,
            version=item.version,
            metadata={
                "trace_id": item.trace_id,
                "reason": reason,
                "requires_manual_intervention": bool(summary.get("requires_manual_intervention", False)),
            },
        )
        self._emit_fact(
            workspace=workspace,
            event_type="task_market.saga_compensated",
            run_id=item.run_id,
            task_id=item.task_id,
            payload={
                "trace_id": item.trace_id,
                "reason": reason,
                "requires_manual_intervention": bool(summary.get("requires_manual_intervention", False)),
            },
        )
        return {
            "task_id": item.task_id,
            **summary,
        }

    def _compensate_children_for_parent_failure(
        self,
        *,
        workspace: str,
        store: Any,
        items: dict[str, TaskWorkItemRecord],
        parent_task_id: str,
        reason: str,
    ) -> dict[str, Any]:
        affected_statuses = {"resolved", "in_execution", "pending_qa", "in_qa"}
        child_items = [
            child
            for child in items.values()
            if child.parent_task_id == parent_task_id and child.status in affected_statuses
        ]
        summaries: list[dict[str, Any]] = []
        requires_manual = False
        for child in child_items:
            summary = self._compensate_task_no_lock(
                workspace=workspace,
                store=store,
                items=items,
                item=child,
                reason=reason,
                initiator="parent_failure",
            )
            summaries.append(summary)
            if bool(summary.get("requires_manual_intervention", False)):
                requires_manual = True
        return {
            "parent_task_id": parent_task_id,
            "child_count": len(child_items),
            "compensation_summaries": tuple(summaries),
            "requires_manual_intervention": requires_manual,
        }

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

        review = HumanReviewManager(store).create_review_request(
            task_id=task_id,
            trace_id=item.trace_id,
            workspace=workspace,
            reason=reason,
            escalation_policy="tri_council",
            requested_by=requested_by,
        )

        items = store.load_items()
        item = self._require_item(items, task_id)
        if item.version == previous_version and item.status == previous_status and item.stage == previous_stage:
            return {"item": item, "review": review}
        store.append_transition(
            task_id=item.task_id,
            from_status=previous_status,
            to_status=item.status,
            event_type="human_review_requested",
            worker_id=requested_by,
            lease_token="",
            version=item.version,
            metadata={
                "trace_id": item.trace_id,
                "reason": reason,
                "from_stage": previous_stage,
                "to_stage": item.stage,
                "escalation_policy": review.get("escalation_policy", "tri_council"),
                "next_role": review.get("next_role", get_next_escalation_role("director") or ""),
            },
        )
        self._emit_fact(
            workspace=workspace,
            event_type="task_market.human_review_requested",
            run_id=item.run_id,
            task_id=item.task_id,
            payload={
                "trace_id": item.trace_id,
                "reason": reason,
                "requested_by": requested_by,
                "from_stage": previous_stage,
                "to_stage": item.stage,
                "escalation_policy": review.get("escalation_policy", "tri_council"),
                "next_role": review.get("next_role", get_next_escalation_role("director") or ""),
            },
        )
        return {"item": item, "review": review}

    def _select_claim_candidate(
        self,
        *,
        items: dict[str, TaskWorkItemRecord],
        stage: str,
        task_id_filter: str | None,
        at_epoch: float,
    ) -> TaskWorkItemRecord | None:
        if task_id_filter:
            item = items.get(task_id_filter)
            if item is None:
                return None
            return item if item.is_claimable(stage, at_epoch=at_epoch) else None

        candidates = [item for item in items.values() if item.is_claimable(stage, at_epoch=at_epoch)]
        if not candidates:
            return None

        candidates.sort(
            key=lambda item: (
                PRIORITY_WEIGHT.get(item.priority, 1),
                item.created_at,
                item.task_id,
            ),
            reverse=True,
        )
        return candidates[0]

    def _result_from_item(
        self,
        item: TaskWorkItemRecord,
        *,
        ok: bool = True,
        lease_token: str = "",
        reason: str = "",
    ) -> TaskWorkItemResultV1:
        return TaskWorkItemResultV1(
            ok=ok,
            task_id=item.task_id,
            stage=item.stage,
            status=item.status,
            version=item.version,
            trace_id=item.trace_id,
            run_id=item.run_id,
            lease_token=lease_token or item.lease_token,
            reason=reason,
            payload=item.payload,
        )

    def _maybe_emit_webhook(
        self,
        *,
        workspace: str,
        run_id: str,
        task_id: str,
        action: str,
        callback_url: str,
        current_role: str,
        review: dict[str, Any],
    ) -> None:
        """Emit a webhook outbox record if callback_url is provided."""
        url = str(callback_url or "").strip()
        if not url:
            return
        outbox = self._build_outbox_record(
            workspace=workspace,
            event_type="task_market.human_review_callback",
            run_id=run_id,
            task_id=task_id,
            payload={
                "callback_url": url,
                "task_id": task_id,
                "action": action,
                "current_role": current_role,
                "review": review,
            },
        )
        try:
            store = self._get_store(workspace)
            store.append_outbox_message(outbox)
        except (OSError, RuntimeError, ValueError) as exc:
            logger.warning(
                "task_market webhook outbox append failed: task_id=%s action=%s error=%s",
                task_id,
                action,
                exc,
            )

    def _build_outbox_record(
        self,
        *,
        workspace: str,
        event_type: str,
        run_id: str,
        task_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        """Build an outbox record for later atomic write or direct append."""
        return {
            "outbox_id": uuid.uuid4().hex,
            "workspace": workspace,
            "stream": "task_market.events",
            "event_type": event_type,
            "source": "runtime.task_market",
            "run_id": run_id,
            "task_id": task_id,
            "payload": dict(payload),
            "status": "pending",
            "attempts": 0,
            "last_error": "",
            "created_at": now_iso(),
            "failed_at": "",
            "delivered_at": "",
        }

    def _emit_fact(
        self,
        *,
        workspace: str,
        event_type: str,
        run_id: str,
        task_id: str,
        payload: dict[str, Any],
    ) -> None:
        """Write an outbox record for async fact_stream delivery via relay.

        This method does NOT call append_fact_event inline - that would violate
        the outbox_atomic fitness rule. The outbox record is written to the store,
        and a relay process handles delivery to fact_stream.
        """
        outbox_id = uuid.uuid4().hex
        outbox_record: dict[str, Any] = {
            "outbox_id": outbox_id,
            "workspace": workspace,
            "stream": "task_market.events",
            "event_type": event_type,
            "source": "runtime.task_market",
            "run_id": run_id,
            "task_id": task_id,
            "payload": dict(payload),
            "status": "pending",
            "attempts": 0,
            "last_error": "",
            "created_at": now_iso(),
            "failed_at": "",
            "delivered_at": "",
        }
        try:
            store = self._get_store(workspace)
            store.append_outbox_message(outbox_record)
        except (OSError, RuntimeError, ValueError) as exc:
            logger.warning(
                "task_market outbox append failed: event_type=%s task_id=%s outbox_id=%s error=%s",
                event_type,
                task_id,
                outbox_id,
                exc,
            )
            return


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

_service_lock = threading.Lock()
_service_singleton: TaskMarketService | None = None


def get_task_market_service() -> TaskMarketService:
    global _service_singleton
    if _service_singleton is not None:
        return _service_singleton
    with _service_lock:
        if _service_singleton is None:
            _service_singleton = TaskMarketService()
        return _service_singleton


def reset_task_market_service() -> None:
    global _service_singleton
    with _service_lock:
        singleton = _service_singleton
        _service_singleton = None
    if singleton is not None:
        singleton.stop_all_consumer_loops()
        singleton.stop_all_reconciliation_loops()


__all__ = [
    "TaskMarketService",
    "get_task_market_service",
    "reset_task_market_service",
]
