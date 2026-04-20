from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from polaris.infrastructure.storage.local_fs_adapter import LocalFileSystemAdapter
from polaris.kernelone.events.sourcing import JsonlEventStore
from polaris.kernelone.fs import set_default_adapter

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture(autouse=True)
def _inject_kernel_fs_adapter() -> None:
    set_default_adapter(LocalFileSystemAdapter())


def test_jsonl_event_store_appends_monotonic_seq(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    store = JsonlEventStore(str(workspace))

    first = store.append(
        stream="taskboard.terminal.events",
        event_type="completed",
        source="runtime.task_runtime",
        payload={"task_id": "task-1"},
    )
    second = store.append(
        stream="taskboard.terminal.events",
        event_type="failed",
        source="runtime.task_runtime",
        payload={"task_id": "task-2"},
    )

    assert first.seq == 1
    assert second.seq == 2
    assert first.event_version == 1

    result = store.query(stream="taskboard.terminal.events", limit=20, offset=0)
    assert result.total == 2
    assert [event.seq for event in result.events] == [1, 2]
    assert result.storage_path == "runtime/events/taskboard.terminal.events.jsonl"


def test_jsonl_event_store_query_filters_by_event_type_run_and_task(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    store = JsonlEventStore(str(workspace))

    store.append(
        stream="task_runtime.execution",
        event_type="claimed",
        source="runtime.task_runtime",
        payload={"task_id": "task-1", "run_id": "run-A"},
    )
    store.append(
        stream="task_runtime.execution",
        event_type="completed",
        source="runtime.task_runtime",
        payload={"task_id": "task-1", "run_id": "run-A"},
    )
    store.append(
        stream="task_runtime.execution",
        event_type="failed",
        source="runtime.task_runtime",
        payload={"task_id": "task-2", "run_id": "run-B"},
    )

    filtered = store.query(
        stream="task_runtime.execution",
        limit=10,
        offset=0,
        event_type="completed",
        run_id="run-A",
        task_id="task-1",
    )
    assert filtered.total == 1
    assert len(filtered.events) == 1
    assert filtered.events[0].event_type == "completed"
    assert filtered.events[0].payload["task_id"] == "task-1"
