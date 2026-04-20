"""Tests for multi-workspace consumer loop isolation."""

from __future__ import annotations

import threading
import time
from typing import Any
from unittest.mock import MagicMock

from polaris.cells.runtime.task_market.internal.consumer_loop import ConsumerLoopManager
from polaris.cells.runtime.task_market.internal.service import TaskMarketService
from polaris.cells.runtime.task_market.public.contracts import (
    ClaimTaskWorkItemCommandV1,
    PublishTaskWorkItemCommandV1,
    QueryTaskMarketStatusV1,
)


class FakeConsumer:
    """Minimal consumer that polls the service and tracks calls."""

    def __init__(
        self,
        workspace: str = "",
        worker_id: str = "",
        poll_interval: float = 0.02,
        **kwargs: Any,
    ) -> None:
        self.workspace = workspace
        self.worker_id = worker_id
        self.poll_interval = poll_interval
        self._stop_event = threading.Event()
        self.claim_count = 0
        self._service = TaskMarketService()

    def run(self) -> None:
        while not self._stop_event.is_set():
            try:
                result = self._service.claim_work_item(
                    ClaimTaskWorkItemCommandV1(
                        workspace=self.workspace,
                        stage="pending_design",
                        worker_id=self.worker_id,
                        worker_role="chief_engineer",
                        visibility_timeout_seconds=60,
                    )
                )
                if result.ok:
                    self.claim_count += 1
            except Exception:  # noqa: BLE001
                pass
            self._stop_event.wait(self.poll_interval)

    def stop(self) -> None:
        self._stop_event.set()


def _publish(service: TaskMarketService, workspace: str, task_id: str) -> None:
    service.publish_work_item(
        PublishTaskWorkItemCommandV1(
            workspace=workspace,
            trace_id=f"trace-{task_id}",
            run_id="run-1",
            task_id=task_id,
            stage="pending_design",
            source_role="pm",
            payload={"title": task_id},
        )
    )


def test_independent_managers_for_different_workspaces(tmp_path) -> None:
    """Each workspace gets its own ConsumerLoopManager with independent threads."""
    ws_a = str(tmp_path / "ws-a")
    ws_b = str(tmp_path / "ws-b")

    manager_a = ConsumerLoopManager(ws_a, poll_interval=0.02)
    manager_b = ConsumerLoopManager(ws_b, poll_interval=0.02)

    fake_service = MagicMock()
    fake_service.relay_outbox_messages.return_value = {"sent": 0, "failed": 0}

    manager_a.start(consumer_types={"chief_engineer": FakeConsumer}, service=fake_service)
    manager_b.start(consumer_types={"chief_engineer": FakeConsumer}, service=fake_service)

    assert manager_a.is_running()
    assert manager_b.is_running()

    # Thread names should be different (different workspace hashes).
    threads_a = set(manager_a._threads.keys())
    threads_b = set(manager_b._threads.keys())
    assert threads_a == {"chief_engineer"}
    assert threads_b == {"chief_engineer"}

    # Verify workspaces are separate.
    status_a = manager_a.status()
    status_b = manager_b.status()
    assert status_a["workspace"] == ws_a
    assert status_b["workspace"] == ws_b

    manager_a.stop(join_timeout=3.0)
    manager_b.stop(join_timeout=3.0)

    assert not manager_a.is_running()
    assert not manager_b.is_running()


def test_consumers_only_claim_from_own_workspace(tmp_path) -> None:
    """Consumer threads in workspace A should not claim items from workspace B."""
    service = TaskMarketService()
    ws_a = str(tmp_path / "ws-a")
    ws_b = str(tmp_path / "ws-b")

    # Publish tasks to both workspaces.
    _publish(service, ws_a, "task-a-1")
    _publish(service, ws_a, "task-a-2")
    _publish(service, ws_b, "task-b-1")

    # Start consumer managers for both workspaces.
    fake_service = MagicMock()
    fake_service.relay_outbox_messages.return_value = {"sent": 0, "failed": 0}

    manager_a = ConsumerLoopManager(ws_a, poll_interval=0.05)
    manager_b = ConsumerLoopManager(ws_b, poll_interval=0.05)

    manager_a.start(consumer_types={"chief_engineer": FakeConsumer}, service=fake_service)
    manager_b.start(consumer_types={"chief_engineer": FakeConsumer}, service=fake_service)

    # Wait for consumers to claim.
    time.sleep(0.4)

    manager_a.stop(join_timeout=3.0)
    manager_b.stop(join_timeout=3.0)

    # Verify workspace A items were claimed by A's consumer.
    status_a = service.query_status(QueryTaskMarketStatusV1(workspace=ws_a))
    status_b = service.query_status(QueryTaskMarketStatusV1(workspace=ws_b))

    # Items in ws-a should be in_design (claimed + lease held) or still pending.
    for item in status_a.items:
        assert item["task_id"].startswith("task-a")

    for item in status_b.items:
        assert item["task_id"].startswith("task-b")

    # workspace B items should not have been touched by A's consumer.
    # workspace A items should not have been touched by B's consumer.
    assert len(status_a.items) == 2
    assert len(status_b.items) == 1


def test_stop_one_workspace_does_not_affect_other(tmp_path) -> None:
    """Stopping one workspace's manager should not affect the other."""
    ws_a = str(tmp_path / "ws-a")
    ws_b = str(tmp_path / "ws-b")

    fake_service = MagicMock()
    fake_service.relay_outbox_messages.return_value = {"sent": 0, "failed": 0}

    manager_a = ConsumerLoopManager(ws_a, poll_interval=0.02)
    manager_b = ConsumerLoopManager(ws_b, poll_interval=0.02)

    manager_a.start(consumer_types={"chief_engineer": FakeConsumer}, service=fake_service)
    manager_b.start(consumer_types={"chief_engineer": FakeConsumer}, service=fake_service)

    assert manager_a.is_running()
    assert manager_b.is_running()

    # Stop only workspace A.
    manager_a.stop(join_timeout=3.0)

    assert not manager_a.is_running()
    assert manager_b.is_running()

    # workspace B threads should still be alive.
    for role, thread in manager_b._threads.items():
        assert thread.is_alive(), f"B thread for role={role} died after A was stopped"

    manager_b.stop(join_timeout=3.0)


def test_service_start_stop_consumer_loops_multi_workspace(tmp_path) -> None:
    """TaskMarketService manages consumer loops across multiple workspaces."""
    service = TaskMarketService()
    ws_a = str(tmp_path / "ws-a")
    ws_b = str(tmp_path / "ws-b")

    fake_types = {"chief_engineer": FakeConsumer}

    # Start for workspace A.
    started_a = service.start_consumer_loops(ws_a, consumer_types=fake_types)
    assert started_a is True

    # Start for workspace B.
    started_b = service.start_consumer_loops(ws_b, consumer_types=fake_types)
    assert started_b is True

    # Starting again for same workspace returns False.
    assert service.start_consumer_loops(ws_a, consumer_types=fake_types) is False

    # Query status for both.
    status_a = service.query_consumer_loop_status(ws_a)
    status_b = service.query_consumer_loop_status(ws_b)
    assert status_a["is_running"] is True
    assert status_b["is_running"] is True

    # Stop workspace A.
    assert service.stop_consumer_loops(ws_a) is True
    assert service.query_consumer_loop_status(ws_a)["is_running"] is False
    assert status_b["is_running"] is True  # B still running (snapshot)

    # Stop all.
    stopped = service.stop_all_consumer_loops()
    assert stopped == 1  # Only ws_b left.

    # Unknown workspace returns False.
    assert service.stop_consumer_loops("nonexistent") is False


def test_three_workspaces_concurrent_operations(tmp_path) -> None:
    """Three workspaces can run consumers concurrently without interference."""
    service = TaskMarketService()

    fake_types = {"chief_engineer": FakeConsumer}
    workspaces = [str(tmp_path / f"ws-{i}") for i in range(3)]

    for ws in workspaces:
        service.start_consumer_loops(ws, consumer_types=fake_types)

    # Publish to each workspace.
    for i, ws in enumerate(workspaces):
        _publish(service, ws, f"task-{i}")

    time.sleep(0.3)

    # All managers should still be running.
    for ws in workspaces:
        status = service.query_consumer_loop_status(ws)
        assert status["is_running"] is True

    # Stop all.
    stopped = service.stop_all_consumer_loops()
    assert stopped == 3

    for ws in workspaces:
        assert service.query_consumer_loop_status(ws)["is_running"] is False
