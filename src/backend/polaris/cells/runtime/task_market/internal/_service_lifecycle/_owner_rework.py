# mypy: disable-error-code="attr-defined"
from __future__ import annotations

import logging
import time
from collections.abc import Mapping
from typing import Any

from polaris.cells.runtime.task_market.public.contracts import (
    OWNER_REWORK_HANDOFFS_METADATA_KEY,
    OWNER_REWORK_RESOLVED_ONLY_DEPENDENCY_MODE,
    OWNER_REWORK_ROUTE_SCHEMA_V1,
    OwnerReworkHandoffV1,
    OwnerReworkRouteReasonV1,
    OwnerReworkRouteResultV1,
    RequeueTaskCommandV1,
    RouteOwnerReworkCommandV1,
    TaskMarketError,
)

from ..errors import (
    StaleLeaseTokenError,
    StaleWriteConflictError,
    TaskMarketError as InternalTaskMarketError,
)
from ..lease_manager import LeaseManager
from ..models import (
    TERMINAL_STATUSES,
    TaskWorkItemRecord,
    now_iso,
)

logger = logging.getLogger(__name__.rsplit(".", 1)[0])


class OwnerReworkMixin:
    """Owner-rework routing and handoff helpers."""

    # ---- Owner rework ------------------------------------------------------

    def route_owner_rework(
        self,
        command: RouteOwnerReworkCommandV1,
    ) -> OwnerReworkRouteResultV1:
        """Atomically route a leased requester behind its file-owning task.

        The owner and requester are loaded, validated, and committed under one
        workspace lock with both read versions supplied to the existing CAS
        store operation. Validation and idempotency checks complete before any
        lifecycle receipt or Task Market state is persisted.
        """

        t0 = time.monotonic()
        with self._workspace_lock(command.workspace):
            store = self._get_store(command.workspace)
            items = store.load_items()
            requester = items.get(command.requester_task_id)
            if requester is None:
                return self._owner_rework_result(
                    command,
                    ok=False,
                    reason=OwnerReworkRouteReasonV1.REQUESTER_NOT_FOUND,
                )
            owner = items.get(command.owner_task_id)
            if owner is None:
                return self._owner_rework_result(
                    command,
                    ok=False,
                    reason=OwnerReworkRouteReasonV1.OWNER_NOT_FOUND,
                    requester=requester,
                )
            if owner.task_id == requester.task_id:
                return self._owner_rework_result(
                    command,
                    ok=False,
                    reason=OwnerReworkRouteReasonV1.OWNER_REQUESTER_SAME_TASK,
                    owner=owner,
                    requester=requester,
                )

            idempotency_reason = self._owner_rework_idempotency_reason(
                owner=owner,
                requester=requester,
                command=command,
            )
            if idempotency_reason is OwnerReworkRouteReasonV1.ALREADY_ROUTED:
                return self._owner_rework_result(
                    command,
                    ok=True,
                    reason=idempotency_reason,
                    owner=owner,
                    requester=requester,
                    dependency_added=False,
                    idempotent=True,
                )
            if idempotency_reason is OwnerReworkRouteReasonV1.HANDOFF_STATE_CONFLICT:
                return self._owner_rework_result(
                    command,
                    ok=False,
                    reason=idempotency_reason,
                    owner=owner,
                    requester=requester,
                )

            lease_manager = LeaseManager(store)
            try:
                lease_manager.validate_token(requester, command.requester_lease_token)
            except StaleLeaseTokenError:
                return self._owner_rework_result(
                    command,
                    ok=False,
                    reason=OwnerReworkRouteReasonV1.REQUESTER_LEASE_MISMATCH,
                    owner=owner,
                    requester=requester,
                )

            owner_reopened = owner.status == "resolved"
            if owner.status in TERMINAL_STATUSES and not owner_reopened:
                return self._owner_rework_result(
                    command,
                    ok=False,
                    reason=OwnerReworkRouteReasonV1.OWNER_TERMINAL_UNRECOVERABLE,
                    owner=owner,
                    requester=requester,
                )
            if owner_reopened and self._safe_reopen_count(owner.metadata) >= command.max_reopen_count:
                return self._owner_rework_result(
                    command,
                    ok=False,
                    reason=OwnerReworkRouteReasonV1.OWNER_REOPEN_BUDGET_EXCEEDED,
                    owner=owner,
                    requester=requester,
                )
            if self._dependency_reaches(
                items=items,
                start_task_id=owner.task_id,
                target_task_id=requester.task_id,
            ):
                return self._owner_rework_result(
                    command,
                    ok=False,
                    reason=OwnerReworkRouteReasonV1.DEPENDENCY_CYCLE,
                    owner=owner,
                    requester=requester,
                )

            owner_previous_status = owner.status
            owner_previous_version = int(owner.version)
            requester_previous_status = requester.status
            requester_previous_version = int(requester.version)
            requester_worker_id = requester.claimed_by or ""
            dependency_added = owner.task_id not in requester.depends_on
            routed_at = now_iso()
            handoff_record = self._owner_rework_handoff_record(
                command=command,
                owner_previous_status=owner_previous_status,
                requester_previous_status=requester_previous_status,
                owner_reopened=owner_reopened,
                routed_at=routed_at,
            )

            if owner_reopened:
                owner.stage = "pending_exec"
                owner.status = "pending_exec"
                owner.attempts = 0
                lease_manager.clear_lease(owner)
                owner.metadata = dict(owner.metadata)
                owner.metadata["reopen_count"] = self._safe_reopen_count(owner.metadata) + 1
                owner.metadata["last_reopen_source"] = "runtime.task_market.owner_rework"
            self._persist_owner_rework_handoff(owner, handoff_record)
            owner.payload = {
                **dict(owner.payload),
                "owner_rework_request": handoff_record.to_record(),
            }
            owner.version += 1
            owner.updated_at = routed_at

            requester.stage = "pending_exec"
            requester.status = "pending_exec"
            lease_manager.clear_lease(requester)
            if owner.task_id not in requester.depends_on:
                requester.depends_on.append(owner.task_id)
            requester.metadata = dict(requester.metadata)
            self._persist_owner_rework_handoff(requester, handoff_record)
            failure_payload = self._owner_rework_failure_payload(command)
            requester.last_error = {
                "error_code": failure_payload["error_code"],
                "error_message": failure_payload["error_message"],
                "metadata": {
                    "handoff_id": command.handoff_id,
                    "owner_task_id": owner.task_id,
                    "failure_metadata": dict(command.failure_metadata),
                    "evidence_metadata": dict(command.evidence_metadata),
                },
                "occurred_at": routed_at,
            }
            requester.payload = {
                **dict(requester.payload),
                "last_failure": failure_payload,
                "owner_rework_wait": {
                    "handoff_id": command.handoff_id,
                    "owner_task_id": owner.task_id,
                    "dependency_mode": OWNER_REWORK_RESOLVED_ONLY_DEPENDENCY_MODE,
                    "evidence_metadata": dict(command.evidence_metadata),
                },
            }
            requester.version += 1
            requester.updated_at = routed_at

            items[owner.task_id] = owner
            items[requester.task_id] = requester
            owner_event_type = "owner_rework_reopened" if owner_reopened else "owner_rework_reused"
            owner_transition = {
                "task_id": owner.task_id,
                "from_status": owner_previous_status,
                "to_status": owner.status,
                "event_type": owner_event_type,
                "worker_id": "",
                "lease_token": "",
                "version": owner.version,
                "metadata": {
                    "trace_id": owner.trace_id,
                    "handoff_id": command.handoff_id,
                    "requester_task_id": requester.task_id,
                    "owner_reopened": owner_reopened,
                },
            }
            requester_transition = {
                "task_id": requester.task_id,
                "from_status": requester_previous_status,
                "to_status": requester.status,
                "event_type": "owner_rework_requester_deferred",
                "worker_id": requester_worker_id,
                "lease_token": command.requester_lease_token,
                "version": requester.version,
                "metadata": {
                    "trace_id": requester.trace_id,
                    "handoff_id": command.handoff_id,
                    "owner_task_id": owner.task_id,
                    "dependency_mode": OWNER_REWORK_RESOLVED_ONLY_DEPENDENCY_MODE,
                    "failure_metadata": dict(command.failure_metadata),
                    "evidence_metadata": dict(command.evidence_metadata),
                },
            }
            outbox = self._build_outbox_record(
                workspace=command.workspace,
                event_type="task_market.owner_rework_routed",
                run_id=requester.run_id,
                task_id=requester.task_id,
                payload={
                    "trace_id": requester.trace_id,
                    "handoff_id": command.handoff_id,
                    "owner_task_id": owner.task_id,
                    "requester_task_id": requester.task_id,
                    "owner_from_status": owner_previous_status,
                    "owner_to_status": owner.status,
                    "requester_from_status": requester_previous_status,
                    "requester_to_status": requester.status,
                    "owner_reopened": owner_reopened,
                    "dependency_mode": OWNER_REWORK_RESOLVED_ONLY_DEPENDENCY_MODE,
                    "failure_metadata": dict(command.failure_metadata),
                    "evidence_metadata": dict(command.evidence_metadata),
                },
            )
            try:
                self._atomic_save_changed_items(
                    store=store,
                    items=items,
                    transitions=[owner_transition, requester_transition],
                    outbox_records=[outbox],
                    expected_versions={
                        owner.task_id: owner_previous_version,
                        requester.task_id: requester_previous_version,
                    },
                )
            except StaleWriteConflictError:
                current_items = store.load_items()
                return self._owner_rework_result(
                    command,
                    ok=False,
                    reason=OwnerReworkRouteReasonV1.STALE_WRITE_CONFLICT,
                    owner=current_items.get(owner.task_id),
                    requester=current_items.get(requester.task_id),
                )

            # The lifecycle receipt is an external success fact.  It must be
            # recorded only after the two-row CAS commits, otherwise a stale
            # write would leave an orphan ``owner_rework_routed`` receipt.
            try:
                post_commit_evidence = self._record_cognitive_runtime_lifecycle_receipt(
                    item=requester,
                    event_type="owner_rework_routed",
                    from_status=requester_previous_status,
                    to_status=requester.status,
                    worker_id=requester_worker_id,
                    lease_token=command.requester_lease_token,
                    metadata={
                        **dict(command.metadata),
                        "handoff": handoff_record.to_record(),
                    },
                )
            except TaskMarketError as exc:
                # The two-row CAS and its transactional outbox are the
                # authoritative commit. A downstream Cognitive Runtime
                # projection cannot roll that commit back, so surface a
                # structured post-commit warning instead of returning a false
                # operation failure that would trigger duplicate routing.
                logger.error(
                    "Owner-rework Cognitive Runtime projection failed after commit: task_id=%s handoff_id=%s error=%s",
                    requester.task_id,
                    command.handoff_id,
                    exc,
                )
                post_commit_evidence = {
                    "source": "runtime.task_market.transactional_outbox",
                    "authoritative_commit_recorded": True,
                    "cognitive_runtime_projection_recorded": False,
                    "error_code": str(getattr(exc, "code", "") or "cognitive_runtime_projection_failed"),
                    "error_message": str(exc),
                }

            self._observe(
                "route_owner_rework",
                (time.monotonic() - t0) * 1000.0,
                stage=requester.stage,
                task_id=requester.task_id,
                trace_id=requester.trace_id,
            )
            return self._owner_rework_result(
                command,
                ok=True,
                reason=OwnerReworkRouteReasonV1.ROUTED,
                owner=owner,
                requester=requester,
                owner_reopened=owner_reopened,
                dependency_added=dependency_added,
                post_commit_evidence=post_commit_evidence,
            )

    @staticmethod
    def _integration_qa_reopen_allowed(command: RequeueTaskCommandV1) -> bool:
        metadata = dict(command.metadata)
        source = str(metadata.get("source") or "").strip()
        return source == "pm_dispatch.integration_qa"

    @staticmethod
    def _owner_rework_result(
        command: RouteOwnerReworkCommandV1,
        *,
        ok: bool,
        reason: OwnerReworkRouteReasonV1,
        owner: TaskWorkItemRecord | None = None,
        requester: TaskWorkItemRecord | None = None,
        owner_reopened: bool = False,
        dependency_added: bool = False,
        idempotent: bool = False,
        post_commit_evidence: Mapping[str, Any] | None = None,
    ) -> OwnerReworkRouteResultV1:
        return OwnerReworkRouteResultV1(
            ok=ok,
            reason=reason,
            handoff_id=command.handoff_id,
            owner_task_id=command.owner_task_id,
            requester_task_id=command.requester_task_id,
            owner_status=owner.status if owner is not None else "",
            requester_status=requester.status if requester is not None else "",
            owner_version=int(owner.version) if owner is not None else 0,
            requester_version=int(requester.version) if requester is not None else 0,
            owner_reopened=owner_reopened,
            dependency_added=dependency_added,
            idempotent=idempotent,
            post_commit_evidence=dict(post_commit_evidence or {}),
        )

    @staticmethod
    def _owner_rework_handoff_map(
        item: TaskWorkItemRecord,
    ) -> dict[str, OwnerReworkHandoffV1] | None:
        raw_handoffs = dict(item.metadata).get(OWNER_REWORK_HANDOFFS_METADATA_KEY)
        if raw_handoffs is None:
            return {}
        if not isinstance(raw_handoffs, dict):
            return None
        handoffs: dict[str, OwnerReworkHandoffV1] = {}
        for raw_handoff_id, raw_record in raw_handoffs.items():
            try:
                handoff = OwnerReworkHandoffV1.from_record(raw_record)
            except ValueError:
                return None
            if str(raw_handoff_id or "").strip() != handoff.handoff_id:
                return None
            handoffs[handoff.handoff_id] = handoff
        return handoffs

    @staticmethod
    def _owner_rework_handoff_matches(
        record: OwnerReworkHandoffV1 | None,
        command: RouteOwnerReworkCommandV1,
    ) -> bool:
        if record is None:
            return False
        return (
            record.handoff_id == command.handoff_id
            and record.owner_task_id == command.owner_task_id
            and record.requester_task_id == command.requester_task_id
        )

    @classmethod
    def _owner_rework_idempotency_reason(
        cls,
        *,
        owner: TaskWorkItemRecord,
        requester: TaskWorkItemRecord,
        command: RouteOwnerReworkCommandV1,
    ) -> OwnerReworkRouteReasonV1 | None:
        owner_handoffs = cls._owner_rework_handoff_map(owner)
        requester_handoffs = cls._owner_rework_handoff_map(requester)
        if owner_handoffs is None or requester_handoffs is None:
            return OwnerReworkRouteReasonV1.HANDOFF_STATE_CONFLICT
        owner_present = command.handoff_id in owner_handoffs
        requester_present = command.handoff_id in requester_handoffs
        if not owner_present and not requester_present:
            return None
        fully_applied = (
            owner_present
            and requester_present
            and cls._owner_rework_handoff_matches(
                owner_handoffs.get(command.handoff_id),
                command,
            )
            and cls._owner_rework_handoff_matches(
                requester_handoffs.get(command.handoff_id),
                command,
            )
            and owner.task_id in requester.depends_on
        )
        if fully_applied:
            return OwnerReworkRouteReasonV1.ALREADY_ROUTED
        return OwnerReworkRouteReasonV1.HANDOFF_STATE_CONFLICT

    @staticmethod
    def _dependency_reaches(
        *,
        items: dict[str, TaskWorkItemRecord],
        start_task_id: str,
        target_task_id: str,
    ) -> bool:
        pending = [start_task_id]
        visited: set[str] = set()
        while pending:
            task_id = pending.pop()
            if task_id in visited:
                continue
            visited.add(task_id)
            if task_id == target_task_id:
                return True
            item = items.get(task_id)
            if item is None:
                continue
            pending.extend(str(dep_id) for dep_id in item.depends_on if str(dep_id))
        return False

    @staticmethod
    def _owner_rework_handoff_record(
        *,
        command: RouteOwnerReworkCommandV1,
        owner_previous_status: str,
        requester_previous_status: str,
        owner_reopened: bool,
        routed_at: str,
    ) -> OwnerReworkHandoffV1:
        return OwnerReworkHandoffV1(
            schema_version=OWNER_REWORK_ROUTE_SCHEMA_V1,
            handoff_id=command.handoff_id,
            owner_task_id=command.owner_task_id,
            requester_task_id=command.requester_task_id,
            owner_previous_status=owner_previous_status,
            requester_previous_status=requester_previous_status,
            owner_reopened=owner_reopened,
            dependency_mode=OWNER_REWORK_RESOLVED_ONLY_DEPENDENCY_MODE,
            failure_metadata=command.failure_metadata,
            evidence_metadata=command.evidence_metadata,
            metadata=command.metadata,
            routed_at=routed_at,
        )

    @classmethod
    def _persist_owner_rework_handoff(
        cls,
        item: TaskWorkItemRecord,
        handoff_record: OwnerReworkHandoffV1,
    ) -> None:
        item.metadata = dict(item.metadata)
        handoffs = cls._owner_rework_handoff_map(item)
        if handoffs is None:
            raise InternalTaskMarketError(
                "Cannot persist owner-rework handoff over malformed handoff state",
                code="owner_rework_handoff_state_conflict",
                details={"task_id": item.task_id},
            )
        serialized_handoffs = {handoff_id: handoff.to_record() for handoff_id, handoff in handoffs.items()}
        serialized_handoffs[handoff_record.handoff_id] = handoff_record.to_record()
        item.metadata[OWNER_REWORK_HANDOFFS_METADATA_KEY] = serialized_handoffs
        item.metadata["last_owner_rework_handoff"] = handoff_record.to_record()

    @staticmethod
    def _owner_rework_failure_payload(
        command: RouteOwnerReworkCommandV1,
    ) -> dict[str, Any]:
        failure = dict(command.failure_metadata)
        error_code = str(failure.get("error_code") or failure.get("code") or "OWNER_REWORK_REQUIRED").strip()
        error_message = str(
            failure.get("error_message")
            or failure.get("message")
            or failure.get("reason")
            or "Owner task rework is required before requester retry"
        ).strip()
        return {
            **failure,
            "error_code": error_code,
            "error_message": error_message,
            "handoff_id": command.handoff_id,
            "owner_task_id": command.owner_task_id,
            "evidence_metadata": dict(command.evidence_metadata),
        }
