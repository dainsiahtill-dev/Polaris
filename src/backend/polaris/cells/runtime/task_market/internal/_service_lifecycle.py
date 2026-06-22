"""Lease and stage-transition lifecycle for the task-market service facade.

``LifecycleMixin`` owns the hot path: publish / claim / renew / acknowledge /
fail / requeue / dead-letter / status-query, plus the claim-candidate selector
and the terminal-dependency cascade sweep. Bodies are moved verbatim from the
original ``service.py`` so transactional behaviour is preserved exactly.
"""

from __future__ import annotations

import logging
import time
from collections import Counter
from datetime import datetime, timezone
from typing import Any

from polaris.cells.runtime.task_market.public.contracts import (
    AcknowledgeTaskStageCommandV1,
    ClaimTaskWorkItemCommandV1,
    FailTaskStageCommandV1,
    MoveTaskToDeadLetterCommandV1,
    PublishTaskWorkItemCommandV1,
    QueryTaskMarketStatusV1,
    RenewTaskLeaseCommandV1,
    RequeueTaskCommandV1,
    TaskLeaseRenewResultV1,
    TaskMarketError,
    TaskMarketStatusResultV1,
    TaskWorkItemResultV1,
)

from ._service_base import ServiceBaseMixin
from .claim_readiness import design_claim_ready, exec_claim_ready
from .dlq import DLQManager
from .errors import (
    StaleLeaseTokenError,
    TaskMarketError as InternalTaskMarketError,
    TaskNotClaimableError,
)
from .fsm import PRIORITY_WEIGHT
from .lease_manager import LeaseManager
from .models import (
    TERMINAL_STATUSES,
    TaskWorkItemRecord,
    now_epoch,
    now_iso,
)

logger = logging.getLogger(__name__)
_IN_PROGRESS_STATUSES = {"in_design", "in_execution", "in_qa"}
_NON_CONSUMING_REQUEUE_ERROR_CODES = frozenset({"SCOPE_CONFLICT"})
_LEGACY_RESOLVED_REOPEN_SOURCES = frozenset({"pm_dispatch.integration_qa"})
# Statuses a depends_on dependency can never recover from (subset of
# models.TERMINAL_STATUSES minus "resolved"): dependents must cascade,
# not strand.
_DEPENDENCY_TERMINAL_FAILURE_STATUSES = frozenset({"rejected", "dead_letter"})

__all__ = [
    "_DEPENDENCY_TERMINAL_FAILURE_STATUSES",
    "_IN_PROGRESS_STATUSES",
    "_NON_CONSUMING_REQUEUE_ERROR_CODES",
    "LifecycleMixin",
]


class LifecycleMixin(ServiceBaseMixin):
    """Lease-aware publish / claim / acknowledge / fail / requeue path."""

    # ---- Publish ------------------------------------------------------------

    def publish_work_item(self, command: PublishTaskWorkItemCommandV1) -> TaskWorkItemResultV1:
        t0 = time.monotonic()
        self._maybe_start_reconciliation_loop(command.workspace)
        with self._workspace_lock(command.workspace):
            store = self._get_store(command.workspace)
            items = store.load_items()
            item = items.get(command.task_id)

            if item is None:
                expected_versions = {command.task_id: 0}
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
                expected_versions = {item.task_id: int(item.version)}
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
            lifecycle_evidence = self._record_cognitive_runtime_lifecycle_receipt(
                item=item,
                event_type="published",
                from_status="",
                to_status=item.status,
                metadata={
                    "source_role": command.source_role,
                    "priority": item.priority,
                },
            )
            self._attach_lifecycle_evidence(
                item=item,
                transition=transition,
                outbox_record=outbox_record,
                evidence=lifecycle_evidence,
            )
            items[item.task_id] = item

            self._atomic_save_changed_items(
                store=store,
                items=items,
                transitions=[transition],
                outbox_records=[outbox_record],
                expected_versions=expected_versions,
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

            # Queue-scan claims first sweep terminally-stranded dependents
            # into the DLQ (targeted claims are supervision paths and must
            # see the market as-is). A sweep failure must never poison the
            # claim itself: discard partial in-memory mutations and claim on
            # pristine state — the idempotent DLQ append lets the next sweep
            # redo the work consistently.
            if not command.task_id:
                try:
                    (
                        cascade_transitions,
                        cascade_outbox,
                        cascade_expected_versions,
                        cascade_dead_letters,
                    ) = self._cascade_dead_letter_dependents(
                        store=store,
                        items=items,
                        worker_id=command.worker_id,
                    )
                    if cascade_transitions:
                        self._atomic_save_changed_items(
                            store=store,
                            items=items,
                            transitions=cascade_transitions,
                            outbox_records=cascade_outbox,
                            expected_versions=cascade_expected_versions,
                            dead_letter_records=cascade_dead_letters,
                        )
                except (TaskMarketError, InternalTaskMarketError, OSError) as exc:
                    logger.warning("dependency cascade sweep failed; claiming on pristine state: %s", exc)
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
                # Capture before move_to_dead_letter mutates the item — the
                # audit trail otherwise records dead_letter→dead_letter
                # (live I3-r9 forensics).
                exhausted_from_status = selected.status
                selected_expected_version = int(selected.version)
                dlq = DLQManager(store)
                dead_letter_record = dlq.move_to_dead_letter(
                    item=selected,
                    reason="retry_exhausted_on_claim",
                    error_code="retry_exhausted",
                    metadata={"worker_id": command.worker_id, "worker_role": command.worker_role},
                    persist=False,
                )
                transition = {
                    "task_id": selected.task_id,
                    "from_status": exhausted_from_status,
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
                lifecycle_evidence = self._record_cognitive_runtime_lifecycle_receipt(
                    item=selected,
                    event_type="retry_exhausted_on_claim",
                    from_status=str(transition["from_status"]),
                    to_status="dead_letter",
                    worker_id=command.worker_id,
                    metadata={
                        "worker_role": command.worker_role,
                        "attempts": selected.attempts,
                        "reason": "retry_exhausted_on_claim",
                    },
                )
                self._attach_lifecycle_evidence(
                    item=selected,
                    transition=transition,
                    outbox_record=outbox,
                    evidence=lifecycle_evidence,
                )
                items[selected.task_id] = selected
                self._atomic_save_changed_items(
                    store=store,
                    items=items,
                    transitions=[transition],
                    outbox_records=[outbox],
                    expected_versions={selected.task_id: selected_expected_version},
                    dead_letter_records=[dead_letter_record],
                )
                return self._result_from_item(selected, ok=False, reason="retry_exhausted_on_claim")

            # Grant lease via LeaseManager.
            lm = LeaseManager(store)
            from_status = selected.status
            selected_expected_version = int(selected.version)
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
            lifecycle_evidence = self._record_cognitive_runtime_lifecycle_receipt(
                item=selected,
                event_type="claimed",
                from_status=from_status,
                to_status=selected.status,
                worker_id=command.worker_id,
                lease_token=lease_token,
                metadata={
                    "worker_role": command.worker_role,
                    "lease_expires_at": expires_at,
                },
            )
            self._attach_lifecycle_evidence(
                item=selected,
                transition=transition,
                outbox_record=outbox,
                evidence=lifecycle_evidence,
            )
            items[selected.task_id] = selected
            self._atomic_save_changed_items(
                store=store,
                items=items,
                transitions=[transition],
                outbox_records=[outbox],
                expected_versions={selected.task_id: selected_expected_version},
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
            previous_version = int(item.version)
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
                self._atomic_save_changed_items(
                    store=store,
                    items=items,
                    transitions=[],
                    outbox_records=[outbox],
                    expected_versions={item.task_id: previous_version},
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
            previous_version = int(item.version)
            ack_metadata = dict(command.metadata)

            # Determine next status.
            if command.next_stage is not None:
                self._fsm.validate_transition(item, "ack", next_stage=command.next_stage)
                next_is_leaf = False if ack_metadata.get("is_leaf") is False else item.is_leaf
                if previous_status == "in_execution" and command.next_stage == "pending_exec" and next_is_leaf:
                    raise TaskMarketError(
                        "Leaf execution work items cannot acknowledge directly back to pending_exec; use fail/requeue",
                        code="leaf_execution_ack_requeue_forbidden",
                        details={
                            "task_id": item.task_id,
                            "from_status": previous_status,
                            "next_stage": command.next_stage,
                        },
                    )
                item.stage = command.next_stage
                item.status = command.next_stage
                # A stage advance opens a fresh attempt budget: attempts
                # burned at the previous stage must not be charged to the
                # next one (live I3-r9: a step that succeeded on exec
                # attempt 3/3 was retry-exhausted-killed by the QA queue
                # claim before QA ever judged it).
                item.attempts = 0
            else:
                terminal_status = str(command.terminal_status or "resolved").strip().lower()
                if terminal_status not in TERMINAL_STATUSES:
                    raise TaskMarketError(
                        f"Unsupported terminal status: {terminal_status}",
                        code="unsupported_terminal_status",
                        details={"task_id": item.task_id, "status": terminal_status},
                    )
                self._fsm.validate_transition(item, "ack", terminal_status=terminal_status)
                item.status = terminal_status

            item.metadata = dict(item.metadata)
            item.metadata["last_summary"] = command.summary
            item.metadata["last_ack_metadata"] = ack_metadata
            # Three-tier fission: when CE fissions a task into leaf steps it
            # demotes the parent to a supervision row; the market record must
            # reflect that so the exec claim gate can skip it.
            if ack_metadata.get("is_leaf") is False:
                item.is_leaf = False

            # Merge ack metadata into item.payload so downstream consumers (Director, QA)
            # can access fields generated by upstream workers (CE sets blueprint_id, guardrails, etc.).
            item.payload = {**dict(item.payload), **dict(command.metadata)}
            # A successful advance retires the failure teaching — stale
            # bounce reasons must not leak into the next stage's context.
            item.payload.pop("last_failure", None)

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
            lifecycle_evidence = self._record_cognitive_runtime_lifecycle_receipt(
                item=item,
                event_type="acknowledged",
                from_status=previous_status,
                to_status=item.status,
                worker_id=item.claimed_by or "",
                lease_token=command.lease_token,
                metadata={
                    "next_stage": command.next_stage or "",
                    "terminal_status": command.terminal_status or "",
                    "summary": command.summary,
                    "ack_metadata": dict(command.metadata),
                },
            )
            self._attach_lifecycle_evidence(
                item=item,
                transition=transition,
                outbox_record=outbox,
                evidence=lifecycle_evidence,
            )
            items[item.task_id] = item

            self._atomic_save_changed_items(
                store=store,
                items=items,
                transitions=[transition],
                outbox_records=[outbox],
                expected_versions={item.task_id: previous_version},
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
            previous_version = int(item.version)

            item.last_error = {
                "error_code": command.error_code,
                "error_message": command.error_message,
                "metadata": dict(command.metadata),
                "occurred_at": now_iso(),
            }
            # Teach the next attempt: a requeued/replayed item's worker must
            # see WHY the last attempt failed — claim results expose payload,
            # not last_error (live I3-r10: QA verify bounces retried blind,
            # made no changes, and died no_materialized_changes).
            item.payload = {
                **dict(item.payload),
                "last_failure": {
                    "error_code": command.error_code,
                    "error_message": str(command.error_message or "")[:600],
                    "occurred_at": str(item.last_error["occurred_at"]),
                },
            }
            feedback_counters = self._normalize_feedback_counters(
                dict(item.payload).get("feedback_counters"),
                dict(command.metadata).get("feedback_counters"),
            )
            if feedback_counters:
                item.payload = {
                    **dict(item.payload),
                    "feedback_counters": feedback_counters,
                }
                item.metadata = {
                    **dict(item.metadata),
                    "feedback_counters": feedback_counters,
                }

            # Determine disposition.
            non_consuming_requeue = (
                command.requeue_stage is not None
                and not command.to_dead_letter
                and str(command.error_code or "").strip().upper() in _NON_CONSUMING_REQUEUE_ERROR_CODES
            )
            if non_consuming_requeue and item.attempts > 0:
                # claim_work_item increments attempts before consumers can inspect
                # file-scope conflicts. A transient lock conflict should wait for
                # the owner, not burn the task's execution retry budget.
                item.attempts -= 1
            move_to_dead_letter = bool(command.to_dead_letter) or (
                not non_consuming_requeue and item.attempts >= item.max_attempts
            )
            dead_letter_records: list[dict[str, Any]] = []

            if move_to_dead_letter:
                dlq = DLQManager(store)
                dead_letter_record = dlq.move_to_dead_letter(
                    item=item,
                    reason=command.error_message,
                    error_code=command.error_code,
                    metadata=dict(command.metadata),
                    persist=False,
                )
                dead_letter_records.append(dead_letter_record)
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
            lifecycle_evidence = self._record_cognitive_runtime_lifecycle_receipt(
                item=item,
                event_type=event_type,
                from_status=previous_status,
                to_status=item.status,
                worker_id=item.claimed_by or "",
                lease_token=command.lease_token,
                metadata={
                    "error_code": command.error_code,
                    "error_message": command.error_message,
                    "requeue_stage": command.requeue_stage or "",
                    "to_dead_letter": bool(command.to_dead_letter),
                    "failure_metadata": dict(command.metadata),
                },
            )
            self._attach_lifecycle_evidence(
                item=item,
                transition=transition,
                outbox_record=outbox,
                evidence=lifecycle_evidence,
            )
            items[item.task_id] = item

            self._atomic_save_changed_items(
                store=store,
                items=items,
                transitions=[transition],
                outbox_records=[outbox],
                expected_versions={item.task_id: previous_version},
                dead_letter_records=dead_letter_records,
            )

            # Saga compensation only on terminal failure paths (not requeue).
            if command.requeue_stage is None:
                saga_expected_version = int(item.version)
                saga_expected_versions = {item.task_id: saga_expected_version}
                task_compensation_summary = self._compensate_task_no_lock(
                    workspace=command.workspace,
                    store=store,
                    items=items,
                    item=item,
                    reason=f"task_failed:{command.error_code}",
                    initiator="fail_task_stage",
                )
                saga_transitions = self._collect_compensation_transitions(task_compensation_summary)
                saga_outbox_records = self._collect_compensation_outbox(task_compensation_summary)
                item.metadata = dict(item.metadata)
                item.metadata["saga_task_compensation"] = self._strip_compensation_side_effects(
                    task_compensation_summary
                )

                parent_compensation_summary: dict[str, Any] | None = None
                if not item.is_leaf:
                    parent_compensation_summary = self._compensate_children_for_parent_failure(
                        workspace=command.workspace,
                        store=store,
                        items=items,
                        parent_task_id=item.task_id,
                        reason=f"parent_failed:{command.error_code}",
                    )
                    child_expected_versions_raw = parent_compensation_summary.get("expected_versions")
                    if isinstance(child_expected_versions_raw, dict):
                        saga_expected_versions.update(
                            {str(task_id): int(version) for task_id, version in child_expected_versions_raw.items()}
                        )
                    saga_transitions.extend(self._collect_compensation_transitions(parent_compensation_summary))
                    saga_outbox_records.extend(self._collect_compensation_outbox(parent_compensation_summary))
                    item.metadata["saga_child_compensation"] = {
                        key: value
                        for key, value in parent_compensation_summary.items()
                        if key != "expected_versions" and key not in {"_transitions", "_outbox_records"}
                    }

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

                self._atomic_save_changed_items(
                    store=store,
                    items=items,
                    transitions=[*saga_transitions, saga_transition],
                    outbox_records=[*saga_outbox_records, saga_outbox],
                    expected_versions=saga_expected_versions,
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

            if item.status in {"rejected", "dead_letter"}:
                return self._result_from_item(item, ok=False, reason="terminal_status")
            resolved_reopen_source = ""
            if item.status == "resolved":
                reopen_allowed, reopen_reason, resolved_reopen_source = self._resolved_reopen_allowed(item, command)
                if not reopen_allowed:
                    return self._result_from_item(item, ok=False, reason=reopen_reason)
            if item.status in {"completed", "cancelled"}:
                return self._result_from_item(item, ok=False, reason="unsupported_status")
            if item.status == "waiting_human":
                return self._result_from_item(item, ok=False, reason="waiting_human")

            lm = LeaseManager(store)
            if str(item.lease_token or "").strip() and not lm.is_lease_expired(item):
                return self._result_from_item(item, ok=False, reason="active_lease")

            previous_status = item.status
            previous_version = int(item.version)
            item.stage = command.target_stage
            item.status = command.target_stage
            lm.clear_lease(item)
            requeue_metadata = dict(command.metadata)
            item.metadata = dict(item.metadata)
            item.metadata["requeue_reason"] = command.reason
            item.metadata["requeue_metadata"] = requeue_metadata
            item.metadata["requeued_at"] = now_iso()
            if previous_status == "resolved":
                item.metadata["reopen_count"] = self._safe_reopen_count(item.metadata) + 1
                if resolved_reopen_source:
                    item.metadata["last_reopen_source"] = resolved_reopen_source
            last_failure = requeue_metadata.get("last_failure")
            if isinstance(last_failure, dict):
                item.payload = {
                    **dict(item.payload),
                    "last_failure": dict(last_failure),
                }
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
            lifecycle_evidence = self._record_cognitive_runtime_lifecycle_receipt(
                item=item,
                event_type="requeued",
                from_status=previous_status,
                to_status=item.status,
                metadata={
                    "target_stage": command.target_stage,
                    "reason": command.reason,
                    "requeue_metadata": dict(command.metadata),
                },
            )
            self._attach_lifecycle_evidence(
                item=item,
                transition=transition,
                outbox_record=outbox,
                evidence=lifecycle_evidence,
            )
            items[item.task_id] = item

            self._atomic_save_changed_items(
                store=store,
                items=items,
                transitions=[transition],
                outbox_records=[outbox],
                expected_versions={item.task_id: previous_version},
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
            previous_version = int(item.version)
            dlq = DLQManager(store)
            dead_letter_record = dlq.move_to_dead_letter(
                item=item,
                reason=command.reason,
                error_code=str(command.error_code or "").strip(),
                metadata=dict(command.metadata),
                persist=False,
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
            lifecycle_evidence = self._record_cognitive_runtime_lifecycle_receipt(
                item=item,
                event_type="dead_lettered",
                from_status=previous_status,
                to_status="dead_letter",
                metadata={
                    "reason": command.reason,
                    "error_code": command.error_code or "",
                    "dead_letter_metadata": dict(command.metadata),
                },
            )
            self._attach_lifecycle_evidence(
                item=item,
                transition=transition,
                outbox_record=outbox,
                evidence=lifecycle_evidence,
            )
            items[item.task_id] = item

            self._atomic_save_changed_items(
                store=store,
                items=items,
                transitions=[transition],
                outbox_records=[outbox],
                expected_versions={item.task_id: previous_version},
                dead_letter_records=[dead_letter_record],
            )

            self._observe("dead_letter", (time.monotonic() - t0) * 1000.0, task_id=item.task_id)
            return self._result_from_item(item, reason="dead_lettered")

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

    # ---- Claim selection / cascade -----------------------------------------

    def _cascade_dead_letter_dependents(
        self,
        *,
        store: Any,
        items: dict[str, TaskWorkItemRecord],
        worker_id: str,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, int], list[dict[str, Any]]]:
        """Dead-letter ``pending_exec`` steps whose dependency terminally failed.

        The readiness gate blocks dependents of a failed-and-requeued
        dependency until it recovers; a *terminally* failed dependency
        (rejected/dead_letter) can never recover, so its dependents would
        otherwise strand as permanently-unclaimable ``pending_exec`` rows —
        never claimed, never escalated, invisible to the DLQ (live I3-r7).
        Cascade them into the DLQ with a distinct error code so the whole
        cluster is visible and bulk-replayable after the dependency is fixed.

        Deliberately bypasses ``fail_task_stage``: a cascaded dependent never
        executed, so there is nothing to compensate — and skipping saga
        compensation breaks any cascade→compensate recursion by construction.
        The sweep iterates to a fixpoint so a dependency chain collapses in
        one pass; dead-letter is absorbing, so termination is guaranteed.
        """
        transitions: list[dict[str, Any]] = []
        outbox_records: list[dict[str, Any]] = []
        expected_versions: dict[str, int] = {}
        dead_letter_records: list[dict[str, Any]] = []
        dlq = DLQManager(store)
        changed = True
        while changed:
            changed = False
            for item in list(items.values()):
                # status check excludes claimed in-flight rows: a leased step
                # keeps stage "pending_exec" with status "in_execution", and
                # killing it would wipe a live lease mid-execution (its dep
                # may fail terminally at QA after the dependent was legally
                # claimed). Only queued, unleased rows cascade.
                if item.stage != "pending_exec" or item.status != "pending_exec" or not item.is_leaf:
                    continue
                dead_dep_id = ""
                dead_dep_status = ""
                for dep_id in item.depends_on or []:
                    dep = items.get(str(dep_id))
                    if dep is not None and dep.status in _DEPENDENCY_TERMINAL_FAILURE_STATUSES:
                        dead_dep_id = dep.task_id
                        dead_dep_status = dep.status
                        break
                if not dead_dep_id:
                    continue
                from_status = item.status
                expected_version = int(item.version)
                reason = f"dependency_terminal:{dead_dep_id}:{dead_dep_status}"
                # Receipt FIRST: a required-receipt failure must skip this
                # item before any durable side effect — otherwise one
                # poisoned item aborts every queue-scan claim at every
                # stage, and the workspace wedges with no self-heal path.
                try:
                    lifecycle_evidence = self._record_cognitive_runtime_lifecycle_receipt(
                        item=item,
                        event_type="dependency_terminal_cascade",
                        from_status=from_status,
                        to_status="dead_letter",
                        worker_id=worker_id,
                        metadata={
                            "reason": reason,
                            "dependency_task_id": dead_dep_id,
                            "dependency_status": dead_dep_status,
                        },
                    )
                except (TaskMarketError, InternalTaskMarketError) as exc:
                    # Both classes: the service raises the public-contract
                    # TaskMarketError, internal collaborators (store/DLQ)
                    # raise internal.errors.TaskMarketError — same name,
                    # different classes.
                    logger.warning(
                        "dependency cascade skipped %s (lifecycle receipt failed): %s",
                        item.task_id,
                        exc,
                    )
                    continue
                dead_letter_record = dlq.move_to_dead_letter(
                    item=item,
                    reason=reason,
                    error_code="dependency_terminal_failure",
                    metadata={
                        "dependency_task_id": dead_dep_id,
                        "dependency_status": dead_dep_status,
                    },
                    persist=False,
                )
                transition = {
                    "task_id": item.task_id,
                    "from_status": from_status,
                    "to_status": "dead_letter",
                    "event_type": "dead_lettered",
                    "worker_id": worker_id,
                    "lease_token": "",
                    "version": item.version,
                    "metadata": {
                        "trace_id": item.trace_id,
                        "reason": reason,
                        "dependency_task_id": dead_dep_id,
                        "dependency_status": dead_dep_status,
                    },
                }
                outbox = self._build_outbox_record(
                    workspace=item.workspace,
                    event_type="task_market.work_item_dead_lettered",
                    run_id=item.run_id,
                    task_id=item.task_id,
                    payload={
                        "trace_id": item.trace_id,
                        "reason": reason,
                        "dependency_task_id": dead_dep_id,
                        "dependency_status": dead_dep_status,
                    },
                )
                self._attach_lifecycle_evidence(
                    item=item,
                    transition=transition,
                    outbox_record=outbox,
                    evidence=lifecycle_evidence,
                )
                items[item.task_id] = item
                transitions.append(transition)
                outbox_records.append(outbox)
                expected_versions[item.task_id] = expected_version
                dead_letter_records.append(dead_letter_record)
                changed = True
        self._reconcile_parents_after_terminal_children(
            store=store,
            items=items,
            transitions=transitions,
            outbox_records=outbox_records,
            expected_versions=expected_versions,
            dead_letter_records=dead_letter_records,
        )
        return transitions, outbox_records, expected_versions, dead_letter_records

    def _reconcile_parents_after_terminal_children(
        self,
        *,
        store: Any,
        items: dict[str, TaskWorkItemRecord],
        transitions: list[dict[str, Any]],
        outbox_records: list[dict[str, Any]],
        expected_versions: dict[str, int],
        dead_letter_records: list[dict[str, Any]],
    ) -> None:
        """Synchronously reconcile parent rows during queue-scan sweeps.

        The periodic reconciler eventually fixes parent status, but factory
        bench chains run inside short-lived subprocesses and can strand
        non-leaf parents in ``pending_exec`` after a child dead-letters. Queue
        scans already sweep terminal dependencies; folding parent convergence
        into that same transaction makes failure visible immediately.
        """
        children_by_parent: dict[str, list[TaskWorkItemRecord]] = {}
        for candidate in items.values():
            parent_task_id = str(candidate.parent_task_id or "").strip()
            if parent_task_id:
                children_by_parent.setdefault(parent_task_id, []).append(candidate)

        if not children_by_parent:
            return

        dlq = DLQManager(store)
        changed = True
        while changed:
            changed = False
            for parent in [item for item in items.values() if not item.is_leaf]:
                children = children_by_parent.get(parent.task_id, [])
                if not children:
                    continue

                expected_status, expected_stage = self._expected_parent_state_from_children(children)
                if parent.status == expected_status and (not expected_stage or parent.stage == expected_stage):
                    continue

                previous_status = parent.status
                previous_stage = parent.stage
                expected_versions.setdefault(parent.task_id, int(parent.version))
                child_status_counts = dict(Counter(child.status for child in children))

                if expected_status == "dead_letter":
                    dead_letter_record = dlq.move_to_dead_letter(
                        item=parent,
                        reason="child_dead_lettered",
                        error_code="child_terminal_failure",
                        metadata={"child_status_counts": child_status_counts},
                        persist=False,
                    )
                    dead_letter_records.append(dead_letter_record)
                else:
                    parent.status = expected_status
                    if expected_stage:
                        parent.stage = expected_stage
                    LeaseManager(store).clear_lease(parent)
                    parent.version += 1
                    parent.updated_at = now_iso()

                parent.metadata = dict(parent.metadata)
                parent.metadata["reconciled_from_children_at"] = parent.updated_at
                parent.metadata["reconciled_child_status_counts"] = child_status_counts
                parent.metadata["reconciled_expected_status"] = expected_status
                if expected_stage:
                    parent.metadata["reconciled_expected_stage"] = expected_stage

                items[parent.task_id] = parent
                transitions.append(
                    {
                        "task_id": parent.task_id,
                        "from_status": previous_status,
                        "to_status": parent.status,
                        "event_type": "reconciled",
                        "worker_id": "dependency_cascade_sweep",
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
                outbox_records.append(
                    self._build_outbox_record(
                        workspace=parent.workspace,
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
                changed = True

    @staticmethod
    def _expected_parent_state_from_children(children: list[TaskWorkItemRecord]) -> tuple[str, str]:
        statuses = {child.status for child in children}

        if statuses and statuses <= {"resolved"}:
            return "resolved", ""
        if "dead_letter" in statuses:
            return "dead_letter", ""
        if "rejected" in statuses:
            return "rejected", ""
        if "waiting_human" in statuses:
            return "waiting_human", "waiting_human"
        if statuses & {"pending_qa", "in_qa"}:
            return "in_qa", "pending_qa"
        if statuses & {"pending_exec", "in_execution"}:
            return "in_execution", "pending_exec"
        if statuses & {"pending_design", "in_design"}:
            return "in_design", "pending_design"

        return "pending_design", "pending_design"

    # Pure readiness predicates live in ``claim_readiness``; bound here as
    # staticmethods so the existing ``self._exec_claim_ready`` /
    # ``self._design_claim_ready`` call sites resolve unchanged.
    _exec_claim_ready = staticmethod(exec_claim_ready)
    _design_claim_ready = staticmethod(design_claim_ready)

    def _select_claim_candidate(
        self,
        *,
        items: dict[str, TaskWorkItemRecord],
        stage: str,
        task_id_filter: str | None,
        at_epoch: float,
    ) -> TaskWorkItemRecord | None:
        if task_id_filter:
            # Targeted claims (explicit task_id) bypass the exec readiness
            # gate: saga supervision legitimately claims non-leaf parents to
            # fail/compensate them. The gate protects queue-scan claims —
            # the Director worker pull path.
            item = items.get(task_id_filter)
            if item is None:
                return None
            return item if item.is_claimable(stage, at_epoch=at_epoch) else None

        candidates = [
            item
            for item in items.values()
            if item.is_claimable(stage, at_epoch=at_epoch)
            and self._exec_claim_ready(item, items)
            and self._design_claim_ready(item, items)
        ]
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

    @staticmethod
    def _normalize_feedback_counters(*sources: Any) -> dict[str, int]:
        counters: dict[str, int] = {}
        for source in sources:
            if not isinstance(source, dict):
                continue
            for raw_key, raw_value in source.items():
                key = str(raw_key or "").strip()
                if not key:
                    continue
                try:
                    value = int(raw_value or 0)
                except (TypeError, ValueError):
                    continue
                counters[key] = max(counters.get(key, 0), max(0, value))
        return counters

    @staticmethod
    def _integration_qa_reopen_allowed(command: RequeueTaskCommandV1) -> bool:
        metadata = dict(command.metadata)
        source = str(metadata.get("source") or "").strip()
        return source == "pm_dispatch.integration_qa"

    @classmethod
    def _resolved_reopen_allowed(
        cls,
        item: TaskWorkItemRecord,
        command: RequeueTaskCommandV1,
    ) -> tuple[bool, str, str]:
        metadata = dict(command.metadata)
        source = str(metadata.get("source") or "").strip()
        if source in _LEGACY_RESOLVED_REOPEN_SOURCES:
            return True, "requeued", source

        policy = cls._resolved_reopen_policy(command)
        if not policy:
            return False, "terminal_status", source
        if not cls._reopen_policy_source_allowed(source, policy):
            return False, "terminal_status", source

        max_reopen_count = cls._policy_max_reopen_count(policy)
        if cls._safe_reopen_count(item.metadata) >= max_reopen_count:
            return False, "reopen_limit_exceeded", source

        if bool(policy.get("requires_failure_report", True)):
            failure_report = metadata.get("verification_failure_report") or metadata.get("failure_report")
            last_failure = metadata.get("last_failure")
            if not isinstance(failure_report, dict) and not isinstance(last_failure, dict):
                return False, "missing_failure_report", source

        return True, "requeued", source

    @staticmethod
    def _resolved_reopen_policy(command: RequeueTaskCommandV1) -> dict[str, Any]:
        metadata = dict(command.metadata)
        raw_policy = command.reopen_policy or metadata.get("reopen_policy") or metadata.get("reopenPolicy")
        return dict(raw_policy) if isinstance(raw_policy, dict) else {}

    @staticmethod
    def _string_list(value: Any) -> list[str]:
        if isinstance(value, str):
            return [value.strip()] if value.strip() else []
        if isinstance(value, (list, tuple, set)):
            return [str(item or "").strip() for item in value if str(item or "").strip()]
        return []

    @classmethod
    def _reopen_policy_source_allowed(cls, source: str, policy: dict[str, Any]) -> bool:
        if not source:
            return False
        allowed_sources = set(cls._string_list(policy.get("allowed_sources")))
        if source in allowed_sources:
            return True
        allowed_prefixes = cls._string_list(policy.get("allowed_source_prefixes"))
        return any(source.startswith(prefix) for prefix in allowed_prefixes)

    @staticmethod
    def _policy_max_reopen_count(policy: dict[str, Any]) -> int:
        try:
            value = int(policy.get("max_reopen_count", 1) or 1)
        except (TypeError, ValueError):
            value = 1
        return max(1, min(value, 20))

    @staticmethod
    def _safe_reopen_count(metadata: dict[str, Any]) -> int:
        try:
            return max(0, int(metadata.get("reopen_count", 0) or 0))
        except (TypeError, ValueError):
            return 0
