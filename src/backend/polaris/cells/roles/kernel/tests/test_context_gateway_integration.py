"""Test for Phase 2: StateFirstContextOS.project() integration in RoleContextGateway.

This test verifies that RoleContextGateway.build_context() now calls
StateFirstContextOS.project() and uses the projection's active_window,
head_anchor, tail_anchor, and run_card instead of simple transcript expansion.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from polaris.kernelone.context.contracts import TurnEngineContextRequest as ContextRequest


class TestStateFirstContextOSIntegration:
    """Test Phase 2: StateFirstContextOS.project() integration."""

    @pytest.mark.asyncio
    async def test_build_context_calls_state_first_project(self):
        """Verify build_context calls StateFirstContextOS.project()."""
        # Import here to avoid import errors if module has issues
        from polaris.cells.roles.kernel.internal.context_gateway import RoleContextGateway
        from polaris.kernelone.context.context_os.models_v2 import (
            ContextOSProjectionV2 as ContextOSProjection,
            ContextOSSnapshotV2 as ContextOSSnapshot,
            TranscriptEventV2 as TranscriptEvent,
        )

        # Create mock profile
        mock_profile = MagicMock()
        mock_profile.context_policy = MagicMock()
        mock_profile.context_policy.max_history_turns = 8
        mock_profile.context_policy.max_context_tokens = 128000
        mock_profile.context_policy.include_project_structure = False
        mock_profile.context_policy.include_task_history = False
        mock_profile.context_policy.compression_strategy = "truncate"
        mock_profile.context_domain = None
        mock_profile.provider_id = "test_provider"
        mock_profile.model = "test_model"
        mock_profile.role_id = "director"
        mock_profile.display_name = "Director"

        # Create gateway
        gateway = RoleContextGateway(mock_profile, workspace=".")

        # Mock the StateFirstContextOS.project() method
        mock_snapshot = MagicMock(spec=ContextOSSnapshot)
        mock_snapshot.budget_plan = MagicMock()
        mock_snapshot.budget_plan.validation_error = ""

        mock_projection = MagicMock(spec=ContextOSProjection)
        mock_projection.head_anchor = "Test head anchor"
        mock_projection.tail_anchor = "Test tail anchor"
        mock_projection.active_window = (
            TranscriptEvent(
                event_id="test_1",
                sequence=1,
                role="user",
                kind="user_turn",
                route="clear",
                content="Test message",
            ),
        )
        mock_projection.run_card = MagicMock()
        mock_projection.run_card.current_goal = "Test goal"
        mock_projection.run_card.open_loops = ()
        mock_projection.run_card.latest_user_intent = ""
        mock_projection.run_card.pending_followup_action = ""
        mock_projection.run_card.last_turn_outcome = ""
        mock_projection.snapshot = mock_snapshot

        with patch.object(gateway._context_os, "project", return_value=mock_projection) as mock_project:
            # Create request
            request = ContextRequest(
                message="Test message",
                history=[("user", "Hello")],
                context_os_snapshot=None,
            )

            # Build context
            result = await gateway.build_context(request)

            # Verify project() was called
            mock_project.assert_called_once()

            # Verify call arguments
            call_args = mock_project.call_args
            assert call_args is not None
            assert "messages" in call_args.kwargs or len(call_args.args) > 0
            assert "existing_snapshot" in call_args.kwargs or len(call_args.args) > 1
            assert "recent_window_messages" in call_args.kwargs

            # Verify messages were built from projection
            assert len(result.messages) > 0

            # Verify source includes state_first_context_os_initial_projection
            assert "state_first_context_os_initial_projection" in result.context_sources

    @pytest.mark.asyncio
    async def test_build_context_with_snapshot_uses_projection(self):
        """Verify build_context uses projection when snapshot is provided."""
        from polaris.cells.roles.kernel.internal.context_gateway import RoleContextGateway
        from polaris.kernelone.context.context_os.models_v2 import (
            ContextOSProjectionV2 as ContextOSProjection,
            ContextOSSnapshotV2 as ContextOSSnapshot,
            TranscriptEventV2 as TranscriptEvent,
            WorkingStateV2 as WorkingState,
        )

        # Create mock profile
        mock_profile = MagicMock()
        mock_profile.context_policy = MagicMock()
        mock_profile.context_policy.max_history_turns = 8
        mock_profile.context_policy.max_context_tokens = 128000
        mock_profile.context_policy.include_project_structure = False
        mock_profile.context_policy.include_task_history = False
        mock_profile.context_policy.compression_strategy = "truncate"
        mock_profile.context_domain = None
        mock_profile.provider_id = "test_provider"
        mock_profile.model = "test_model"
        mock_profile.role_id = "director"
        mock_profile.display_name = "Director"

        # Create gateway
        gateway = RoleContextGateway(mock_profile, workspace=".")

        # Create mock snapshot
        mock_snapshot = MagicMock(spec=ContextOSSnapshot)
        mock_snapshot.budget_plan = MagicMock()
        mock_snapshot.budget_plan.validation_error = ""
        mock_snapshot.transcript_log = (
            TranscriptEvent(
                event_id="test_1",
                sequence=1,
                role="user",
                kind="user_turn",
                route="clear",
                content="Previous message",
            ),
        )
        mock_snapshot.working_state = WorkingState()
        mock_snapshot.artifact_store = ()
        mock_snapshot.pending_followup = None

        mock_projection = MagicMock(spec=ContextOSProjection)
        mock_projection.head_anchor = "Head anchor from projection"
        mock_projection.tail_anchor = "Tail anchor from projection"
        mock_projection.active_window = (
            TranscriptEvent(
                event_id="test_2",
                sequence=2,
                role="assistant",
                kind="assistant_turn",
                route="clear",
                content="Response from projection",
            ),
        )
        mock_projection.run_card = MagicMock()
        mock_projection.run_card.current_goal = ""
        mock_projection.run_card.open_loops = ()
        mock_projection.run_card.latest_user_intent = ""
        mock_projection.run_card.pending_followup_action = ""
        mock_projection.run_card.last_turn_outcome = ""
        mock_projection.snapshot = mock_snapshot

        with patch.object(gateway._context_os, "project", return_value=mock_projection) as mock_project:
            # Create request with snapshot
            request = ContextRequest(
                message="New message",
                history=[("user", "New input")],
                context_os_snapshot={"version": 1, "transcript_log": []},
            )

            # Build context
            result = await gateway.build_context(request)

            # Verify project() was called
            mock_project.assert_called_once()

            # Verify source includes state_first_context_os_projection
            assert "state_first_context_os_projection" in result.context_sources

    @pytest.mark.asyncio
    async def test_budget_validation_error_triggers_emergency_truncate(self):
        """Verify BudgetPlan validation error triggers emergency truncation."""
        from polaris.cells.roles.kernel.internal.context_gateway import RoleContextGateway
        from polaris.kernelone.context.context_os.models_v2 import (
            BudgetPlanV2 as BudgetPlan,
            ContextOSProjectionV2 as ContextOSProjection,
            ContextOSSnapshotV2 as ContextOSSnapshot,
            TranscriptEventV2 as TranscriptEvent,
        )

        # Create mock profile
        mock_profile = MagicMock()
        mock_profile.context_policy = MagicMock()
        mock_profile.context_policy.max_history_turns = 8
        mock_profile.context_policy.max_context_tokens = 1000  # Small limit
        mock_profile.context_policy.include_project_structure = False
        mock_profile.context_policy.include_task_history = False
        mock_profile.context_policy.compression_strategy = "truncate"
        mock_profile.context_domain = None
        mock_profile.provider_id = "test_provider"
        mock_profile.model = "test_model"
        mock_profile.role_id = "director"
        mock_profile.display_name = "Director"

        # Create gateway
        gateway = RoleContextGateway(mock_profile, workspace=".")

        # Create mock snapshot with budget validation error
        budget_plan = BudgetPlan(
            model_context_window=1000,
            output_reserve=100,
            tool_reserve=50,
            safety_margin=50,
            input_budget=800,
            retrieval_budget=100,
            soft_limit=500,
            hard_limit=600,
            emergency_limit=700,
            current_input_tokens=500,
            expected_next_input_tokens=1200,
            p95_tool_result_tokens=200,
            planned_retrieval_tokens=100,
            validation_error="BudgetPlan invariant violated: expected_next_input_tokens exceeds model_context_window",
        )

        mock_snapshot = MagicMock(spec=ContextOSSnapshot)
        mock_snapshot.budget_plan = budget_plan
        mock_snapshot.transcript_log = ()
        mock_snapshot.artifact_store = ()
        mock_snapshot.pending_followup = None

        mock_projection = MagicMock(spec=ContextOSProjection)
        mock_projection.head_anchor = ""
        mock_projection.tail_anchor = ""
        mock_projection.active_window = (
            TranscriptEvent(
                event_id="test_1",
                sequence=1,
                role="user",
                kind="user_turn",
                route="clear",
                content="A" * 1000,  # Large content to exceed budget
            ),
        )
        mock_projection.run_card = None
        mock_projection.snapshot = mock_snapshot

        with patch.object(gateway._context_os, "project", return_value=mock_projection):
            # Create request
            request = ContextRequest(
                message="Test",
                history=[("user", "Test")],
                context_os_snapshot={"version": 1},
            )

            # Build context - should apply emergency truncation
            result = await gateway.build_context(request)

            # Verify emergency truncation was applied
            assert "budget_violation_emergency_truncate" in result.context_sources or result.compression_applied


class TestMessagesFromProjection:
    """Test the _messages_from_projection helper."""

    def test_creates_head_anchor_message(self):
        """Verify head_anchor creates a system message."""
        from polaris.cells.roles.kernel.internal.context_gateway import RoleContextGateway
        from polaris.kernelone.context.context_os.models_v2 import ContextOSProjectionV2 as ContextOSProjection

        mock_profile = MagicMock()
        mock_profile.context_policy = MagicMock()
        mock_profile.context_domain = None
        mock_profile.provider_id = None
        mock_profile.model = None

        gateway = RoleContextGateway(mock_profile, workspace=".")

        mock_projection = MagicMock(spec=ContextOSProjection)
        mock_projection.head_anchor = "Test head"
        mock_projection.tail_anchor = ""
        mock_projection.active_window = ()
        mock_projection.run_card = None
        mock_projection.snapshot = None

        messages = gateway._messages_from_projection(mock_projection)

        # Should have head anchor message
        assert len(messages) >= 1
        head_msg = messages[0]
        assert head_msg["role"] == "system"
        assert head_msg["content"] == "Test head"
        assert head_msg["name"] == "context_head_anchor"

    def test_creates_active_window_messages(self):
        """Verify active_window events become messages."""
        from polaris.cells.roles.kernel.internal.context_gateway import RoleContextGateway
        from polaris.kernelone.context.context_os.models_v2 import (
            ContextOSProjectionV2 as ContextOSProjection,
            TranscriptEventV2 as TranscriptEvent,
        )

        mock_profile = MagicMock()
        mock_profile.context_policy = MagicMock()
        mock_profile.context_domain = None
        mock_profile.provider_id = None
        mock_profile.model = None

        gateway = RoleContextGateway(mock_profile, workspace=".")

        event = TranscriptEvent(
            event_id="test_1",
            sequence=1,
            role="user",
            kind="user_turn",
            route="clear",
            content="Test content",
            _metadata={"key": "value"},
        )

        mock_projection = MagicMock(spec=ContextOSProjection)
        mock_projection.head_anchor = ""
        mock_projection.tail_anchor = ""
        mock_projection.active_window = (event,)
        mock_projection.run_card = None
        mock_projection.snapshot = None

        messages = gateway._messages_from_projection(mock_projection)

        # Should have active window message
        assert len(messages) >= 1
        active_msg = messages[0]
        assert active_msg["role"] == "user"
        assert active_msg["content"] == "Test content"
        assert "metadata" in active_msg

    def test_creates_run_card_message(self):
        """Verify run_card creates a system message."""
        from polaris.cells.roles.kernel.internal.context_gateway import RoleContextGateway
        from polaris.kernelone.context.context_os.models_v2 import (
            ContextOSProjectionV2 as ContextOSProjection,
            RunCardV2 as RunCard,
        )

        mock_profile = MagicMock()
        mock_profile.context_policy = MagicMock()
        mock_profile.context_domain = None
        mock_profile.provider_id = None
        mock_profile.model = None

        gateway = RoleContextGateway(mock_profile, workspace=".")

        run_card = RunCard(
            current_goal="Test goal",
            open_loops=("loop1", "loop2"),
            latest_user_intent="Test intent",
            pending_followup_action="Confirm",
            last_turn_outcome="affirm",
        )

        mock_projection = MagicMock(spec=ContextOSProjection)
        mock_projection.head_anchor = ""
        mock_projection.tail_anchor = ""
        mock_projection.active_window = ()
        mock_projection.run_card = run_card
        mock_projection.snapshot = None

        messages = gateway._messages_from_projection(mock_projection)

        # Should have run card message
        run_card_msgs = [m for m in messages if m.get("name") == "run_card"]
        assert len(run_card_msgs) == 1
        assert "Test goal" in run_card_msgs[0]["content"]
        assert "Open loops: 2" in run_card_msgs[0]["content"]
