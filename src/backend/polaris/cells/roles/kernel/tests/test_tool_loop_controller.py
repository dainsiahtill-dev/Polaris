"""Unit tests for ToolLoopController.

Tests cover tool loop controller lifecycle, safety policies, and cycle management.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from polaris.cells.roles.kernel.internal.tool_loop_controller import (
    ToolLoopController,
    ToolLoopSafetyPolicy,
)


class TestToolLoopSafetyPolicy:
    def test_default_values(self) -> None:
        policy = ToolLoopSafetyPolicy()
        assert policy.max_total_tool_calls == 64
        assert policy.max_stall_cycles == 2
        assert policy.max_wall_time_seconds == 900

    def test_custom_values(self) -> None:
        policy = ToolLoopSafetyPolicy(
            max_total_tool_calls=5,
            max_stall_cycles=1,
            max_wall_time_seconds=60,
        )
        assert policy.max_total_tool_calls == 5


class TestToolLoopController:
    def test_creation_with_request(self) -> None:
        mock_request = MagicMock()
        mock_request.message = "Test"
        mock_request.history = []
        mock_request.tool_results = []
        # Wave 1 SSOT: context_override must be a dict with context_os_snapshot
        mock_request.context_override = {"context_os_snapshot": {"transcript_log": [], "working_state": {}}}
        mock_profile = MagicMock()
        policy = ToolLoopSafetyPolicy()

        controller = ToolLoopController(
            request=mock_request,
            profile=mock_profile,
            safety_policy=policy,
        )
        assert controller.request == mock_request
        assert controller._total_tool_calls == 0

    def test_build_context_request(self) -> None:
        mock_request = MagicMock()
        mock_request.message = "Hello"
        mock_request.history = []
        mock_request.task_id = "task-1"
        mock_request.context_override = {"context_os_snapshot": {"transcript_log": [], "working_state": {}}}
        mock_request.tool_results = []
        mock_request.context_os_snapshot = {"transcript_log": [], "working_state": {}}
        mock_profile = MagicMock()

        controller = ToolLoopController.from_request(
            request=mock_request,
            profile=mock_profile,
        )
        ctx = controller.build_context_request()
        assert ctx.message == "Hello"
        assert ctx.task_id == "task-1"

    def test_max_tool_calls_exceeded(self) -> None:
        mock_request = MagicMock()
        mock_request.message = "Test"
        mock_request.history = []
        mock_request.tool_results = []
        # Wave 1 SSOT: context_override must be a dict with context_os_snapshot
        mock_request.context_override = {"context_os_snapshot": {"transcript_log": [], "working_state": {}}}
        mock_profile = MagicMock()
        policy = ToolLoopSafetyPolicy(max_total_tool_calls=2)

        controller = ToolLoopController(
            request=mock_request,
            profile=mock_profile,
            safety_policy=policy,
        )

        result = controller.register_cycle(
            executed_tool_calls=[MagicMock(), MagicMock()],
            deferred_tool_calls=[],
            tool_results=[],
        )
        assert result is None

        result = controller.register_cycle(
            executed_tool_calls=[MagicMock()],
            deferred_tool_calls=[],
            tool_results=[],
        )
        assert result is not None
        assert "total tool calls exceeded" in result

    def test_append_tool_cycle(self) -> None:
        mock_request = MagicMock()
        mock_request.message = "Test"
        mock_request.history = []
        mock_request.tool_results = []
        # Wave 1 SSOT: context_override must be a dict with context_os_snapshot
        mock_request.context_override = {"context_os_snapshot": {"transcript_log": [], "working_state": {}}}
        mock_profile = MagicMock()

        controller = ToolLoopController.from_request(
            request=mock_request,
            profile=mock_profile,
        )

        controller.append_tool_cycle(
            assistant_message="Response",
            tool_results=[],
        )
        # Wave 1: _history now stores ContextEvent objects
        # Check that we have a ContextEvent with role='assistant' and content='Response'
        assert len(controller._history) == 1
        event = controller._history[0]
        assert event.role == "assistant"
        assert event.content == "Response"
