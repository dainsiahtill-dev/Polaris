"""Runtime integration tests for the self-hosted workflow stack."""

from __future__ import annotations

import asyncio
import tempfile
import uuid
from typing import TYPE_CHECKING, Any

import pytest

if TYPE_CHECKING:
    from polaris.kernelone.workflow.engine import ActivityRegistryOps, HandlerRegistry, WorkflowRegistryOps


def _make_handler_registry() -> HandlerRegistry:
    """Build a HandlerRegistry that bridges to the embedded API singletons.

    The new WorkflowEngine (kernelone/workflow/engine.py) requires an injected
    HandlerRegistry to bootstrap @workflow.defn / @activity.defn registrations.
    The old embedded engine called get_workflow_registry() /
    get_activity_registry() directly; this adapter fills the same role for
    the protocol-based DI boundary.
    """

    class _WorkflowRegOps:
        __slots__ = ()

        def list_workflows(self) -> list[str]:
            from polaris.cells.orchestration.workflow_runtime.internal.embedded_api import (
                get_workflow_registry,
            )

            return get_workflow_registry().list_workflows()

        def get(self, name: str) -> Any | None:
            from polaris.cells.orchestration.workflow_runtime.internal.embedded_api import (
                get_workflow_registry,
            )

            return get_workflow_registry().get(name)

    class _ActivityRegOps:
        __slots__ = ()

        def list_activities(self) -> list[str]:
            from polaris.cells.orchestration.workflow_runtime.internal.embedded_api import (
                get_activity_registry,
            )

            return get_activity_registry().list_activities()

        def get(self, name: str) -> Any | None:
            from polaris.cells.orchestration.workflow_runtime.internal.embedded_api import (
                get_activity_registry,
            )

            return get_activity_registry().get(name)

    class CellHandlerRegistry:
        __slots__ = ()

        @property
        def workflows(self) -> WorkflowRegistryOps:
            return _WorkflowRegOps()

        @property
        def activities(self) -> ActivityRegistryOps:
            return _ActivityRegOps()

    return CellHandlerRegistry()  # type: ignore[return-value]


async def _build_engine():
    from polaris.cells.orchestration.workflow_runtime.public.runtime import (
        ActivityRunner,
        SqliteRuntimeStore,
        TaskQueueManager,
        TimerWheel,
        WorkflowEngine,
    )

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as temp_file:
        db_path = temp_file.name

    engine = WorkflowEngine(
        store=SqliteRuntimeStore(db_path),
        timer_wheel=TimerWheel(tick_interval=0.02),
        task_queue_manager=TaskQueueManager(),
        activity_runner=ActivityRunner(max_concurrent=10),
        handler_registry=_make_handler_registry(),
    )
    await engine.start()
    return engine


async def _wait_terminal(
    engine: Any,
    workflow_id: str,
    *,
    timeout: float = 5.0,
):
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        snapshot = await engine.describe_workflow(workflow_id)
        if snapshot.status in {"completed", "failed", "cancelled"}:
            return snapshot
        await asyncio.sleep(0.05)
    raise TimeoutError(f"workflow `{workflow_id}` did not finish in {timeout}s")


@pytest.mark.asyncio
async def test_basic_workflow() -> None:
    engine = await _build_engine()
    try:

        async def test_workflow_handler(workflow_id: str, payload: dict[str, Any]) -> dict[str, Any]:
            await asyncio.sleep(0.05)
            return {"status": "completed", "result": payload, "workflow_id": workflow_id}

        engine.register_workflow("test_workflow", test_workflow_handler)
        submitted = await engine.start_workflow(
            workflow_name="test_workflow",
            workflow_id="test-001",
            payload={"message": "hello"},
        )
        assert submitted.submitted is True

        snapshot = await _wait_terminal(engine, "test-001")
        assert snapshot.status == "completed"
    finally:
        await engine.stop()


@pytest.mark.asyncio
async def test_activity() -> None:
    from polaris.cells.orchestration.workflow_runtime.public.runtime import ActivityRunner

    runner = ActivityRunner(max_concurrent=5)
    await runner.start()
    try:

        async def test_activity_handler(**kwargs: Any) -> dict[str, Any]:
            input_dict = kwargs.get("input", {})
            await asyncio.sleep(0.05)
            return {"result": f"processed: {input_dict.get('value')}"}

        runner.register_handler("test_activity", test_activity_handler)
        await runner.submit_activity(
            activity_id="activity-001",
            activity_name="test_activity",
            workflow_id="workflow-001",
            input={"value": "test"},
        )

        deadline = asyncio.get_running_loop().time() + 2.0
        while True:
            status = await runner.get_activity_status("activity-001")
            if status is not None and status.status in {"completed", "failed", "cancelled"}:
                break
            if asyncio.get_running_loop().time() >= deadline:
                raise TimeoutError("activity did not finish in time")
            await asyncio.sleep(0.05)

        assert status is not None
        assert status.status == "completed"
    finally:
        await runner.stop()


@pytest.mark.asyncio
async def test_timer() -> None:
    from polaris.cells.orchestration.workflow_runtime.public.runtime import TimerWheel

    wheel = TimerWheel(tick_interval=0.02)
    await wheel.start()
    try:
        fired: list[bool] = []

        async def on_timer() -> None:
            fired.append(True)

        await wheel.schedule_timer(
            timer_id="timer-001",
            workflow_id="workflow-001",
            delay_seconds=0.1,
            callback=on_timer,
        )

        deadline = asyncio.get_running_loop().time() + 2.0
        while not fired:
            if asyncio.get_running_loop().time() >= deadline:
                raise TimeoutError("timer did not fire in time")
            await asyncio.sleep(0.02)
        assert fired
    finally:
        await wheel.stop()


@pytest.mark.asyncio
async def test_workflow_activity_and_child_workflow_chain() -> None:
    from polaris.cells.orchestration.workflow_runtime.internal.workflow_client import get_activity_api, get_workflow_api

    workflow = get_workflow_api()
    activity = get_activity_api()
    suffix = uuid.uuid4().hex[:8]
    activity_name = f"test_scale_{suffix}"
    child_workflow_name = f"child_math_{suffix}"
    parent_workflow_name = f"parent_math_{suffix}"

    @activity.defn(name=activity_name)
    async def scale(value: int, factor: int = 1) -> dict[str, Any]:
        return {"value": int(value) * int(factor)}

    @workflow.defn(name=child_workflow_name)
    class ChildMathWorkflow:
        @workflow.run
        async def run(self, payload: dict[str, Any]) -> dict[str, Any]:
            return {"child_value": int(payload.get("value", 0)) + 1}

    @workflow.defn(name=parent_workflow_name)
    class ParentMathWorkflow:
        @workflow.run
        async def run(self, payload: dict[str, Any]) -> dict[str, Any]:
            first = await workflow.execute_activity(
                activity_name,
                {"value": int(payload.get("value", 0)), "factor": 3},
            )
            child = await workflow.execute_child_workflow(
                child_workflow_name,
                {"value": int(first.get("value", 0))},
            )
            second = await workflow.execute_activity(
                activity_name,
                value=int(child.get("child_value", 0)),
                factor=2,
            )
            return {
                "first": int(first.get("value", 0)),
                "child": int(child.get("child_value", 0)),
                "final": int(second.get("value", 0)),
            }

    engine = await _build_engine()
    workflow_id = f"chain-{suffix}"
    try:
        submission = await engine.start_workflow(
            workflow_name=parent_workflow_name,
            workflow_id=workflow_id,
            payload={"value": 5},
        )
        assert submission.submitted is True

        snapshot = await _wait_terminal(engine, workflow_id, timeout=8.0)
        assert snapshot.status == "completed"
        assert isinstance(snapshot.result, dict)

        runtime_result = snapshot.result.get("result") if isinstance(snapshot.result, dict) else {}
        assert isinstance(runtime_result, dict)
        assert runtime_result["first"] == 15
        assert runtime_result["child"] == 16
        assert runtime_result["final"] == 32
    finally:
        await engine.stop()


@pytest.mark.asyncio
async def test_workflow_query_snapshot_updates_realtime_and_after_completion() -> None:
    from polaris.cells.orchestration.workflow_runtime.internal.workflow_client import get_workflow_api

    workflow = get_workflow_api()
    suffix = uuid.uuid4().hex[:8]
    workflow_name = f"query_snapshot_{suffix}"
    workflow_id = f"query-snapshot-{suffix}"

    @workflow.defn(name=workflow_name)
    class QuerySnapshotWorkflow:
        def __init__(self) -> None:
            self._stage = "idle"
            self._tasks: dict[str, dict[str, Any]] = {}
            self._history: list[dict[str, Any]] = []

        @workflow.query
        def get_runtime_snapshot(self) -> dict[str, Any]:
            return {
                "stage": self._stage,
                "tasks": dict(self._tasks),
                "history": list(self._history),
            }

        @workflow.run
        async def run(self, payload: dict[str, Any]) -> dict[str, Any]:
            self._stage = "director_started"
            self._tasks["PM-1"] = {"task_id": "PM-1", "state": "running", "summary": "running"}
            self._history.append({"stage": self._stage})
            await asyncio.sleep(0.2)
            self._stage = "director_completed"
            self._tasks["PM-1"] = {"task_id": "PM-1", "state": "completed", "summary": "completed"}
            self._history.append({"stage": self._stage})
            return {"ok": True, "payload": payload}

    engine = await _build_engine()
    try:
        submission = await engine.start_workflow(
            workflow_name=workflow_name,
            workflow_id=workflow_id,
            payload={"value": 1},
        )
        assert submission.submitted is True

        await asyncio.sleep(0.05)
        live_snapshot = await engine.query_workflow(workflow_id, "get_runtime_snapshot")
        assert isinstance(live_snapshot, dict)
        live_tasks = live_snapshot.get("tasks") if isinstance(live_snapshot.get("tasks"), dict) else {}
        assert "PM-1" in live_tasks
        assert str(live_tasks["PM-1"].get("state") or "").strip() in {"running", "completed"}

        snapshot = await _wait_terminal(engine, workflow_id, timeout=8.0)
        assert snapshot.status == "completed"

        cached_snapshot = await engine.query_workflow(workflow_id, "get_runtime_snapshot")
        assert isinstance(cached_snapshot, dict)
        cached_tasks = cached_snapshot.get("tasks") if isinstance(cached_snapshot.get("tasks"), dict) else {}
        assert str(cached_snapshot.get("stage") or "").strip() == "director_completed"
        assert str(cached_tasks.get("PM-1", {}).get("state") or "").strip() == "completed"
    finally:
        await engine.stop()


@pytest.mark.asyncio
async def test_workflow_run_annotation_is_coerced_from_mapping() -> None:
    from polaris.cells.orchestration.workflow_runtime.internal.models import PMWorkflowInput
    from polaris.cells.orchestration.workflow_runtime.internal.workflow_client import get_workflow_api

    workflow = get_workflow_api()
    suffix = uuid.uuid4().hex[:8]
    workflow_name = f"annotation_input_{suffix}"
    workflow_id = f"annotation-run-{suffix}"

    @workflow.defn(name=workflow_name)
    class AnnotationInputWorkflow:
        @workflow.run
        async def run(self, workflow_input: PMWorkflowInput) -> dict[str, Any]:
            return {
                "run_id": workflow_input.run_id,
                "workspace": workflow_input.workspace,
            }

    engine = await _build_engine()
    try:
        submission = await engine.start_workflow(
            workflow_name=workflow_name,
            workflow_id=workflow_id,
            payload={"workspace": "X:\\workspace", "run_id": "pm-anno-001"},
        )
        assert submission.submitted is True

        snapshot = await _wait_terminal(engine, workflow_id, timeout=8.0)
        assert snapshot.status == "completed"
        assert isinstance(snapshot.result, dict)
        runtime_result = snapshot.result.get("result") if isinstance(snapshot.result, dict) else {}
        assert isinstance(runtime_result, dict)
        assert runtime_result["run_id"] == "pm-anno-001"
        assert runtime_result["workspace"] == "X:\\workspace"
    finally:
        await engine.stop()
