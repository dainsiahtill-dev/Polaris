from __future__ import annotations

from pathlib import Path

from polaris.cells.events.fact_stream.public import (
    BootstrapFactStreamWorkspaceCommandV1,
    bootstrap_fact_stream_workspace,
    fact_stream_bootstrap_streams,
)
from polaris.cells.events.fact_stream.public.contracts import QueryFactEventsV1
from polaris.cells.events.fact_stream.public.service import query_fact_events
from polaris.cells.runtime.task_runtime.public.contracts import (
    SettleTaskRuntimeExecutionAttemptCommandV1,
    TaskRuntimeExecutionAttemptIdentityV1,
)
from polaris.cells.runtime.task_runtime.public.service import TaskRuntimeService
from polaris.delivery.cli.director.director_service import DirectorService


def _bootstrap_workspace(workspace: Path) -> None:
    """Explicitly provision FactStream before direct low-level TaskRuntime I/O."""

    bootstrap_fact_stream_workspace(
        BootstrapFactStreamWorkspaceCommandV1(
            workspace=str(workspace),
            streams=fact_stream_bootstrap_streams(),
            maintenance_reason="director_cli_task_runtime_projection_test_setup",
        )
    )


def test_director_cli_status_check_does_not_write_sessionless_terminal_event(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    _bootstrap_workspace(workspace)
    task_runtime = TaskRuntimeService(str(workspace))
    task = task_runtime.create_task_row(subject="Director CLI execution projection")
    claim = task_runtime.claim_execution(
        task["id"],
        worker_id="director",
        role_id="director",
        run_id="run-director-cli-status-check",
        selection_source="test",
    )
    assert claim["success"] is True
    identity = TaskRuntimeExecutionAttemptIdentityV1.from_record(claim["execution_attempt"])
    completed = task_runtime.settle_execution_attempt(
        SettleTaskRuntimeExecutionAttemptCommandV1(
            workspace=identity.workspace,
            identity=identity,
            outcome="completed",
            summary="director public execution completed",
            metadata={
                "adapter": "director.execution.public",
                "changed_files": ["src/main.py"],
            },
        )
    )
    assert completed["success"] is True
    service = DirectorService(workspace=workspace)

    service._update_task_board(
        str(task["id"]),
        {
            "success": True,
            "metadata": {
                "adapter": "director.execution.public",
                "changed_files": ["src/main.py"],
            },
        },
    )

    events = query_fact_events(QueryFactEventsV1(workspace=str(workspace), stream="task_runtime.execution")).events
    terminal_events = [event for event in events if event.get("event_type") == "completed"]
    sessionless_updated_events = [
        event
        for event in events
        if event.get("event_type") == "updated" and not str(event.get("payload", {}).get("session_id") or "").strip()
    ]

    assert len(terminal_events) == 1
    assert not sessionless_updated_events

    updated_row = TaskRuntimeService(str(workspace)).get_task(task["id"])
    assert updated_row is not None
    assert updated_row["status"] == "completed"
    assert updated_row["metadata"]["adapter"] == "director.execution.public"


def test_director_cli_ready_tasks_use_task_runtime_projection(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    _bootstrap_workspace(workspace)
    task_runtime = TaskRuntimeService(str(workspace))
    ready = task_runtime.create_task_row(subject="ready task")
    blocked = task_runtime.create_task_row(subject="blocked task", blocked_by=[ready["id"]])
    claimed = task_runtime.create_task_row(subject="claimed task")
    claim_result = task_runtime.claim_execution(
        claimed["id"],
        worker_id="director",
        role_id="director",
        run_id="run-director-cli-ready",
        selection_source="test",
    )
    assert claim_result["success"] is True

    service = DirectorService(workspace=workspace)

    rows = service._get_ready_tasks()

    assert [row["id"] for row in rows] == [ready["id"]]
    assert all(row["id"] != blocked["id"] for row in rows)
    assert all(row["id"] != claimed["id"] for row in rows)
