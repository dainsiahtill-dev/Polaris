"""Orchestrator E2E Integration Tests.

Covers critical multi-turn paths:
1. Analysis-only path: read tools -> summary -> END_SESSION
2. Write path: read -> write -> mutation_satisfied -> END_SESSION
3. Context switch: new user prompt updates goal and triggers write

Uses deterministic mocked LLM (FakeTxController) to avoid real LLM calls.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, cast
from unittest.mock import MagicMock, patch

import pytest
from polaris.cells.roles.kernel.public.turn_contracts import (
    TurnContinuationMode,
    TurnId,
    TurnResult,
)
from polaris.cells.roles.kernel.public.turn_events import (
    CompletionEvent,
)
from polaris.cells.roles.runtime.internal.session_orchestrator import RoleSessionOrchestrator
from polaris.delivery.cli.director.console_host import RoleConsoleHost


class FakeTxController:
    """Deterministic fake transaction controller.

    Yields pre-configured events per turn. After yielding CompletionEvent,
    the orchestrator's _build_envelope_from_completion determines continuation.
    """

    def __init__(self, events_by_turn: dict[str, list[Any]]) -> None:
        self.events_by_turn = events_by_turn
        self.tool_runtime = MagicMock()
        self.execute_stream_call_count = 0
        self.contexts: list[list[dict]] = []

    async def execute_stream(
        self,
        turn_id: str,
        context: list[dict],
        tool_definitions: list[dict],
    ) -> Any:
        self.execute_stream_call_count += 1
        self.contexts.append(context)
        for event in self.events_by_turn.get(turn_id, []):
            yield event


def _make_envelope(
    event: CompletionEvent,
    *,
    continuation_mode: TurnContinuationMode,
    batch_receipt: dict[str, Any] | None = None,
    session_patch: dict[str, Any] | None = None,
) -> Any:
    """Build a realistic TurnOutcomeEnvelope for testing."""
    from types import SimpleNamespace

    return SimpleNamespace(
        turn_result=TurnResult(
            turn_id=TurnId(event.turn_id),
            kind="tool_batch_with_receipt",
            visible_content=event.visible_content or "",
            decision={},
            batch_receipt=batch_receipt or {},
        ),
        continuation_mode=continuation_mode,
        next_intent=None,
        session_patch=session_patch or {},
        artifacts_to_persist=[],
        speculative_hints={},
    )


class TestAnalysisOnlyPath:
    """Scenario: User asks 'summarize code'. LLM reads, summarizes, should end."""

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

    def test_analysis_only_ends_after_summary(
        self,
        mock_host: RoleConsoleHost,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Turn 1: repo_tree. Turn 2: summary visible_content. Should END_SESSION."""
        monkeypatch.setenv("KERNELONE_ENABLE_SESSION_ORCHESTRATOR", "1")
        session_id = "e2e-analysis-1"

        # Turn 1: repo_tree tool call -> completion
        # Turn 2: LLM produces summary text (no tools)
        events_by_turn = {
            f"{session_id}_turn0": [
                CompletionEvent(
                    turn_id=f"{session_id}_turn0",
                    status="success",
                    visible_content="",
                    batch_receipt={
                        "results": [{"tool_name": "repo_tree", "status": "success", "result": {"entries": []}}]
                    },
                ),
            ],
            f"{session_id}_turn1": [
                CompletionEvent(
                    turn_id=f"{session_id}_turn1",
                    status="success",
                    visible_content="# Summary\nThis is a file server project.",
                    batch_receipt={"results": []},
                ),
            ],
        }

        mock_runtime = cast(MagicMock, mock_host._runtime_service)
        mock_runtime.create_transaction_controller.return_value = FakeTxController(events_by_turn)

        create_payload = {
            "id": session_id,
            "context_config": {"role": "director", "host_kind": "cli"},
            "messages": [],
            "capability_profile": {"enable_session_orchestrator": True, "max_auto_turns": 5},
        }

        call_count = 0

        def _patched_build(event: CompletionEvent) -> Any:
            nonlocal call_count
            call_count += 1
            # Turn 1: AUTO_CONTINUE (read tools, need to summarize)
            # Turn 2: END_SESSION (summary produced, no write tools needed)
            mode = TurnContinuationMode.END_SESSION if call_count >= 2 else TurnContinuationMode.AUTO_CONTINUE
            return _make_envelope(event, continuation_mode=mode, batch_receipt=event.batch_receipt)

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
            patch.object(
                RoleSessionOrchestrator,
                "_build_envelope_from_completion",
                staticmethod(_patched_build),
            ),
        ):

            async def run() -> list[dict[str, Any]]:
                events: list[dict[str, Any]] = []
                async for evt in mock_host.stream_turn(None, "summarize the project"):
                    events.append(evt)
                return events

            collected = asyncio.run(run())

        # Should complete in 2 turns (read -> summarize -> end)
        assert call_count == 2
        visible_types = [e.get("type") for e in collected]
        assert "session_completed" in visible_types


class TestWritePath:
    """Scenario: User asks 'split server.py'. LLM reads, then writes."""

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

    def test_write_path_mutation_satisfied(
        self,
        mock_host: RoleConsoleHost,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Turn 1: repo_tree. Turn 2: write_file. Should END_SESSION."""
        monkeypatch.setenv("KERNELONE_ENABLE_SESSION_ORCHESTRATOR", "1")
        session_id = "e2e-write-1"

        events_by_turn = {
            f"{session_id}_turn0": [
                CompletionEvent(
                    turn_id=f"{session_id}_turn0",
                    status="success",
                    visible_content="",
                    batch_receipt={
                        "results": [{"tool_name": "repo_tree", "status": "success", "result": {"entries": []}}]
                    },
                ),
            ],
            f"{session_id}_turn1": [
                CompletionEvent(
                    turn_id=f"{session_id}_turn1",
                    status="success",
                    visible_content="Split complete.",
                    batch_receipt={
                        "results": [{"tool_name": "write_file", "status": "success", "result": {"path": "routes.py"}}]
                    },
                ),
            ],
        }

        mock_runtime = cast(MagicMock, mock_host._runtime_service)
        controller = FakeTxController(events_by_turn)
        mock_runtime.create_transaction_controller.return_value = controller

        create_payload = {
            "id": session_id,
            "context_config": {"role": "director", "host_kind": "cli"},
            "messages": [],
            "capability_profile": {"enable_session_orchestrator": True, "max_auto_turns": 5},
        }

        call_count = 0

        def _patched_build(event: CompletionEvent) -> Any:
            nonlocal call_count
            call_count += 1
            # Turn 1: AUTO_CONTINUE (exploring)
            # Turn 2: END_SESSION (write done)
            mode = TurnContinuationMode.END_SESSION if call_count >= 2 else TurnContinuationMode.AUTO_CONTINUE
            return _make_envelope(event, continuation_mode=mode, batch_receipt=event.batch_receipt)

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
            patch.object(
                RoleSessionOrchestrator,
                "_build_envelope_from_completion",
                staticmethod(_patched_build),
            ),
        ):

            async def run() -> list[dict[str, Any]]:
                events: list[dict[str, Any]] = []
                async for evt in mock_host.stream_turn(None, "split server.py into modules"):
                    events.append(evt)
                return events

            collected = asyncio.run(run())

        # Verify 2 turns
        assert call_count == 2
        visible_types = [e.get("type") for e in collected]
        assert "session_completed" in visible_types

        # Verify goal was updated
        checkpoint_path = Path(mock_host.workspace) / ".polaris" / "checkpoints" / f"{session_id}.json"
        assert checkpoint_path.exists()
        import json

        checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
        assert "split server.py" in checkpoint.get("goal", "")


class TestContextSwitch:
    """Scenario: User first asks 'summarize', then 'split server.py'.

    Verifies that the second prompt updates goal and triggers write.
    """

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

    def test_context_switch_updates_goal(
        self,
        mock_host: RoleConsoleHost,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """First turn: summarize. Second turn: split server.py.

        The second prompt should update goal and task_progress.
        """
        monkeypatch.setenv("KERNELONE_ENABLE_SESSION_ORCHESTRATOR", "1")
        session_id = "e2e-context-switch-1"

        events_by_turn = {
            f"{session_id}_turn0": [
                CompletionEvent(
                    turn_id=f"{session_id}_turn0",
                    status="success",
                    visible_content="# Summary\nA file server project.",
                    batch_receipt={"results": []},
                ),
            ],
        }

        mock_runtime = cast(MagicMock, mock_host._runtime_service)
        controller = FakeTxController(events_by_turn)
        mock_runtime.create_transaction_controller.return_value = controller

        create_payload = {
            "id": session_id,
            "context_config": {"role": "director", "host_kind": "cli"},
            "messages": [],
            "capability_profile": {"enable_session_orchestrator": True, "max_auto_turns": 5},
        }

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
        ):

            async def run_first() -> list[dict[str, Any]]:
                events: list[dict[str, Any]] = []
                async for evt in mock_host.stream_turn(None, "summarize the project"):
                    events.append(evt)
                return events

            # First request: summarize
            collected1 = asyncio.run(run_first())

            # Verify session ended after summary (read-only termination exempt)
            visible_types = [e.get("type") for e in collected1]
            assert "session_completed" in visible_types

            # Check checkpoint after first turn
            checkpoint_path = Path(mock_host.workspace) / ".polaris" / "checkpoints" / f"{session_id}.json"
            assert checkpoint_path.exists()
            import json

            checkpoint1 = json.loads(checkpoint_path.read_text(encoding="utf-8"))
            assert "summarize" in checkpoint1.get("goal", "")

            # Now simulate second request in same session with NEW goal
            # Reset the controller for turn 1 of the new request
            events_by_turn[f"{session_id}_turn1"] = [
                CompletionEvent(
                    turn_id=f"{session_id}_turn1",
                    status="success",
                    visible_content="Split done.",
                    batch_receipt={
                        "results": [{"tool_name": "write_file", "status": "success", "result": {"path": "routes.py"}}]
                    },
                ),
            ]

            call_count = 0

            def _patched_build(event: CompletionEvent) -> Any:
                nonlocal call_count
                call_count += 1
                mode = TurnContinuationMode.END_SESSION if call_count >= 1 else TurnContinuationMode.AUTO_CONTINUE
                return _make_envelope(event, continuation_mode=mode, batch_receipt=event.batch_receipt)

            with patch.object(
                RoleSessionOrchestrator,
                "_build_envelope_from_completion",
                staticmethod(_patched_build),
            ):

                async def run_second() -> list[dict[str, Any]]:
                    events: list[dict[str, Any]] = []
                    async for evt in mock_host.stream_turn(None, "split server.py into modules"):
                        events.append(evt)
                    return events

                collected2 = asyncio.run(run_second())

            # Verify second session also completes
            visible_types2 = [e.get("type") for e in collected2]
            assert "session_completed" in visible_types2

            # Check checkpoint was updated with new goal
            checkpoint2 = json.loads(checkpoint_path.read_text(encoding="utf-8"))
            assert "split server.py" in checkpoint2.get("goal", "")
