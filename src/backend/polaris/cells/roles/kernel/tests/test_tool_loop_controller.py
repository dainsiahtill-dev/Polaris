"""Unit tests for ToolLoopController.

Tests cover tool loop controller lifecycle, safety policies, and cycle management.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from polaris.cells.roles.kernel.internal.tool_loop_controller import (
    ToolLoopController,
    ToolLoopSafetyPolicy,
)
from polaris.kernelone.context.prompt_safety import format_tool_failure_summary, parse_tool_failure_summary


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

    def test_build_context_request_passes_cognitive_strategy_override(self) -> None:
        mock_request = MagicMock()
        mock_request.message = "Hello"
        mock_request.history = []
        mock_request.task_id = "task-1"
        mock_request.context_override = {"context_os_snapshot": {"transcript_log": [], "working_state": {}}}
        mock_request.metadata = {
            "cognitive_strategy_override": {
                "exploration": {"max_expansion_depth": 4},
                "compaction": {"trigger_at_budget_pct": 0.9},
                "cognitive_runtime": {"applied": True},
            }
        }
        mock_request.tool_results = []
        mock_profile = MagicMock()

        controller = ToolLoopController.from_request(
            request=mock_request,
            profile=mock_profile,
        )

        ctx = controller.build_context_request()

        assert ctx.strategy_override is not None
        assert ctx.strategy_override["exploration"]["max_expansion_depth"] == 4
        assert ctx.strategy_override["compaction"]["trigger_at_budget_pct"] == 0.9
        assert ctx.strategy_override["cognitive_runtime"]["applied"] is True

    def test_snapshot_history_compacts_repeated_tool_failure_summaries(self) -> None:
        failure = format_tool_failure_summary(
            {
                "tool": "write_file",
                "error_type": "tool_failure",
                "reason": "write_file failed",
                "prompt_safe": True,
                "receipt_detail": "omitted; see runtime tool_result event for audit evidence",
            }
        )
        mock_request = MagicMock()
        mock_request.message = "Continue"
        mock_request.history = []
        mock_request.task_id = "task-1"
        mock_request.context_override = {
            "context_os_snapshot": {
                "transcript_log": [
                    {"role": "tool", "content": failure, "sequence": index}
                    for index in range(6)
                ]
                + [{"role": "user", "content": "continue", "sequence": 6}],
                "working_state": {},
            }
        }
        mock_request.tool_results = []
        mock_profile = MagicMock()

        controller = ToolLoopController.from_request(
            request=mock_request,
            profile=mock_profile,
        )

        summaries = [event for event in controller._history if parse_tool_failure_summary(event.content)]
        assert len(summaries) == 1
        assert summaries[0].role == "system"
        digest = parse_tool_failure_summary(summaries[0].content)
        assert digest is not None
        assert digest["schema_version"] == "tool_failure_summary_digest.v1"
        assert digest["failure_count"] == 6
        assert controller._history[-1].role == "user"

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
