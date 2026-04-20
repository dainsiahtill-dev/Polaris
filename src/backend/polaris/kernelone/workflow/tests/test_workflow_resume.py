"""Tests for WorkflowEngine.resume_workflow().

Covers:
- resume_workflow() returns not_found when workflow doesn't exist
- resume_workflow() returns already_running when workflow is in-memory
- resume_workflow() returns invalid_request when id or name is missing
- resume_workflow() returns invalid_contract on bad payload
- Running tasks are reset to pending on resume
- Completed tasks are preserved on resume
- Successful resume stores state and starts workflow task
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest
from polaris.kernelone.workflow.contracts import WorkflowContract
from polaris.kernelone.workflow.engine import (
    WorkflowEngine,
    WorkflowRuntimeState,
)

# ---------------------------------------------------------------------------
# Mock Store
# ---------------------------------------------------------------------------


class MockExecution:
    """Minimal mock for stored workflow execution record."""

    def __init__(
        self,
        workflow_id: str = "wf-1",
        workflow_name: str = "test_workflow",
        status: str = "running",
    ) -> None:
        self.workflow_id = workflow_id
        self.workflow_name = workflow_name
        self.status = status


class MockTaskState:
    """Minimal mock for persisted task state."""

    def __init__(
        self,
        task_id: str,
        task_type: str = "activity",
        handler_name: str = "test_handler",
        status: str = "pending",
        attempt: int = 0,
        max_attempts: int = 3,
        error: str = "",
    ) -> None:
        self.task_id = task_id
        self.task_type = task_type
        self.handler_name = handler_name
        self.status = status
        self.attempt = attempt
        self.max_attempts = max_attempts
        self.started_at: str | None = None
        self.ended_at: str | None = None
        self.result: dict[str, Any] | None = None
        self.error = error
        self.metadata: dict[str, Any] = {}


class MockEvent:
    """Minimal mock for stored workflow event."""

    def __init__(
        self,
        event_type: str,
        payload: dict[str, Any] | None = None,
        created_at: str = "2026-04-04T00:00:00Z",
    ) -> None:
        self.event_type = event_type
        self.payload = payload or {}
        self.created_at = created_at


@dataclass
class MockWorkflowRuntimeStore:
    """Mock WorkflowRuntimeStore for testing resume_workflow."""

    executions: dict[str, MockExecution] = field(default_factory=dict)
    task_states: list[MockTaskState] = field(default_factory=list)
    events: list[MockEvent] = field(default_factory=list)
    appended_events: list[tuple[str, str, dict[str, Any]]] = field(default_factory=list)

    def init_schema(self) -> None:
        pass

    async def get_execution(self, workflow_id: str) -> MockExecution | None:
        return self.executions.get(workflow_id)

    async def create_execution(
        self,
        workflow_id: str,
        workflow_name: str,
        payload: dict[str, Any],
    ) -> None:
        pass

    async def append_event(
        self,
        workflow_id: str,
        event_type: str,
        payload: dict[str, Any],
    ) -> None:
        self.appended_events.append((workflow_id, event_type, payload))

    async def update_execution(
        self,
        workflow_id: str,
        *,
        status: str,
        result: dict[str, Any],
        close_time: str,
    ) -> None:
        pass

    async def upsert_task_state(
        self,
        *,
        workflow_id: str,
        task_id: str,
        task_type: str,
        handler_name: str,
        status: str,
        attempt: int,
        max_attempts: int,
        started_at: str | None,
        ended_at: str | None,
        result: dict[str, Any] | None,
        error: str,
        metadata: dict[str, Any],
    ) -> None:
        pass

    async def create_snapshot(self, workflow_id: str) -> Any:
        return None

    async def list_task_states(self, workflow_id: str) -> list[MockTaskState]:
        return self.task_states

    async def get_events(self, workflow_id: str, *, limit: int = 100) -> list[MockEvent]:
        return self.events


# ---------------------------------------------------------------------------
# Mock Components
# ---------------------------------------------------------------------------


class MockTimerWheel:
    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        pass

    async def schedule(self, delay: float, callback: Any) -> Any:
        return None


class MockTaskQueueManager:
    pass


class MockActivityRunner:
    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        pass

    async def submit(self, task: Any) -> Any:
        return None


class MockHandlerRegistry:
    def get_activity_handler(self, name: str) -> Any:
        async def dummy_activity(payload: dict[str, Any]) -> dict[str, Any]:
            return {"status": "ok"}

        return dummy_activity

    def get_workflow_handler(self, name: str) -> Any:
        return None


class MockDeadLetterQueue:
    async def enqueue(self, item: Any) -> None:
        pass


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_store() -> MockWorkflowRuntimeStore:
    return MockWorkflowRuntimeStore()


@pytest.fixture
def engine(mock_store: MockWorkflowRuntimeStore) -> WorkflowEngine:
    return WorkflowEngine(
        store=mock_store,
        timer_wheel=MockTimerWheel(),  # type: ignore[arg-type]
        task_queue_manager=MockTaskQueueManager(),  # type: ignore[arg-type]
        activity_runner=MockActivityRunner(),  # type: ignore[arg-type]
        handler_registry=MockHandlerRegistry(),  # type: ignore[arg-type]
        dead_letter_queue=MockDeadLetterQueue(),  # type: ignore[arg-type]
        max_concurrent_workflows=10,
    )


# ---------------------------------------------------------------------------
# resume_workflow: not found
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resume_workflow_not_found(engine: WorkflowEngine) -> None:
    """resume_workflow() must return not_found when workflow doesn't exist in store."""
    result = await engine.resume_workflow(
        workflow_name="nonexistent",
        workflow_id="does-not-exist",
    )

    assert result.submitted is False
    assert result.status == "not_found"
    assert "not found" in result.error


# ---------------------------------------------------------------------------
# resume_workflow: invalid request
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resume_workflow_invalid_request_missing_id(
    engine: WorkflowEngine,
    mock_store: MockWorkflowRuntimeStore,
) -> None:
    """resume_workflow() must return invalid_request when workflow_id is empty."""
    mock_store.executions["wf-1"] = MockExecution(
        workflow_id="wf-1",
        workflow_name="test",
    )
    result = await engine.resume_workflow(
        workflow_name="test",
        workflow_id="",
    )

    assert result.submitted is False
    assert result.status == "invalid_request"
    assert "required" in result.error


@pytest.mark.asyncio
async def test_resume_workflow_invalid_request_missing_name(
    engine: WorkflowEngine,
    mock_store: MockWorkflowRuntimeStore,
) -> None:
    """resume_workflow() must return invalid_request when workflow_name is empty."""
    mock_store.executions["wf-1"] = MockExecution(
        workflow_id="wf-1",
        workflow_name="test",
    )
    result = await engine.resume_workflow(
        workflow_name="",
        workflow_id="wf-1",
    )

    assert result.submitted is False
    assert result.status == "invalid_request"


# ---------------------------------------------------------------------------
# resume_workflow: already running
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resume_workflow_already_running(
    engine: WorkflowEngine,
    mock_store: MockWorkflowRuntimeStore,
) -> None:
    """resume_workflow() must return already_running when workflow is in memory."""
    mock_store.executions["wf-1"] = MockExecution(
        workflow_id="wf-1",
        workflow_name="test_workflow",
    )
    # Pre-populate _workflow_state to simulate already running
    engine._workflow_state["wf-1"] = WorkflowRuntimeState(
        workflow_id="wf-1",
        workflow_name="test_workflow",
        payload={},
        contract=WorkflowContract(
            mode="sequential",
            task_specs=(),
            max_concurrency=1,
            continue_on_error=False,
        ),
    )

    result = await engine.resume_workflow(
        workflow_name="test_workflow",
        workflow_id="wf-1",
    )

    assert result.submitted is False
    assert result.status == "already_running"


# ---------------------------------------------------------------------------
# resume_workflow: invalid contract
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resume_workflow_invalid_contract(
    engine: WorkflowEngine,
    mock_store: MockWorkflowRuntimeStore,
) -> None:
    """resume_workflow() must return invalid_contract when contract payload is invalid."""
    mock_store.executions["wf-1"] = MockExecution(
        workflow_id="wf-1",
        workflow_name="test_workflow",
    )
    # TaskSpec.from_mapping raises WorkflowContractError when task_id is missing
    result = await engine.resume_workflow(
        workflow_name="test_workflow",
        workflow_id="wf-1",
        payload={
            "orchestration": {
                "tasks": [
                    {"task_type": "activity"}  # missing task_id
                ]
            }
        },
    )

    assert result.submitted is False
    assert result.status == "invalid_contract"


# ---------------------------------------------------------------------------
# resume_workflow: running tasks reset to pending
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resume_workflow_resets_running_tasks(
    engine: WorkflowEngine,
    mock_store: MockWorkflowRuntimeStore,
) -> None:
    """Running tasks must be reset to pending with attempt=0 on resume."""
    mock_store.executions["wf-1"] = MockExecution(
        workflow_id="wf-1",
        workflow_name="test_workflow",
    )
    mock_store.task_states = [
        MockTaskState(
            task_id="t1",
            task_type="activity",
            handler_name="h1",
            status="running",  # Was running when checkpointed
            attempt=2,
        ),
        MockTaskState(
            task_id="t2",
            task_type="activity",
            handler_name="h2",
            status="completed",
            attempt=1,
        ),
    ]
    mock_store.events = [
        MockEvent(
            event_type="workflow_contract_loaded",
            payload={},
            created_at="2026-04-04T00:00:00Z",
        ),
    ]

    result = await engine.resume_workflow(
        workflow_name="test_workflow",
        workflow_id="wf-1",
        payload={
            "orchestration": {
                "tasks": [
                    {"id": "t1", "task_type": "activity", "handler": "h1"},
                    {"id": "t2", "task_type": "activity", "handler": "h2"},
                ]
            }
        },
    )

    assert result.submitted is True
    assert result.status == "resumed"

    # Verify state was stored in _workflow_state
    assert "wf-1" in engine._workflow_state
    state = engine._workflow_state["wf-1"]

    # t1 was "running" → should be reset to "pending" with attempt=0
    assert state.task_states["t1"].status == "pending"
    assert state.task_states["t1"].attempt == 0

    # t2 was "completed" → should be preserved
    assert state.task_states["t2"].status == "completed"
    assert state.task_states["t2"].attempt == 1


# ---------------------------------------------------------------------------
# resume_workflow: successful resume stores state and appends event
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resume_workflow_success(
    engine: WorkflowEngine,
    mock_store: MockWorkflowRuntimeStore,
) -> None:
    """Successful resume must store state, start workflow task, and append event."""
    mock_store.executions["wf-1"] = MockExecution(
        workflow_id="wf-1",
        workflow_name="test_workflow",
    )
    mock_store.task_states = [
        MockTaskState(
            task_id="t1",
            task_type="activity",
            handler_name="h1",
            status="completed",
        ),
    ]
    mock_store.events = [
        MockEvent(
            event_type="workflow_contract_loaded",
            payload={},
            created_at="2026-04-04T00:00:00Z",
        ),
    ]

    result = await engine.resume_workflow(
        workflow_name="test_workflow",
        workflow_id="wf-1",
        payload={
            "orchestration": {
                "tasks": [
                    {"id": "t1", "task_type": "activity", "handler": "h1"},
                ]
            }
        },
    )

    assert result.submitted is True
    assert result.status == "resumed"
    assert "wf-1" in engine._workflow_state
    assert "wf-1" in engine._workflow_tasks

    # Verify workflow_resumed event was appended
    appended = mock_store.appended_events
    assert any(ev[1] == "workflow_resumed" for ev in appended)


# ---------------------------------------------------------------------------
# resume_workflow: resume with new payload merged
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resume_workflow_new_payload(
    engine: WorkflowEngine,
    mock_store: MockWorkflowRuntimeStore,
) -> None:
    """resume_workflow() with new payload must merge it with original."""
    mock_store.executions["wf-1"] = MockExecution(
        workflow_id="wf-1",
        workflow_name="test_workflow",
    )
    mock_store.events = [
        MockEvent(
            event_type="workflow_contract_loaded",
            payload={},
            created_at="2026-04-04T00:00:00Z",
        ),
    ]

    result = await engine.resume_workflow(
        workflow_name="test_workflow",
        workflow_id="wf-1",
        payload={
            "orchestration": {
                "tasks": [
                    {"id": "t1", "task_type": "activity", "handler": "h1"},
                ]
            }
        },
    )

    assert result.submitted is True
    state = engine._workflow_state["wf-1"]
    # Payload should contain the new payload
    assert "t1" in state.task_states


# ---------------------------------------------------------------------------
# resume_workflow: resume with failed task preserved
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resume_workflow_resets_failed_tasks_to_pending_for_retry(
    engine: WorkflowEngine,
    mock_store: MockWorkflowRuntimeStore,
) -> None:
    """Failed (DLQ) tasks must be reset to pending on resume so they can be retried.

    This is the core self-healing behavior of the Phoenix Protocol: a task that
    exhausted all retries and entered the DLQ must be re-executed when the
    workflow is resumed. The fix resets status=running|failed -> pending.
    """
    mock_store.executions["wf-1"] = MockExecution(
        workflow_id="wf-1",
        workflow_name="test_workflow",
    )
    # t1 was failed (DLQ) - status "failed" from exhausted retries
    mock_store.task_states = [
        MockTaskState(
            task_id="t1",
            task_type="activity",
            handler_name="h1",
            status="failed",
            attempt=3,
            error="all retries exhausted",
        ),
    ]
    mock_store.events = [
        MockEvent(
            event_type="workflow_contract_loaded",
            payload={},
            created_at="2026-04-04T00:00:00Z",
        ),
    ]

    result = await engine.resume_workflow(
        workflow_name="test_workflow",
        workflow_id="wf-1",
        payload={
            "orchestration": {
                "tasks": [
                    {"id": "t1", "task_type": "activity", "handler": "h1"},
                ]
            }
        },
    )

    assert result.submitted is True
    state = engine._workflow_state["wf-1"]
    # Failed task MUST be reset to pending so _ready_specs picks it up for retry
    assert state.task_states["t1"].status == "pending"
    assert state.task_states["t1"].attempt == 0
    # Error is cleared so retry starts fresh
    assert state.task_states["t1"].error == ""


@pytest.mark.asyncio
async def test_resume_workflow_completed_tasks_preserved(
    engine: WorkflowEngine,
    mock_store: MockWorkflowRuntimeStore,
) -> None:
    """Completed tasks must NOT be reset on resume - they have already succeeded."""
    mock_store.executions["wf-1"] = MockExecution(
        workflow_id="wf-1",
        workflow_name="test_workflow",
    )
    mock_store.task_states = [
        MockTaskState(
            task_id="t1",
            task_type="activity",
            handler_name="h1",
            status="completed",
            attempt=1,
        ),
    ]
    mock_store.events = [
        MockEvent(
            event_type="workflow_contract_loaded",
            payload={},
            created_at="2026-04-04T00:00:00Z",
        ),
    ]

    result = await engine.resume_workflow(
        workflow_name="test_workflow",
        workflow_id="wf-1",
        payload={
            "orchestration": {
                "tasks": [
                    {"id": "t1", "task_type": "activity", "handler": "h1"},
                ]
            }
        },
    )

    assert result.submitted is True
    state = engine._workflow_state["wf-1"]
    # Completed tasks are preserved as terminal state
    assert state.task_states["t1"].status == "completed"
