"""Tests for RoleConsoleHost Phase 4 orchestrator integration.

Covers:
- Feature flag routing (_use_orchestrator)
- TurnEvent -> dict normalization (_normalize_orchestrator_event)
- Session-isolated event logging (_write_session_event)
- End-to-end orchestrator path in stream_turn
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, cast
from unittest.mock import MagicMock, patch

import pytest
from polaris.cells.roles.kernel.public.turn_events import (
    CompletionEvent,
    ContentChunkEvent,
    ErrorEvent,
    RuntimeCompletedEvent,
    RuntimeStartedEvent,
    SessionCompletedEvent,
    SessionStartedEvent,
    SessionWaitingHumanEvent,
    ToolBatchEvent,
    TurnPhaseEvent,
)
from polaris.delivery.cli.director.console_host import RoleConsoleHost


class TestUseOrchestrator:
    """Tests for _use_orchestrator feature flag logic."""

    def test_env_var_true_enables(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("POLARIS_ENABLE_SESSION_ORCHESTRATOR", "1")
        assert RoleConsoleHost._use_orchestrator({}) is True

    def test_env_var_false_disables(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("POLARIS_ENABLE_SESSION_ORCHESTRATOR", "false")
        assert RoleConsoleHost._use_orchestrator({"enable_session_orchestrator": True}) is False

    def test_capability_profile_true_enables(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("POLARIS_ENABLE_SESSION_ORCHESTRATOR", raising=False)
        assert RoleConsoleHost._use_orchestrator({"enable_session_orchestrator": True}) is True

    def test_capability_profile_false_disables(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("POLARIS_ENABLE_SESSION_ORCHESTRATOR", raising=False)
        assert RoleConsoleHost._use_orchestrator({"enable_session_orchestrator": False}) is False

    def test_default_is_false(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("POLARIS_ENABLE_SESSION_ORCHESTRATOR", raising=False)
        assert RoleConsoleHost._use_orchestrator({}) is False


class TestNormalizeOrchestratorEvent:
    """Tests for _normalize_orchestrator_event."""

    def test_content_chunk(self) -> None:
        event = ContentChunkEvent(turn_id="t1", chunk="hello")
        normalized = RoleConsoleHost._normalize_orchestrator_event(event)
        assert normalized == {"type": "content_chunk", "data": {"content": "hello"}}

    def test_tool_batch_started(self) -> None:
        event = ToolBatchEvent(
            turn_id="t1",
            batch_id="b1",
            tool_name="read_file",
            call_id="c1",
            status="started",
            progress=0.0,
            arguments={"path": "foo.py"},
        )
        normalized = RoleConsoleHost._normalize_orchestrator_event(event)
        assert normalized == {"type": "tool_call", "data": {"tool": "read_file", "args": {"path": "foo.py"}}}

    def test_tool_batch_success(self) -> None:
        event = ToolBatchEvent(
            turn_id="t1",
            batch_id="b1",
            tool_name="read_file",
            call_id="c1",
            status="success",
            progress=1.0,
            result={"content": "x"},
        )
        normalized = RoleConsoleHost._normalize_orchestrator_event(event)
        assert normalized == {
            "type": "tool_result",
            "data": {"tool": "read_file", "result": {"content": "x"}, "success": True},
        }

    def test_tool_batch_error(self) -> None:
        event = ToolBatchEvent(
            turn_id="t1",
            batch_id="b1",
            tool_name="read_file",
            call_id="c1",
            status="error",
            progress=1.0,
            error="not found",
        )
        normalized = RoleConsoleHost._normalize_orchestrator_event(event)
        assert normalized == {
            "type": "tool_result",
            "data": {"tool": "read_file", "error": "not found", "success": False},
        }

    def test_completion_event(self) -> None:
        event = CompletionEvent(turn_id="t1", status="success")
        normalized = RoleConsoleHost._normalize_orchestrator_event(event)
        assert normalized == {"type": "complete", "data": {"content": "", "thinking": None, "turn_kind": ""}}

    def test_error_event(self) -> None:
        event = ErrorEvent(turn_id="t1", error_type="RuntimeError", message="boom")
        normalized = RoleConsoleHost._normalize_orchestrator_event(event)
        assert normalized == {"type": "error", "error": "boom"}

    def test_session_started(self) -> None:
        event = SessionStartedEvent(session_id="s1")
        normalized = RoleConsoleHost._normalize_orchestrator_event(event)
        assert normalized == {"type": "session_started", "data": {"session_id": "s1"}}

    def test_session_completed(self) -> None:
        event = SessionCompletedEvent(session_id="s1")
        normalized = RoleConsoleHost._normalize_orchestrator_event(event)
        assert normalized == {"type": "session_completed", "data": {"session_id": "s1"}}

    def test_session_waiting_human(self) -> None:
        event = SessionWaitingHumanEvent(session_id="s1", reason="need_input")
        normalized = RoleConsoleHost._normalize_orchestrator_event(event)
        assert normalized == {"type": "session_waiting_human", "data": {"session_id": "s1", "reason": "need_input"}}

    def test_runtime_started(self) -> None:
        event = RuntimeStartedEvent(name="DevelopmentWorkflow")
        normalized = RoleConsoleHost._normalize_orchestrator_event(event)
        assert normalized == {"type": "runtime_started", "data": {"name": "DevelopmentWorkflow"}}

    def test_runtime_completed(self) -> None:
        event = RuntimeCompletedEvent()
        normalized = RoleConsoleHost._normalize_orchestrator_event(event)
        assert normalized == {"type": "runtime_completed", "data": {}}

    def test_turn_phase(self) -> None:
        event = TurnPhaseEvent.create(turn_id="t1", phase="workflow_handoff", metadata={"handoff_target": "dev"})
        normalized = RoleConsoleHost._normalize_orchestrator_event(event)
        assert normalized is not None
        assert normalized["type"] == "turn_phase"
        assert normalized["data"]["phase"] == "workflow_handoff"
        assert normalized["data"]["metadata"]["handoff_target"] == "dev"

    def test_unknown_event_returns_none(self) -> None:
        assert RoleConsoleHost._normalize_orchestrator_event("unknown") is None

    def test_dict_fallback(self) -> None:
        event = {"type": "content_chunk", "content": "hi"}
        normalized = RoleConsoleHost._normalize_orchestrator_event(event)
        assert normalized == {"type": "content_chunk", "data": {"content": "hi"}}


class TestSessionEventLog:
    """Tests for session-isolated event logging."""

    def test_get_session_event_log_path(self, tmp_path: Path) -> None:
        host = RoleConsoleHost(str(tmp_path))
        path = host._get_session_event_log_path("sess-1")
        assert path == tmp_path / ".polaris" / "runtime" / "events" / "sess-1.jsonl"
        assert path.parent.exists()

    def test_write_session_event(self, tmp_path: Path) -> None:
        host = RoleConsoleHost(str(tmp_path))
        host._write_session_event("sess-1", {"type": "content_chunk", "data": {"content": "hello"}})
        log_path = host._get_session_event_log_path("sess-1")
        lines = log_path.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 1
        assert json.loads(lines[0]) == {"type": "content_chunk", "data": {"content": "hello"}}

    def test_write_multiple_events_to_same_session(self, tmp_path: Path) -> None:
        host = RoleConsoleHost(str(tmp_path))
        host._write_session_event("sess-1", {"type": "a"})
        host._write_session_event("sess-1", {"type": "b"})
        log_path = host._get_session_event_log_path("sess-1")
        lines = log_path.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 2
        assert json.loads(lines[0]) == {"type": "a"}
        assert json.loads(lines[1]) == {"type": "b"}


class TestStreamTurnOrchestratorPath:
    """End-to-end tests for stream_turn using the orchestrator path."""

    @pytest.fixture
    def mock_host(self) -> RoleConsoleHost:
        with (
            patch("polaris.delivery.cli.director.console_host._ensure_minimal_runtime_bindings"),
            patch("polaris.delivery.cli.director.console_host.RoleRuntimeService") as mock_runtime_cls,
        ):
            mock_runtime = MagicMock()
            mock_runtime_cls.return_value = mock_runtime
            host = RoleConsoleHost(".", role="director")
            return host

    def test_stream_turn_uses_orchestrator_when_flag_enabled(
        self, mock_host: RoleConsoleHost, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("POLARIS_ENABLE_SESSION_ORCHESTRATOR", "1")

        create_payload = {
            "id": "sess-orch",
            "context_config": {"role": "director", "host_kind": "cli"},
            "messages": [],
            "capability_profile": {"enable_session_orchestrator": True, "max_auto_turns": 1},
        }

        class FakeTxController:
            async def execute_stream(self, turn_id: str, context: list[dict], tool_definitions: list[dict]) -> Any:
                yield ContentChunkEvent(turn_id=turn_id, chunk="hello from orch")
                yield CompletionEvent(turn_id=turn_id, status="success")

        mock_tx_controller = FakeTxController()
        mock_runtime = cast(MagicMock, mock_host._runtime_service)
        mock_runtime.create_transaction_controller.return_value = mock_tx_controller

        with (
            patch.object(mock_host, "create_session", return_value=create_payload),
            patch.object(
                mock_host,
                "_project_session_continuity",
                return_value=MagicMock(
                    recent_messages=(), prompt_context={}, persisted_context_config={}, changed=False
                ),
            ),
            patch.object(mock_host, "_persist_message") as mock_persist,
            patch.object(mock_host, "_build_runtime_history", return_value=()),
        ):

            async def run() -> list[dict[str, Any]]:
                events: list[dict[str, Any]] = []
                async for evt in mock_host.stream_turn(None, "hello"):
                    events.append(evt)
                return events

            events = asyncio.run(run())

        content_events = [e for e in events if e.get("type") == "content_chunk"]
        complete_events = [e for e in events if e.get("type") == "complete"]
        assert len(content_events) == 1
        assert content_events[0]["data"]["content"] == "hello from orch"
        assert len(complete_events) == 1
        mock_persist.assert_called()

    def test_stream_turn_falls_back_to_legacy_on_controller_failure(
        self, mock_host: RoleConsoleHost, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("POLARIS_ENABLE_SESSION_ORCHESTRATOR", "1")

        create_payload = {
            "id": "sess-fallback",
            "context_config": {"role": "director", "host_kind": "cli"},
            "messages": [],
        }

        mock_runtime = cast(MagicMock, mock_host._runtime_service)
        mock_runtime.create_transaction_controller.side_effect = RuntimeError("no kernel")

        async def fake_stream(_command: Any) -> Any:
            yield {"type": "content_chunk", "content": "legacy"}
            yield {"type": "complete", "data": {"content": "legacy", "thinking": None}}

        mock_runtime.stream_chat_turn = fake_stream

        with (
            patch.object(mock_host, "create_session", return_value=create_payload),
            patch.object(
                mock_host,
                "_project_session_continuity",
                return_value=MagicMock(
                    recent_messages=(), prompt_context={}, persisted_context_config={}, changed=False
                ),
            ),
            patch.object(mock_host, "_persist_message"),
            patch.object(mock_host, "_build_runtime_history", return_value=()),
        ):

            async def run() -> list[dict[str, Any]]:
                events: list[dict[str, Any]] = []
                async for evt in mock_host.stream_turn(None, "hello"):
                    events.append(evt)
                return events

            events = asyncio.run(run())

        assert any(e.get("type") == "content_chunk" and e.get("data", {}).get("content") == "legacy" for e in events)

    def test_session_events_written_to_isolated_log(
        self, mock_host: RoleConsoleHost, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("POLARIS_ENABLE_SESSION_ORCHESTRATOR", "1")
        mock_host.workspace = str(tmp_path)

        create_payload = {
            "id": "sess-log",
            "context_config": {"role": "director", "host_kind": "cli"},
            "messages": [],
            "capability_profile": {"enable_session_orchestrator": True, "max_auto_turns": 1},
        }

        class FakeTxController:
            async def execute_stream(self, turn_id: str, context: list[dict], tool_definitions: list[dict]) -> Any:
                yield ContentChunkEvent(turn_id=turn_id, chunk="log me")
                yield CompletionEvent(turn_id=turn_id, status="success")

        mock_runtime = cast(MagicMock, mock_host._runtime_service)
        mock_runtime.create_transaction_controller.return_value = FakeTxController()

        with (
            patch.object(mock_host, "create_session", return_value=create_payload),
            patch.object(
                mock_host,
                "_project_session_continuity",
                return_value=MagicMock(
                    recent_messages=(), prompt_context={}, persisted_context_config={}, changed=False
                ),
            ),
            patch.object(mock_host, "_persist_message"),
            patch.object(mock_host, "_build_runtime_history", return_value=()),
        ):

            async def run() -> None:
                async for _ in mock_host.stream_turn(None, "hello"):
                    pass

            asyncio.run(run())

        log_path = tmp_path / ".polaris" / "runtime" / "events" / "sess-log.jsonl"
        assert log_path.exists()
        lines = log_path.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) >= 1
        types = {json.loads(line)["type"] for line in lines}
        assert "content_chunk" in types
