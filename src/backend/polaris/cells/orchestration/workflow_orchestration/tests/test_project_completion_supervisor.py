"""Event-driven production-supervisor behavior."""

from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path

import pytest
from polaris.cells.orchestration.workflow_orchestration.internal.project_completion_supervisor import (
    EventDrivenProjectCompletionSupervisorV1,
)
from polaris.cells.orchestration.workflow_orchestration.public.project_completion import (
    _PROJECT_COMPLETION_RESULT_AUTHORITY_TOKEN,
    AdvanceProjectCompletionCommandV1,
    ProjectCompletionAdvanceResultV1,
    ProjectCompletionIdentityV1,
)
from polaris.cells.orchestration.workflow_runtime.internal.project_completion_cursor import (
    SqliteProjectCompletionCursorV1,
)
from polaris.cells.orchestration.workflow_runtime.public.project_completion_cursor import (
    ProjectCompletionCursorIdentityV1,
    ProjectCompletionCursorLimitsV1,
    ProjectCompletionCursorRegistrationV1,
)
from polaris.infrastructure.db.repositories.workflow_runtime_store import SqliteRuntimeStore


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _command_from_registration(
    registration: ProjectCompletionCursorRegistrationV1,
) -> AdvanceProjectCompletionCommandV1:
    return AdvanceProjectCompletionCommandV1(
        identity=ProjectCompletionIdentityV1(
            workspace=registration.identity.workspace,
            project_id=registration.identity.project_id,
            run_id=registration.identity.run_id,
            completion_contract_hash=registration.identity.completion_contract_hash,
        ),
        max_actions=registration.limits.max_actions,
        max_dispatch_attempts=registration.limits.max_dispatch_attempts,
        max_no_progress_observations=registration.limits.max_no_progress_observations,
        dispatch_lease_seconds=registration.limits.dispatch_lease_seconds,
    )


@pytest.mark.asyncio
async def test_nonterminal_control_plane_block_retries_only_on_explicit_wake(
    tmp_path: Path,
) -> None:
    identity = ProjectCompletionIdentityV1(
        workspace=str(tmp_path / "workspace"),
        project_id="project-a",
        run_id="run-a",
        completion_contract_hash=_hash("contract"),
    )
    command = AdvanceProjectCompletionCommandV1(identity=identity)
    invoked = asyncio.Event()
    calls = 0

    async def advance(
        current: AdvanceProjectCompletionCommandV1,
    ) -> ProjectCompletionAdvanceResultV1:
        nonlocal calls
        calls += 1
        invoked.set()
        status = "control_plane_blocked" if calls == 1 else "completed_verified"
        return ProjectCompletionAdvanceResultV1(
            identity=current.identity,
            workflow_id=_hash("workflow"),
            status=status,
            reason_codes=(status,),
            event_seq=calls,
            _authority_token=_PROJECT_COMPLETION_RESULT_AUTHORITY_TOKEN,
        )

    supervisor = EventDrivenProjectCompletionSupervisorV1(advance=advance)
    await supervisor.start()
    try:
        await supervisor.submit(command)
        await asyncio.wait_for(invoked.wait(), timeout=1)
        await asyncio.sleep(0)
        assert calls == 1
        first = supervisor.last_result(command)
        assert first is not None
        assert first.status == "control_plane_blocked"
        assert first.terminal is False

        invoked.clear()
        await supervisor.wake()
        await asyncio.wait_for(invoked.wait(), timeout=1)
        await asyncio.sleep(0)
        assert calls == 2
        second = supervisor.last_result(command)
        assert second is not None
        assert second.status == "completed_verified"
        assert second.terminal is True

        invoked.clear()
        await supervisor.wake()
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(invoked.wait(), timeout=0.02)
        assert calls == 2
    finally:
        await supervisor.stop()


@pytest.mark.asyncio
async def test_restart_recovers_active_identity_from_sqlite_cursor(tmp_path: Path) -> None:
    database_path = str(tmp_path / "convergence.sqlite3")
    workspace = str((tmp_path / "workspace").resolve())
    identity = ProjectCompletionIdentityV1(
        workspace=workspace,
        project_id="project-restart",
        run_id="run-restart",
        completion_contract_hash=_hash("restart-contract"),
    )
    cursor_identity = ProjectCompletionCursorIdentityV1(**identity.as_payload())
    limits = ProjectCompletionCursorLimitsV1(
        max_actions=8,
        max_dispatch_attempts=3,
        max_no_progress_observations=3,
        dispatch_lease_seconds=120,
    )
    first_cursor = SqliteProjectCompletionCursorV1(
        SqliteRuntimeStore(database_path, workspace=workspace)
    )
    await first_cursor.ensure_cursor(_hash("restart-workflow"), cursor_identity, limits)

    calls: list[str] = []
    invoked = asyncio.Event()

    async def advance(
        current: AdvanceProjectCompletionCommandV1,
    ) -> ProjectCompletionAdvanceResultV1:
        calls.append(current.identity.run_id)
        invoked.set()
        return ProjectCompletionAdvanceResultV1(
            identity=current.identity,
            workflow_id=_hash("restart-workflow"),
            status="control_plane_blocked",
            reason_codes=("owner_temporarily_unavailable",),
            event_seq=1,
            _authority_token=_PROJECT_COMPLETION_RESULT_AUTHORITY_TOKEN,
        )

    async def recover() -> tuple[AdvanceProjectCompletionCommandV1, ...]:
        restarted_cursor = SqliteProjectCompletionCursorV1(
            SqliteRuntimeStore(database_path, workspace=workspace)
        )
        return tuple(
            _command_from_registration(registration)
            for registration in await restarted_cursor.list_resumable_cursors()
        )

    first_process = EventDrivenProjectCompletionSupervisorV1(advance=advance, recover=recover)
    await first_process.start()
    await asyncio.wait_for(invoked.wait(), timeout=1)
    await first_process.stop()

    invoked.clear()
    restarted_process = EventDrivenProjectCompletionSupervisorV1(advance=advance, recover=recover)
    await restarted_process.start()
    try:
        await asyncio.wait_for(invoked.wait(), timeout=1)
        assert calls == ["run-restart", "run-restart"]
    finally:
        await restarted_process.stop()


@pytest.mark.asyncio
async def test_restart_recovery_rejects_coerced_cursor_limits(tmp_path: Path) -> None:
    """Durable recovery must not normalize a corrupt string budget into authority."""

    workspace = str((tmp_path / "strict-workspace").resolve())
    store = SqliteRuntimeStore(str(tmp_path / "strict.sqlite3"), workspace=workspace)
    await store.create_execution(
        _hash("strict-workflow"),
        "project_completion_convergence.v1",
        {
            "identity": {
                "workspace": workspace,
                "project_id": "project-strict",
                "run_id": "run-strict",
                "completion_contract_hash": _hash("strict-contract"),
            },
            "limits": {
                "max_actions": "8",
                "max_dispatch_attempts": 3,
                "max_no_progress_observations": 3,
                "dispatch_lease_seconds": 120,
            },
        },
    )

    cursor = SqliteProjectCompletionCursorV1(store)
    with pytest.raises(ValueError, match="max_actions must be an exact positive int"):
        await cursor.list_resumable_cursors()
