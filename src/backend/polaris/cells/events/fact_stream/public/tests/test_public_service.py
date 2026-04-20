from __future__ import annotations

from typing import TYPE_CHECKING

from polaris.cells.events.fact_stream.public.service import (
    AppendFactEventCommandV1,
    QueryFactEventsV1,
    append_fact_event,
    query_fact_events,
)

if TYPE_CHECKING:
    from pathlib import Path


def test_append_fact_event_and_query_roundtrip(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)

    appended = append_fact_event(
        AppendFactEventCommandV1(
            workspace=str(workspace),
            stream="task_runtime.execution",
            event_type="claimed",
            payload={"task_id": "task-1", "run_id": "run-1"},
            source="runtime.task_runtime",
            task_id="task-1",
            run_id="run-1",
        )
    )
    assert appended.workspace == str(workspace)
    assert appended.stream == "task_runtime.execution"
    assert appended.storage_path == "runtime/events/task_runtime.execution.jsonl"
    assert str(appended.event_id).strip()

    queried = query_fact_events(
        QueryFactEventsV1(
            workspace=str(workspace),
            stream="task_runtime.execution",
            limit=50,
            offset=0,
            task_id="task-1",
        )
    )
    assert queried.total == 1
    assert len(queried.events) == 1
    assert queried.events[0]["event_type"] == "claimed"
    assert queried.events[0]["task_id"] == "task-1"


def test_query_fact_events_pagination(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)

    for idx in range(3):
        append_fact_event(
            AppendFactEventCommandV1(
                workspace=str(workspace),
                stream="taskboard.terminal.events",
                event_type="completed",
                payload={"task_id": f"task-{idx}"},
                source="runtime.task_runtime.task_board",
                task_id=f"task-{idx}",
            )
        )

    first_page = query_fact_events(
        QueryFactEventsV1(
            workspace=str(workspace),
            stream="taskboard.terminal.events",
            limit=2,
            offset=0,
        )
    )
    assert first_page.total == 3
    assert len(first_page.events) == 2
    assert first_page.next_offset == 2

    second_page = query_fact_events(
        QueryFactEventsV1(
            workspace=str(workspace),
            stream="taskboard.terminal.events",
            limit=2,
            offset=first_page.next_offset,
        )
    )
    assert second_page.total == 3
    assert len(second_page.events) == 1
    assert second_page.next_offset == 0
