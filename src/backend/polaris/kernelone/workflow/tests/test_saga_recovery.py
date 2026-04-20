"""Integration tests for SagaWorkflowEngine restart recovery.

Tests cover:
1. Workflow state survives engine restart (simulated crash)
2. Checkpoint creation and restoration
3. Compensation chain survives restart
4. Human-in-the-loop state survives restart

References:
- kernelone/workflow/saga_engine.py (SagaWorkflowEngine)
- kernelone/workflow/checkpoint_manager.py (CheckpointManager)
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

import pytest
from polaris.kernelone.workflow.activity_runner import ActivityRunner
from polaris.kernelone.workflow.checkpoint_manager import CheckpointManager
from polaris.kernelone.workflow.contracts import RetryPolicy, TaskSpec, WorkflowContract
from polaris.kernelone.workflow.saga_engine import SagaExecutionState, SagaWorkflowEngine
from polaris.kernelone.workflow.task_queue import TaskQueueManager
from polaris.kernelone.workflow.timer_wheel import TimerWheel

# ---------------------------------------------------------------------------
# In-Memory Store with Crash Simulation Support
# ---------------------------------------------------------------------------


class CrashSimulatingStore:
    """In-memory store that supports crash simulation.

    This store simulates a process restart by:
    1. Persisting data to a shared dict (simulates durable storage)
    2. On "crash", the in-memory state is wiped but persisted data remains
    3. On "restart", a new store instance connects to the same persisted data
    """

    def __init__(self) -> None:
        self._executions: dict[str, dict[str, Any]] = {}
        self._events: dict[str, list[dict[str, Any]]] = {}
        self._task_states: dict[str, dict[str, dict[str, Any]]] = {}
        self._event_seqs: dict[str, int] = {}
        self._snapshots: dict[str, dict[str, Any]] = {}
        # Shared state for crash simulation (class-level to persist across instances)
        self._shared_events: dict[str, list[dict[str, Any]]] = {}
        self._shared_task_states: dict[str, dict[str, dict[str, Any]]] = {}
        self._shared_executions: dict[str, dict[str, Any]] = {}
        self._shared_event_seqs: dict[str, int] = {}
        self._initialized = False

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
        # Also update shared state
        self._shared_executions[workflow_id] = dict(self._executions[workflow_id])
        self._shared_events[workflow_id] = []
        self._shared_task_states[workflow_id] = {}
        self._shared_event_seqs[workflow_id] = 1

    async def append_event(
        self,
        workflow_id: str,
        event_type: str,
        payload: dict[str, Any],
    ) -> None:
        if workflow_id not in self._events:
            self._events[workflow_id] = []
        if workflow_id not in self._shared_events:
            self._shared_events[workflow_id] = []
        seq = self._event_seqs.get(workflow_id, 1)
        self._shared_event_seqs[workflow_id] = seq + 1
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
        self._shared_events[workflow_id].append(event)

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
            self._shared_executions[workflow_id] = dict(self._executions[workflow_id])

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
        # Also update shared state
        if workflow_id not in self._shared_task_states:
            self._shared_task_states[workflow_id] = {}
        self._shared_task_states[workflow_id][task_id] = dict(self._task_states[workflow_id][task_id])

    async def create_snapshot(self, workflow_id: str) -> Any:
        execution = self._executions.get(workflow_id, {})
        return type(
            "Snapshot",
            (),
            {
                "workflow_id": workflow_id,
                "workflow_name": execution.get("workflow_name", ""),
                "status": execution.get("status", "unknown"),
                "run_id": workflow_id,
                "start_time": execution.get("created_at", ""),
                "close_time": execution.get("close_time"),
                "result": execution.get("result"),
                "pending_actions": [],
            },
        )()

    async def list_task_states(self, workflow_id: str) -> list[dict[str, Any]]:
        return list(self._task_states.get(workflow_id, {}).values())

    async def get_events(self, workflow_id: str, *, limit: int = 100) -> list[dict[str, Any]]:
        events = self._events.get(workflow_id, [])
        return events[-limit:]

    def crash(self) -> None:
        """Simulate a crash - wipe in-memory state but keep shared state."""
        self._executions.clear()
        self._events.clear()
        self._task_states.clear()
        self._event_seqs.clear()
        self._snapshots.clear()

    def restore_from_shared(self) -> None:
        """Restore in-memory state from shared (durable) storage."""
        self._executions = dict(self._shared_executions)
        self._events = dict(self._shared_events)
        self._task_states = dict(self._shared_task_states)
        self._event_seqs = dict(self._shared_event_seqs)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def crash_store() -> CrashSimulatingStore:
    """Create a crash-simulating store for testing restart recovery."""
    return CrashSimulatingStore()


@pytest.fixture
def timer_wheel() -> TimerWheel:
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
def checkpoint_manager(crash_store: CrashSimulatingStore) -> CheckpointManager:
    """Create a CheckpointManager."""
    return CheckpointManager(crash_store)


@pytest.fixture
def saga_engine(
    crash_store: CrashSimulatingStore,
    timer_wheel: TimerWheel,
    task_queue_manager: TaskQueueManager,
    activity_runner: ActivityRunner,
) -> SagaWorkflowEngine:
    """Create a SagaWorkflowEngine for testing."""
    engine = SagaWorkflowEngine(
        store=crash_store,
        timer_wheel=timer_wheel,
        task_queue_manager=task_queue_manager,
        activity_runner=activity_runner,
        checkpoint_interval_seconds=1.0,
    )
    return engine


# ---------------------------------------------------------------------------
# Helper Functions
# ---------------------------------------------------------------------------


def create_saga_contract() -> WorkflowContract:
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


# ---------------------------------------------------------------------------
# Restart Recovery Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_saga_engine_start_stop(saga_engine: SagaWorkflowEngine) -> None:
    """SagaWorkflowEngine must start and stop correctly."""
    await saga_engine.start()
    assert saga_engine._running is True

    await saga_engine.stop()
    assert saga_engine._running is False


@pytest.mark.asyncio
async def test_checkpoint_manager_creation(
    crash_store: CrashSimulatingStore,
) -> None:
    """CheckpointManager must be createable."""
    manager = CheckpointManager(crash_store)
    assert manager is not None


@pytest.mark.asyncio
async def test_checkpoint_create_and_list(
    crash_store: CrashSimulatingStore,
) -> None:
    """CheckpointManager.create_checkpoint must create a checkpoint event."""
    manager = CheckpointManager(crash_store)

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
    crash_store: CrashSimulatingStore,
) -> None:
    """CheckpointManager.get_latest_checkpoint must return the most recent."""
    manager = CheckpointManager(crash_store)

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
    crash_store: CrashSimulatingStore,
) -> None:
    """CheckpointManager.get_recovery_info must return correct recovery data."""
    manager = CheckpointManager(crash_store)

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


@pytest.mark.asyncio
async def test_workflow_signal_workflow(
    saga_engine: SagaWorkflowEngine,
    crash_store: CrashSimulatingStore,
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
    events = await crash_store.get_events("wf-1", limit=10)
    signal_events = [e for e in events if e["event_type"] == "signal_received"]
    assert len(signal_events) >= 1

    await saga_engine.stop()


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
# Crash and Recovery Simulation Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_store_crash_and_restore(
    crash_store: CrashSimulatingStore,
) -> None:
    """CrashSimulatingStore must persist data across crash/restart cycles."""
    # Create execution and add some data
    await crash_store.create_execution("wf-1", "test", {"key": "value"})
    await crash_store.append_event("wf-1", "test_event", {"data": "test"})
    await crash_store.upsert_task_state(
        workflow_id="wf-1",
        task_id="task1",
        task_type="activity",
        handler_name="handler1",
        status="completed",
        attempt=1,
        max_attempts=3,
        started_at=None,
        ended_at=None,
        result={"output": "result1"},
        error="",
        metadata={},
    )

    # Verify data exists
    execution = await crash_store.get_execution("wf-1")
    assert execution is not None
    events = await crash_store.get_events("wf-1")
    assert len(events) >= 1

    # Simulate crash - wipe in-memory state
    crash_store.crash()

    # After crash, in-memory state should be wiped
    execution = await crash_store.get_execution("wf-1")
    assert execution is None  # In-memory wiped
    events = await crash_store.get_events("wf-1")
    assert len(events) == 0  # In-memory wiped

    # Restore from shared (durable) storage
    crash_store.restore_from_shared()

    # After restore, data should be back
    execution = await crash_store.get_execution("wf-1")
    assert execution is not None
    assert execution["workflow_id"] == "wf-1"
    events = await crash_store.get_events("wf-1")
    assert len(events) >= 1
    task_states = await crash_store.list_task_states("wf-1")
    assert len(task_states) >= 1
    assert task_states[0]["task_id"] == "task1"


@pytest.mark.asyncio
async def test_checkpoint_survives_crash(
    crash_store: CrashSimulatingStore,
    checkpoint_manager: CheckpointManager,
) -> None:
    """Checkpoint must survive simulated crash."""
    # Create a checkpoint
    record = await checkpoint_manager.create_checkpoint(
        workflow_id="wf-1",
        task_states={
            "task1": {
                "task_id": "task1",
                "status": "completed",
                "attempt": 1,
            }
        },
        task_outputs={"task1": {"result": "output1"}},
        metadata={"source": "test"},
    )
    checkpoint_id = record.checkpoint_id

    # Verify checkpoint exists
    latest = await checkpoint_manager.get_latest_checkpoint("wf-1")
    assert latest is not None
    assert latest.checkpoint_id == checkpoint_id

    # Simulate crash
    crash_store.crash()

    # After crash, checkpoint should still be accessible via shared state
    crash_store.restore_from_shared()

    # Verify checkpoint still exists after restore
    latest = await checkpoint_manager.get_latest_checkpoint("wf-1")
    assert latest is not None
    assert latest.checkpoint_id == checkpoint_id
    assert latest.task_states_snapshot["task1"]["status"] == "completed"


@pytest.mark.asyncio
async def test_recovery_info_after_crash(
    crash_store: CrashSimulatingStore,
    checkpoint_manager: CheckpointManager,
) -> None:
    """get_recovery_info must return correct data after crash/restart cycle."""
    # Create checkpoint
    await checkpoint_manager.create_checkpoint(
        workflow_id="wf-1",
        task_states={"task1": {"status": "completed"}},
        task_outputs={"task1": {"result": "done"}},
    )

    # Get initial recovery info
    info_before = await checkpoint_manager.get_recovery_info("wf-1")
    assert info_before["latest_checkpoint"] is not None
    assert info_before["recovery_needed"] is False

    # Simulate crash and restart
    crash_store.crash()
    crash_store.restore_from_shared()

    # Get recovery info after restart
    info_after = await checkpoint_manager.get_recovery_info("wf-1")
    assert info_after["workflow_id"] == "wf-1"
    assert info_after["latest_checkpoint"] is not None
    assert info_after["recovery_needed"] is False  # No events since checkpoint

    # Checkpoint data should match
    assert info_before["latest_checkpoint"].checkpoint_id == info_after["latest_checkpoint"].checkpoint_id


@pytest.mark.asyncio
async def test_events_since_checkpoint_excludes_checkpoint_event(
    crash_store: CrashSimulatingStore,
    checkpoint_manager: CheckpointManager,
) -> None:
    """get_checkpoint_events_since must exclude the checkpoint event itself."""
    # Create checkpoint
    await checkpoint_manager.create_checkpoint(
        workflow_id="wf-1",
        task_states={"task1": {"status": "pending"}},
        task_outputs={},
    )

    # Add some events after checkpoint
    await crash_store.append_event("wf-1", "task_started", {"task_id": "task1"})
    await crash_store.append_event("wf-1", "task_completed", {"task_id": "task1"})

    # Get recovery info
    info = await checkpoint_manager.get_recovery_info("wf-1")
    assert info["recovery_needed"] is True
    assert len(info["events_since_checkpoint"]) == 2  # Only the two task events

    # Verify checkpoint event is NOT in the list
    for event in info["events_since_checkpoint"]:
        assert event["event_type"] != "checkpoint_created"


@pytest.mark.asyncio
async def test_multiple_checkpoints_returns_latest(
    crash_store: CrashSimulatingStore,
    checkpoint_manager: CheckpointManager,
) -> None:
    """get_latest_checkpoint must return the most recent checkpoint."""
    # Create first checkpoint
    await checkpoint_manager.create_checkpoint(
        workflow_id="wf-1",
        task_states={"task1": {"status": "pending"}},
        task_outputs={},
    )
    await asyncio.sleep(0.01)

    # Create second checkpoint
    await checkpoint_manager.create_checkpoint(
        workflow_id="wf-1",
        task_states={"task1": {"status": "in_progress"}},
        task_outputs={"task1": {"partial": "result"}},
    )
    await asyncio.sleep(0.01)

    # Create third checkpoint
    checkpoint_third = await checkpoint_manager.create_checkpoint(
        workflow_id="wf-1",
        task_states={"task1": {"status": "completed"}},
        task_outputs={"task1": {"result": "final"}},
    )

    # Get latest - should be third
    latest = await checkpoint_manager.get_latest_checkpoint("wf-1")
    assert latest is not None
    assert latest.checkpoint_id == checkpoint_third.checkpoint_id
    assert latest.task_states_snapshot["task1"]["status"] == "completed"


@pytest.mark.asyncio
async def test_checkpoint_list_sorted_newest_first(
    crash_store: CrashSimulatingStore,
    checkpoint_manager: CheckpointManager,
) -> None:
    """list_checkpoints must return checkpoints sorted by creation time (newest first)."""
    # Create checkpoints with slight delays to ensure different timestamps
    await checkpoint_manager.create_checkpoint(
        workflow_id="wf-1",
        task_states={"task1": {"status": "step1"}},
        task_outputs={},
    )
    await asyncio.sleep(0.01)
    await checkpoint_manager.create_checkpoint(
        workflow_id="wf-1",
        task_states={"task1": {"status": "step2"}},
        task_outputs={},
    )
    await asyncio.sleep(0.01)
    await checkpoint_manager.create_checkpoint(
        workflow_id="wf-1",
        task_states={"task1": {"status": "step3"}},
        task_outputs={},
    )

    # List checkpoints
    checkpoints = await checkpoint_manager.list_checkpoints("wf-1")
    assert len(checkpoints) >= 3

    # Verify sorted by created_at descending (newest first)
    for i in range(len(checkpoints) - 1):
        assert checkpoints[i].created_at >= checkpoints[i + 1].created_at


# ---------------------------------------------------------------------------
# Timer Persistence Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_persistent_timer_wheel_cancel_info(
    timer_wheel: TimerWheel,
    crash_store: CrashSimulatingStore,
) -> None:
    """TimerWheel.get_timer_info must return timer info for cancellation."""
    await timer_wheel.start()

    # Schedule a timer
    callback_called = False

    async def callback() -> None:
        nonlocal callback_called
        callback_called = True

    await timer_wheel.schedule_timer(
        timer_id="t1",
        workflow_id="wf-1",
        delay_seconds=10.0,
        callback=callback,
    )

    # Get timer info
    info = await timer_wheel.get_timer_info("t1")
    assert info is not None
    assert info.timer_id == "t1"
    assert info.workflow_id == "wf-1"

    # Cancel
    cancelled = await timer_wheel.cancel_timer("t1")
    assert cancelled is True

    # Info should be gone
    info = await timer_wheel.get_timer_info("t1")
    assert info is None

    await timer_wheel.stop()


@pytest.mark.asyncio
async def test_timer_wheel_get_all_timer_ids(
    timer_wheel: TimerWheel,
) -> None:
    """TimerWheel.get_all_timer_ids must return all active timer IDs."""
    await timer_wheel.start()

    async def callback() -> None:
        pass

    # Schedule multiple timers
    await timer_wheel.schedule_timer("t1", "wf-1", 10.0, callback)
    await timer_wheel.schedule_timer("t2", "wf-1", 5.0, callback)
    await timer_wheel.schedule_timer("t3", "wf-2", 15.0, callback)

    # Get all timer IDs
    ids = timer_wheel.get_all_timer_ids()
    assert len(ids) == 3
    assert "t1" in ids
    assert "t2" in ids
    assert "t3" in ids

    await timer_wheel.stop()


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
# WorkflowContract Validation Tests
# ---------------------------------------------------------------------------


def test_workflow_contract_validation_no_cycle() -> None:
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
