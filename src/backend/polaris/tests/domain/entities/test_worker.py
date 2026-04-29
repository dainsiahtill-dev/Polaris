"""Comprehensive tests for polaris.domain.entities.worker."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

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

# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class TestWorkerStatus:
    def test_members(self):
        assert WorkerStatus.IDLE.name == "IDLE"
        assert WorkerStatus.BUSY.name == "BUSY"
        assert WorkerStatus.STOPPING.name == "STOPPING"
        assert WorkerStatus.STOPPED.name == "STOPPED"
        assert WorkerStatus.FAILED.name == "FAILED"

    def test_auto_values(self):
        # auto() assigns sequential integers starting at 1
        values = [ws.value for ws in WorkerStatus]
        assert len(values) == len(set(values))


class TestWorkerType:
    def test_members(self):
        assert WorkerType.LOCAL.name == "LOCAL"
        assert WorkerType.REMOTE.name == "REMOTE"
        assert WorkerType.CONTAINER.name == "CONTAINER"


# ---------------------------------------------------------------------------
# WorkerCapabilities
# ---------------------------------------------------------------------------


class TestWorkerCapabilities:
    def test_defaults(self):
        caps = WorkerCapabilities()
        assert caps.can_execute_bash is True
        assert caps.can_write_files is True
        assert caps.can_access_network is True
        assert caps.max_file_size_mb == 100
        assert caps.supported_languages == ["python", "bash"]

    def test_custom_values(self):
        caps = WorkerCapabilities(
            can_execute_bash=False,
            can_write_files=False,
            can_access_network=False,
            max_file_size_mb=500,
            supported_languages=["python", "javascript"],
        )
        assert caps.can_execute_bash is False
        assert caps.can_write_files is False
        assert caps.can_access_network is False
        assert caps.max_file_size_mb == 500
        assert caps.supported_languages == ["python", "javascript"]

    def test_empty_languages(self):
        caps = WorkerCapabilities(supported_languages=[])
        assert caps.supported_languages == []


# ---------------------------------------------------------------------------
# WorkerHealth
# ---------------------------------------------------------------------------


class TestWorkerHealth:
    def test_defaults(self):
        h = WorkerHealth()
        assert h.tasks_completed == 0
        assert h.tasks_failed == 0
        assert h.total_execution_time_ms == 0
        assert h.consecutive_failures == 0
        assert isinstance(h.last_heartbeat, datetime)

    def test_is_healthy_fresh(self):
        h = WorkerHealth()
        assert h.is_healthy(60) is True

    def test_is_healthy_stale(self):
        old = datetime.now(timezone.utc) - timedelta(seconds=120)
        h = WorkerHealth(last_heartbeat=old)
        assert h.is_healthy(60) is False

    def test_is_healthy_boundary(self):
        old = datetime.now(timezone.utc) - timedelta(seconds=60)
        h = WorkerHealth(last_heartbeat=old)
        assert h.is_healthy(60) is False

    def test_is_healthy_just_under(self):
        old = datetime.now(timezone.utc) - timedelta(seconds=59)
        h = WorkerHealth(last_heartbeat=old)
        assert h.is_healthy(60) is True

    def test_is_healthy_custom_timeout(self):
        old = datetime.now(timezone.utc) - timedelta(seconds=5)
        h = WorkerHealth(last_heartbeat=old)
        assert h.is_healthy(3) is False
        assert h.is_healthy(10) is True

    def test_with_updates_all(self):
        now = datetime.now(timezone.utc)
        h = WorkerHealth()
        h2 = h.with_updates(
            last_heartbeat=now,
            tasks_completed=5,
            tasks_failed=1,
            total_execution_time_ms=1000,
            consecutive_failures=2,
        )
        assert h2.last_heartbeat == now
        assert h2.tasks_completed == 5
        assert h2.tasks_failed == 1
        assert h2.total_execution_time_ms == 1000
        assert h2.consecutive_failures == 2

    def test_with_updates_partial(self):
        h = WorkerHealth(tasks_completed=3)
        h2 = h.with_updates(tasks_completed=5)
        assert h2.tasks_completed == 5
        assert h2.tasks_failed == 0
        assert h2.last_heartbeat == h.last_heartbeat

    def test_with_updates_none(self):
        h = WorkerHealth(tasks_completed=3)
        h2 = h.with_updates()
        assert h2.tasks_completed == 3
        assert h2 == h

    def test_immutability(self):
        h = WorkerHealth(tasks_completed=0)
        h2 = h.with_updates(tasks_completed=5)
        assert h.tasks_completed == 0
        assert h2.tasks_completed == 5
        assert h is not h2


# ---------------------------------------------------------------------------
# Worker
# ---------------------------------------------------------------------------


class TestWorkerCreation:
    def test_minimal_creation(self):
        w = Worker(id="w1", name="worker1")
        assert w.id == "w1"
        assert w.name == "worker1"
        assert w.worker_type == WorkerType.LOCAL
        assert w.status == WorkerStatus.IDLE
        assert w.current_task_id is None
        assert w.max_concurrent_tasks == 1
        assert w.heartbeat_interval_seconds == 30
        assert w.task_timeout_seconds == 300
        assert isinstance(w.capabilities, WorkerCapabilities)
        assert isinstance(w.health, WorkerHealth)
        assert isinstance(w.created_at, datetime)
        assert w.started_at is None
        assert w.stopped_at is None
        assert w.metadata == {}

    def test_full_creation(self):
        created = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        caps = WorkerCapabilities(can_execute_bash=False)
        health = WorkerHealth(tasks_completed=5)
        w = Worker(
            id="w2",
            name="worker2",
            worker_type=WorkerType.REMOTE,
            status=WorkerStatus.BUSY,
            current_task_id="task-1",
            capabilities=caps,
            health=health,
            max_concurrent_tasks=2,
            heartbeat_interval_seconds=60,
            task_timeout_seconds=600,
            created_at=created,
            started_at=created,
            stopped_at=None,
            metadata={"region": "us-east"},
        )
        assert w.worker_type == WorkerType.REMOTE
        assert w.status == WorkerStatus.BUSY
        assert w.current_task_id == "task-1"
        assert w.capabilities.can_execute_bash is False
        assert w.health.tasks_completed == 5
        assert w.max_concurrent_tasks == 2
        assert w.heartbeat_interval_seconds == 60
        assert w.task_timeout_seconds == 600
        assert w.created_at == created
        assert w.started_at == created
        assert w.metadata == {"region": "us-east"}


class TestWorkerIsAvailable:
    def test_idle_worker(self):
        w = Worker(id="w1", name="worker1")
        assert w.is_available() is True

    def test_busy_worker(self):
        w = Worker(id="w1", name="worker1", status=WorkerStatus.BUSY)
        assert w.is_available() is False

    def test_failed_worker(self):
        w = Worker(id="w1", name="worker1", status=WorkerStatus.FAILED)
        assert w.is_available() is False

    def test_stopped_worker(self):
        w = Worker(id="w1", name="worker1", status=WorkerStatus.STOPPED)
        assert w.is_available() is False

    def test_stale_heartbeat(self):
        old = datetime.now(timezone.utc) - timedelta(seconds=120)
        w = Worker(id="w1", name="worker1", health=WorkerHealth(last_heartbeat=old))
        assert w.is_available() is False

    def test_stopping_worker(self):
        w = Worker(id="w1", name="worker1", status=WorkerStatus.STOPPING)
        assert w.is_available() is False


class TestWorkerCanAcceptTask:
    def test_basic_task(self):
        w = Worker(id="w1", name="worker1")
        t = Task(id=1, subject="test")
        t.mark_ready()
        assert w.can_accept_task(t) is True

    def test_bash_task_rejected(self):
        w = Worker(id="w1", name="worker1", capabilities=WorkerCapabilities(can_execute_bash=False))
        t = Task(id=1, subject="test", command="bash script.sh")
        t.mark_ready()
        assert w.can_accept_task(t) is False

    def test_non_bash_task_accepted(self):
        w = Worker(id="w1", name="worker1", capabilities=WorkerCapabilities(can_execute_bash=False))
        t = Task(id=1, subject="test", command="python script.py")
        t.mark_ready()
        assert w.can_accept_task(t) is True

    def test_unavailable_worker(self):
        w = Worker(id="w1", name="worker1", status=WorkerStatus.BUSY)
        t = Task(id=1, subject="test")
        t.mark_ready()
        assert w.can_accept_task(t) is False

    def test_task_without_command(self):
        w = Worker(id="w1", name="worker1", capabilities=WorkerCapabilities(can_execute_bash=False))
        t = Task(id=1, subject="test")
        t.mark_ready()
        assert w.can_accept_task(t) is True


class TestWorkerClaimTask:
    def test_claim_success(self):
        w = Worker(id="w1", name="worker1")
        w.claim_task("task-1")
        assert w.status == WorkerStatus.BUSY
        assert w.current_task_id == "task-1"
        assert w.started_at is not None
        assert isinstance(w.started_at, datetime)

    def test_claim_already_busy_raises(self):
        w = Worker(id="w1", name="worker1", status=WorkerStatus.BUSY)
        with pytest.raises(WorkerStateError):
            w.claim_task("task-1")

    def test_claim_stopped_raises(self):
        w = Worker(id="w1", name="worker1", status=WorkerStatus.STOPPED)
        with pytest.raises(WorkerStateError):
            w.claim_task("task-1")

    def test_claim_failed_raises(self):
        w = Worker(id="w1", name="worker1", status=WorkerStatus.FAILED)
        with pytest.raises(WorkerStateError):
            w.claim_task("task-1")

    def test_claim_stopping_raises(self):
        w = Worker(id="w1", name="worker1", status=WorkerStatus.STOPPING)
        with pytest.raises(WorkerStateError):
            w.claim_task("task-1")


class TestWorkerReleaseTask:
    def test_release_success(self):
        w = Worker(id="w1", name="worker1")
        w.claim_task("task-1")
        result = TaskResult(success=True, duration_ms=1000)
        w.release_task(result)
        assert w.status == WorkerStatus.IDLE
        assert w.current_task_id is None
        assert w.health.tasks_completed == 1
        assert w.health.tasks_failed == 0
        assert w.health.total_execution_time_ms == 1000
        assert w.health.consecutive_failures == 0

    def test_release_failure(self):
        w = Worker(id="w1", name="worker1")
        w.claim_task("task-1")
        result = TaskResult(success=False, duration_ms=500)
        w.release_task(result)
        assert w.status == WorkerStatus.IDLE
        assert w.health.tasks_completed == 0
        assert w.health.tasks_failed == 1
        assert w.health.total_execution_time_ms == 500
        assert w.health.consecutive_failures == 1

    def test_release_not_busy_raises(self):
        w = Worker(id="w1", name="worker1")
        with pytest.raises(WorkerStateError):
            w.release_task(TaskResult(success=True))

    def test_release_consecutive_failures(self):
        w = Worker(id="w1", name="worker1")
        w.claim_task("task-1")
        w.release_task(TaskResult(success=False, duration_ms=100))
        w.claim_task("task-2")
        w.release_task(TaskResult(success=False, duration_ms=200))
        assert w.health.tasks_failed == 2
        assert w.health.consecutive_failures == 2
        assert w.health.total_execution_time_ms == 300

    def test_release_resets_consecutive_on_success(self):
        w = Worker(id="w1", name="worker1")
        w.claim_task("task-1")
        w.release_task(TaskResult(success=False, duration_ms=100))
        assert w.health.consecutive_failures == 1
        w.claim_task("task-2")
        w.release_task(TaskResult(success=True, duration_ms=200))
        assert w.health.consecutive_failures == 0
        assert w.health.tasks_completed == 1


class TestWorkerUpdateHeartbeat:
    def test_updates_heartbeat(self):
        w = Worker(id="w1", name="worker1")
        old = w.health.last_heartbeat
        w.update_heartbeat()
        assert w.health.last_heartbeat >= old
        assert w.health is not old

    def test_heartbeat_makes_healthy(self):
        old = datetime.now(timezone.utc) - timedelta(seconds=120)
        w = Worker(id="w1", name="worker1", health=WorkerHealth(last_heartbeat=old))
        assert w.health.is_healthy(60) is False
        w.update_heartbeat()
        assert w.health.is_healthy(60) is True


class TestWorkerMarkFailed:
    def test_mark_failed(self):
        w = Worker(id="w1", name="worker1")
        w.mark_failed("out of memory")
        assert w.status == WorkerStatus.FAILED
        assert w.metadata["failure_reason"] == "out of memory"
        assert w.stopped_at is not None

    def test_mark_failed_overwrites_reason(self):
        w = Worker(id="w1", name="worker1")
        w.mark_failed("oom")
        w.mark_failed("disk full")
        assert w.metadata["failure_reason"] == "disk full"


class TestWorkerRequestStop:
    def test_request_stop_idle(self):
        w = Worker(id="w1", name="worker1")
        w.request_stop()
        assert w.status == WorkerStatus.STOPPED
        assert w.stopped_at is not None

    def test_request_stop_busy(self):
        w = Worker(id="w1", name="worker1")
        w.claim_task("task-1")
        w.request_stop()
        assert w.status == WorkerStatus.STOPPING
        assert w.stopped_at is None

    def test_request_stop_already_stopped(self):
        w = Worker(id="w1", name="worker1", status=WorkerStatus.STOPPED)
        w.request_stop()
        assert w.status == WorkerStatus.STOPPED


class TestWorkerToDict:
    def test_minimal(self):
        w = Worker(id="w1", name="worker1")
        d = w.to_dict()
        assert d["id"] == "w1"
        assert d["name"] == "worker1"
        assert d["worker_type"] == "LOCAL"
        assert d["status"] == "IDLE"
        assert d["current_task_id"] is None
        assert d["capabilities"]["can_execute_bash"] is True
        assert d["capabilities"]["supported_languages"] == ["python", "bash"]
        assert d["health"]["is_healthy"] is True
        assert d["health"]["tasks_completed"] == 0
        assert d["max_concurrent_tasks"] == 1
        assert d["heartbeat_interval_seconds"] == 30
        assert d["task_timeout_seconds"] == 300
        assert d["started_at"] is None
        assert d["stopped_at"] is None
        assert d["metadata"] == {}

    def test_full(self):
        created = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        started = datetime(2024, 1, 1, 12, 1, 0, tzinfo=timezone.utc)
        stopped = datetime(2024, 1, 1, 12, 2, 0, tzinfo=timezone.utc)
        w = Worker(
            id="w2",
            name="worker2",
            worker_type=WorkerType.REMOTE,
            status=WorkerStatus.FAILED,
            current_task_id="task-1",
            capabilities=WorkerCapabilities(can_execute_bash=False, supported_languages=["python"]),
            health=WorkerHealth(tasks_completed=5, tasks_failed=1),
            max_concurrent_tasks=2,
            heartbeat_interval_seconds=60,
            task_timeout_seconds=600,
            created_at=created,
            started_at=started,
            stopped_at=stopped,
            metadata={"region": "us-east"},
        )
        d = w.to_dict()
        assert d["worker_type"] == "REMOTE"
        assert d["status"] == "FAILED"
        assert d["current_task_id"] == "task-1"
        assert d["capabilities"]["can_execute_bash"] is False
        assert d["capabilities"]["supported_languages"] == ["python"]
        assert d["health"]["tasks_completed"] == 5
        assert d["health"]["tasks_failed"] == 1
        assert d["health"]["is_healthy"] is True
        assert d["max_concurrent_tasks"] == 2
        assert d["heartbeat_interval_seconds"] == 60
        assert d["task_timeout_seconds"] == 600
        assert d["created_at"] == created.isoformat()
        assert d["started_at"] == started.isoformat()
        assert d["stopped_at"] == stopped.isoformat()
        assert d["metadata"] == {"region": "us-east"}
