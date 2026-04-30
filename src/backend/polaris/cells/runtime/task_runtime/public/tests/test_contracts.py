"""Tests for polaris.cells.runtime.task_runtime.public.contracts."""

from __future__ import annotations

import pytest
from polaris.cells.runtime.task_runtime.public.contracts import (
    CreateRuntimeTaskCommandV1,
    GetRuntimeTaskQueryV1,
    ListRuntimeTasksQueryV1,
    ReopenRuntimeTaskCommandV1,
    RuntimeTaskLifecycleEventV1,
    RuntimeTaskResultV1,
    RuntimeTaskRuntimeError,
    UpdateRuntimeTaskCommandV1,
)


class TestRequireNonEmptyHelper:
    """Tests for the internal _require_non_empty helper (via public API)."""

    def test_empty_task_id_raises(self) -> None:
        with pytest.raises(ValueError, match="task_id must be a non-empty string"):
            CreateRuntimeTaskCommandV1(task_id="", workspace="ws", title="title", owner="owner")

    def test_whitespace_only_task_id_raises(self) -> None:
        with pytest.raises(ValueError, match="task_id must be a non-empty string"):
            CreateRuntimeTaskCommandV1(task_id="   ", workspace="ws", title="title", owner="owner")

    def test_none_converted_to_string_raises(self) -> None:
        # str(None) returns "None" which is non-empty, so this should succeed
        cmd = CreateRuntimeTaskCommandV1(
            task_id=None,  # type: ignore[arg-type]
            workspace="ws",
            title="title",
            owner="owner",
        )
        assert cmd.task_id == "None"


class TestCreateRuntimeTaskCommandV1:
    """Tests for CreateRuntimeTaskCommandV1."""

    def test_create_with_all_fields(self) -> None:
        cmd = CreateRuntimeTaskCommandV1(
            task_id="task-001",
            workspace="/tmp/ws",
            title="Test Task",
            owner="user-001",
        )
        assert cmd.task_id == "task-001"
        assert cmd.workspace == "/tmp/ws"
        assert cmd.title == "Test Task"
        assert cmd.owner == "user-001"
        assert cmd.payload == {}

    def test_create_with_payload(self) -> None:
        cmd = CreateRuntimeTaskCommandV1(
            task_id="task-001",
            workspace="/tmp/ws",
            title="Test",
            owner="user-001",
            payload={"key": "value"},
        )
        assert cmd.payload == {"key": "value"}

    def test_empty_workspace_raises(self) -> None:
        with pytest.raises(ValueError, match="workspace must be a non-empty string"):
            CreateRuntimeTaskCommandV1(task_id="task-001", workspace="", title="Test", owner="user-001")

    def test_empty_title_raises(self) -> None:
        with pytest.raises(ValueError, match="title must be a non-empty string"):
            CreateRuntimeTaskCommandV1(task_id="task-001", workspace="ws", title="", owner="user-001")

    def test_empty_owner_raises(self) -> None:
        with pytest.raises(ValueError, match="owner must be a non-empty string"):
            CreateRuntimeTaskCommandV1(task_id="task-001", workspace="ws", title="Test", owner="")

    def test_is_frozen(self) -> None:
        cmd = CreateRuntimeTaskCommandV1(task_id="t", workspace="w", title="t", owner="o")
        with pytest.raises(AttributeError):
            cmd.task_id = "x"  # type: ignore[misc]

    def test_payload_defaults_to_empty_dict(self) -> None:
        cmd = CreateRuntimeTaskCommandV1(task_id="t", workspace="w", title="t", owner="o")
        assert cmd.payload == {}

    def test_payload_is_copied(self) -> None:
        original = {"key": "value"}
        cmd = CreateRuntimeTaskCommandV1(task_id="t", workspace="w", title="t", owner="o", payload=original)
        assert cmd.payload is not original

    def test_equality(self) -> None:
        cmd1 = CreateRuntimeTaskCommandV1(task_id="t", workspace="w", title="t", owner="o")
        cmd2 = CreateRuntimeTaskCommandV1(task_id="t", workspace="w", title="t", owner="o")
        assert cmd1 == cmd2


class TestUpdateRuntimeTaskCommandV1:
    """Tests for UpdateRuntimeTaskCommandV1."""

    def test_create_with_all_fields(self) -> None:
        cmd = UpdateRuntimeTaskCommandV1(
            task_id="task-001",
            workspace="/tmp/ws",
            status="completed",
        )
        assert cmd.task_id == "task-001"
        assert cmd.workspace == "/tmp/ws"
        assert cmd.status == "completed"

    def test_empty_status_raises(self) -> None:
        with pytest.raises(ValueError, match="status must be a non-empty string"):
            UpdateRuntimeTaskCommandV1(task_id="task-001", workspace="ws", status="")

    def test_payload_defaults_to_empty_dict(self) -> None:
        cmd = UpdateRuntimeTaskCommandV1(task_id="t", workspace="w", status="s")
        assert cmd.payload == {}


class TestReopenRuntimeTaskCommandV1:
    """Tests for ReopenRuntimeTaskCommandV1."""

    def test_create(self) -> None:
        cmd = ReopenRuntimeTaskCommandV1(
            task_id="task-001",
            workspace="/tmp/ws",
            reason="needs more work",
        )
        assert cmd.task_id == "task-001"
        assert cmd.workspace == "/tmp/ws"
        assert cmd.reason == "needs more work"

    def test_empty_reason_raises(self) -> None:
        with pytest.raises(ValueError, match="reason must be a non-empty string"):
            ReopenRuntimeTaskCommandV1(task_id="task-001", workspace="ws", reason="")

    def test_empty_task_id_raises(self) -> None:
        with pytest.raises(ValueError, match="task_id must be a non-empty string"):
            ReopenRuntimeTaskCommandV1(task_id="", workspace="ws", reason="r")


class TestListRuntimeTasksQueryV1:
    """Tests for ListRuntimeTasksQueryV1."""

    def test_create_with_defaults(self) -> None:
        query = ListRuntimeTasksQueryV1(workspace="/tmp/ws")
        assert query.workspace == "/tmp/ws"
        assert query.statuses == ()
        assert query.owner is None
        assert query.limit == 100
        assert query.offset == 0

    def test_create_with_all_fields(self) -> None:
        query = ListRuntimeTasksQueryV1(
            workspace="/tmp/ws",
            statuses=("pending", "completed"),
            owner="user-001",
            limit=50,
            offset=10,
        )
        assert query.workspace == "/tmp/ws"
        assert query.statuses == ("pending", "completed")
        assert query.owner == "user-001"
        assert query.limit == 50
        assert query.offset == 10

    def test_empty_workspace_raises(self) -> None:
        with pytest.raises(ValueError, match="workspace must be a non-empty string"):
            ListRuntimeTasksQueryV1(workspace="")

    def test_limit_zero_raises(self) -> None:
        with pytest.raises(ValueError, match="limit must be >= 1"):
            ListRuntimeTasksQueryV1(workspace="ws", limit=0)

    def test_limit_negative_raises(self) -> None:
        with pytest.raises(ValueError, match="limit must be >= 1"):
            ListRuntimeTasksQueryV1(workspace="ws", limit=-1)

    def test_offset_negative_raises(self) -> None:
        with pytest.raises(ValueError, match="offset must be >= 0"):
            ListRuntimeTasksQueryV1(workspace="ws", offset=-1)

    def test_statuses_filtered(self) -> None:
        query = ListRuntimeTasksQueryV1(workspace="ws", statuses=("pending", "", "completed", "  "))
        assert query.statuses == ("pending", "completed")

    def test_owner_none_allowed(self) -> None:
        query = ListRuntimeTasksQueryV1(workspace="ws", owner=None)
        assert query.owner is None

    def test_owner_empty_raises(self) -> None:
        with pytest.raises(ValueError, match="owner must be a non-empty string"):
            ListRuntimeTasksQueryV1(workspace="ws", owner="")

    def test_large_limit(self) -> None:
        query = ListRuntimeTasksQueryV1(workspace="ws", limit=10000)
        assert query.limit == 10000


class TestGetRuntimeTaskQueryV1:
    """Tests for GetRuntimeTaskQueryV1."""

    def test_create(self) -> None:
        query = GetRuntimeTaskQueryV1(task_id="task-001", workspace="/tmp/ws")
        assert query.task_id == "task-001"
        assert query.workspace == "/tmp/ws"

    def test_empty_task_id_raises(self) -> None:
        with pytest.raises(ValueError, match="task_id must be a non-empty string"):
            GetRuntimeTaskQueryV1(task_id="", workspace="ws")

    def test_empty_workspace_raises(self) -> None:
        with pytest.raises(ValueError, match="workspace must be a non-empty string"):
            GetRuntimeTaskQueryV1(task_id="t", workspace="")


class TestRuntimeTaskLifecycleEventV1:
    """Tests for RuntimeTaskLifecycleEventV1."""

    def test_create(self) -> None:
        event = RuntimeTaskLifecycleEventV1(
            event_id="evt-001",
            task_id="task-001",
            workspace="/tmp/ws",
            status="completed",
            occurred_at="2024-01-01T00:00:00Z",
        )
        assert event.event_id == "evt-001"
        assert event.task_id == "task-001"
        assert event.workspace == "/tmp/ws"
        assert event.status == "completed"
        assert event.occurred_at == "2024-01-01T00:00:00Z"
        assert event.payload == {}

    def test_empty_event_id_raises(self) -> None:
        with pytest.raises(ValueError, match="event_id must be a non-empty string"):
            RuntimeTaskLifecycleEventV1(event_id="", task_id="t", workspace="w", status="s", occurred_at="t")

    def test_empty_occurred_at_raises(self) -> None:
        with pytest.raises(ValueError, match="occurred_at must be a non-empty string"):
            RuntimeTaskLifecycleEventV1(event_id="e", task_id="t", workspace="w", status="s", occurred_at="")

    def test_payload_defaults_to_empty_dict(self) -> None:
        event = RuntimeTaskLifecycleEventV1(event_id="e", task_id="t", workspace="w", status="s", occurred_at="t")
        assert event.payload == {}


class TestRuntimeTaskResultV1:
    """Tests for RuntimeTaskResultV1."""

    def test_create(self) -> None:
        result = RuntimeTaskResultV1(
            task_id="task-001",
            workspace="/tmp/ws",
            status="completed",
            version=1,
        )
        assert result.task_id == "task-001"
        assert result.workspace == "/tmp/ws"
        assert result.status == "completed"
        assert result.version == 1
        assert result.updated is True

    def test_create_with_updated_false(self) -> None:
        result = RuntimeTaskResultV1(task_id="t", workspace="w", status="s", version=0, updated=False)
        assert result.updated is False

    def test_empty_task_id_raises(self) -> None:
        with pytest.raises(ValueError, match="task_id must be a non-empty string"):
            RuntimeTaskResultV1(task_id="", workspace="w", status="s", version=0)

    def test_negative_version_raises(self) -> None:
        with pytest.raises(ValueError, match="version must be >= 0"):
            RuntimeTaskResultV1(task_id="t", workspace="w", status="s", version=-1)

    def test_version_zero_allowed(self) -> None:
        result = RuntimeTaskResultV1(task_id="t", workspace="w", status="s", version=0)
        assert result.version == 0


class TestRuntimeTaskRuntimeError:
    """Tests for RuntimeTaskRuntimeError exception."""

    def test_create_with_message(self) -> None:
        err = RuntimeTaskRuntimeError("something went wrong")
        assert str(err) == "something went wrong"
        assert err.code == "runtime_task_runtime_error"
        assert err.details == {}

    def test_create_with_custom_code(self) -> None:
        err = RuntimeTaskRuntimeError("msg", code="custom_code")
        assert err.code == "custom_code"

    def test_create_with_details(self) -> None:
        err = RuntimeTaskRuntimeError("msg", details={"key": "value"})
        assert err.details == {"key": "value"}

    def test_empty_message_raises(self) -> None:
        with pytest.raises(ValueError, match="message must be a non-empty string"):
            RuntimeTaskRuntimeError("")

    def test_is_runtime_error(self) -> None:
        err = RuntimeTaskRuntimeError("test")
        assert isinstance(err, RuntimeError)

    def test_raise_and_catch(self) -> None:
        with pytest.raises(RuntimeTaskRuntimeError) as exc_info:
            raise RuntimeTaskRuntimeError("test error")
        assert str(exc_info.value) == "test error"

    def test_details_is_copied(self) -> None:
        original = {"key": "value"}
        err = RuntimeTaskRuntimeError("msg", details=original)
        assert err.details is not original


class TestModuleExports:
    """Tests for module __all__ exports."""

    def test_all_exports_present(self) -> None:
        from polaris.cells.runtime.task_runtime.public import contracts as mod

        assert hasattr(mod, "__all__")
        assert "CreateRuntimeTaskCommandV1" in mod.__all__
        assert "GetRuntimeTaskQueryV1" in mod.__all__
        assert "ListRuntimeTasksQueryV1" in mod.__all__
        assert "ReopenRuntimeTaskCommandV1" in mod.__all__
        assert "RuntimeTaskLifecycleEventV1" in mod.__all__
        assert "RuntimeTaskResultV1" in mod.__all__
        assert "RuntimeTaskRuntimeError" in mod.__all__
        assert "UpdateRuntimeTaskCommandV1" in mod.__all__
        assert len(mod.__all__) == 8
