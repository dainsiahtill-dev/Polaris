"""CQS-compliance tests for DirectorService.get_status().

Verifies:
1. get_status() is idempotent – repeated calls do NOT mutate self.state.
2. Concurrent get_status() calls are stable – no race-induced state mutation.
3. State advancement (RUNNING -> IDLE) only occurs via _try_finalize_idle(),
   never inside get_status().
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Minimal stubs — avoids importing heavy infrastructure dependencies
# ---------------------------------------------------------------------------


@dataclass
class _FakeTask:
    id: str
    status: object
    blocked_by: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"id": self.id, "status": str(self.status)}


class _FakeWorker:
    def __init__(self, *, available: bool = True) -> None:
        self._available = available
        self.status = "IDLE" if available else "BUSY"

    def is_available(self) -> bool:
        return self._available


class _FakeBudget:
    def to_dict(self) -> dict:
        return {"remaining": 1000}


class _FakeTokenService:
    def get_budget_status(self) -> _FakeBudget:
        return _FakeBudget()


class _FakeTaskService:
    def __init__(self, *, tasks: list | None = None) -> None:
        self._tasks = tasks or []

    async def get_tasks(self, status=None):
        return self._tasks

    async def get_ready_task_count(self) -> int:
        return 0


class _FakeWorkerService:
    def __init__(self, *, workers: list | None = None) -> None:
        self._workers = workers or []

    async def get_workers(self) -> list:
        return self._workers


# ---------------------------------------------------------------------------
# Helper: build a DirectorService with minimal injectable stubs
# ---------------------------------------------------------------------------


def _make_service(
    *,
    state_name: str = "RUNNING",
    workers: list | None = None,
    tasks: list | None = None,
    main_loop_done: bool = False,
):
    """Construct a DirectorService with mocked-out dependencies."""
    from polaris.cells.director.execution.service import DirectorConfig, DirectorService, DirectorState

    config = DirectorConfig(workspace="/tmp/test_workspace")

    # Patch all heavy collaborators so __init__ doesn't touch the filesystem
    with (
        patch("polaris.cells.director.execution.service.get_security_service", return_value=MagicMock()),
        patch("polaris.cells.director.execution.service.get_todo_service", return_value=MagicMock()),
        patch("polaris.cells.director.execution.service.get_token_service", return_value=_FakeTokenService()),
        patch("polaris.cells.director.execution.service.get_transcript_service", return_value=MagicMock()),
        patch("polaris.cells.director.execution.service.MessageBus", return_value=MagicMock()),
        patch("polaris.cells.director.execution.service.TaskService"),
        patch("polaris.cells.director.execution.service.WorkerService"),
    ):
        svc = DirectorService(config)

    # Inject fake services
    svc._task_service = _FakeTaskService(tasks=tasks)
    svc._worker_service = _FakeWorkerService(workers=workers)
    svc.token = _FakeTokenService()

    # Set initial state
    svc.state = DirectorState[state_name]

    # Wire main_loop_task mock
    if main_loop_done:
        task_mock = MagicMock()
        task_mock.done.return_value = True
        svc._main_loop_task = task_mock
    else:
        svc._main_loop_task = None

    return svc


# ---------------------------------------------------------------------------
# Test 1: get_status() is a pure query – no state mutation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_status_does_not_mutate_state_when_running_with_no_workers() -> None:
    """Regression: get_status() must NOT advance RUNNING -> IDLE even when
    the main loop has exited and there are no workers.  That transition belongs
    exclusively to _try_finalize_idle().
    """

    svc = _make_service(state_name="RUNNING", workers=[], main_loop_done=True)
    original_state = svc.state

    # Call get_status() – must not mutate state
    await svc.get_status()

    assert svc.state == original_state, (
        f"get_status() must not modify state. Expected {original_state!r}, got {svc.state!r}"
    )


@pytest.mark.asyncio
async def test_get_status_idempotent_on_repeated_calls() -> None:
    """Multiple successive get_status() calls must return identical state fields."""
    from polaris.cells.director.execution.service import DirectorState

    svc = _make_service(state_name="RUNNING", workers=[], main_loop_done=True)

    result1 = await svc.get_status()
    result2 = await svc.get_status()
    result3 = await svc.get_status()

    assert result1["state"] == result2["state"] == result3["state"], (
        "Repeated get_status() calls must return the same state"
    )
    assert svc.state == DirectorState.RUNNING, "Underlying state must remain RUNNING after repeated queries"


# ---------------------------------------------------------------------------
# Test 2: concurrent get_status() calls are stable
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_status_concurrent_calls_do_not_mutate_state() -> None:
    """Concurrent invocations of get_status() must not race each other to
    produce a state mutation.
    """
    from polaris.cells.director.execution.service import DirectorState

    svc = _make_service(state_name="RUNNING", workers=[], main_loop_done=True)

    results = await asyncio.gather(*(svc.get_status() for _ in range(20)))

    # All results must agree on RUNNING
    states = {r["state"] for r in results}
    assert states == {"RUNNING"}, f"Concurrent get_status() produced divergent states: {states}"
    assert svc.state == DirectorState.RUNNING, "Underlying state must remain RUNNING after concurrent queries"


# ---------------------------------------------------------------------------
# Test 3: _try_finalize_idle() IS the correct mutation path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_try_finalize_idle_advances_running_to_idle() -> None:
    """_try_finalize_idle() must transition RUNNING -> IDLE when the loop has
    exited and no workers remain.  This is the single authorised mutation path.
    """
    from polaris.cells.director.execution.service import DirectorState

    svc = _make_service(state_name="RUNNING", workers=[], main_loop_done=True)

    await svc._try_finalize_idle()

    assert svc.state == DirectorState.IDLE, (
        f"_try_finalize_idle() should have advanced state to IDLE, got {svc.state!r}"
    )
    assert svc._stopped_at is not None, "_stopped_at must be recorded on finalization"


@pytest.mark.asyncio
async def test_try_finalize_idle_is_noop_when_workers_remain() -> None:
    """_try_finalize_idle() must NOT advance state when workers are still alive."""
    from polaris.cells.director.execution.service import DirectorState

    svc = _make_service(
        state_name="RUNNING",
        workers=[_FakeWorker()],  # one active worker
        main_loop_done=True,
    )

    await svc._try_finalize_idle()

    assert svc.state == DirectorState.RUNNING, "_try_finalize_idle() must not transition when workers remain"


@pytest.mark.asyncio
async def test_try_finalize_idle_is_noop_when_not_running() -> None:
    """_try_finalize_idle() must be a no-op for states other than RUNNING."""

    for state_name in ("IDLE", "STOPPED", "STOPPING", "PAUSED"):
        svc = _make_service(state_name=state_name, workers=[], main_loop_done=True)
        initial = svc.state
        await svc._try_finalize_idle()
        assert svc.state == initial, f"_try_finalize_idle() must not modify state {state_name!r}"


# ---------------------------------------------------------------------------
# Test 4: get_status() after _try_finalize_idle() reflects new state
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_status_reflects_idle_after_finalize() -> None:
    """After _try_finalize_idle() runs, get_status() must report IDLE without
    triggering another mutation itself.
    """
    from polaris.cells.director.execution.service import DirectorState

    svc = _make_service(state_name="RUNNING", workers=[], main_loop_done=True)

    # Explicit lifecycle command
    await svc._try_finalize_idle()
    assert svc.state == DirectorState.IDLE

    # Query must reflect the updated state
    status = await svc.get_status()
    assert status["state"] == "IDLE", f"get_status() did not reflect IDLE after finalization: {status['state']!r}"

    # Second query must be stable
    status2 = await svc.get_status()
    assert status2["state"] == "IDLE"
    assert svc.state == DirectorState.IDLE, "State must not change after second query"
