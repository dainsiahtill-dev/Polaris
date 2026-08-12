# mypy: disable-error-code="attr-defined"
from __future__ import annotations

import logging
from collections import Counter
from typing import Any

from polaris.cells.runtime.task_market.public.contracts import (
    QueryTaskMarketStatusV1,
    TaskMarketError,
    TaskMarketStatusResultV1,
)

from ..dlq import DLQManager
from ..errors import (
    TaskMarketError as InternalTaskMarketError,
)
from ..fsm import PRIORITY_WEIGHT
from ..lease_manager import LeaseManager
from ..models import (
    TaskWorkItemRecord,
    now_iso,
)
from ._constants import (
    _DEPENDENCY_TERMINAL_FAILURE_STATUSES,
)

logger = logging.getLogger(__name__.rsplit(".", 1)[0])


class CascadeStatusMixin:
    """Status query, dependency cascade, and parent reconcile."""

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
                    cascade_suppressed = bool(
                        dep is not None and dict(dep.metadata).get("dependency_terminal_cascade_suppressed", False)
                    )
                    if (
                        dep is not None
                        and dep.status in _DEPENDENCY_TERMINAL_FAILURE_STATUSES
                        and not cascade_suppressed
                    ):
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
