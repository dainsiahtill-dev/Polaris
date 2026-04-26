"""Workflow runtime DAG tests."""

from __future__ import annotations

import asyncio
import tempfile
from typing import Any

import pytest


async def _build_engine():
    from polaris.cells.orchestration.workflow_runtime.public.runtime import (
        ActivityRunner,
        SqliteRuntimeStore,
        TaskQueueManager,
        TimerWheel,
        WorkflowEngine,
    )

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        db_path = tmp.name
    engine = WorkflowEngine(
        store=SqliteRuntimeStore(db_path),
        timer_wheel=TimerWheel(tick_interval=0.02),
        task_queue_manager=TaskQueueManager(),
        activity_runner=ActivityRunner(max_concurrent=8),
    )
    await engine.start()
    return engine


async def _wait_terminal(engine: Any, workflow_id: str, *, timeout: float = 8.0) -> str:
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        snapshot = await engine.describe_workflow(workflow_id)
        if snapshot.status in {"completed", "failed", "cancelled"}:
            return snapshot.status
        await asyncio.sleep(0.05)
    raise TimeoutError(f"workflow {workflow_id} did not finish in {timeout}s")


@pytest.mark.asyncio
async def test_workflow_engine_runs_dag_with_dataflow() -> None:
    engine = await _build_engine()
    try:

        async def extract(**kwargs):
            return {"records": [1, 2, 3], "source": kwargs.get("source", "n/a")}

        async def transform(records, **kwargs):
            return {"count": len(records), "source": kwargs.get("workflow_id", "")}

        async def load(count, **kwargs):
            return {"loaded": count, "task_id": kwargs.get("task_id", "")}

        engine.register_activity("extract_data", extract)
        engine.register_activity("transform_data", transform)
        engine.register_activity("load_data", load)

        payload = {
            "orchestration": {
                "max_concurrency": 2,
                "tasks": [
                    {
                        "id": "extract",
                        "type": "activity",
                        "handler": "extract_data",
                        "input": {"source": "unit_test"},
                    },
                    {
                        "id": "transform",
                        "type": "activity",
                        "handler": "transform_data",
                        "depends_on": ["extract"],
                        "input_from": {"records": "extract.records"},
                    },
                    {
                        "id": "load",
                        "type": "activity",
                        "handler": "load_data",
                        "depends_on": ["transform"],
                        "input_from": {"count": "transform.count"},
                    },
                ],
            }
        }
        submitted = await engine.start_workflow("etl_pipeline", "dag-001", payload)
        assert submitted.submitted is True
        assert submitted.details.get("task_count") == 3
        status = await _wait_terminal(engine, "dag-001")
        assert status == "completed"

        tasks = await engine.query_workflow("dag-001", "tasks")
        assert tasks["tasks"]["extract"]["state"] == "completed"
        assert tasks["tasks"]["transform"]["state"] == "completed"
        assert tasks["tasks"]["load"]["state"] == "completed"
        assert tasks["tasks"]["load"]["result"]["loaded"] == 3
    finally:
        await engine.stop()


@pytest.mark.asyncio
async def test_workflow_engine_retries_activity_until_success() -> None:
    engine = await _build_engine()
    try:
        attempts = {"count": 0}

        async def flaky(**kwargs):
            attempts["count"] += 1
            if attempts["count"] < 3:
                raise RuntimeError("transient_failure")
            return {"attempt": attempts["count"]}

        engine.register_activity("flaky_step", flaky)
        payload = {
            "orchestration": {
                "tasks": [
                    {
                        "id": "retry_task",
                        "type": "activity",
                        "handler": "flaky_step",
                        "retry": {
                            "max_attempts": 3,
                            "initial_interval_seconds": 0.01,
                            "backoff_coefficient": 1.0,
                        },
                    }
                ]
            }
        }
        submitted = await engine.start_workflow("retry_workflow", "dag-retry-001", payload)
        assert submitted.submitted is True
        status = await _wait_terminal(engine, "dag-retry-001")
        assert status == "completed"

        tasks = await engine.query_workflow("dag-retry-001", "tasks")
        assert tasks["tasks"]["retry_task"]["state"] == "completed"
        assert tasks["tasks"]["retry_task"]["attempt"] == 3
        assert tasks["tasks"]["retry_task"]["result"]["attempt"] == 3

        events = await engine.query_workflow("dag-retry-001", "events", 200)
        retry_events = [event for event in events["events"] if event["type"] == "task_retry_scheduled"]
        assert len(retry_events) == 2
    finally:
        await engine.stop()


@pytest.mark.asyncio
async def test_workflow_engine_rejects_cyclic_contract() -> None:
    engine = await _build_engine()
    try:
        payload = {
            "orchestration": {
                "tasks": [
                    {"id": "a", "type": "noop", "depends_on": ["b"]},
                    {"id": "b", "type": "noop", "depends_on": ["a"]},
                ]
            }
        }
        submitted = await engine.start_workflow("cycle", "dag-cycle-001", payload)
        assert submitted.submitted is False
        assert submitted.status == "invalid_contract"
        errors = submitted.details.get("errors") if isinstance(submitted.details, dict) else []
        assert isinstance(errors, list)
        assert any("cycle" in str(item).lower() for item in errors)
    finally:
        await engine.stop()


@pytest.mark.asyncio
async def test_workflow_engine_pause_resume_signal() -> None:
    engine = await _build_engine()
    try:
        second_started = asyncio.Event()

        async def slow_start(**kwargs):
            await asyncio.sleep(0.25)
            return {"ok": True}

        async def second_step(**kwargs):
            second_started.set()
            return {"ok": True}

        engine.register_activity("slow_start", slow_start)
        engine.register_activity("second_step", second_step)

        payload = {
            "orchestration": {
                "tasks": [
                    {"id": "first", "type": "activity", "handler": "slow_start"},
                    {
                        "id": "second",
                        "type": "activity",
                        "handler": "second_step",
                        "depends_on": ["first"],
                    },
                ]
            }
        }
        submitted = await engine.start_workflow("pause_resume", "dag-signal-001", payload)
        assert submitted.submitted is True

        await asyncio.sleep(0.02)
        await engine.signal_workflow("dag-signal-001", "pause", {})
        await asyncio.sleep(0.45)
        tasks = await engine.query_workflow("dag-signal-001", "tasks")
        assert tasks["tasks"]["first"]["state"] == "completed"
        assert tasks["tasks"]["second"]["state"] == "pending"
        assert second_started.is_set() is False

        await engine.signal_workflow("dag-signal-001", "resume", {})
        status = await _wait_terminal(engine, "dag-signal-001")
        assert status == "completed"
        assert second_started.is_set() is True
    finally:
        await engine.stop()
