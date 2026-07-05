"""Regression tests for delivery CLI Director task-row projection reads."""

from __future__ import annotations

from typing import Any

from polaris.delivery.cli.director.director_service import DirectorService


class _ProjectionOnlyTaskRuntime:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = [dict(row) for row in rows]

    def list_observable_task_rows(self) -> list[dict[str, Any]]:
        return [dict(row) for row in self._rows]

    def list_task_rows(self, *args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        raise AssertionError("Director CLI must read observable task rows")


def test_delivery_director_service_ready_tasks_use_observable_rows(tmp_path) -> None:
    service = DirectorService(workspace=tmp_path)
    service._task_runtime = _ProjectionOnlyTaskRuntime(
        [
            {"id": 1, "status": "pending", "subject": "ready pending"},
            {"id": 2, "status": "ready", "subject": "ready row"},
            {"id": 3, "status": "pending", "blocked_by": [1]},
            {"id": 4, "status": "ready", "claimed_by": "director"},
            {"id": 5, "status": "completed"},
        ]
    )  # type: ignore[assignment]

    ready_tasks = service._get_ready_tasks()

    assert [row["id"] for row in ready_tasks] == [1, 2]
