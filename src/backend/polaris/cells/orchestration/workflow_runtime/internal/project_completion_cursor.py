"""SQLite-backed private project-completion cursor implementation."""

from __future__ import annotations

import sqlite3

from polaris.cells.orchestration.workflow_runtime.public.project_completion_cursor import (
    ProjectCompletionCursorConflictError,
    ProjectCompletionCursorEventV1,
    ProjectCompletionCursorIdentityV1,
    ProjectCompletionCursorLimitsV1,
    ProjectCompletionCursorRegistrationV1,
    ProjectCompletionCursorTransitionV1,
)
from polaris.infrastructure.db.repositories.workflow_runtime_store import (
    _PROJECT_COMPLETION_CURSOR_AUTHORITY_TOKEN,
    SqliteRuntimeStore,
    WorkflowEventVersionConflictError,
)

_WORKFLOW_NAME = "project_completion_convergence.v1"


def _exact_nonempty_str(payload: dict[object, object], field_name: str) -> str:
    value = payload.get(field_name)
    if type(value) is not str or not value.strip():
        raise ValueError(f"project-completion cursor {field_name} must be an exact non-empty str")
    return value


def _exact_positive_int(payload: dict[object, object], field_name: str) -> int:
    value = payload.get(field_name)
    if type(value) is not int or value <= 0:
        raise ValueError(f"project-completion cursor {field_name} must be an exact positive int")
    return value


def _registration_from_execution(execution: object) -> ProjectCompletionCursorRegistrationV1:
    metadata = getattr(execution, "metadata", None)
    if type(metadata) is not dict:
        raise ValueError("project-completion cursor metadata must be an exact dict")
    payload = metadata.get("payload")
    if type(payload) is not dict:
        raise ValueError("project-completion cursor payload must be an exact dict")
    identity_payload = payload.get("identity")
    limits_payload = payload.get("limits")
    if type(identity_payload) is not dict or type(limits_payload) is not dict:
        raise ValueError("project-completion cursor registration is incomplete")
    identity = ProjectCompletionCursorIdentityV1(
        workspace=_exact_nonempty_str(identity_payload, "workspace"),
        project_id=_exact_nonempty_str(identity_payload, "project_id"),
        run_id=_exact_nonempty_str(identity_payload, "run_id"),
        completion_contract_hash=_exact_nonempty_str(identity_payload, "completion_contract_hash"),
    )
    limits = ProjectCompletionCursorLimitsV1(
        max_actions=_exact_positive_int(limits_payload, "max_actions"),
        max_dispatch_attempts=_exact_positive_int(limits_payload, "max_dispatch_attempts"),
        max_no_progress_observations=_exact_positive_int(
            limits_payload,
            "max_no_progress_observations",
        ),
        dispatch_lease_seconds=_exact_positive_int(limits_payload, "dispatch_lease_seconds"),
    )
    return ProjectCompletionCursorRegistrationV1(
        workflow_id=_exact_nonempty_str(
            {"workflow_id": getattr(execution, "workflow_id", None)},
            "workflow_id",
        ),
        identity=identity,
        limits=limits,
    )


class SqliteProjectCompletionCursorV1:
    """Hide the generic event store behind a typed, identity-injecting CAS port."""

    def __init__(self, store: SqliteRuntimeStore) -> None:
        if not isinstance(store, SqliteRuntimeStore):
            raise TypeError("store must be a SqliteRuntimeStore")
        self._store = store

    async def ensure_cursor(
        self,
        workflow_id: str,
        identity: ProjectCompletionCursorIdentityV1,
        limits: ProjectCompletionCursorLimitsV1,
    ) -> None:
        if await self._store.get_execution(workflow_id) is not None:
            return
        try:
            await self._store.create_execution(
                workflow_id,
                _WORKFLOW_NAME,
                {"identity": identity.as_payload(), "limits": limits.as_payload()},
            )
        except sqlite3.IntegrityError:
            if await self._store.get_execution(workflow_id) is None:
                raise

    async def load_cursor(
        self,
        workflow_id: str,
        identity: ProjectCompletionCursorIdentityV1,
    ) -> tuple[ProjectCompletionCursorEventV1, ...]:
        events = await self._store.get_events(workflow_id)
        expected_identity = identity.as_payload()
        projected: list[ProjectCompletionCursorEventV1] = []
        for event in events:
            if type(event.payload) is not dict or event.payload.get("identity") != expected_identity:
                raise ValueError("project-completion cursor identity drift")
            projected.append(
                ProjectCompletionCursorEventV1(
                    seq=event.seq,
                    event_type=event.event_type,
                    payload=dict(event.payload),
                )
            )
        return tuple(projected)

    async def list_resumable_cursors(
        self,
    ) -> tuple[ProjectCompletionCursorRegistrationV1, ...]:
        """Recover every nonterminal registration from the durable workflow table.

        ``control_plane_blocked`` is intentionally resumable.  The secondary
        result check also migrates executions written as ``failed`` by the
        pre-F3B-FIX4 projection bug.
        """

        executions = await self._store.list_workflows(limit=2_147_483_647)
        registrations: list[ProjectCompletionCursorRegistrationV1] = []
        for execution in executions:
            if execution.workflow_name != _WORKFLOW_NAME:
                continue
            result_status = ""
            if isinstance(execution.result, dict):
                result_status = str(execution.result.get("status") or "").strip()
            if execution.status != "running" and result_status != "control_plane_blocked":
                continue
            registrations.append(_registration_from_execution(execution))
        registrations.sort(key=lambda item: item.workflow_id)
        return tuple(registrations)

    async def append_transition(
        self,
        workflow_id: str,
        identity: ProjectCompletionCursorIdentityV1,
        transition: ProjectCompletionCursorTransitionV1,
        *,
        expected_previous_seq: int,
    ) -> ProjectCompletionCursorEventV1:
        payload = dict(transition.payload)
        payload["identity"] = identity.as_payload()
        try:
            event = await self._store.append_event(
                workflow_id,
                transition.event_type,
                payload,
                expected_previous_seq=expected_previous_seq,
                _authority_token=_PROJECT_COMPLETION_CURSOR_AUTHORITY_TOKEN,
            )
        except WorkflowEventVersionConflictError as exc:
            raise ProjectCompletionCursorConflictError(str(exc)) from exc
        return ProjectCompletionCursorEventV1(
            seq=event.seq,
            event_type=event.event_type,
            payload=dict(event.payload),
        )

    async def repair_execution_projection(
        self,
        workflow_id: str,
        *,
        status: str,
        result: dict[str, object],
        close_time: str | None,
    ) -> None:
        await self._store.update_execution(
            workflow_id,
            status=status,
            result=result,
            close_time=close_time,
            clear_close_time=close_time is None,
        )


__all__ = ["SqliteProjectCompletionCursorV1"]
