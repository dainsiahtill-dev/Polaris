"""End-to-end smoke test for RoleConsoleHost with Session Orchestrator enabled.

This test runs the full stack from console host down to the filesystem,
using a mocked transaction controller to avoid real LLM calls.
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
    TurnPhaseEvent,
)
from polaris.cells.roles.runtime.internal.session_orchestrator import RoleSessionOrchestrator
from polaris.delivery.cli.director.console_host import RoleConsoleHost


class FakeTxController:
    """A fake transaction controller that yields events per turn."""

    def __init__(self, events_by_turn: dict[str, list[Any]]) -> None:
        self.events_by_turn = events_by_turn
        self.tool_runtime = MagicMock()
        self.execute_stream_call_count = 0

    async def execute_stream(
        self,
        turn_id: str,
        context: list[dict],
        tool_definitions: list[dict],
    ) -> Any:
        self.execute_stream_call_count += 1
        for event in self.events_by_turn.get(turn_id, []):
            yield event


class TestConsoleHostE2ESmoke:
    """Full-stack smoke test with real filesystem side effects."""

    @pytest.fixture
    def mock_host(self, tmp_path: Path) -> RoleConsoleHost:
        with (
            patch("polaris.delivery.cli.director.console_host._ensure_minimal_runtime_bindings"),
            patch("polaris.delivery.cli.director.console_host.RoleRuntimeService") as mock_runtime_cls,
        ):
            mock_runtime = MagicMock()
            mock_runtime_cls.return_value = mock_runtime
            host = RoleConsoleHost(str(tmp_path), role="director")
            return host

    def test_orchestrator_multi_turn_writes_checkpoint_and_events(
        self,
        mock_host: RoleConsoleHost,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """
        Simulate a 2-turn session via the orchestrator path and verify:
        - checkpoint file is written after each turn
        - session-isolated event log accumulates events
        - final complete event is surfaced to the caller
        """
        monkeypatch.setenv("POLARIS_ENABLE_SESSION_ORCHESTRATOR", "1")
        workspace = Path(mock_host.workspace)
        session_id = "e2e-smoke-1"

        events_by_turn = {
            f"{session_id}_turn0": [
                ContentChunkEvent(turn_id=f"{session_id}_turn0", chunk="turn1 chunk"),
                CompletionEvent(turn_id=f"{session_id}_turn0", status="success"),
            ],
            f"{session_id}_turn1": [
                ContentChunkEvent(turn_id=f"{session_id}_turn1", chunk="turn2 chunk"),
                CompletionEvent(turn_id=f"{session_id}_turn1", status="success"),
            ],
        }

        tx_controller: FakeTxController | None = None

        def _make_controller(_cmd: Any) -> FakeTxController:
            nonlocal tx_controller
            tx_controller = FakeTxController(events_by_turn)
            return tx_controller

        mock_runtime = cast(MagicMock, mock_host._runtime_service)
        mock_runtime.create_transaction_controller.side_effect = _make_controller

        create_payload = {
            "id": session_id,
            "context_config": {"role": "director", "host_kind": "cli"},
            "messages": [],
            "capability_profile": {"enable_session_orchestrator": True, "max_auto_turns": 5},
        }

        # Monkey-patch the orchestrator envelope builder so the first turn
        # requests AUTO_CONTINUE and the second turn ends the session.
        envelope_call_count = 0

        def _patched_build(event: CompletionEvent) -> Any:
            nonlocal envelope_call_count
            from types import SimpleNamespace

            from polaris.cells.roles.kernel.public.turn_contracts import (
                TurnContinuationMode,
                TurnId,
                TurnResult,
            )

            envelope_call_count += 1
            mode = TurnContinuationMode.END_SESSION if envelope_call_count >= 2 else TurnContinuationMode.AUTO_CONTINUE
            return SimpleNamespace(
                turn_result=TurnResult(
                    turn_id=TurnId(event.turn_id),
                    kind="final_answer",
                    visible_content="",
                    decision={},
                ),
                continuation_mode=mode,
                next_intent=None,
                session_patch={},
                artifacts_to_persist=[],
                speculative_hints={},
            )

        with (
            patch.object(mock_host, "create_session", return_value=create_payload),
            patch.object(
                mock_host,
                "_project_session_continuity",
                return_value=MagicMock(
                    recent_messages=(),
                    prompt_context={},
                    persisted_context_config={},
                    changed=False,
                ),
            ),
            patch.object(mock_host, "_persist_message"),
            patch.object(mock_host, "_build_runtime_history", return_value=()),
            patch.object(RoleSessionOrchestrator, "_build_envelope_from_completion", staticmethod(_patched_build)),
        ):

            async def run() -> list[dict[str, Any]]:
                events: list[dict[str, Any]] = []
                async for evt in mock_host.stream_turn(None, "read file test.py"):
                    events.append(evt)
                return events

            collected = asyncio.run(run())

        # Assertions on control plane
        assert tx_controller is not None
        assert tx_controller.execute_stream_call_count == 2, (
            f"expected 2 turns, got {tx_controller.execute_stream_call_count}"
        )
        assert envelope_call_count == 2

        # Assertions on filesystem: checkpoint
        checkpoint_path = workspace / ".polaris" / "checkpoints" / f"{session_id}.json"
        assert checkpoint_path.exists(), "checkpoint should be written"
        checkpoint_data = json.loads(checkpoint_path.read_text(encoding="utf-8"))
        assert checkpoint_data["session_id"] == session_id
        assert checkpoint_data["turn_count"] == 2

        # Assertions on filesystem: session-isolated event log
        events_log_path = workspace / ".polaris" / "runtime" / "events" / f"{session_id}.jsonl"
        assert events_log_path.exists(), "session event log should be written"
        lines = events_log_path.read_text(encoding="utf-8").strip().splitlines()
        logged_types = [json.loads(line)["type"] for line in lines]

        # We expect orchestrator events to be logged (SessionStarted, content chunks,
        # completions, session completed).  The exact count depends on implementation,
        # but key types must be present.
        assert "session_started" in logged_types
        assert "content_chunk" in logged_types
        assert "complete" in logged_types
        assert "session_completed" in logged_types

        # Assertions on caller-visible stream events
        visible_types = [e.get("type") for e in collected]
        assert "content_chunk" in visible_types
        assert "complete" in visible_types
        # Session-level events are also yielded to the caller
        assert "session_started" in visible_types
        assert "session_completed" in visible_types

    def test_orchestrator_handoff_development_writes_events_and_exits(
        self,
        mock_host: RoleConsoleHost,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """
        Simulate a single turn that hands off to DevelopmentWorkflowRuntime.
        Verify the handoff events are logged and the stream exits cleanly.
        """
        monkeypatch.setenv("POLARIS_ENABLE_SESSION_ORCHESTRATOR", "1")
        workspace = Path(mock_host.workspace)
        session_id = "e2e-handoff-1"

        turn_events = {
            f"{session_id}_turn0": [
                TurnPhaseEvent.create(turn_id=f"{session_id}_turn0", phase="decision_completed"),
                CompletionEvent(turn_id=f"{session_id}_turn0", status="handoff"),
            ]
        }

        mock_runtime = cast(MagicMock, mock_host._runtime_service)
        mock_runtime.create_transaction_controller.return_value = FakeTxController(turn_events)

        create_payload = {
            "id": session_id,
            "context_config": {"role": "director", "host_kind": "cli"},
            "messages": [],
            "capability_profile": {"enable_session_orchestrator": True},
        }

        def _patched_build(event: CompletionEvent) -> Any:
            from types import SimpleNamespace

            from polaris.cells.roles.kernel.public.turn_contracts import (
                TurnContinuationMode,
                TurnId,
                TurnResult,
            )

            return SimpleNamespace(
                turn_result=TurnResult(
                    turn_id=TurnId(event.turn_id),
                    kind="final_answer",
                    visible_content="",
                    decision={},
                ),
                continuation_mode=TurnContinuationMode.HANDOFF_DEVELOPMENT,
                next_intent="fix the bug",
                session_patch={},
                artifacts_to_persist=[],
                speculative_hints={},
            )

        async def _fake_dev_stream(intent: str, session_state: Any) -> Any:
            yield TurnPhaseEvent.create(turn_id=session_id, phase="workflow_handoff", metadata={"dev": True})
            yield ContentChunkEvent(turn_id=session_id, chunk="patch applied")
            yield CompletionEvent(turn_id=session_id, status="success")

        with (
            patch.object(mock_host, "create_session", return_value=create_payload),
            patch.object(
                mock_host,
                "_project_session_continuity",
                return_value=MagicMock(
                    recent_messages=(),
                    prompt_context={},
                    persisted_context_config={},
                    changed=False,
                ),
            ),
            patch.object(mock_host, "_persist_message"),
            patch.object(mock_host, "_build_runtime_history", return_value=()),
            patch.object(RoleSessionOrchestrator, "_build_envelope_from_completion", staticmethod(_patched_build)),
            patch(
                "polaris.cells.roles.runtime.internal.session_orchestrator.DevelopmentWorkflowRuntime.execute_stream"
            ) as mock_dev_stream,
        ):
            mock_dev_stream.side_effect = _fake_dev_stream

            async def run() -> list[dict[str, Any]]:
                events: list[dict[str, Any]] = []
                async for evt in mock_host.stream_turn(None, "fix bug in test.py"):
                    events.append(evt)
                return events

            collected = asyncio.run(run())

        # Verify event log contains development handoff events
        events_log_path = workspace / ".polaris" / "runtime" / "events" / f"{session_id}.jsonl"
        assert events_log_path.exists()
        lines = events_log_path.read_text(encoding="utf-8").strip().splitlines()
        logged_types = [json.loads(line)["type"] for line in lines]
        assert "turn_phase" in logged_types
        assert "content_chunk" in logged_types
        assert "complete" in logged_types

        # Caller-visible stream should include the development chunk and complete
        visible_types = [e.get("type") for e in collected]
        assert "content_chunk" in visible_types
        assert "complete" in visible_types
