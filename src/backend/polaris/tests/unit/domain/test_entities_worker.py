"""Tests for polaris.domain.entities.worker."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from polaris.domain.entities.task import Task, TaskResult
from polaris.domain.entities.worker import (
    Worker,
    WorkerCapabilities,
    WorkerHealth,
    WorkerStateError,
    WorkerStatus,
    WorkerType,
)


class TestWorkerStatus:
    def test_values(self) -> None:
        assert WorkerStatus.IDLE.name == "IDLE"
        assert WorkerStatus.BUSY.name == "BUSY"
        assert WorkerStatus.STOPPING.name == "STOPPING"
        assert WorkerStatus.STOPPED.name == "STOPPED"
        assert WorkerStatus.FAILED.name == "FAILED"


class TestWorkerType:
    def test_values(self) -> None:
        assert WorkerType.LOCAL.name == "LOCAL"
        assert WorkerType.REMOTE.name == "REMOTE"
        assert WorkerType.CONTAINER.name == "CONTAINER"


class TestWorkerCapabilities:
    def test_defaults(self) -> None:
        caps = WorkerCapabilities()
        assert caps.can_execute_bash is True
        assert caps.can_write_files is True
        assert caps.supported_languages == ["python", "bash"]


class TestWorkerHealth:
    def test_defaults(self) -> None:
        health = WorkerHealth()
        assert health.tasks_completed == 0
        assert health.tasks_failed == 0
        assert health.consecutive_failures == 0

    def test_is_healthy_recent_heartbeat(self) -> None:
        health = WorkerHealth(last_heartbeat=datetime.now(timezone.utc))
        assert health.is_healthy(timeout_seconds=60) is True

    def test_is_healthy_old_heartbeat(self) -> None:
        old = datetime(2020, 1, 1, tzinfo=timezone.utc)
        health = WorkerHealth(last_heartbeat=old)
        assert health.is_healthy(timeout_seconds=60) is False

    def test_with_updates(self) -> None:
        health = WorkerHealth()
        updated = health.with_updates(tasks_completed=5)
        assert updated.tasks_completed == 5
        assert updated.tasks_failed == 0

    def test_with_updates_preserves_unset(self) -> None:
        health = WorkerHealth(tasks_completed=3)
        updated = health.with_updates(tasks_failed=1)
        assert updated.tasks_completed == 3
        assert updated.tasks_failed == 1


class TestWorker:
    def test_defaults(self) -> None:
        worker = Worker(id="w1", name="worker-1")
        assert worker.status == WorkerStatus.IDLE
        assert worker.worker_type == WorkerType.LOCAL
        assert worker.max_concurrent_tasks == 1

    def test_is_available_idle_healthy(self) -> None:
        worker = Worker(id="w1", name="worker-1")
        assert worker.is_available() is True

    def test_is_available_not_idle(self) -> None:
        worker = Worker(id="w1", name="worker-1", status=WorkerStatus.BUSY)
        assert worker.is_available() is False

    def test_can_accept_task_available(self) -> None:
        worker = Worker(id="w1", name="worker-1")
        task = Task(id=1, subject="test")
        assert worker.can_accept_task(task) is True

    def test_can_accept_task_unavailable(self) -> None:
        worker = Worker(id="w1", name="worker-1", status=WorkerStatus.BUSY)
        task = Task(id=1, subject="test")
        assert worker.can_accept_task(task) is False

    def test_can_accept_task_no_bash(self) -> None:
        worker = Worker(
            id="w1",
            name="worker-1",
            capabilities=WorkerCapabilities(can_execute_bash=False),
        )
        task = Task(id=1, subject="test", command="bash script.sh")
        assert worker.can_accept_task(task) is False

    def test_claim_task(self) -> None:
        worker = Worker(id="w1", name="worker-1")
        worker.claim_task("task-1")
        assert worker.status == WorkerStatus.BUSY
        assert worker.current_task_id == "task-1"
        assert worker.started_at is not None

    def test_claim_task_not_available(self) -> None:
        worker = Worker(id="w1", name="worker-1", status=WorkerStatus.BUSY)
        with pytest.raises(WorkerStateError):
            worker.claim_task("task-1")

    def test_release_task_success(self) -> None:
        worker = Worker(id="w1", name="worker-1")
        worker.claim_task("task-1")
        result = TaskResult(success=True, duration_ms=1000)
        worker.release_task(result)
        assert worker.status == WorkerStatus.IDLE
        assert worker.current_task_id is None
        assert worker.health.tasks_completed == 1

    def test_release_task_failure(self) -> None:
        worker = Worker(id="w1", name="worker-1")
        worker.claim_task("task-1")
        result = TaskResult(success=False, duration_ms=500)
        worker.release_task(result)
        assert worker.health.tasks_failed == 1
        assert worker.health.consecutive_failures == 1

    def test_release_task_not_busy(self) -> None:
        worker = Worker(id="w1", name="worker-1")
        with pytest.raises(WorkerStateError):
            worker.release_task(TaskResult(success=True))

    def test_update_heartbeat(self) -> None:
        old_heartbeat = datetime(2020, 1, 1, tzinfo=timezone.utc)
        worker = Worker(id="w1", name="worker-1", health=WorkerHealth(last_heartbeat=old_heartbeat))
        worker.update_heartbeat()
        assert worker.health.last_heartbeat > old_heartbeat

    def test_mark_failed(self) -> None:
        worker = Worker(id="w1", name="worker-1")
        worker.mark_failed("crashed")
        assert worker.status == WorkerStatus.FAILED
        assert worker.metadata["failure_reason"] == "crashed"

    def test_request_stop_idle(self) -> None:
        worker = Worker(id="w1", name="worker-1")
        worker.request_stop()
        assert worker.status == WorkerStatus.STOPPED
        assert worker.stopped_at is not None

    def test_request_stop_busy(self) -> None:
        worker = Worker(id="w1", name="worker-1", status=WorkerStatus.BUSY)
        worker.request_stop()
        assert worker.status == WorkerStatus.STOPPING

    def test_to_dict(self) -> None:
        worker = Worker(id="w1", name="worker-1")
        d = worker.to_dict()
        assert d["id"] == "w1"
        assert d["status"] == "IDLE"
        assert "health" in d
        assert "capabilities" in d
