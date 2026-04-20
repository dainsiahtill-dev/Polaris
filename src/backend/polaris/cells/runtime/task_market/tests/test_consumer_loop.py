"""Tests for ConsumerLoopManager — durable pull-consumer daemon thread management."""

from __future__ import annotations

import threading
import time
from typing import Any
from unittest.mock import MagicMock

import pytest
from polaris.cells.runtime.task_market.internal.consumer_loop import ConsumerLoopManager


class FakeConsumer:
    """Minimal consumer stub that tracks run/stop calls."""

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
        self.run_count = 0
        self.stopped = False

    def run(self) -> None:
        self.run_count += 1
        while not self._stop_event.wait(self.poll_interval):
            pass
        self.stopped = True

    def stop(self) -> None:
        self._stop_event.set()


class CrashingConsumer(FakeConsumer):
    """Consumer that raises on first run."""

    def run(self) -> None:
        self.run_count += 1
        if self.run_count == 1:
            raise RuntimeError("consumer crash on first run")
        # Subsequent runs succeed.
        super().run()


class TestConsumerLoopManagerStartStop:
    def test_start_returns_true_and_creates_threads(self, tmp_path: Any) -> None:
        workspace = str(tmp_path / "ws")
        manager = ConsumerLoopManager(workspace, poll_interval=0.02)

        fake_service = MagicMock()
        started = manager.start(
            consumer_types={
                "chief_engineer": FakeConsumer,
                "director": FakeConsumer,
                "qa": FakeConsumer,
            },
            service=fake_service,
        )

        assert started is True
        assert manager.is_running()

        status = manager.status()
        assert status["started"] is True
        assert status["is_running"] is True
        assert set(status["roles"].keys()) == {"chief_engineer", "director", "qa"}
        assert status["outbox_relay_running"] is True

        # All consumer threads should be alive.
        for role, role_status in status["roles"].items():
            assert role_status["running"] is True, f"role={role} not running"

        manager.stop(join_timeout=3.0)
        assert not manager.is_running()

    def test_start_returns_false_if_already_running(self, tmp_path: Any) -> None:
        workspace = str(tmp_path / "ws")
        manager = ConsumerLoopManager(workspace, poll_interval=0.02)

        fake_service = MagicMock()
        manager.start(
            consumer_types={"chief_engineer": FakeConsumer, "director": FakeConsumer, "qa": FakeConsumer},
            service=fake_service,
        )

        # Second start should return False.
        started_again = manager.start(
            consumer_types={"chief_engineer": FakeConsumer, "director": FakeConsumer, "qa": FakeConsumer},
            service=fake_service,
        )
        assert started_again is False

        manager.stop(join_timeout=3.0)

    def test_stop_joins_all_threads(self, tmp_path: Any) -> None:
        workspace = str(tmp_path / "ws")
        manager = ConsumerLoopManager(workspace, poll_interval=0.02)

        fake_service = MagicMock()
        manager.start(
            consumer_types={"chief_engineer": FakeConsumer, "director": FakeConsumer, "qa": FakeConsumer},
            service=fake_service,
        )

        threads_before = dict(manager._threads)
        assert len(threads_before) == 3

        manager.stop(join_timeout=3.0)

        # All threads should have exited.
        for role, thread in threads_before.items():
            assert not thread.is_alive(), f"thread for role={role} still alive after stop"

        # Outbox relay thread should also be done.
        assert manager._outbox_relay_thread is None

    def test_status_reports_running_state(self, tmp_path: Any) -> None:
        workspace = str(tmp_path / "ws")
        manager = ConsumerLoopManager(workspace, poll_interval=0.02)

        # Before start.
        status = manager.status()
        assert status["started"] is False
        assert status["is_running"] is False
        assert status["roles"] == {}

        fake_service = MagicMock()
        manager.start(
            consumer_types={"chief_engineer": FakeConsumer, "director": FakeConsumer, "qa": FakeConsumer},
            service=fake_service,
        )

        # After start.
        status = manager.status()
        assert status["started"] is True
        assert status["is_running"] is True
        for role in ("chief_engineer", "director", "qa"):
            assert status["roles"][role]["running"] is True

        manager.stop(join_timeout=3.0)

        # After stop.
        status = manager.status()
        assert status["started"] is False
        assert status["is_running"] is False


class TestConsumerLoopManagerExceptionIsolation:
    def test_exception_in_one_consumer_does_not_kill_others(self, tmp_path: Any) -> None:
        """One crashing consumer should not affect other consumer threads."""
        workspace = str(tmp_path / "ws")
        manager = ConsumerLoopManager(workspace, poll_interval=0.02)

        fake_service = MagicMock()
        manager.start(
            consumer_types={
                "chief_engineer": CrashingConsumer,
                "director": FakeConsumer,
                "qa": FakeConsumer,
            },
            service=fake_service,
        )

        # Wait a bit for consumers to run.
        time.sleep(0.3)

        status = manager.status()

        # Director and QA should still be running.
        assert status["roles"]["director"]["running"] is True
        assert status["roles"]["qa"]["running"] is True

        # chief_engineer crashed but manager is still "running" (it only
        # tracks the overall started state, not per-role health).
        assert status["is_running"] is True

        manager.stop(join_timeout=3.0)


class TestConsumerLoopManagerOutboxRelay:
    def test_outbox_relay_calls_service(self, tmp_path: Any) -> None:
        workspace = str(tmp_path / "ws")
        manager = ConsumerLoopManager(workspace, poll_interval=0.02, outbox_relay_interval=0.02)

        fake_service = MagicMock()
        fake_service.relay_outbox_messages.return_value = {"sent": 0, "failed": 0}

        manager.start(
            consumer_types={"chief_engineer": FakeConsumer, "director": FakeConsumer, "qa": FakeConsumer},
            service=fake_service,
        )

        # Wait for at least 3 relay calls.
        deadline = time.monotonic() + 2.0
        while fake_service.relay_outbox_messages.call_count < 3 and time.monotonic() < deadline:
            time.sleep(0.01)

        assert fake_service.relay_outbox_messages.call_count >= 3

        manager.stop(join_timeout=3.0)


class TestConsumerLoopManagerInit:
    def test_empty_workspace_raises(self) -> None:
        with pytest.raises(ValueError, match="workspace must be a non-empty string"):
            ConsumerLoopManager("")

    def test_workspace_stored(self, tmp_path: Any) -> None:
        workspace = str(tmp_path / "ws")
        manager = ConsumerLoopManager(workspace)
        assert manager.workspace == workspace
