"""Tests for workflow_runtime internal process_runner_port module."""

from __future__ import annotations

from polaris.cells.orchestration.workflow_runtime.internal.process_runner_port import (
    ProcessHandle,
    ProcessRunnerError,
    ProcessStatus,
)


class TestProcessStatus:
    def test_enum_values(self) -> None:
        assert ProcessStatus.PENDING.value == "pending"
        assert ProcessStatus.RUNNING.value == "running"
        assert ProcessStatus.COMPLETED.value == "completed"


class TestProcessHandle:
    def test_creation(self) -> None:
        handle = ProcessHandle(process_id="p1", pid=123, name="test")
        assert handle.process_id == "p1"
        assert handle.pid == 123
        assert handle.name == "test"
        assert handle.metadata == {}

    def test_post_init_metadata(self) -> None:
        handle = ProcessHandle(process_id="p1", metadata=None)
        assert handle.metadata == {}


class TestProcessRunnerError:
    def test_error_message(self) -> None:
        err = ProcessRunnerError("boom")
        assert str(err) == "boom"
        assert err.message == "boom"
        assert err.handle is None

    def test_error_with_handle(self) -> None:
        handle = ProcessHandle(process_id="p1")
        err = ProcessRunnerError("boom", handle=handle)
        assert err.handle is handle
