"""Tests for polaris.application.runtime_admin."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from polaris.application.runtime_admin import (
    IOrchestratorSession,
    IRoleOrchestratorFactory,
    OrchestratorHandle,
    RuntimeAdminError,
    RuntimeAdminService,
)
from polaris.cells.roles.runtime.public.contracts import (
    ExecuteRoleSessionCommandV1,
    RoleRuntimeError,
    StreamTurnOptions,
)


class FakeRuntimeError(RoleRuntimeError):
    """Concrete fake for RoleRuntimeError (which may be abstract)."""

    def __init__(self, message: str, code: str = "fake_error") -> None:
        super().__init__(message)
        self.code = code


class FakeOrchestrator:
    """Fake orchestrator that yields events."""

    def __init__(self, events: list[dict[str, Any]] | None = None) -> None:
        self.events = events or []

    async def execute_stream(
        self,
        user_message: str,
        *,
        context: dict[str, Any] | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        for event in self.events:
            yield event


class FakeFailingOrchestrator:
    """Fake orchestrator that raises on stream."""

    async def execute_stream(
        self,
        user_message: str,
        *,
        context: dict[str, Any] | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        raise FakeRuntimeError("stream failed", code="stream_fail")
        yield  # pragma: no cover  # make it an async generator


class TestRuntimeAdminError:
    def test_default_code(self) -> None:
        err = RuntimeAdminError("oops")
        assert err.code == "runtime_admin_error"
        assert str(err) == "oops"

    def test_custom_code_and_cause(self) -> None:
        cause = ValueError("inner")
        err = RuntimeAdminError("oops", code="custom", cause=cause)
        assert err.code == "custom"
        assert err.cause is cause


class TestOrchestratorHandle:
    async def test_stream_events_yields(self) -> None:
        fake = FakeOrchestrator([{"type": "chunk"}, {"type": "done"}])
        handle = OrchestratorHandle(fake)
        events = []
        async for event in handle.stream_events("hello"):
            events.append(event)
        assert len(events) == 2

    async def test_stream_events_wraps_error(self) -> None:
        fake = FakeFailingOrchestrator()
        handle = OrchestratorHandle(fake)
        with pytest.raises(RuntimeAdminError) as exc_info:
            async for _event in handle.stream_events("hello"):
                pass  # pragma: no cover
        assert exc_info.value.code == "stream_fail"


class TestRuntimeAdminService:
    # -- build_session_command -----------------------------------------------

    def test_build_session_command_defaults(self) -> None:
        cmd = RuntimeAdminService.build_session_command(
            role="pm",
            session_id="s1",
            workspace="/tmp",
            user_message="hi",
        )
        assert isinstance(cmd, ExecuteRoleSessionCommandV1)
        assert cmd.role == "pm"
        assert cmd.session_id == "s1"
        assert cmd.workspace == "/tmp"
        assert cmd.user_message == "hi"
        assert cmd.history == ()
        assert cmd.context == {}
        assert cmd.metadata == {}
        assert cmd.stream is True
        assert cmd.host_kind is None
        assert cmd.stream_options is None

    def test_build_session_command_with_options(self) -> None:
        opts = StreamTurnOptions(max_tokens=100)
        cmd = RuntimeAdminService.build_session_command(
            role="architect",
            session_id="s2",
            workspace="/ws",
            user_message="design",
            history=(("user", "q"), ("assistant", "a")),
            context={"key": "val"},
            metadata={"tag": "v1"},
            stream=False,
            host_kind="web",
            stream_options=opts,
        )
        assert cmd.role == "architect"
        assert cmd.history == (("user", "q"), ("assistant", "a"))
        assert cmd.context == {"key": "val"}
        assert cmd.metadata == {"tag": "v1"}
        assert cmd.stream is False
        assert cmd.host_kind == "web"
        assert cmd.stream_options is opts

    # -- _resolve_runtime / runtime property ---------------------------------

    def test_runtime_property_uses_injected(self) -> None:
        fake = MagicMock()
        svc = RuntimeAdminService(runtime=fake)
        assert svc.runtime is fake

    def test_resolve_runtime_lazy_import_failure(self) -> None:
        svc = RuntimeAdminService()
        with patch(
            "polaris.application.runtime_admin.RoleRuntimeService",
            side_effect=ImportError("no module"),
        ):
            with pytest.raises(RuntimeAdminError) as exc_info:
                svc._resolve_runtime()
        assert exc_info.value.code == "runtime_resolution_error"

    # -- stream_chat_turn ----------------------------------------------------

    async def test_stream_chat_turn_delegates(self) -> None:
        fake = MagicMock()
        fake.stream_chat_turn = AsyncMock(return_value=async_gen([{"e": 1}]))
        svc = RuntimeAdminService(runtime=fake)
        cmd = RuntimeAdminService.build_session_command(
            role="pm", session_id="s1", workspace="/tmp", user_message="hi"
        )
        events = []
        async for event in svc.stream_chat_turn(cmd):
            events.append(event)
        assert events == [{"e": 1}]
        fake.stream_chat_turn.assert_called_once_with(cmd)

    # -- create_transaction_controller ---------------------------------------

    def test_create_transaction_controller_success(self) -> None:
        fake = MagicMock()
        fake.create_transaction_controller.return_value = {"ctrl": 1}
        svc = RuntimeAdminService(runtime=fake)
        cmd = RuntimeAdminService.build_session_command(
            role="pm", session_id="s1", workspace="/tmp", user_message="hi"
        )
        result = svc.create_transaction_controller(cmd)
        assert result == {"ctrl": 1}

    def test_create_transaction_controller_unsupported(self) -> None:
        fake = MagicMock()
        fake.create_transaction_controller = None
        svc = RuntimeAdminService(runtime=fake)
        cmd = RuntimeAdminService.build_session_command(
            role="pm", session_id="s1", workspace="/tmp", user_message="hi"
        )
        with pytest.raises(RuntimeAdminError) as exc_info:
            svc.create_transaction_controller(cmd)
        assert exc_info.value.code == "unsupported_runtime_capability"

    def test_create_transaction_controller_creation_error(self) -> None:
        fake = MagicMock()
        fake.create_transaction_controller.side_effect = ValueError("boom")
        svc = RuntimeAdminService(runtime=fake)
        cmd = RuntimeAdminService.build_session_command(
            role="pm", session_id="s1", workspace="/tmp", user_message="hi"
        )
        with pytest.raises(RuntimeAdminError) as exc_info:
            svc.create_transaction_controller(cmd)
        assert exc_info.value.code == "transaction_controller_creation_error"

    # -- create_orchestrator_handle ------------------------------------------

    def test_create_orchestrator_handle_success(self) -> None:
        fake_runtime = MagicMock()
        fake_runtime.create_transaction_controller.return_value = {"ctrl": 1}
        svc = RuntimeAdminService(runtime=fake_runtime)
        cmd = RuntimeAdminService.build_session_command(
            role="pm", session_id="s1", workspace="/tmp", user_message="hi"
        )
        with patch(
            "polaris.application.runtime_admin.RoleSessionOrchestrator",
            return_value=FakeOrchestrator(),
        ) as mock_orch:
            handle = svc.create_orchestrator_handle(
                session_id="s1",
                workspace="/tmp",
                role="pm",
                command=cmd,
                max_auto_turns=5,
            )
            mock_orch.assert_called_once_with(
                session_id="s1",
                kernel={"ctrl": 1},
                workspace="/tmp",
                role="pm",
                max_auto_turns=5,
                shadow_engine=None,
            )
        assert isinstance(handle, OrchestratorHandle)

    def test_create_orchestrator_handle_instantiation_error(self) -> None:
        fake_runtime = MagicMock()
        fake_runtime.create_transaction_controller.return_value = {"ctrl": 1}
        svc = RuntimeAdminService(runtime=fake_runtime)
        cmd = RuntimeAdminService.build_session_command(
            role="pm", session_id="s1", workspace="/tmp", user_message="hi"
        )
        with patch(
            "polaris.application.runtime_admin.RoleSessionOrchestrator",
            side_effect=ImportError("no module"),
        ):
            with pytest.raises(RuntimeAdminError) as exc_info:
                svc.create_orchestrator_handle(
                    session_id="s1",
                    workspace="/tmp",
                    role="pm",
                    command=cmd,
                )
        assert exc_info.value.code == "orchestrator_instantiation_error"


# -- helpers -----------------------------------------------------------------

async def async_gen(items: list[Any]) -> AsyncIterator[Any]:
    for item in items:
        yield item
