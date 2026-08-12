# mypy: disable-error-code="attr-defined"
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone

from polaris.cells.runtime.task_market.public.contracts import (
    TASK_REQUEUE_RECEIPTS_METADATA_KEY,
    ClaimTaskWorkItemCommandV1,
    PublishTaskWorkItemCommandV1,
    RenewTaskLeaseCommandV1,
    TaskLeaseRenewResultV1,
    TaskMarketError,
    TaskWorkItemResultV1,
)

from ..claim_readiness import design_claim_ready, exec_claim_ready
from ..dlq import DLQManager
from ..errors import (
    StaleLeaseTokenError,
    TaskMarketError as InternalTaskMarketError,
    TaskNotClaimableError,
)
from ..fsm import PRIORITY_WEIGHT
from ..lease_manager import LeaseManager
from ..models import (
    TaskWorkItemRecord,
    now_iso,
)
from ._bind import _mod

logger = logging.getLogger(__name__.rsplit(".", 1)[0])


class PublishClaimMixin:
    """Publish / claim / renew / claim-candidate selection."""

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
                prior_requeue_receipts = dict(item.metadata).get(TASK_REQUEUE_RECEIPTS_METADATA_KEY)
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
                if isinstance(prior_requeue_receipts, dict) and prior_requeue_receipts:
                    # Idempotency receipts are durable execution history.  A
                    # later task projection refresh must not erase them and
                    # accidentally permit the same physical rework twice.
                    item.metadata[TASK_REQUEUE_RECEIPTS_METADATA_KEY] = dict(prior_requeue_receipts)
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
                at_epoch=_mod().now_epoch(),
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
