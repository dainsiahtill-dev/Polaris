"""Unit tests for the stream protocol layer.

Tests cover:
- Task #2: StreamTurnOptions and StandardStreamEvent in contracts.py
- Task #3: StreamEventType enum and console_protocol.py converters
- Task #4: RoleConsoleHost multi-role generalisation
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, cast
from unittest.mock import MagicMock, patch

import pytest
from polaris.cells.roles.runtime.public.contracts import (
    ExecuteRoleSessionCommandV1,
    IRoleRuntime,
    StandardStreamEvent,
    StreamTurnOptions,
)
from polaris.delivery.cli.director.console_host import (
    DirectorConsoleHost,
    DirectorConsoleHostConfig,
    RoleConsoleHost,
    SessionContinuityProjection,
)
from polaris.delivery.cli.director.console_protocol import (
    StandardStreamEvent as ProtocolStreamEvent,
    StreamEventType,
    from_kernel_event,
    to_standard_event,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

# ─────────────────────────────────────────────────────────────────────────────
# Task #2: StreamTurnOptions and StandardStreamEvent contract tests
# ─────────────────────────────────────────────────────────────────────────────


class TestStreamTurnOptions:
    """Tests for StreamTurnOptions dataclass."""

    def test_defaults(self) -> None:
        opts = StreamTurnOptions()
        assert opts.stream is True
        assert opts.context is None
        assert opts.history_limit is None
        assert opts.prompt_appendix is None

    def test_custom_values(self) -> None:
        ctx = {"key": "value"}
        opts = StreamTurnOptions(
            stream=False,
            context=ctx,
            history_limit=50,
            prompt_appendix="Extra system prompt",
        )
        assert opts.stream is False
        assert opts.context == ctx
        assert opts.history_limit == 50
        assert opts.prompt_appendix == "Extra system prompt"

    def test_immutable(self) -> None:
        opts = StreamTurnOptions()
        with pytest.raises(AttributeError):
            opts.stream = False  # type: ignore[misc]


class TestStandardStreamEvent:
    """Tests for StandardStreamEvent dict subclass."""

    def test_construction(self) -> None:
        evt = StandardStreamEvent(type="content_chunk", data={"content": "hello"})
        assert evt["type"] == "content_chunk"
        assert evt["data"] == {"content": "hello"}
        assert evt.event_type == "content_chunk"
        assert evt.event_data == {"content": "hello"}

    def test_dict_subclass_behaviour(self) -> None:
        evt = StandardStreamEvent(type="tool_call", data={"tool": "Read"})
        assert isinstance(evt, dict)
        assert "type" in evt
        assert evt.get("type") == "tool_call"


class TestExecuteRoleSessionCommandV1StreamOptions:
    """Tests for ExecuteRoleSessionCommandV1 with stream_options field."""

    def test_stream_options_field_exists(self) -> None:
        opts = StreamTurnOptions(stream=True, history_limit=20)
        cmd = ExecuteRoleSessionCommandV1(
            role="director",
            session_id="s1",
            workspace=".",
            user_message="hello",
            stream_options=opts,
        )
        assert cmd.stream_options is opts
        assert cmd.stream_options.stream is True
        assert cmd.stream_options.history_limit == 20

    def test_stream_options_defaults_to_none(self) -> None:
        cmd = ExecuteRoleSessionCommandV1(
            role="director",
            session_id="s1",
            workspace=".",
            user_message="hello",
        )
        assert cmd.stream_options is None

    def test_stream_options_type_validation(self) -> None:
        with pytest.raises(TypeError, match="must be a StreamTurnOptions"):
            ExecuteRoleSessionCommandV1(
                role="director",
                session_id="s1",
                workspace=".",
                user_message="hello",
                stream_options="not_a_stream_turn_options",  # type: ignore[arg-type]
            )


class TestIRoleRuntimeStreamChatTurn:
    """Tests that IRoleRuntime interface includes stream_chat_turn with options."""

    def test_interface_has_stream_chat_turn(self) -> None:
        # Verify the method exists on the Protocol
        assert hasattr(IRoleRuntime, "stream_chat_turn")

    def test_role_runtime_service_has_stream_chat_turn(self) -> None:
        # Verify RoleRuntimeService (concrete) has the method
        from polaris.cells.roles.runtime.public.service import RoleRuntimeService

        service = RoleRuntimeService()
        assert callable(getattr(service, "stream_chat_turn", None))


# ─────────────────────────────────────────────────────────────────────────────
# Task #3: console_protocol.py tests
# ─────────────────────────────────────────────────────────────────────────────


class TestStreamEventType:
    """Tests for StreamEventType enum."""

    def test_all_event_types_present(self) -> None:
        expected = {
            "thinking_chunk",
            "content_chunk",
            "tool_call",
            "tool_result",
            "fingerprint",
            "complete",
            "error",
            "done",
        }
        actual = {e.value for e in StreamEventType}
        assert actual == expected

    def test_from_string_valid(self) -> None:
        assert StreamEventType.from_string("content_chunk") == StreamEventType.CONTENT_CHUNK
        assert StreamEventType.from_string("tool_call") == StreamEventType.TOOL_CALL

    def test_from_string_unknown_returns_error(self) -> None:
        result = StreamEventType.from_string("unknown_event_type")
        assert result == StreamEventType.ERROR

    def test_is_str_enum(self) -> None:
        assert isinstance(StreamEventType.CONTENT_CHUNK, str)
        assert StreamEventType.CONTENT_CHUNK == "content_chunk"


class TestProtocolStandardStreamEvent:
    """Tests for StandardStreamEvent in console_protocol.py."""

    def test_construction(self) -> None:
        evt = ProtocolStreamEvent(
            type=StreamEventType.CONTENT_CHUNK,
            data={"content": "hello world"},
            metadata={"token_count": 2},
        )
        assert evt.type == StreamEventType.CONTENT_CHUNK
        assert evt.data == {"content": "hello world"}
        assert evt.metadata == {"token_count": 2}

    def test_to_dict_roundtrip(self) -> None:
        evt = ProtocolStreamEvent(
            type=StreamEventType.TOOL_CALL,
            data={"tool": "Read", "args": {"file_path": "foo.txt"}},
            metadata={"seq": 1},
        )
        d = evt.to_dict()
        assert d["type"] == "tool_call"
        assert d["data"] == {"tool": "Read", "args": {"file_path": "foo.txt"}}
        assert d["metadata"] == {"seq": 1}

    def test_from_dict_roundtrip(self) -> None:
        original = {
            "type": "complete",
            "data": {"content": "done", "thinking": "finished"},
            "metadata": {},
        }
        evt = ProtocolStreamEvent.from_dict(original)
        assert evt.type == StreamEventType.COMPLETE
        assert evt.data == {"content": "done", "thinking": "finished"}


class TestToStandardEvent:
    """Tests for to_standard_event() converter."""

    def test_passthrough_standard_stream_event(self) -> None:
        original = ProtocolStreamEvent(
            type=StreamEventType.CONTENT_CHUNK,
            data={"content": "already standardised"},
        )
        result = to_standard_event(original)
        assert result is original

    def test_kernel_dict_form(self) -> None:
        kernel_event = {
            "type": "content_chunk",
            "content": "hello",
        }
        result = to_standard_event(kernel_event)
        assert result is not None
        assert result.type == StreamEventType.CONTENT_CHUNK
        assert result.data == kernel_event

    def test_serialised_form(self) -> None:
        serialised = {
            "type": "tool_call",
            "data": {"tool": "Write", "args": {}},
            "metadata": {"seq": 0},
        }
        result = to_standard_event(serialised)
        assert result is not None
        assert result.type == StreamEventType.TOOL_CALL
        assert result.data == {"tool": "Write", "args": {}}
        assert result.metadata == {"seq": 0}

    def test_missing_type_returns_none(self) -> None:
        result = to_standard_event({"content": "no type field"})
        assert result is None

    def test_unknown_type_returns_error_event(self) -> None:
        result = to_standard_event({"type": "completely_unknown"})
        assert result is not None
        assert result.type == StreamEventType.ERROR

    def test_none_returns_none(self) -> None:
        assert to_standard_event(None) is None

    def test_non_dict_returns_none(self) -> None:
        assert to_standard_event("string event") is None
        assert to_standard_event(42) is None


class TestFromKernelEvent:
    """Tests for from_kernel_event() converter."""

    def test_valid_kernel_event(self) -> None:
        kernel_event = {
            "type": "thinking_chunk",
            "content": "reasoning...",
        }
        result = from_kernel_event(kernel_event)
        assert result is not None
        assert result.type == StreamEventType.THINKING_CHUNK
        assert result.metadata == {"source": "RoleExecutionKernel"}

    def test_missing_type_returns_none(self) -> None:
        result = from_kernel_event({"content": "no type"})
        assert result is None

    def test_non_dict_returns_none(self) -> None:
        assert from_kernel_event({"not": "a dict"}) is None

    def test_complete_event(self) -> None:
        result = from_kernel_event(
            {
                "type": "complete",
                "result": {"content": "final answer", "thinking": None},
            }
        )
        assert result is not None
        assert result.type == StreamEventType.COMPLETE


# ─────────────────────────────────────────────────────────────────────────────
# Task #4: RoleConsoleHost multi-role generalisation tests
# ─────────────────────────────────────────────────────────────────────────────


class TestRoleConsoleHostAllowedRoles:
    """Tests for _ALLOWED_ROLES set."""

    def test_default_allowed_roles(self) -> None:
        assert frozenset({"director", "pm", "architect", "chief_engineer", "qa"}) == RoleConsoleHost._ALLOWED_ROLES

    def test_director_console_host_inherits_allowed_roles(self) -> None:
        assert DirectorConsoleHost._ALLOWED_ROLES == RoleConsoleHost._ALLOWED_ROLES


class TestRoleConsoleHostConstruction:
    """Tests for RoleConsoleHost construction with different roles."""

    @patch("polaris.delivery.cli.director.console_host._ensure_minimal_runtime_bindings")
    @patch("polaris.delivery.cli.director.console_host.RoleRuntimeService")
    def test_constructs_with_director_role(self, mock_runtime_cls: MagicMock, mock_bindings: MagicMock) -> None:
        mock_runtime_cls.return_value = MagicMock()
        host = RoleConsoleHost(".", role="director")
        assert host.role == "director"
        assert host.config.role == "director"

    @patch("polaris.delivery.cli.director.console_host._ensure_minimal_runtime_bindings")
    @patch("polaris.delivery.cli.director.console_host.RoleRuntimeService")
    def test_constructs_with_pm_role(self, mock_runtime_cls: MagicMock, mock_bindings: MagicMock) -> None:
        mock_runtime_cls.return_value = MagicMock()
        host = RoleConsoleHost(".", role="pm")
        assert host.role == "pm"

    @patch("polaris.delivery.cli.director.console_host._ensure_minimal_runtime_bindings")
    @patch("polaris.delivery.cli.director.console_host.RoleRuntimeService")
    def test_constructs_with_qa_role(self, mock_runtime_cls: MagicMock, mock_bindings: MagicMock) -> None:
        mock_runtime_cls.return_value = MagicMock()
        host = RoleConsoleHost(".", role="qa")
        assert host.role == "qa"

    @patch("polaris.delivery.cli.director.console_host._ensure_minimal_runtime_bindings")
    @patch("polaris.delivery.cli.director.console_host.RoleRuntimeService")
    def test_constructs_with_architect_role(self, mock_runtime_cls: MagicMock, mock_bindings: MagicMock) -> None:
        mock_runtime_cls.return_value = MagicMock()
        host = RoleConsoleHost(".", role="architect")
        assert host.role == "architect"

    @patch("polaris.delivery.cli.director.console_host._ensure_minimal_runtime_bindings")
    @patch("polaris.delivery.cli.director.console_host.RoleRuntimeService")
    def test_constructs_with_chief_engineer_role(self, mock_runtime_cls: MagicMock, mock_bindings: MagicMock) -> None:
        mock_runtime_cls.return_value = MagicMock()
        host = RoleConsoleHost(".", role="chief_engineer")
        assert host.role == "chief_engineer"

    @patch("polaris.delivery.cli.director.console_host._ensure_minimal_runtime_bindings")
    @patch("polaris.delivery.cli.director.console_host.RoleRuntimeService")
    def test_workspace_required(self, mock_runtime_cls: MagicMock, mock_bindings: MagicMock) -> None:
        mock_runtime_cls.return_value = MagicMock()
        with pytest.raises(ValueError, match="workspace is required"):
            RoleConsoleHost("")  # type: ignore[arg-type]


class TestAddRoleHostKind:
    """Tests for add_role_host_kind() dynamic role registration."""

    @patch("polaris.delivery.cli.director.console_host._ensure_minimal_runtime_bindings")
    @patch("polaris.delivery.cli.director.console_host.RoleRuntimeService")
    def test_registers_custom_role(self, mock_runtime_cls: MagicMock, mock_bindings: MagicMock) -> None:
        mock_runtime_cls.return_value = MagicMock()
        host = RoleConsoleHost(".", role="director")
        original = host._ALLOWED_ROLES

        host.add_role_host_kind("scout")

        assert "scout" in host._ALLOWED_ROLES
        assert original | {"scout"} == host._ALLOWED_ROLES

    @patch("polaris.delivery.cli.director.console_host._ensure_minimal_runtime_bindings")
    @patch("polaris.delivery.cli.director.console_host.RoleRuntimeService")
    def test_add_role_host_kind_normalises_to_lowercase(
        self, mock_runtime_cls: MagicMock, mock_bindings: MagicMock
    ) -> None:
        mock_runtime_cls.return_value = MagicMock()
        host = RoleConsoleHost(".", role="director")
        host.add_role_host_kind("SCOUT")
        assert "scout" in host._ALLOWED_ROLES

    @patch("polaris.delivery.cli.director.console_host._ensure_minimal_runtime_bindings")
    @patch("polaris.delivery.cli.director.console_host.RoleRuntimeService")
    def test_add_role_host_kind_empty_raises(self, mock_runtime_cls: MagicMock, mock_bindings: MagicMock) -> None:
        mock_runtime_cls.return_value = MagicMock()
        host = RoleConsoleHost(".", role="director")
        with pytest.raises(ValueError, match="non-empty string"):
            host.add_role_host_kind("   ")


class TestDirectorConsoleHostAlias:
    """Tests for DirectorConsoleHost backward-compatible alias."""

    @patch("polaris.delivery.cli.director.console_host._ensure_minimal_runtime_bindings")
    @patch("polaris.delivery.cli.director.console_host.RoleRuntimeService")
    def test_is_subclass_of_role_console_host(self, mock_runtime_cls: MagicMock, mock_bindings: MagicMock) -> None:
        mock_runtime_cls.return_value = MagicMock()
        assert issubclass(DirectorConsoleHost, RoleConsoleHost)

    @patch("polaris.delivery.cli.director.console_host._ensure_minimal_runtime_bindings")
    @patch("polaris.delivery.cli.director.console_host.RoleRuntimeService")
    def test_default_role_is_director(self, mock_runtime_cls: MagicMock, mock_bindings: MagicMock) -> None:
        mock_runtime_cls.return_value = MagicMock()
        host = DirectorConsoleHost(".")
        assert host.role == "director"

    @patch("polaris.delivery.cli.director.console_host._ensure_minimal_runtime_bindings")
    @patch("polaris.delivery.cli.director.console_host.RoleRuntimeService")
    def test_accepts_custom_config(self, mock_runtime_cls: MagicMock, mock_bindings: MagicMock) -> None:
        mock_runtime_cls.return_value = MagicMock()
        config = DirectorConsoleHostConfig(workspace=".", role="director", default_session_title="Custom Title")
        host = DirectorConsoleHost(".", config=config)
        assert host.config.default_session_title == "Custom Title"


class TestStreamTurnRoleRouting:
    """Tests for stream_turn() role routing precedence."""

    @pytest.fixture
    def mock_host(self) -> RoleConsoleHost:
        """A RoleConsoleHost with all dependencies mocked."""
        with (
            patch("polaris.delivery.cli.director.console_host._ensure_minimal_runtime_bindings"),
            patch("polaris.delivery.cli.director.console_host.RoleRuntimeService") as mock_runtime_cls,
        ):
            mock_runtime_cls.return_value = MagicMock()
            host = RoleConsoleHost(".", role="director")
            return host

    @pytest.fixture
    def mock_runtime_service(self, mock_host: RoleConsoleHost) -> IRoleRuntime:
        """Return the mocked runtime service on the host."""
        return mock_host._runtime_service

    def test_role_precedence_1_explicit_param(self, mock_host: RoleConsoleHost) -> None:
        """Explicit role= parameter takes highest precedence."""
        with (
            patch.object(
                mock_host,
                "create_session",
                return_value={"id": "sess-1", "context_config": {"role": "pm"}, "messages": []},
            ),
            patch.object(
                mock_host,
                "_project_session_continuity",
                return_value=SessionContinuityProjection(
                    recent_messages=(),
                    prompt_context={},
                    persisted_context_config={},
                    changed=False,
                ),
            ),
            patch.object(mock_host, "_persist_message"),
            patch.object(mock_host, "_build_runtime_history", return_value=()),
        ):
            recorded_role: str | None = None

            async def fake_stream(command: ExecuteRoleSessionCommandV1) -> AsyncIterator[StandardStreamEvent]:
                nonlocal recorded_role
                recorded_role = command.role
                yield StandardStreamEvent(type="complete", data={"content": "done", "thinking": None})

            cast("IRoleRuntime", mock_host._runtime_service).stream_chat_turn = fake_stream  # type: ignore[method-assign,assignment]

            async def run() -> None:
                events = []
                async for evt in mock_host.stream_turn(None, "hello", role="architect"):
                    events.append(evt)

            asyncio.run(run())
            assert recorded_role == "architect"

    def test_role_precedence_2_context_dict(self, mock_host: RoleConsoleHost) -> None:
        """Context dict role is used when no explicit role= parameter."""
        with (
            patch.object(
                mock_host,
                "create_session",
                return_value={"id": "sess-2", "context_config": None, "messages": []},
            ),
            patch.object(
                mock_host,
                "_project_session_continuity",
                return_value=SessionContinuityProjection(
                    recent_messages=(),
                    prompt_context={},
                    persisted_context_config={},
                    changed=False,
                ),
            ),
            patch.object(mock_host, "_persist_message"),
            patch.object(mock_host, "_build_runtime_history", return_value=()),
        ):
            recorded_role: str | None = None

            async def fake_stream(command: ExecuteRoleSessionCommandV1) -> AsyncIterator[StandardStreamEvent]:
                nonlocal recorded_role
                recorded_role = command.role
                yield StandardStreamEvent(type="complete", data={"content": "done", "thinking": None})

            cast("IRoleRuntime", mock_host._runtime_service).stream_chat_turn = fake_stream  # type: ignore[method-assign,assignment]

            async def run() -> None:
                async for _ in mock_host.stream_turn(None, "hello", context={"role": "pm"}):
                    pass

            asyncio.run(run())
            assert recorded_role == "pm"

    def test_stream_turn_without_session_id_creates_fresh_session(self, mock_host: RoleConsoleHost) -> None:
        create_payload = {
            "id": "sess-new",
            "context_config": {"role": "director", "host_kind": "cli"},
            "messages": [],
        }
        with (
            patch.object(mock_host, "create_session", return_value=create_payload) as create_session,
            patch.object(
                mock_host,
                "_load_session_payload",
            ) as load_session,
            patch.object(
                mock_host,
                "_project_session_continuity",
                return_value=SessionContinuityProjection(
                    recent_messages=(),
                    prompt_context={},
                    persisted_context_config={},
                    changed=False,
                ),
            ),
            patch.object(mock_host, "_persist_message"),
            patch.object(mock_host, "_build_runtime_history", return_value=()),
        ):

            async def fake_stream(command: ExecuteRoleSessionCommandV1) -> AsyncIterator[StandardStreamEvent]:
                assert command.session_id == "sess-new"
                yield StandardStreamEvent(type="complete", data={"content": "done", "thinking": None})

            cast("IRoleRuntime", mock_host._runtime_service).stream_chat_turn = fake_stream  # type: ignore[method-assign,assignment]

            async def run() -> None:
                async for _ in mock_host.stream_turn(None, "hello"):
                    pass

            asyncio.run(run())
            create_session.assert_called_once()
            load_session.assert_not_called()

    def test_project_session_continuity_filters_reserved_context_and_omits_stale_meta(
        self, mock_host: RoleConsoleHost
    ) -> None:
        session_payload = {
            "id": "sess-memory",
            "title": "Role CLI",
            "messages": [
                {"sequence": 0, "role": "user", "content": "你好"},
                {"sequence": 1, "role": "assistant", "content": "你好，我是当前会话助手。"},
                {"sequence": 2, "role": "user", "content": "你能换个名字吗，叫二郎"},
                {
                    "sequence": 3,
                    "role": "user",
                    "content": "session/history/context 一直重复，请重构 continuity summary 和 compaction 策略",
                },
                {"sequence": 4, "role": "assistant", "content": "我会检查 session、history、context compaction 链路。"},
                {"sequence": 5, "role": "user", "content": "继续"},
                {"sequence": 6, "role": "assistant", "content": "继续处理中。"},
                {"sequence": 7, "role": "user", "content": "先总结问题"},
                {"sequence": 8, "role": "assistant", "content": "问题集中在 history 注入和旧 session 复用。"},
                {"sequence": 9, "role": "user", "content": "现在开始改"},
            ],
        }
        session_context = {
            "role": "director",
            "host_kind": "cli",
            "governance_scope": "role:director",
            "workspace": ".",
            "history": [{"role": "user", "content": "legacy"}],
            "session_id": "sess-memory",
        }
        projection = asyncio.run(
            mock_host._project_session_continuity(
                session_id="sess-memory",
                role="director",
                session_payload=session_payload,
                session_context_config=session_context,
                incoming_context={"host_kind": "cli", "history": [{"role": "assistant", "content": "dup"}]},
                history_limit=4,
            )
        )

        assert len(projection.recent_messages) == 4
        assert "history" not in projection.prompt_context
        assert "session_id" not in projection.prompt_context
        assert "host_kind" not in projection.prompt_context
        continuity = projection.prompt_context.get("session_continuity")
        assert isinstance(continuity, dict)
        summary = str(continuity.get("summary") or "")
        assert "session/history/context" in summary
        assert "二郎" not in summary
        assert continuity.get("stable_facts")
        assert continuity.get("open_loops")
        state_first = projection.prompt_context.get("state_first_context_os")
        assert isinstance(state_first, dict)
        assert isinstance(state_first.get("run_card"), dict)
        assert isinstance(state_first.get("context_slice_plan"), dict)
        assert projection.changed is True

    def test_trim_current_user_from_recent_messages_removes_tail_match(self, mock_host: RoleConsoleHost) -> None:
        recent_messages = [
            {"role": "assistant", "content": "previous"},
            {"role": "user", "content": "hello"},
        ]
        trimmed = mock_host._trim_current_user_from_recent_messages(
            recent_messages,
            current_user_message="hello",
        )
        assert trimmed == [{"role": "assistant", "content": "previous"}]

    def test_trim_current_user_from_recent_messages_normalizes_bom_and_newline(
        self,
        mock_host: RoleConsoleHost,
    ) -> None:
        recent_messages = [
            {"role": "assistant", "content": "previous"},
            {"role": "user", "content": "\ufeffhello\r\n"},
        ]
        trimmed = mock_host._trim_current_user_from_recent_messages(
            recent_messages,
            current_user_message="hello",
        )
        assert trimmed == [{"role": "assistant", "content": "previous"}]

    def test_trim_current_user_from_recent_messages_respects_drop_flag(self, mock_host: RoleConsoleHost) -> None:
        recent_messages = [
            {"role": "assistant", "content": "previous"},
            {"role": "user", "content": "hello"},
        ]
        trimmed = mock_host._trim_current_user_from_recent_messages(
            recent_messages,
            current_user_message="hello",
            drop_current_user_tail=False,
        )
        assert trimmed == recent_messages

    def test_append_current_user_message_skips_duplicate_tail(self, mock_host: RoleConsoleHost) -> None:
        messages = [{"sequence": 3, "role": "user", "content": "\ufeffhello\r\n"}]
        projected, injected = mock_host._append_current_user_message(
            messages,
            current_user_message="hello",
        )
        assert projected == messages
        assert injected is False

    def test_append_current_user_message_collapses_trailing_duplicate_user_turns(
        self,
        mock_host: RoleConsoleHost,
    ) -> None:
        messages = [
            {"sequence": 1, "role": "assistant", "content": "ack"},
            {"sequence": 2, "role": "user", "content": "hello"},
            {"sequence": 3, "role": "user", "content": "hello"},
        ]
        projected, injected = mock_host._append_current_user_message(
            messages,
            current_user_message="hello",
        )
        assert injected is False
        assert projected == [
            {"sequence": 1, "role": "assistant", "content": "ack"},
            {"sequence": 2, "role": "user", "content": "hello"},
        ]

    def test_stream_turn_projects_current_user_without_duplicate_history(self, mock_host: RoleConsoleHost) -> None:
        create_payload = {
            "id": "sess-projection",
            "context_config": {"role": "director", "host_kind": "cli"},
            "messages": [{"sequence": 0, "role": "assistant", "content": "previous"}],
        }
        recorded_history: tuple[tuple[str, str], ...] | None = None

        with (
            patch.object(mock_host, "create_session", return_value=create_payload),
            patch.object(mock_host, "_write_session_event"),
            patch.object(mock_host, "_persist_message") as mock_persist,
        ):

            async def _project(
                *,
                session_id: str,
                role: str,
                session_payload: dict[str, object],
                session_context_config: dict[str, object],
                incoming_context: dict[str, object],
                history_limit: int | None,
            ) -> SessionContinuityProjection:
                assert session_id == "sess-projection"
                assert role == "director"
                assert mock_persist.call_count == 1
                first_call = mock_persist.call_args_list[0]
                assert first_call.kwargs["role"] == "user"
                assert first_call.kwargs["content"] == "hello"
                projected_messages = session_payload.get("messages")
                assert isinstance(projected_messages, list)
                tail = projected_messages[-1]
                assert isinstance(tail, dict)
                assert tail.get("role") == "user"
                assert tail.get("content") == "hello"
                return SessionContinuityProjection(
                    recent_messages=(
                        {"role": "assistant", "content": "previous"},
                        {"role": "user", "content": "hello"},
                    ),
                    prompt_context={},
                    persisted_context_config={},
                    changed=False,
                )

            async def fake_stream(command: ExecuteRoleSessionCommandV1) -> AsyncIterator[StandardStreamEvent]:
                nonlocal recorded_history
                recorded_history = command.history
                yield StandardStreamEvent(type="complete", data={"content": "done", "thinking": None})

            cast("IRoleRuntime", mock_host._runtime_service).stream_chat_turn = fake_stream  # type: ignore[method-assign,assignment]
            with patch.object(mock_host, "_project_session_continuity", side_effect=_project):

                async def run() -> None:
                    async for _ in mock_host.stream_turn(None, "hello"):
                        pass

                asyncio.run(run())

        assert recorded_history == (("assistant", "previous"),)

    def test_stream_turn_trims_duplicate_tail_when_current_user_not_injected(self, mock_host: RoleConsoleHost) -> None:
        create_payload = {
            "id": "sess-projection-existing-user-tail",
            "context_config": {"role": "director", "host_kind": "cli"},
            "messages": [{"sequence": 0, "role": "user", "content": "hello"}],
        }
        recorded_history: tuple[tuple[str, str], ...] | None = None

        with (
            patch.object(mock_host, "create_session", return_value=create_payload),
            patch.object(mock_host, "_write_session_event"),
            patch.object(mock_host, "_persist_message"),
        ):

            async def _project(
                *,
                session_id: str,
                role: str,
                session_payload: dict[str, object],
                session_context_config: dict[str, object],
                incoming_context: dict[str, object],
                history_limit: int | None,
            ) -> SessionContinuityProjection:
                projected_messages = session_payload.get("messages")
                assert isinstance(projected_messages, list)
                assert len(projected_messages) == 1
                return SessionContinuityProjection(
                    recent_messages=({"role": "user", "content": "hello"},),
                    prompt_context={},
                    persisted_context_config={},
                    changed=False,
                )

            async def fake_stream(command: ExecuteRoleSessionCommandV1) -> AsyncIterator[StandardStreamEvent]:
                nonlocal recorded_history
                recorded_history = command.history
                yield StandardStreamEvent(type="complete", data={"content": "done", "thinking": None})

            cast("IRoleRuntime", mock_host._runtime_service).stream_chat_turn = fake_stream  # type: ignore[method-assign,assignment]
            with patch.object(mock_host, "_project_session_continuity", side_effect=_project):

                async def run() -> None:
                    async for _ in mock_host.stream_turn(None, "hello"):
                        pass

                asyncio.run(run())

        assert recorded_history == ()

    def test_stream_turn_debug_event_includes_context_os_summary(self, mock_host: RoleConsoleHost) -> None:
        create_payload = {
            "id": "sess-debug",
            "context_config": {"role": "director", "host_kind": "cli"},
            "messages": [],
        }
        projection = SessionContinuityProjection(
            recent_messages=(),
            prompt_context={
                "session_continuity": {
                    "summary": "Carry working state across resumed turns.",
                    "stable_facts": ["context.engine remains facade"],
                    "open_loops": ["wire cli observability"],
                },
                "state_first_context_os": {
                    "adapter_id": "code",
                    "run_card": {
                        "current_goal": "Fix Context OS observability",
                        "hard_constraints": ["Do not replace context.engine"],
                        "open_loops": ["wire cli observability"],
                        "active_entities": ["polaris/delivery/cli/director/console_host.py"],
                        "active_artifacts": ["art_001"],
                        "next_action_hint": "Emit debug payload with context_os summary",
                    },
                    "context_slice_plan": {
                        "plan_id": "plan-debug",
                        "budget_tokens": 2048,
                        "roots": ["latest_user_turn"],
                        "included": [{"type": "state", "ref": "run_card", "reason": "pin"}],
                        "excluded": [],
                        "pressure_level": "soft",
                    },
                    "episode_cards": [{"episode_id": "ep_1"}],
                },
            },
            persisted_context_config={},
            changed=False,
        )
        captured_payloads: list[dict[str, object]] = []

        with (
            patch.object(mock_host, "create_session", return_value=create_payload),
            patch.object(
                mock_host,
                "_project_session_continuity",
                return_value=projection,
            ),
            patch.object(mock_host, "_persist_message"),
            patch.object(mock_host, "_build_runtime_history", return_value=()),
            patch("polaris.delivery.cli.director.console_host.emit_debug_event") as mock_emit_debug_event,
        ):

            async def fake_stream(
                _command: ExecuteRoleSessionCommandV1,
            ) -> AsyncIterator[StandardStreamEvent]:
                yield StandardStreamEvent(
                    type="complete",
                    data={"content": "done", "thinking": None},
                )

            cast("IRoleRuntime", mock_host._runtime_service).stream_chat_turn = fake_stream  # type: ignore[method-assign,assignment]

            def _capture(*, payload: dict[str, object], **_kwargs: object) -> None:
                captured_payloads.append(payload)

            mock_emit_debug_event.side_effect = _capture

            async def run() -> None:
                async for _ in mock_host.stream_turn(None, "hello", debug=True):
                    pass

            asyncio.run(run())

        continuity_payload = next(item for item in captured_payloads if "recent_message_count" in item)
        context_os = continuity_payload.get("context_os")
        assert isinstance(context_os, dict)
        assert context_os["adapter_id"] == "code"
        assert context_os["current_goal"] == "Fix Context OS observability"
        assert context_os["pressure_level"] == "soft"
        assert context_os["hard_constraint_count"] == 1


# ─────────────────────────────────────────────────────────────────────────────
# Smoke test: console_protocol module exports
# ─────────────────────────────────────────────────────────────────────────────


class TestModuleExports:
    """Sanity checks that all public exports are present."""

    def test_contracts_exports(self) -> None:
        from polaris.cells.roles.runtime.public.contracts import __all__ as exports

        assert "StreamTurnOptions" in exports
        assert "StandardStreamEvent" in exports

    def test_console_protocol_exports(self) -> None:
        from polaris.delivery.cli.director.console_protocol import __all__ as exports

        assert "StreamEventType" in exports
        assert "StandardStreamEvent" in exports
        assert "to_standard_event" in exports
        assert "from_kernel_event" in exports

    def test_console_host_exports(self) -> None:
        from polaris.delivery.cli.director.console_host import __all__ as exports

        assert "RoleConsoleHost" in exports
        assert "DirectorConsoleHost" in exports
        assert "RoleConsoleHostConfig" in exports
        assert "DirectorConsoleHostConfig" in exports
