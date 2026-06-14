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
    TaskMarketError as InternalTaskMarketError,
    TaskNotClaimableError,
    TaskNotFoundError,
)
from .fsm import PRIORITY_WEIGHT, get_fsm
from .human_review import RESOLUTION_TO_STAGE, HumanReviewManager, get_next_escalation_role
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
# Statuses a depends_on dependency can never recover from (subset of
# models.TERMINAL_STATUSES minus "resolved"): dependents must cascade,
# not strand.
_DEPENDENCY_TERMINAL_FAILURE_STATUSES = frozenset({"rejected", "dead_letter"})
_COGNITIVE_REQUIRED_KEYS = (
    "cognitive_runtime_required",
    "task_market_cognitive_runtime_required",
)
_CONTEXT_OS_EXPECTED_KEYS = (
    "context_os_expected",
    "task_market_context_os_expected",
)


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

    def _get_store(self, workspace: str) -> Any:
        """Return the appropriate store backend (lazy)."""
        return get_store(workspace)

    @staticmethod
    def _atomic_save_changed_items(
        *,
        store: Any,
        items: dict[str, TaskWorkItemRecord],
        transitions: list[dict[str, Any]],
        outbox_records: list[dict[str, Any]],
        expected_versions: dict[str, int],
        dead_letter_records: list[dict[str, Any]] | None = None,
        human_review_records: list[dict[str, Any]] | None = None,
    ) -> None:
        """Persist only rows whose read-version baseline is known."""
        expected = {str(task_id): int(version) for task_id, version in expected_versions.items()}
        changed_items = {task_id: items[task_id] for task_id in expected if task_id in items}
        store.save_items_and_outbox_atomic(
            items=changed_items,
            transitions=transitions,
            outbox_records=outbox_records,
            expected_versions=expected,
            dead_letter_records=dead_letter_records or [],
            human_review_records=human_review_records or [],
        )

    @staticmethod
    def _coerce_optional_bool(value: Any) -> bool | None:
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return value != 0
        if isinstance(value, str):
            token = value.strip().lower()
            if token in {"1", "true", "yes", "y", "on", "required"}:
                return True
            if token in {"0", "false", "no", "n", "off", "optional", "disabled"}:
                return False
        return None

    @classmethod
    def _payload_flag(cls, *payloads: dict[str, Any], keys: tuple[str, ...]) -> bool:
        for payload in payloads:
            if not isinstance(payload, dict):
                continue
            for key in keys:
                if key not in payload:
                    continue
                coerced = cls._coerce_optional_bool(payload.get(key))
                if coerced is not None:
                    return coerced
            metadata = payload.get("metadata")
            if isinstance(metadata, dict):
                for key in keys:
                    if key not in metadata:
                        continue
                    coerced = cls._coerce_optional_bool(metadata.get(key))
                    if coerced is not None:
                        return coerced
        return False

    @staticmethod
    def _task_market_session_id(item: TaskWorkItemRecord) -> str:
        for payload in (item.payload, item.metadata):
            if not isinstance(payload, dict):
                continue
            for key in ("task_market_session_id", "role_session_id", "session_id"):
                token = str(payload.get(key) or "").strip()
                if token:
                    return token
        basis = str(item.run_id or item.task_id or "").strip()
        return f"task-market-{basis}"

    def _record_cognitive_runtime_lifecycle_receipt(
        self,
        *,
        item: TaskWorkItemRecord,
        event_type: str,
        from_status: str,
        to_status: str,
        worker_id: str = "",
        lease_token: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Record a Cognitive Runtime receipt for a task-market lifecycle event."""

        event_metadata = dict(metadata or {})
        required = self._payload_flag(
            item.payload,
            item.metadata,
            event_metadata,
            keys=_COGNITIVE_REQUIRED_KEYS,
        )
        context_os_expected = self._payload_flag(
            item.payload,
            item.metadata,
            event_metadata,
            keys=_CONTEXT_OS_EXPECTED_KEYS,
        )
        evidence: dict[str, Any] = {
            "source": "runtime.task_market",
            "event_type": event_type,
            "required": required,
            "receipt_recorded": False,
            "context_os_expected": context_os_expected,
        }
        try:
            from polaris.cells.factory.cognitive_runtime.public import (
                RecordRuntimeReceiptCommandV1,
                get_cognitive_runtime_public_service,
            )

            service = get_cognitive_runtime_public_service()
            try:
                result = service.record_runtime_receipt(
                    RecordRuntimeReceiptCommandV1(
                        workspace=item.workspace,
                        receipt_type="task_market_lifecycle",
                        payload={
                            "event_type": event_type,
                            "task_id": item.task_id,
                            "trace_id": item.trace_id,
                            "run_id": item.run_id,
                            "stage": item.stage,
                            "from_status": from_status,
                            "to_status": to_status,
                            "worker_id": worker_id,
                            "lease_token_present": bool(str(lease_token or "").strip()),
                            "plan_id": item.plan_id,
                            "plan_revision_id": item.plan_revision_id,
                            "root_task_id": item.root_task_id or item.task_id,
                            "parent_task_id": item.parent_task_id,
                            "context_os_expected": context_os_expected,
                            "metadata": event_metadata,
                        },
                        session_id=self._task_market_session_id(item),
                        run_id=item.run_id or None,
                        trace_refs=(item.trace_id,) if item.trace_id else (),
                        turn_envelope={
                            "source": "runtime.task_market",
                            "event_type": event_type,
                            "task_id": item.task_id,
                            "from_status": from_status,
                            "to_status": to_status,
                        },
                    )
                )
            finally:
                service.close()
        except (ImportError, RuntimeError, ValueError) as exc:
            evidence["error_message"] = str(exc)
            if required:
                raise TaskMarketError(
                    f"Cognitive Runtime receipt failed for task-market event {event_type}: {exc}",
                    code="cognitive_runtime_receipt_failed",
                    details={"task_id": item.task_id, "event_type": event_type},
                ) from exc
            return evidence

        if not bool(getattr(result, "ok", False)):
            error_message = str(getattr(result, "error_message", "") or "").strip()
            error_code = str(getattr(result, "error_code", "") or "").strip()
            evidence["error_code"] = error_code
            evidence["error_message"] = error_message
            if required:
                raise TaskMarketError(
                    error_message or error_code or "cognitive_runtime_receipt_failed",
                    code="cognitive_runtime_receipt_failed",
                    details={"task_id": item.task_id, "event_type": event_type},
                )
            return evidence

        receipt = getattr(result, "receipt", None)
        receipt_id = str(getattr(receipt, "receipt_id", "") or "").strip()
        evidence["receipt_recorded"] = bool(receipt_id)
        if receipt_id:
            evidence["receipt_id"] = receipt_id
        if required and not receipt_id:
            raise TaskMarketError(
                "Cognitive Runtime receipt did not return a receipt id",
                code="cognitive_runtime_receipt_missing_id",
                details={"task_id": item.task_id, "event_type": event_type},
            )
        return evidence

    @staticmethod
    def _attach_lifecycle_evidence(
        *,
        item: TaskWorkItemRecord,
        transition: dict[str, Any],
        outbox_record: dict[str, Any],
        evidence: dict[str, Any],
    ) -> None:
        item.metadata = dict(item.metadata)
        item.metadata["last_cognitive_runtime_lifecycle"] = dict(evidence)
        receipt_id = str(evidence.get("receipt_id") or "").strip()
        if receipt_id:
            existing = item.metadata.get("cognitive_runtime_receipt_ids")
            receipt_ids = [str(row) for row in existing] if isinstance(existing, list) else []
            if receipt_id not in receipt_ids:
                receipt_ids.append(receipt_id)
            item.metadata["cognitive_runtime_receipt_ids"] = receipt_ids

        transition_metadata = transition.get("metadata")
        if not isinstance(transition_metadata, dict):
            transition_metadata = {}
            transition["metadata"] = transition_metadata
        transition_metadata["cognitive_runtime"] = dict(evidence)

        payload = outbox_record.get("payload")
        if not isinstance(payload, dict):
            payload = {}
            outbox_record["payload"] = payload
        payload["cognitive_runtime"] = dict(evidence)

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
            move_to_dead_letter = bool(command.to_dead_letter) or item.attempts >= item.max_attempts
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
            if item.status == "resolved" and not self._integration_qa_reopen_allowed(command):
                return self._result_from_item(item, ok=False, reason="terminal_status")
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

    # ---- HITL / Human Review -----------------------------------------------

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
            expected_versions: dict[str, int] = {}

            for item in candidates:
                previous_version = int(item.version)
                impact = self._apply_change_order_impact(
                    item=item,
                    to_revision_id=command.to_revision_id,
                    change_type=command.change_type,
                    current_time=current_time,
                )
                impact_counts[impact] = impact_counts.get(impact, 0) + 1
                if impact != "unaffected":
                    expected_versions[item.task_id] = previous_version
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

            self._atomic_save_changed_items(
                store=store,
                items=items,
                transitions=change_transitions,
                outbox_records=[outbox],
                expected_versions=expected_versions,
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
            previous_version = int(item.version)
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
            self._atomic_save_changed_items(
                store=store,
                items=items,
                transitions=[transition],
                outbox_records=[outbox],
                expected_versions={item.task_id: previous_version},
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
            previous_version = int(item.version)
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
            self._atomic_save_changed_items(
                store=store,
                items=items,
                transitions=[transition],
                outbox_records=[outbox],
                expected_versions={item.task_id: previous_version},
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
            previous_version = int(item.version)
            summary = self._compensate_task_no_lock(
                workspace=workspace_token,
                store=store,
                items=items,
                item=item,
                reason=reason,
                initiator=initiator,
            )
            if bool(summary.get("changed", False)):
                self._atomic_save_changed_items(
                    store=store,
                    items=items,
                    transitions=self._collect_compensation_transitions(summary),
                    outbox_records=self._collect_compensation_outbox(summary),
                    expected_versions={item.task_id: previous_version},
                )
            return self._strip_compensation_side_effects(summary)

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
            reconciliation_dead_letters: list[dict[str, Any]] = []
            expected_versions: dict[str, int] = {}
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
                expected_versions[parent.task_id] = int(parent.version)
                child_status_counts = dict(Counter(child.status for child in children))
                if expected_status == "dead_letter":
                    # A terminally-failed child makes the parent a real
                    # dead-letter: stage + status + DLQ store entry,
                    # consistent with every other dead-letter path (a bare
                    # status write left the parent DLQ-invisible). No saga
                    # compensation here — parent-failure compensation stays
                    # exclusively in fail_task_stage.
                    dead_letter_record = DLQManager(store).move_to_dead_letter(
                        item=parent,
                        reason="child_dead_lettered",
                        error_code="child_terminal_failure",
                        metadata={"child_status_counts": child_status_counts},
                        persist=False,
                    )
                    reconciliation_dead_letters.append(dead_letter_record)
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
                self._atomic_save_changed_items(
                    store=store,
                    items=items,
                    transitions=reconciliation_transitions,
                    outbox_records=reconciliation_outbox,
                    expected_versions=expected_versions,
                    dead_letter_records=reconciliation_dead_letters,
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
            expected_versions: dict[str, int] = {}

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
                    lm = LeaseManager(store)
                    if str(item.lease_token or "").strip() and not lm.is_lease_expired(item):
                        continue

                    previous_status = item.status
                    previous_stage = item.stage
                    previous_version = int(item.version)

                    # Requeue to pending_design.
                    item.stage = "pending_design"
                    item.status = "pending_design"
                    lm.clear_lease(item)
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
                    expected_versions[item.task_id] = previous_version

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
                self._atomic_save_changed_items(
                    store=store,
                    items=items,
                    transitions=requeue_transitions,
                    outbox_records=requeue_outbox,
                    expected_versions=expected_versions,
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

    @staticmethod
    def _strip_compensation_side_effects(summary: dict[str, Any]) -> dict[str, Any]:
        return {key: value for key, value in summary.items() if key not in {"_transitions", "_outbox_records"}}

    @staticmethod
    def _collect_compensation_transitions(summary: dict[str, Any]) -> list[dict[str, Any]]:
        raw = summary.get("_transitions")
        return [dict(item) for item in raw if isinstance(item, dict)] if isinstance(raw, (list, tuple)) else []

    @staticmethod
    def _collect_compensation_outbox(summary: dict[str, Any]) -> list[dict[str, Any]]:
        raw = summary.get("_outbox_records")
        return [dict(item) for item in raw if isinstance(item, dict)] if isinstance(raw, (list, tuple)) else []

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
        transition = {
            "task_id": item.task_id,
            "from_status": item.status,
            "to_status": item.status,
            "event_type": "saga_compensated",
            "worker_id": initiator,
            "lease_token": item.lease_token,
            "version": item.version,
            "metadata": {
                "trace_id": item.trace_id,
                "reason": reason,
                "requires_manual_intervention": bool(summary.get("requires_manual_intervention", False)),
            },
        }
        outbox = self._build_outbox_record(
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
            "_transitions": (transition,),
            "_outbox_records": (outbox,),
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
        expected_versions: dict[str, int] = {}
        transitions: list[dict[str, Any]] = []
        outbox_records: list[dict[str, Any]] = []
        requires_manual = False
        for child in child_items:
            previous_version = int(child.version)
            summary = self._compensate_task_no_lock(
                workspace=workspace,
                store=store,
                items=items,
                item=child,
                reason=reason,
                initiator="parent_failure",
            )
            transitions.extend(self._collect_compensation_transitions(summary))
            outbox_records.extend(self._collect_compensation_outbox(summary))
            summaries.append(self._strip_compensation_side_effects(summary))
            if bool(summary.get("changed", False)):
                expected_versions[child.task_id] = previous_version
            if bool(summary.get("requires_manual_intervention", False)):
                requires_manual = True
        return {
            "parent_task_id": parent_task_id,
            "child_count": len(child_items),
            "compensation_summaries": tuple(summaries),
            "expected_versions": expected_versions,
            "_transitions": tuple(transitions),
            "_outbox_records": tuple(outbox_records),
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
        return transitions, outbox_records, expected_versions, dead_letter_records

    @staticmethod
    def _exec_claim_ready(item: TaskWorkItemRecord, items: dict[str, TaskWorkItemRecord]) -> bool:
        """Execution-stage readiness gate (three-tier fission).

        At ``pending_exec``: (a) non-leaf supervision rows (a CE-fissioned
        parent) are never handed to Director workers; (b) a step is claimable
        only when every ``depends_on`` step has left the exec queue — resolved
        or advanced to QA. A failed-and-requeued dependency therefore blocks
        its dependents (fail-closed); orphan references block too (the
        depends_on validator reports them). Terminally-failed dependencies
        are not merely blocked: ``_cascade_dead_letter_dependents`` sweeps
        their dependents into the DLQ at claim time.
        """
        if item.stage != "pending_exec":
            return True
        if not item.is_leaf:
            return False

        def _target(record: TaskWorkItemRecord) -> str:
            payload = getattr(record, "payload", None)
            if not isinstance(payload, dict):
                return ""
            step = payload.get("construction_step")
            if isinstance(step, dict):
                value = str(step.get("target_file") or "").strip()
                if value:
                    return value.replace("\\", "/").lstrip("./")
            targets = payload.get("target_files")
            if isinstance(targets, list) and targets:
                return str(targets[0] or "").strip().replace("\\", "/").lstrip("./")
            return ""

        item_target = _target(item)
        for dep_id in item.depends_on or []:
            dep = items.get(str(dep_id))
            if dep is None:
                return False
            if dep.status == "resolved":
                continue
            # Same-file predecessor must be fully RESOLVED, not merely at QA: the
            # pending_qa fast-path would otherwise let a QA bounce of the predecessor
            # put two writers on the same file concurrently (I3-r29 fill chains /
            # cross-parent edit_on_prior). Independent-file deps keep the fast-path.
            if item_target and _target(dep) == item_target:
                return False
            if dep.stage in ("pending_qa", "in_qa"):
                continue
            return False
        return True

    @staticmethod
    def _design_claim_ready(item: TaskWorkItemRecord, items: dict[str, TaskWorkItemRecord]) -> bool:
        """Design-stage ordering gate (组合律 / cross-parent interface coherence).

        A ``pending_design`` parent that ``depends_on`` another parent must not
        fission until that producer parent has left design (advanced to
        ``pending_exec`` or terminal). Otherwise an enhancement parent can
        fission *before* the base parent and invent colliding interface
        identifiers for a shared file — live I3-r14 shipped a non-running
        product because ``index.html`` was named ``id="game"`` by one parent and
        ``id="gameCanvas"`` by a sibling. Ordering producers first lets the
        interface ledger be populated before the consumer reads it.

        Orphan deps (not in the market) and terminal deps do not block — only a
        producer still actively in design holds the consumer back (no hang).
        """
        if item.stage != "pending_design":
            return True
        for dep_id in item.depends_on or []:
            dep = items.get(str(dep_id))
            if dep is None:
                continue
            if dep.stage in ("pending_design", "in_design"):
                return False
        return True

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
