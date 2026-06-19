"""Background convergence and loop-lifecycle management for the facade.

``ReconciliationMixin`` owns the reconciliation-loop and durable consumer-loop
lifecycle, parent-status reconciliation, and drift-driven requeue. Bodies are
moved verbatim from the original ``service.py``.

Note: ``relay_outbox_messages`` deliberately remains defined in ``service.py``
itself, not here — the existing test suite monkeypatches
``...internal.service.append_fact_event`` and the relay must resolve that name
from the ``service`` module namespace for the patch to take effect.
"""

from __future__ import annotations

import time
from collections import Counter
from typing import Any

from polaris.cells.runtime.task_market.public.contracts import TaskMarketError

from ._service_base import ServiceBaseMixin
from .consumer_loop import ConsumerLoopManager
from .dlq import DLQManager
from .lease_manager import LeaseManager
from .models import (
    TERMINAL_STATUSES,
    TaskWorkItemRecord,
    now_iso,
)
from .reconciler import TaskReconciliationLoop

_EXECUTION_STATUS_SET = {"pending_exec", "in_execution"}
_QA_STATUS_SET = {"pending_qa", "in_qa"}
_DESIGN_STATUS_SET = {"pending_design", "in_design"}

__all__ = [
    "_DESIGN_STATUS_SET",
    "_EXECUTION_STATUS_SET",
    "_QA_STATUS_SET",
    "ReconciliationMixin",
]


class ReconciliationMixin(ServiceBaseMixin):
    """Background convergence + reconciliation/consumer loop lifecycle."""

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
