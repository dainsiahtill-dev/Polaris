"""Tests for polaris.cells.roles.runtime.public.contracts.

Covers dataclass construction, validation, serialization, stream contracts,
and error contracts for the roles.runtime public boundary.
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest
from polaris.cells.roles.runtime.public.contracts import (
    ExecuteRoleSessionCommandV1,
    ExecuteRoleTaskCommandV1,
    GetRoleRuntimeStatusQueryV1,
    IRoleRuntime,
    RoleExecutionResultV1,
    RoleRuntimeError,
    RoleTaskCompletedEventV1,
    RoleTaskStartedEventV1,
    StandardStreamEvent,
    StreamTurnOptions,
)


class TestExecuteRoleTaskCommandV1:
    def test_minimal_construction(self) -> None:
        cmd = ExecuteRoleTaskCommandV1(
            role="pm",
            task_id="t1",
            workspace="/tmp/ws",
            objective="do something",
        )
        assert cmd.role == "pm"
        assert cmd.task_id == "t1"
        assert cmd.workspace == "/tmp/ws"
        assert cmd.objective == "do something"
        assert cmd.run_id is None
        assert cmd.session_id is None
        assert cmd.domain is None
        assert cmd.context == {}
        assert cmd.metadata == {}
        assert cmd.timeout_seconds is None
        assert cmd.stream is False
        assert cmd.host_kind is None

    def test_full_construction(self) -> None:
        cmd = ExecuteRoleTaskCommandV1(
            role="architect",
            task_id="t2",
            workspace="ws",
            objective="design",
            run_id="r1",
            session_id="s1",
            domain="code",
            context={"key": "val"},
            metadata={"trace": "id"},
            timeout_seconds=120,
            stream=True,
            host_kind="cli",
        )
        assert cmd.run_id == "r1"
        assert cmd.session_id == "s1"
        assert cmd.domain == "code"
        assert cmd.context == {"key": "val"}
        assert cmd.metadata == {"trace": "id"}
        assert cmd.timeout_seconds == 120
        assert cmd.stream is True
        assert cmd.host_kind == "cli"

    def test_empty_role_raises(self) -> None:
        with pytest.raises(ValueError, match="role must be a non-empty string"):
            ExecuteRoleTaskCommandV1(
                role="",
                task_id="t1",
                workspace="ws",
                objective="do",
            )

    def test_empty_task_id_raises(self) -> None:
        with pytest.raises(ValueError, match="task_id must be a non-empty string"):
            ExecuteRoleTaskCommandV1(
                role="pm",
                task_id="",
                workspace="ws",
                objective="do",
            )

    def test_empty_workspace_raises(self) -> None:
        with pytest.raises(ValueError, match="workspace must be a non-empty string"):
            ExecuteRoleTaskCommandV1(
                role="pm",
                task_id="t1",
                workspace="",
                objective="do",
            )

    def test_empty_objective_raises(self) -> None:
        with pytest.raises(ValueError, match="objective must be a non-empty string"):
            ExecuteRoleTaskCommandV1(
                role="pm",
                task_id="t1",
                workspace="ws",
                objective="",
            )

    def test_domain_normalized_to_lowercase(self) -> None:
        cmd = ExecuteRoleTaskCommandV1(
            role="pm",
            task_id="t1",
            workspace="ws",
            objective="do",
            domain="CODE",
        )
        assert cmd.domain == "code"

    def test_domain_whitespace_normalized(self) -> None:
        cmd = ExecuteRoleTaskCommandV1(
            role="pm",
            task_id="t1",
            workspace="ws",
            objective="do",
            domain="  code  ",
        )
        assert cmd.domain == "code"

    def test_empty_domain_becomes_none(self) -> None:
        cmd = ExecuteRoleTaskCommandV1(
            role="pm",
            task_id="t1",
            workspace="ws",
            objective="do",
            domain="",
        )
        assert cmd.domain is None

    def test_timeout_zero_raises(self) -> None:
        with pytest.raises(ValueError, match="timeout_seconds must be > 0 when provided"):
            ExecuteRoleTaskCommandV1(
                role="pm",
                task_id="t1",
                workspace="ws",
                objective="do",
                timeout_seconds=0,
            )

    def test_timeout_negative_raises(self) -> None:
        with pytest.raises(ValueError, match="timeout_seconds must be > 0 when provided"):
            ExecuteRoleTaskCommandV1(
                role="pm",
                task_id="t1",
                workspace="ws",
                objective="do",
                timeout_seconds=-5,
            )

    def test_context_is_copied(self) -> None:
        original = {"a": 1}
        cmd = ExecuteRoleTaskCommandV1(
            role="pm",
            task_id="t1",
            workspace="ws",
            objective="do",
            context=original,
        )
        original["a"] = 2
        assert cmd.context == {"a": 1}

    def test_frozen_dataclass(self) -> None:
        cmd = ExecuteRoleTaskCommandV1(
            role="pm",
            task_id="t1",
            workspace="ws",
            objective="do",
        )
        with pytest.raises(FrozenInstanceError):
            cmd.role = "qa"  # type: ignore[misc]


class TestExecuteRoleSessionCommandV1:
    def test_minimal_construction(self) -> None:
        cmd = ExecuteRoleSessionCommandV1(
            role="pm",
            session_id="s1",
            workspace="/tmp/ws",
            user_message="hello",
        )
        assert cmd.role == "pm"
        assert cmd.session_id == "s1"
        assert cmd.workspace == "/tmp/ws"
        assert cmd.user_message == "hello"
        assert cmd.stream is True
        assert cmd.history == ()
        assert cmd.context == {}
        assert cmd.metadata == {}
        assert cmd.stream_options is None

    def test_with_history(self) -> None:
        cmd = ExecuteRoleSessionCommandV1(
            role="pm",
            session_id="s1",
            workspace="ws",
            user_message="hello",
            history=(("user", "hi"), ("assistant", "hey")),
        )
        assert cmd.history == (("user", "hi"), ("assistant", "hey"))

    def test_history_from_dict_list(self) -> None:
        cmd = ExecuteRoleSessionCommandV1(
            role="pm",
            session_id="s1",
            workspace="ws",
            user_message="hello",
            history=[{"role": "user", "content": "hi"}],
        )
        assert cmd.history == (("user", "hi"),)

    def test_history_from_tuple_list(self) -> None:
        cmd = ExecuteRoleSessionCommandV1(
            role="pm",
            session_id="s1",
            workspace="ws",
            user_message="hello",
            history=[["user", "hi"]],
        )
        assert cmd.history == (("user", "hi"),)

    def test_history_from_message_key(self) -> None:
        cmd = ExecuteRoleSessionCommandV1(
            role="pm",
            session_id="s1",
            workspace="ws",
            user_message="hello",
            history=[{"role": "user", "message": "hi"}],
        )
        assert cmd.history == (("user", "hi"),)

    def test_empty_role_raises(self) -> None:
        with pytest.raises(ValueError, match="role must be a non-empty string"):
            ExecuteRoleSessionCommandV1(
                role="",
                session_id="s1",
                workspace="ws",
                user_message="hello",
            )

    def test_empty_session_id_raises(self) -> None:
        with pytest.raises(ValueError, match="session_id must be a non-empty string"):
            ExecuteRoleSessionCommandV1(
                role="pm",
                session_id="",
                workspace="ws",
                user_message="hello",
            )

    def test_empty_user_message_raises(self) -> None:
        with pytest.raises(ValueError, match="user_message must be a non-empty string"):
            ExecuteRoleSessionCommandV1(
                role="pm",
                session_id="s1",
                workspace="ws",
                user_message="",
            )

    def test_invalid_history_type_raises(self) -> None:
        with pytest.raises(ValueError, match="history must be an iterable"):
            ExecuteRoleSessionCommandV1(
                role="pm",
                session_id="s1",
                workspace="ws",
                user_message="hello",
                history="not a list",
            )

    def test_invalid_history_entry_raises(self) -> None:
        with pytest.raises(ValueError, match="history entries must provide non-empty role and content"):
            ExecuteRoleSessionCommandV1(
                role="pm",
                session_id="s1",
                workspace="ws",
                user_message="hello",
                history=[{"role": "", "content": ""}],
            )

    def test_stream_options_type_check(self) -> None:
        opts = StreamTurnOptions(stream=True, context={"key": "val"}, history_limit=10)
        cmd = ExecuteRoleSessionCommandV1(
            role="pm",
            session_id="s1",
            workspace="ws",
            user_message="hello",
            stream_options=opts,
        )
        assert cmd.stream_options == opts

    def test_invalid_stream_options_type_raises(self) -> None:
        with pytest.raises(TypeError, match="stream_options must be a StreamTurnOptions instance"):
            ExecuteRoleSessionCommandV1(
                role="pm",
                session_id="s1",
                workspace="ws",
                user_message="hello",
                stream_options={"stream": True},  # type: ignore[arg-type]
            )

    def test_none_history(self) -> None:
        cmd = ExecuteRoleSessionCommandV1(
            role="pm",
            session_id="s1",
            workspace="ws",
            user_message="hello",
            history=None,  # type: ignore[arg-type]
        )
        assert cmd.history == ()


class TestGetRoleRuntimeStatusQueryV1:
    def test_minimal_construction(self) -> None:
        q = GetRoleRuntimeStatusQueryV1(workspace="/tmp/ws")
        assert q.workspace == "/tmp/ws"
        assert q.role is None
        assert q.include_agent_health is True
        assert q.include_queue is True
        assert q.include_tools is False

    def test_with_role(self) -> None:
        q = GetRoleRuntimeStatusQueryV1(
            workspace="ws",
            role="pm",
            include_agent_health=False,
            include_queue=False,
            include_tools=True,
        )
        assert q.role == "pm"
        assert q.include_agent_health is False
        assert q.include_queue is False
        assert q.include_tools is True

    def test_empty_workspace_raises(self) -> None:
        with pytest.raises(ValueError, match="workspace must be a non-empty string"):
            GetRoleRuntimeStatusQueryV1(workspace="")

    def test_empty_role_raises(self) -> None:
        with pytest.raises(ValueError, match="role must be a non-empty string"):
            GetRoleRuntimeStatusQueryV1(workspace="ws", role="")

    def test_none_role_allowed(self) -> None:
        q = GetRoleRuntimeStatusQueryV1(workspace="ws", role=None)
        assert q.role is None


class TestRoleTaskStartedEventV1:
    def test_minimal_construction(self) -> None:
        ev = RoleTaskStartedEventV1(
            event_id="e1",
            role="pm",
            task_id="t1",
            workspace="ws",
            started_at="2024-01-01T00:00:00Z",
        )
        assert ev.event_id == "e1"
        assert ev.role == "pm"
        assert ev.task_id == "t1"
        assert ev.workspace == "ws"
        assert ev.started_at == "2024-01-01T00:00:00Z"
        assert ev.run_id is None
        assert ev.session_id is None

    def test_with_optional_fields(self) -> None:
        ev = RoleTaskStartedEventV1(
            event_id="e1",
            role="pm",
            task_id="t1",
            workspace="ws",
            started_at="2024-01-01T00:00:00Z",
            run_id="r1",
            session_id="s1",
        )
        assert ev.run_id == "r1"
        assert ev.session_id == "s1"

    def test_empty_event_id_raises(self) -> None:
        with pytest.raises(ValueError, match="event_id must be a non-empty string"):
            RoleTaskStartedEventV1(
                event_id="",
                role="pm",
                task_id="t1",
                workspace="ws",
                started_at="2024-01-01T00:00:00Z",
            )


class TestRoleTaskCompletedEventV1:
    def test_minimal_construction(self) -> None:
        ev = RoleTaskCompletedEventV1(
            event_id="e1",
            role="pm",
            task_id="t1",
            workspace="ws",
            status="ok",
            completed_at="2024-01-01T00:00:00Z",
        )
        assert ev.event_id == "e1"
        assert ev.role == "pm"
        assert ev.task_id == "t1"
        assert ev.workspace == "ws"
        assert ev.status == "ok"
        assert ev.completed_at == "2024-01-01T00:00:00Z"
        assert ev.output_summary is None
        assert ev.error_code is None
        assert ev.error_message is None

    def test_with_optional_fields(self) -> None:
        ev = RoleTaskCompletedEventV1(
            event_id="e1",
            role="pm",
            task_id="t1",
            workspace="ws",
            status="failed",
            completed_at="2024-01-01T00:00:00Z",
            output_summary="summary",
            error_code="E001",
            error_message="error",
        )
        assert ev.output_summary == "summary"
        assert ev.error_code == "E001"
        assert ev.error_message == "error"

    def test_empty_status_raises(self) -> None:
        with pytest.raises(ValueError, match="status must be a non-empty string"):
            RoleTaskCompletedEventV1(
                event_id="e1",
                role="pm",
                task_id="t1",
                workspace="ws",
                status="",
                completed_at="2024-01-01T00:00:00Z",
            )


class TestRoleExecutionResultV1:
    def test_success_result(self) -> None:
        result = RoleExecutionResultV1(
            ok=True,
            status="ok",
            role="pm",
            workspace="ws",
        )
        assert result.ok is True
        assert result.status == "ok"
        assert result.role == "pm"
        assert result.workspace == "ws"
        assert result.output == ""
        assert result.thinking is None
        assert result.tool_calls == ()
        assert result.artifacts == ()
        assert result.usage == {}
        assert result.metadata == {}
        assert result.turn_history == []
        assert result.error_code is None
        assert result.error_message is None

    def test_full_result(self) -> None:
        result = RoleExecutionResultV1(
            ok=True,
            status="ok",
            role="pm",
            workspace="ws",
            task_id="t1",
            session_id="s1",
            run_id="r1",
            output="output",
            thinking="thinking",
            tool_calls=("tool1", "tool2"),
            artifacts=("art1",),
            usage={"tokens": 100},
            metadata={"trace": "id"},
            turn_history=[("user", "hi"), ("assistant", "hey")],
        )
        assert result.task_id == "t1"
        assert result.session_id == "s1"
        assert result.run_id == "r1"
        assert result.output == "output"
        assert result.thinking == "thinking"
        assert result.tool_calls == ("tool1", "tool2")
        assert result.artifacts == ("art1",)
        assert result.usage == {"tokens": 100}
        assert result.metadata == {"trace": "id"}
        assert result.turn_history == [("user", "hi"), ("assistant", "hey")]

    def test_failed_result_without_error_raises(self) -> None:
        with pytest.raises(ValueError, match="failed result must include error_code or error_message"):
            RoleExecutionResultV1(
                ok=False,
                status="failed",
                role="pm",
                workspace="ws",
            )

    def test_tool_calls_coerced_to_strings(self) -> None:
        result = RoleExecutionResultV1(
            ok=True,
            status="ok",
            role="pm",
            workspace="ws",
            tool_calls=(1, 2),  # type: ignore[arg-type]
        )
        assert result.tool_calls == ("1", "2")

    def test_turn_history_is_copied(self) -> None:
        original = [("user", "hi")]
        result = RoleExecutionResultV1(
            ok=True,
            status="ok",
            role="pm",
            workspace="ws",
            turn_history=original,
        )
        original.append(("assistant", "hey"))
        assert result.turn_history == [("user", "hi")]

    def test_empty_status_raises(self) -> None:
        with pytest.raises(ValueError, match="status must be a non-empty string"):
            RoleExecutionResultV1(
                ok=True,
                status="",
                role="pm",
                workspace="ws",
            )


class TestStreamTurnOptions:
    def test_defaults(self) -> None:
        opts = StreamTurnOptions()
        assert opts.stream is True
        assert opts.context is None
        assert opts.history_limit is None
        assert opts.prompt_appendix is None

    def test_custom_values(self) -> None:
        opts = StreamTurnOptions(
            stream=False,
            context={"key": "val"},
            history_limit=20,
            prompt_appendix="appendix",
        )
        assert opts.stream is False
        assert opts.context == {"key": "val"}
        assert opts.history_limit == 20
        assert opts.prompt_appendix == "appendix"

    def test_frozen_dataclass(self) -> None:
        opts = StreamTurnOptions()
        with pytest.raises(FrozenInstanceError):
            opts.stream = False  # type: ignore[misc]


class TestStandardStreamEvent:
    def test_defaults(self) -> None:
        ev = StandardStreamEvent()
        assert ev["type"] == ""
        assert ev["data"] == {}
        assert ev["metadata"] == {}
        assert ev.event_type == ""
        assert ev.event_data == {}

    def test_custom_values(self) -> None:
        ev = StandardStreamEvent(
            type="content_chunk",
            data={"content": "hello"},
            metadata={"turn": 1},
        )
        assert ev["type"] == "content_chunk"
        assert ev["data"] == {"content": "hello"}
        assert ev["metadata"] == {"turn": 1}
        assert ev.event_type == "content_chunk"
        assert ev.event_data == {"content": "hello"}

    def test_is_dict_subclass(self) -> None:
        ev = StandardStreamEvent()
        assert isinstance(ev, dict)

    def test_dict_behavior(self) -> None:
        ev = StandardStreamEvent(type="test")
        assert ev.get("type") == "test"
        assert "data" in ev


class TestRoleRuntimeError:
    def test_default_code(self) -> None:
        err = RoleRuntimeError("something bad")
        assert str(err) == "something bad"
        assert err.code == "role_runtime_error"
        assert err.details == {}

    def test_custom_code_and_details(self) -> None:
        err = RoleRuntimeError(
            "bad request",
            code="validation_error",
            details={"field": "message"},
        )
        assert err.code == "validation_error"
        assert err.details == {"field": "message"}

    def test_empty_message_raises(self) -> None:
        with pytest.raises(ValueError, match="message must be a non-empty string"):
            RoleRuntimeError("")

    def test_empty_code_raises(self) -> None:
        with pytest.raises(ValueError, match="code must be a non-empty string"):
            RoleRuntimeError("msg", code="")

    def test_details_are_copied(self) -> None:
        original = {"a": 1}
        err = RoleRuntimeError("msg", details=original)
        original["a"] = 2
        assert err.details == {"a": 1}

    def test_is_runtime_error(self) -> None:
        assert issubclass(RoleRuntimeError, RuntimeError)

    def test_to_dict(self) -> None:
        err = RoleRuntimeError("msg", code="E001", details={"k": "v"})
        d = err.to_dict()
        assert d["code"] == "E001"
        assert d["message"] == "msg"
        assert d["details"] == {"k": "v"}


class TestIRoleRuntime:
    def test_is_protocol(self) -> None:
        assert hasattr(IRoleRuntime, "execute_role_task")
        assert hasattr(IRoleRuntime, "execute_role_session")
        assert hasattr(IRoleRuntime, "get_runtime_status")
        assert hasattr(IRoleRuntime, "execute_role")
        assert hasattr(IRoleRuntime, "stream_chat_turn")

    def test_runtime_checkable(self) -> None:
        class FakeRuntime:
            async def execute_role_task(self, command): ...
            async def execute_role_session(self, command): ...
            async def get_runtime_status(self, query): ...
            async def execute_role(self, role_id, context): ...
            def stream_chat_turn(self, command): ...

        assert isinstance(FakeRuntime(), IRoleRuntime)

    def test_missing_method_fails_check(self) -> None:
        class BadRuntime:
            async def execute_role_task(self, command): ...

        assert not isinstance(BadRuntime(), IRoleRuntime)
