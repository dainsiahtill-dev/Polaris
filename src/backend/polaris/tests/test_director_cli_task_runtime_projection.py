from __future__ import annotations

from pathlib import Path

from polaris.cells.events.fact_stream.public.contracts import QueryFactEventsV1
from polaris.cells.events.fact_stream.public.service import query_fact_events
from polaris.cells.runtime.task_runtime.public.service import TaskRuntimeService
from polaris.delivery.cli.director.director_service import DirectorService


def test_director_cli_status_update_writes_task_runtime_execution_fact(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    task_runtime = TaskRuntimeService(str(workspace))
    task = task_runtime.create(subject="Director CLI execution projection")
    service = DirectorService(workspace=workspace)

    service._update_task_board(
        str(task.id),
        {
            "success": True,
            "metadata": {
                "adapter": "director.execution.public",
                "changed_files": ["src/main.py"],
            },
        },
    )

    events = query_fact_events(QueryFactEventsV1(workspace=str(workspace), stream="task_runtime.execution")).events
    updated_event = next(event for event in events if event.get("event_type") == "updated")

    assert updated_event["payload"]["status"] == "completed"
    assert updated_event["payload"]["execution_state"] == "completed"
    assert updated_event["payload"]["details"]["metadata_updated"] is True

    updated_row = TaskRuntimeService(str(workspace)).get_task(task.id)
    assert updated_row is not None
    assert updated_row["status"] == "completed"
    assert updated_row["metadata"]["adapter"] == "director.execution.public"
