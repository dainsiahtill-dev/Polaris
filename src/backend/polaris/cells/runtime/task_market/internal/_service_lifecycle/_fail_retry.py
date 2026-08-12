# mypy: disable-error-code="attr-defined"
from __future__ import annotations

import logging
import time
from collections.abc import Mapping
from typing import Any

from polaris.cells.runtime.task_market.public.contracts import (
    TASK_LOCAL_RETRY_SCHEDULE_METADATA_KEY,
    FailTaskStageCommandV1,
    TaskWorkItemResultV1,
)

from ..dlq import DLQManager
from ..lease_manager import LeaseManager
from ..models import (
    now_iso,
)
from ._bind import _mod
from ._constants import (
    _LOCAL_RETRY_BACKOFF_BASE_SECONDS,
    _LOCAL_RETRY_BACKOFF_MAX_SECONDS,
    _LOCAL_RETRY_MAX_ROUNDS,
    _LOCAL_RETRY_PARK_METADATA_KEY,
    _NON_CONSUMING_REQUEUE_ERROR_CODES,
)

logger = logging.getLogger(__name__.rsplit(".", 1)[0])


class FailRetryMixin:
    """Fail stage and local-retry wake delay."""

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
            requeue_context_payload = self._requeue_context_payload(command.metadata) if command.requeue_stage else {}
            item.payload = {
                **dict(item.payload),
                "last_failure": {
                    "error_code": command.error_code,
                    "error_message": str(command.error_message or "")[:600],
                    "occurred_at": str(item.last_error["occurred_at"]),
                },
                **requeue_context_payload,
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

            failure_disposition = str(command.failure_disposition or "default")
            same_task_local_retry = failure_disposition == "same_task_local_retry"
            isolated_contract_blocker = failure_disposition == "isolated_contract_blocker"
            model_ceiling = failure_disposition == "model_ceiling"
            control_plane_blocked = False
            item.metadata = {
                **dict(item.metadata),
                "last_failure_disposition": failure_disposition,
            }
            if isolated_contract_blocker or model_ceiling:
                # Contract/authority contradiction terminates only this row.
                # It must not poison dependencies, trigger saga compensation,
                # enter DLQ, or silently request an upstream role rerun.
                item.metadata["dependency_terminal_cascade_suppressed"] = True
                item.metadata["automatic_upstream_replan"] = False
                item.metadata["automatic_escalation"] = False
            if same_task_local_retry and command.requeue_stage:
                previous_schedule = item.metadata.get(TASK_LOCAL_RETRY_SCHEDULE_METADATA_KEY)
                try:
                    previous_sequence = (
                        int(previous_schedule.get("sequence") or 0) if isinstance(previous_schedule, Mapping) else 0
                    )
                except (TypeError, ValueError):
                    previous_sequence = 0
                sequence = max(1, previous_sequence + 1)
                if sequence > _LOCAL_RETRY_MAX_ROUNDS:
                    # Local repair is deliberately bounded. Exhaustion is not a
                    # product failure, DLQ event, dependency failure, or excuse
                    # to reopen PM/CE. Park this row until the owner-qualified
                    # model-ceiling/control-plane supervisor supplies a terminal
                    # disposition or explicitly resumes it.
                    control_plane_blocked = True
                    same_task_local_retry = False
                    failure_disposition = "control_plane_blocked"
                    item.metadata.pop(TASK_LOCAL_RETRY_SCHEDULE_METADATA_KEY, None)
                    item.metadata[_LOCAL_RETRY_PARK_METADATA_KEY] = {
                        "schema_version": "task-market.local-retry-control-plane-park.v1",
                        "status": "CONTROL_PLANE_BLOCKED",
                        "stage": command.requeue_stage,
                        "rounds_consumed": _LOCAL_RETRY_MAX_ROUNDS,
                        "attempted_sequence": sequence,
                        "last_error_code": command.error_code,
                        "owner_qualification_required": True,
                        "automatic_upstream_replan": False,
                        "automatic_escalation": False,
                    }
                    item.metadata["last_failure_disposition"] = failure_disposition
                    item.metadata["dependency_terminal_cascade_suppressed"] = True
                    item.metadata["automatic_upstream_replan"] = False
                    item.metadata["automatic_escalation"] = False
                else:
                    backoff_seconds = min(
                        _LOCAL_RETRY_BACKOFF_MAX_SECONDS,
                        _LOCAL_RETRY_BACKOFF_BASE_SECONDS * (2 ** min(sequence - 1, 10)),
                    )
                    scheduled_at_epoch = _mod().now_epoch()
                    item.metadata[TASK_LOCAL_RETRY_SCHEDULE_METADATA_KEY] = {
                        "schema_version": "task-market.local-retry-schedule.v1",
                        "stage": command.requeue_stage,
                        "sequence": sequence,
                        "max_rounds": _LOCAL_RETRY_MAX_ROUNDS,
                        "backoff_seconds": backoff_seconds,
                        "scheduled_at_epoch": scheduled_at_epoch,
                        "not_before_epoch": scheduled_at_epoch + backoff_seconds,
                        "error_code": command.error_code,
                    }

            # Determine disposition.
            non_consuming_requeue = (
                command.requeue_stage is not None
                and not command.to_dead_letter
                and (
                    same_task_local_retry
                    or str(command.error_code or "").strip().upper() in _NON_CONSUMING_REQUEUE_ERROR_CODES
                )
            )
            if non_consuming_requeue and item.attempts > 0:
                # claim_work_item increments attempts before consumers can inspect
                # file-scope conflicts. A transient lock conflict should wait for
                # the owner, not burn the task's execution retry budget.
                item.attempts -= 1
            move_to_dead_letter = (
                not isolated_contract_blocker
                and not model_ceiling
                and not control_plane_blocked
                and (bool(command.to_dead_letter) or (not non_consuming_requeue and item.attempts >= item.max_attempts))
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
            elif command.requeue_stage and not control_plane_blocked:
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
                reason = (
                    "control_plane_blocked"
                    if control_plane_blocked
                    else (
                        "model_ceiling"
                        if model_ceiling
                        else ("contract_blocked" if isolated_contract_blocker else "rejected")
                    )
                )
                event_type = (
                    "task_market.stage_control_plane_blocked"
                    if control_plane_blocked
                    else (
                        "task_market.stage_model_ceiling"
                        if model_ceiling
                        else (
                            "task_market.stage_contract_blocked"
                            if isolated_contract_blocker
                            else "task_market.stage_rejected"
                        )
                    )
                )

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
                    "failure_disposition": failure_disposition,
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
            if command.requeue_stage is None and not isolated_contract_blocker and not model_ceiling:
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
            if command.requeue_stage is None and not isolated_contract_blocker and not model_ceiling:
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

    def next_local_retry_delay(self, workspace: str, stage: str) -> float | None:
        """Return the exact durable wake delay for the next deferred local retry."""

        with self._workspace_lock(workspace):
            items = self._get_store(workspace).load_items()
            current_epoch = _mod().now_epoch()
            deadlines: list[float] = []
            for item in items.values():
                if item.stage != stage or item.status != stage:
                    continue
                schedule = item.metadata.get(TASK_LOCAL_RETRY_SCHEDULE_METADATA_KEY)
                if not isinstance(schedule, Mapping) or str(schedule.get("stage") or "") != stage:
                    continue
                try:
                    deadline = float(schedule.get("not_before_epoch") or 0.0)
                except (TypeError, ValueError):
                    continue
                if deadline > current_epoch:
                    deadlines.append(deadline)
            if not deadlines:
                return None
            return max(0.0, min(deadlines) - current_epoch)
