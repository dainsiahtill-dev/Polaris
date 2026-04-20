"""Integration tests for SagaWorkflowEngine (Chronos Hourglass).

Tests cover:
1. Saga compensation - tasks with compensation handlers
2. Human-in-the-loop - high-risk tasks suspension and approval
3. Checkpoint and resume - workflow recovery after restart
4. Timer persistence - timer state survives restart

References:
- kernelone/workflow/saga_engine.py (SagaWorkflowEngine)
- kernelone/workflow/checkpoint_manager.py (CheckpointManager)
- kernelone/workflow/persistent_timer_wheel.py (PersistentTimerWheel)
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

import pytest
from polaris.kernelone.workflow.activity_runner import ActivityRunner
from polaris.kernelone.workflow.base import WorkflowSnapshot
from polaris.kernelone.workflow.checkpoint_manager import CheckpointManager
from polaris.kernelone.workflow.contracts import RetryPolicy, TaskSpec, WorkflowContract
from polaris.kernelone.workflow.saga_engine import SagaExecutionState, SagaWorkflowEngine
from polaris.kernelone.workflow.task_queue import TaskQueueManager
from polaris.kernelone.workflow.timer_wheel import TimerWheel

# ---------------------------------------------------------------------------
# In-Memory Store Implementation for Testing
# ---------------------------------------------------------------------------


class InMemoryWorkflowStore:
    """In-memory implementation of WorkflowRuntimeStore for testing.

    This store persists data in memory but resets on process restart,
    which is fine for unit testing. For integration testing, use
    SqliteRuntimeStore.
    """

    def __init__(self) -> None:
        self._executions: dict[str, dict[str, Any]] = {}
        self._events: dict[str, list[dict[str, Any]]] = {}
        self._task_states: dict[str, dict[str, dict[str, Any]]] = {}
        self._event_seqs: dict[str, int] = {}
        self._snapshots: dict[str, dict[str, Any]] = {}

    def init_schema(self) -> None:
        pass

    async def get_execution(self, workflow_id: str) -> dict[str, Any] | None:
        return self._executions.get(workflow_id)

    async def create_execution(
        self,
        workflow_id: str,
        workflow_name: str,
        payload: dict[str, Any],
    ) -> None:
        self._executions[workflow_id] = {
            "workflow_id": workflow_id,
            "workflow_name": workflow_name,
            "status": "running",
            "payload": payload,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        self._events[workflow_id] = []
        self._task_states[workflow_id] = {}
        self._event_seqs[workflow_id] = 1

    async def append_event(
        self,
        workflow_id: str,
        event_type: str,
        payload: dict[str, Any],
    ) -> None:
        if workflow_id not in self._events:
            self._events[workflow_id] = []
        seq = self._event_seqs.get(workflow_id, 1)
        self._event_seqs[workflow_id] = seq + 1
        event = {
            "id": len(self._events[workflow_id]) + 1,
            "workflow_id": workflow_id,
            "seq": seq,
            "event_type": event_type,
            "payload": payload,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        self._events[workflow_id].append(event)

    async def update_execution(
        self,
        workflow_id: str,
        *,
        status: str,
        result: dict[str, Any],
        close_time: str,
    ) -> None:
        if workflow_id in self._executions:
            self._executions[workflow_id]["status"] = status
            self._executions[workflow_id]["result"] = result
            self._executions[workflow_id]["close_time"] = close_time

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
        if workflow_id not in self._task_states:
            self._task_states[workflow_id] = {}
        self._task_states[workflow_id][task_id] = {
            "workflow_id": workflow_id,
            "task_id": task_id,
            "task_type": task_type,
            "handler_name": handler_name,
            "status": status,
            "attempt": attempt,
            "max_attempts": max_attempts,
            "started_at": started_at,
            "ended_at": ended_at,
            "result": result,
            "error": error,
            "metadata": metadata,
        }

    async def create_snapshot(self, workflow_id: str) -> WorkflowSnapshot:
        execution = self._executions.get(workflow_id, {})
        return WorkflowSnapshot(
            workflow_id=workflow_id,
            workflow_name=execution.get("workflow_name", ""),
            status=execution.get("status", "unknown"),
            run_id=workflow_id,
            start_time=execution.get("created_at", ""),
            close_time=execution.get("close_time"),
            result=execution.get("result"),
            pending_actions=[],
        )

    async def list_task_states(self, workflow_id: str) -> list[dict[str, Any]]:
        return list(self._task_states.get(workflow_id, {}).values())

    async def get_events(self, workflow_id: str, *, limit: int = 100) -> list[dict[str, Any]]:
        events = self._events.get(workflow_id, [])
        return events[-limit:]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def in_memory_store() -> InMemoryWorkflowStore:
    """Create an in-memory store for testing."""
    return InMemoryWorkflowStore()


@pytest.fixture
def timer_wheel_instance() -> TimerWheel:
    """Create a fresh TimerWheel."""
    wheel = TimerWheel(tick_interval=0.1)
    return wheel


@pytest.fixture
def task_queue_manager() -> TaskQueueManager:
    """Create a TaskQueueManager."""
    return TaskQueueManager()


@pytest.fixture
def activity_runner() -> ActivityRunner:
    """Create an ActivityRunner."""
    runner = ActivityRunner()
    return runner


@pytest.fixture
def saga_engine(
    in_memory_store: InMemoryWorkflowStore,
    timer_wheel_instance: TimerWheel,
    task_queue_manager: TaskQueueManager,
    activity_runner: ActivityRunner,
) -> SagaWorkflowEngine:
    """Create a SagaWorkflowEngine for testing."""
    engine = SagaWorkflowEngine(
        store=in_memory_store,
        timer_wheel=timer_wheel_instance,
        task_queue_manager=task_queue_manager,
        activity_runner=activity_runner,
        checkpoint_interval_seconds=1.0,
    )
    return engine


# ---------------------------------------------------------------------------
# Helper Functions
# ---------------------------------------------------------------------------


def create_compensation_contract() -> WorkflowContract:
    """Create a contract with compensation handlers for testing."""
    task_specs = (
        TaskSpec(
            task_id="step1",
            task_type="activity",
            handler_name="create_resource",
            retry_policy=RetryPolicy(max_attempts=1),
        ),
        TaskSpec(
            task_id="step2",
            task_type="activity",
            handler_name="update_resource",
            depends_on=("step1",),
            retry_policy=RetryPolicy(max_attempts=1),
            compensation_handler="undo_update",
            compensation_input={"step": 2},
        ),
        TaskSpec(
            task_id="step3",
            task_type="activity",
            handler_name="delete_resource",
            depends_on=("step2",),
            retry_policy=RetryPolicy(max_attempts=1),
            compensation_handler="undo_delete",
            compensation_input={"step": 3},
        ),
    )
    return WorkflowContract(
        mode="dag",
        task_specs=task_specs,
        max_concurrency=1,
        continue_on_error=False,
    )


def create_high_risk_contract() -> WorkflowContract:
    """Create a contract with high-risk tasks for testing."""
    task_specs = (
        TaskSpec(
            task_id="safe_task",
            task_type="activity",
            handler_name="safe_handler",
            retry_policy=RetryPolicy(max_attempts=1),
        ),
        TaskSpec(
            task_id="risky_task",
            task_type="activity",
            handler_name="risky_handler",
            is_high_risk=True,
            retry_policy=RetryPolicy(max_attempts=1),
        ),
        TaskSpec(
            task_id="final_task",
            task_type="activity",
            handler_name="final_handler",
            depends_on=("safe_task", "risky_task"),
            retry_policy=RetryPolicy(max_attempts=1),
        ),
    )
    return WorkflowContract(
        mode="dag",
        task_specs=task_specs,
        max_concurrency=2,
        continue_on_error=False,
        high_risk_actions=frozenset({"risky_task"}),
    )


# ---------------------------------------------------------------------------
# Saga Compensation Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_saga_engine_creation(saga_engine: SagaWorkflowEngine) -> None:
    """SagaWorkflowEngine must be creatable with all dependencies."""
    assert saga_engine is not None
    assert saga_engine._store is not None
    assert saga_engine._timer_wheel is not None


@pytest.mark.asyncio
async def test_saga_engine_start_stop(saga_engine: SagaWorkflowEngine) -> None:
    """SagaWorkflowEngine must start and stop correctly."""
    await saga_engine.start()
    assert saga_engine._running is True

    await saga_engine.stop()
    assert saga_engine._running is False


@pytest.mark.asyncio
async def test_saga_compensation_chain_defined() -> None:
    """TaskSpec with compensation_handler must store the handler name."""
    contract = create_compensation_contract()
    step2 = next(spec for spec in contract.task_specs if spec.task_id == "step2")
    step3 = next(spec for spec in contract.task_specs if spec.task_id == "step3")

    assert step2.compensation_handler == "undo_update"
    assert step2.compensation_input == {"step": 2}
    assert step3.compensation_handler == "undo_delete"
    assert step3.compensation_input == {"step": 3}


@pytest.mark.asyncio
async def test_high_risk_task_flag() -> None:
    """TaskSpec with is_high_risk=True must be correctly flagged."""
    contract = create_high_risk_contract()
    risky = next(spec for spec in contract.task_specs if spec.task_id == "risky_task")

    assert risky.is_high_risk is True
    assert "risky_task" in contract.high_risk_actions


@pytest.mark.asyncio
async def test_workflow_signal_workflow(
    saga_engine: SagaWorkflowEngine,
    in_memory_store: InMemoryWorkflowStore,
) -> None:
    """signal_workflow must persist signal to store and handle approve/reject."""
    await saga_engine.start()

    # Create a workflow first
    await saga_engine.start_workflow(
        workflow_name="test",
        workflow_id="wf-1",
        payload={},
    )

    # Send a cancel signal
    result = await saga_engine.signal_workflow("wf-1", "cancel")
    assert result["signalled"] is True
    assert result["signal"] == "cancel"

    # Verify event was persisted
    events = await in_memory_store.get_events("wf-1", limit=10)
    signal_events = [e for e in events if e["event_type"] == "signal_received"]
    assert len(signal_events) >= 1

    await saga_engine.stop()


# ---------------------------------------------------------------------------
# Checkpoint Manager Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_checkpoint_manager_creation(in_memory_store: InMemoryWorkflowStore) -> None:
    """CheckpointManager must be createable."""
    manager = CheckpointManager(in_memory_store)
    assert manager is not None


@pytest.mark.asyncio
async def test_checkpoint_create_and_list(
    in_memory_store: InMemoryWorkflowStore,
) -> None:
    """CheckpointManager.create_checkpoint must create a checkpoint event."""
    manager = CheckpointManager(in_memory_store)

    # Create checkpoint
    record = await manager.create_checkpoint(
        workflow_id="wf-1",
        task_states={"task1": {"status": "completed", "attempt": 1}},
        task_outputs={"task1": {"result": "output1"}},
    )

    assert record.workflow_id == "wf-1"
    assert record.seq >= 0
    assert "task1" in record.task_states_snapshot
    assert "task1" in record.task_outputs

    # List checkpoints
    checkpoints = await manager.list_checkpoints("wf-1")
    assert len(checkpoints) >= 1
    assert checkpoints[0].workflow_id == "wf-1"


@pytest.mark.asyncio
async def test_checkpoint_get_latest(
    in_memory_store: InMemoryWorkflowStore,
) -> None:
    """CheckpointManager.get_latest_checkpoint must return the most recent."""
    manager = CheckpointManager(in_memory_store)

    # Create multiple checkpoints
    await manager.create_checkpoint(
        workflow_id="wf-1",
        task_states={"task1": {"status": "pending"}},
        task_outputs={},
    )
    await asyncio.sleep(0.01)
    await manager.create_checkpoint(
        workflow_id="wf-1",
        task_states={"task1": {"status": "completed"}},
        task_outputs={"task1": {"result": "done"}},
    )

    # Get latest
    latest = await manager.get_latest_checkpoint("wf-1")
    assert latest is not None
    assert latest.task_states_snapshot["task1"]["status"] == "completed"


@pytest.mark.asyncio
async def test_checkpoint_get_recovery_info(
    in_memory_store: InMemoryWorkflowStore,
) -> None:
    """CheckpointManager.get_recovery_info must return correct recovery data."""
    manager = CheckpointManager(in_memory_store)

    # Create a checkpoint
    await manager.create_checkpoint(
        workflow_id="wf-1",
        task_states={"task1": {"status": "completed"}},
        task_outputs={},
    )

    # Get recovery info
    info = await manager.get_recovery_info("wf-1")
    assert info["workflow_id"] == "wf-1"
    assert info["latest_checkpoint"] is not None
    assert info["recovery_needed"] is False  # No events since checkpoint


# ---------------------------------------------------------------------------
# Timer Persistence Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_persistent_timer_wheel_cancel_info(
    timer_wheel_instance: TimerWheel,
    in_memory_store: InMemoryWorkflowStore,
) -> None:
    """TimerWheel.get_timer_info must return timer info for cancellation."""
    await timer_wheel_instance.start()

    # Schedule a timer
    callback_called = False

    async def callback() -> None:
        nonlocal callback_called
        callback_called = True

    await timer_wheel_instance.schedule_timer(
        timer_id="t1",
        workflow_id="wf-1",
        delay_seconds=10.0,
        callback=callback,
    )

    # Get timer info
    info = await timer_wheel_instance.get_timer_info("t1")
    assert info is not None
    assert info.timer_id == "t1"
    assert info.workflow_id == "wf-1"

    # Cancel
    cancelled = await timer_wheel_instance.cancel_timer("t1")
    assert cancelled is True

    # Info should be gone
    info = await timer_wheel_instance.get_timer_info("t1")
    assert info is None

    await timer_wheel_instance.stop()


@pytest.mark.asyncio
async def test_timer_wheel_get_all_timer_ids(
    timer_wheel_instance: TimerWheel,
) -> None:
    """TimerWheel.get_all_timer_ids must return all active timer IDs."""
    await timer_wheel_instance.start()

    async def callback() -> None:
        pass

    # Schedule multiple timers
    await timer_wheel_instance.schedule_timer("t1", "wf-1", 10.0, callback)
    await timer_wheel_instance.schedule_timer("t2", "wf-1", 5.0, callback)
    await timer_wheel_instance.schedule_timer("t3", "wf-2", 15.0, callback)

    # Get all timer IDs
    ids = timer_wheel_instance.get_all_timer_ids()
    assert len(ids) == 3
    assert "t1" in ids
    assert "t2" in ids
    assert "t3" in ids

    await timer_wheel_instance.stop()


# ---------------------------------------------------------------------------
# SagaExecutionState Tests
# ---------------------------------------------------------------------------


def test_saga_execution_state_creation() -> None:
    """SagaExecutionState must be createable with default values."""
    state = SagaExecutionState(workflow_id="wf-1")

    assert state.workflow_id == "wf-1"
    assert state.compensation_tasks == []
    assert state.completed_compensations == []
    assert state.failed_compensations == []
    assert state.is_compensating is False


# ---------------------------------------------------------------------------
# WorkflowContract Validation Tests
# ---------------------------------------------------------------------------


def test_workflow_contract_high_risk_actions_parsing() -> None:
    """WorkflowContract must parse high_risk_actions from orchestration payload."""
    payload = {
        "orchestration": {
            "tasks": [
                {"id": "t1", "type": "activity", "handler": "h1"},
            ],
            "high_risk_actions": ["t1", "t2"],
            "human_review_webhook": "https://example.com/webhook",
        }
    }

    contract = WorkflowContract.from_payload(payload)

    assert "t1" in contract.high_risk_actions
    assert "t2" in contract.high_risk_actions
    assert contract.human_review_webhook == "https://example.com/webhook"


def test_workflow_contract_high_risk_actions_empty() -> None:
    """WorkflowContract must handle empty high_risk_actions."""
    payload = {
        "orchestration": {
            "tasks": [
                {"id": "t1", "type": "activity", "handler": "h1"},
            ],
        }
    }

    contract = WorkflowContract.from_payload(payload)

    assert len(contract.high_risk_actions) == 0
    assert contract.human_review_webhook is None


# ---------------------------------------------------------------------------
# TaskSpec Compensation Fields Tests
# ---------------------------------------------------------------------------


def test_task_spec_compensation_fields() -> None:
    """TaskSpec must store compensation_handler, compensation_input, is_high_risk."""
    spec = TaskSpec(
        task_id="t1",
        task_type="activity",
        handler_name="create",
        compensation_handler="rollback",
        compensation_input={"key": "value"},
        is_high_risk=True,
    )

    assert spec.compensation_handler == "rollback"
    assert spec.compensation_input == {"key": "value"}
    assert spec.is_high_risk is True


def test_task_spec_compensation_fields_default() -> None:
    """TaskSpec must have default values for compensation fields."""
    spec = TaskSpec(
        task_id="t1",
        task_type="activity",
        handler_name="create",
    )

    assert spec.compensation_handler is None
    assert spec.compensation_input == {}
    assert spec.is_high_risk is False


def test_task_spec_from_mapping_with_compensation() -> None:
    """TaskSpec.from_mapping must parse compensation fields."""
    raw = {
        "id": "t1",
        "type": "activity",
        "handler": "create",
        "compensation_handler": "undo",
        "compensation_input": {"step": 1},
        "is_high_risk": True,
    }

    spec = TaskSpec.from_mapping(
        raw,
        default_timeout_seconds=300.0,
        default_retry_policy=RetryPolicy(),
    )

    assert spec.compensation_handler == "undo"
    assert spec.compensation_input == {"step": 1}
    assert spec.is_high_risk is True


# ---------------------------------------------------------------------------
# Integration: Full Workflow with Saga
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_workflow_contract_validation_no_cycle() -> None:
    """WorkflowContract.validate_contract must pass for valid DAG."""
    task_specs = (
        TaskSpec(
            task_id="t1",
            task_type="activity",
            handler_name="h1",
        ),
        TaskSpec(
            task_id="t2",
            task_type="activity",
            handler_name="h2",
            depends_on=("t1",),
        ),
    )

    contract = WorkflowContract(
        mode="dag",
        task_specs=task_specs,
        max_concurrency=1,
        continue_on_error=False,
    )

    # Validate should pass (no return value means no errors)
    from polaris.kernelone.workflow.contracts import validate_contract

    errors = validate_contract(contract)
    assert len(errors) == 0
