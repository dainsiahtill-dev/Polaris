"""Plan-revision, change-order, and saga-compensation flows for the facade.

``RevisionSagaMixin`` owns plan-revision registration/query, change-order
submission/query/preview, the dependency DAG validator, read-only drift
detection, and the saga-compensation lifecycle (register / commit / compensate)
plus its no-lock helpers. Bodies are moved verbatim from the original
``service.py``.
"""

from __future__ import annotations

import time
from typing import Any

from polaris.cells.runtime.task_market.public.contracts import (
    ChangeOrderResultV1,
    PlanRevisionResultV1,
    QueryChangeOrdersV1,
    QueryPlanRevisionsV1,
    RegisterPlanRevisionCommandV1,
    SubmitChangeOrderCommandV1,
    TaskMarketError,
)

from ._service_base import ServiceBaseMixin
from .lease_manager import LeaseManager
from .models import (
    TERMINAL_STATUSES,
    TaskWorkItemRecord,
    now_iso,
)
from .saga import CompensationAction, SagaCompensator

_IN_PROGRESS_STATUSES = {"in_design", "in_execution", "in_qa"}

__all__ = ["RevisionSagaMixin"]


class RevisionSagaMixin(ServiceBaseMixin):
    """Plan-revision / change-order / saga-compensation responsibilities."""

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
