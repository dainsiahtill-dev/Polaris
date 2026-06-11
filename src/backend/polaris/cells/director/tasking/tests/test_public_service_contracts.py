"""G4-pattern wiring tests: director.tasking public contracts → TaskService."""

from __future__ import annotations

from pathlib import Path

import pytest
from polaris.cells.director.tasking.public.contracts import (
    CancelTaskCommandV1,
    CreateTaskCommandV1,
    TaskResultQueryV1,
    TaskStatusQueryV1,
)
from polaris.cells.director.tasking.public.service import (
    cancel_task,
    create_task,
    get_task_service,
    query_task_result,
    query_task_status,
    reset_task_services,
)


@pytest.fixture(autouse=True)
def _isolated_services() -> None:
    reset_task_services()


def _workspace(tmp_path: Path) -> str:
    return str(tmp_path)


class TestCreateTask:
    def test_create_returns_real_task(self, tmp_path: Path) -> None:
        result = create_task(
            CreateTaskCommandV1(
                subject="implement feature",
                workspace=_workspace(tmp_path),
                description="details",
                priority="high",
            )
        )

        assert result.ok is True
        assert result.task_id.startswith("task-")
        assert result.subject == "implement feature"
        assert result.status in ("pending", "ready")

    def test_unknown_priority_falls_back_to_medium(self, tmp_path: Path) -> None:
        result = create_task(CreateTaskCommandV1(subject="t", workspace=_workspace(tmp_path), priority="urgent-ish"))

        assert result.ok is True


class TestQueryTaskStatus:
    def test_status_by_id_roundtrip(self, tmp_path: Path) -> None:
        workspace = _workspace(tmp_path)
        created = create_task(CreateTaskCommandV1(subject="roundtrip", workspace=workspace))

        status = query_task_status(TaskStatusQueryV1(workspace=workspace, task_id=created.task_id))

        assert status.ok is True
        assert status.count == 1
        assert status.tasks[0]["subject"] == "roundtrip"

    def test_unknown_task_id_reports_not_found(self, tmp_path: Path) -> None:
        status = query_task_status(TaskStatusQueryV1(workspace=_workspace(tmp_path), task_id="task-404"))

        assert status.ok is False
        assert status.error_code == "task_not_found"

    def test_list_with_invalid_status_filter_fails_closed(self, tmp_path: Path) -> None:
        status = query_task_status(TaskStatusQueryV1(workspace=_workspace(tmp_path), status="weird"))

        assert status.ok is False
        assert status.error_code == "invalid_status_filter"

    def test_list_respects_limit(self, tmp_path: Path) -> None:
        workspace = _workspace(tmp_path)
        for index in range(3):
            create_task(CreateTaskCommandV1(subject=f"task {index}", workspace=workspace))

        status = query_task_status(TaskStatusQueryV1(workspace=workspace, limit=2))

        assert status.ok is True
        assert status.count == 2


class TestCancelTask:
    def test_cancel_pending_task(self, tmp_path: Path) -> None:
        workspace = _workspace(tmp_path)
        created = create_task(CreateTaskCommandV1(subject="to cancel", workspace=workspace))

        result = cancel_task(CancelTaskCommandV1(task_id=created.task_id, workspace=workspace))

        assert result.ok is True
        assert result.tasks[0]["status"] == "cancelled"

    def test_cancel_unknown_task_reports_not_found(self, tmp_path: Path) -> None:
        result = cancel_task(CancelTaskCommandV1(task_id="task-404", workspace=_workspace(tmp_path)))

        assert result.ok is False
        assert result.error_code == "task_not_found"


class TestQueryTaskResult:
    def test_result_for_non_terminal_task(self, tmp_path: Path) -> None:
        workspace = _workspace(tmp_path)
        created = create_task(CreateTaskCommandV1(subject="pending result", workspace=workspace))

        result = query_task_result(TaskResultQueryV1(task_id=created.task_id, workspace=workspace))

        assert result.ok is True
        assert result.success is None  # not terminal yet

    def test_result_for_cancelled_task_is_failure(self, tmp_path: Path) -> None:
        workspace = _workspace(tmp_path)
        created = create_task(CreateTaskCommandV1(subject="cancelled result", workspace=workspace))
        cancel_task(CancelTaskCommandV1(task_id=created.task_id, workspace=workspace))

        result = query_task_result(TaskResultQueryV1(task_id=created.task_id, workspace=workspace))

        assert result.ok is True
        assert result.success is False

    def test_unknown_task_reports_not_found(self, tmp_path: Path) -> None:
        result = query_task_result(TaskResultQueryV1(task_id="task-404", workspace=_workspace(tmp_path)))

        assert result.ok is False
        assert result.error_code == "task_not_found"


class TestServiceCache:
    def test_same_workspace_shares_service(self, tmp_path: Path) -> None:
        workspace = _workspace(tmp_path)

        assert get_task_service(workspace) is get_task_service(workspace)

    def test_reset_drops_cached_services(self, tmp_path: Path) -> None:
        workspace = _workspace(tmp_path)
        before = get_task_service(workspace)
        reset_task_services()

        assert get_task_service(workspace) is not before
