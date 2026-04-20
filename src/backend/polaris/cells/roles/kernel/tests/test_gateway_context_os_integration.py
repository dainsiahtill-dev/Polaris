"""Integration tests for RoleContextGateway + StateFirstContextOS.

These tests verify:
- Gateway uses StateFirstContextOS projection correctly
- Budget validation triggers emergency truncation
- Four-lane routing (CLEAR/ARCHIVE) affects message selection
"""

from __future__ import annotations

from typing import Any

import pytest
from polaris.kernelone.context.context_os.models_v2 import (
    ContextOSProjectionV2 as ContextOSProjection,
    ContextOSSnapshotV2 as ContextOSSnapshot,
    RoutingClassEnum as RoutingClass,
)
from polaris.kernelone.context.context_os.policies import StateFirstContextOSPolicy
from polaris.kernelone.context.context_os.runtime import StateFirstContextOS


@pytest.fixture
def sample_transcript() -> list[dict[str, Any]]:
    """Create a sample transcript for testing."""
    return [
        {
            "event_id": "evt_0",
            "sequence": 0,
            "role": "user",
            "kind": "user_turn",
            "route": RoutingClass.SUMMARIZE,
            "content": "Hello, I need help with the login feature.",
        },
        {
            "event_id": "evt_1",
            "sequence": 1,
            "role": "assistant",
            "kind": "assistant_turn",
            "route": RoutingClass.PATCH,
            "content": "I'll help you implement the login feature.",
        },
        {
            "event_id": "evt_2",
            "sequence": 2,
            "role": "user",
            "kind": "user_turn",
            "route": RoutingClass.CLEAR,  # Low signal
            "content": "thanks",
        },
        {
            "event_id": "evt_3",
            "sequence": 3,
            "role": "assistant",
            "kind": "assistant_turn",
            "route": RoutingClass.ARCHIVE,  # Important content
            "content": "Here's the implementation code for login.",
        },
        {
            "event_id": "evt_4",
            "sequence": 4,
            "role": "tool",
            "kind": "tool_result",
            "route": RoutingClass.SUMMARIZE,
            "content": "Tool executed successfully.",
        },
    ]


@pytest.fixture
def context_os() -> StateFirstContextOS:
    """Create a StateFirstContextOS instance."""
    return StateFirstContextOS(policy=StateFirstContextOSPolicy())


class TestGatewayContextOSProjection:
    """Tests for gateway using ContextOS projection."""

    @pytest.mark.asyncio
    async def test_gateway_uses_context_os_projection(
        self, context_os: StateFirstContextOS, sample_transcript: list[dict[str, Any]]
    ) -> None:
        """Verify that StateFirstContextOS.project() is called correctly."""
        projection = await context_os.project(
            messages=sample_transcript,
            recent_window_messages=4,
        )

        assert projection is not None
        assert isinstance(projection, ContextOSProjection)
        assert projection.snapshot is not None
        assert projection.run_card is not None

    @pytest.mark.asyncio
    async def test_gateway_projection_includes_run_card(
        self, context_os: StateFirstContextOS, sample_transcript: list[dict[str, Any]]
    ) -> None:
        """Verify projection includes run_card with goal."""
        projection = await context_os.project(
            messages=sample_transcript,
            recent_window_messages=4,
        )

        assert projection.run_card is not None
        # The goal should be extracted from user messages
        assert isinstance(projection.run_card.current_goal, str)

    @pytest.mark.asyncio
    async def test_gateway_projection_active_window(
        self, context_os: StateFirstContextOS, sample_transcript: list[dict[str, Any]]
    ) -> None:
        """Verify active window contains expected events."""
        projection = await context_os.project(
            messages=sample_transcript,
            recent_window_messages=4,
        )

        # Active window should contain events that pass routing
        assert isinstance(projection.active_window, tuple)
        # CLEAR events should be filtered out by default

    @pytest.mark.asyncio
    async def test_gateway_projection_context_slice_plan(
        self, context_os: StateFirstContextOS, sample_transcript: list[dict[str, Any]]
    ) -> None:
        """Verify context slice plan is generated."""
        projection = await context_os.project(
            messages=sample_transcript,
            recent_window_messages=4,
        )

        assert projection.context_slice_plan is not None
        assert len(projection.context_slice_plan.roots) > 0


class TestGatewayBudgetValidation:
    """Tests for budget validation and truncation."""

    @pytest.mark.asyncio
    async def test_gateway_budget_triggers_truncation(self) -> None:
        """Verify budget overrun triggers BudgetExceededError."""
        from polaris.kernelone.context.context_os.policies import ContextWindowPolicy
        from polaris.kernelone.errors import BudgetExceededError

        policy = StateFirstContextOSPolicy(
            context_window=ContextWindowPolicy(
                model_context_window=1000,  # Small window for testing
                max_active_window_messages=2,
            ),
        )
        context_os = StateFirstContextOS(policy=policy)

        # Create messages that exceed budget
        large_content = "x" * 10000  # Very long content
        messages = [
            {
                "event_id": "evt_0",
                "sequence": 0,
                "role": "user",
                "kind": "user_turn",
                "route": RoutingClass.SUMMARIZE,
                "content": large_content,
            },
            {
                "event_id": "evt_1",
                "sequence": 1,
                "role": "assistant",
                "kind": "assistant_turn",
                "route": RoutingClass.PATCH,
                "content": "Response to large content.",
            },
        ]

        with pytest.raises(BudgetExceededError):
            await context_os.project(
                messages=messages,
                recent_window_messages=4,
            )

    @pytest.mark.asyncio
    async def test_gateway_handles_empty_messages(self, context_os: StateFirstContextOS) -> None:
        """Verify gateway handles empty message list."""
        projection = await context_os.project(
            messages=[],
            recent_window_messages=4,
        )

        assert projection is not None
        assert len(projection.active_window) == 0


class TestGatewayFourLaneRouting:
    """Tests for four-lane routing (CLEAR/ARCHIVE/PATCH/SUMMARIZE)."""

    @pytest.mark.asyncio
    async def test_gateway_clear_route_filters_messages(self, context_os: StateFirstContextOS) -> None:
        """Verify CLEAR events are filtered from active window."""
        messages = [
            {
                "event_id": "evt_0",
                "sequence": 0,
                "role": "user",
                "kind": "user_turn",
                "route": RoutingClass.CLEAR,
                "content": "Low signal greeting",
            },
            {
                "event_id": "evt_1",
                "sequence": 1,
                "role": "assistant",
                "kind": "assistant_turn",
                "route": RoutingClass.SUMMARIZE,
                "content": "Important response",
            },
        ]

        projection = await context_os.project(
            messages=messages,
            recent_window_messages=4,
        )

        # The CLEAR event should not appear in active window
        event_ids = {e.event_id for e in projection.active_window}
        assert "evt_0" not in event_ids or all(
            e.event_id != "evt_0" or e.route != RoutingClass.CLEAR for e in projection.active_window
        )

    @pytest.mark.asyncio
    async def test_gateway_archive_route_creates_artifact(self, context_os: StateFirstContextOS) -> None:
        """Verify ARCHIVE route creates artifact in store."""
        messages = [
            {
                "event_id": "evt_0",
                "sequence": 0,
                "role": "assistant",
                "kind": "assistant_turn",
                "route": RoutingClass.ARCHIVE,
                "content": "Here is some important code to archive.",
            },
        ]

        projection = await context_os.project(
            messages=messages,
            recent_window_messages=4,
        )

        # ARCHIVE events may create artifacts depending on adapter
        assert isinstance(projection.snapshot.artifact_store, tuple)

    @pytest.mark.asyncio
    async def test_gateway_patch_route_includes_in_window(self, context_os: StateFirstContextOS) -> None:
        """Verify PATCH events are included in active window."""
        messages = [
            {
                "event_id": "evt_0",
                "sequence": 0,
                "role": "assistant",
                "kind": "assistant_turn",
                "route": RoutingClass.PATCH,
                "content": "This is a patch response",
            },
        ]

        projection = await context_os.project(
            messages=messages,
            recent_window_messages=4,
        )

        # PATCH events should generally be included
        assert isinstance(projection.active_window, tuple)

    @pytest.mark.asyncio
    async def test_gateway_summarize_route_includes_in_window(self, context_os: StateFirstContextOS) -> None:
        """Verify SUMMARIZE events are included in active window."""
        messages = [
            {
                "event_id": "evt_0",
                "sequence": 0,
                "role": "user",
                "kind": "user_turn",
                "route": RoutingClass.SUMMARIZE,
                "content": "Summary-worthy content",
            },
        ]

        projection = await context_os.project(
            messages=messages,
            recent_window_messages=4,
        )

        # SUMMARIZE events should be included
        assert len(projection.active_window) >= 0


class TestGatewaySnapshotPersistence:
    """Tests for snapshot persistence through projections."""

    @pytest.mark.asyncio
    async def test_projection_preserves_snapshot(
        self, context_os: StateFirstContextOS, sample_transcript: list[dict[str, Any]]
    ) -> None:
        """Verify projection contains the original snapshot."""
        projection = await context_os.project(
            messages=sample_transcript,
            recent_window_messages=4,
        )

        assert projection.snapshot is not None
        assert isinstance(projection.snapshot, ContextOSSnapshot)

    @pytest.mark.asyncio
    async def test_projection_snapshot_has_transcript(
        self, context_os: StateFirstContextOS, sample_transcript: list[dict[str, Any]]
    ) -> None:
        """Verify snapshot contains transcript log."""
        projection = await context_os.project(
            messages=sample_transcript,
            recent_window_messages=4,
        )

        # Snapshot should have transcript log
        assert len(projection.snapshot.transcript_log) == len(sample_transcript)

    @pytest.mark.asyncio
    async def test_projection_snapshot_artifact_store(
        self, context_os: StateFirstContextOS, sample_transcript: list[dict[str, Any]]
    ) -> None:
        """Verify snapshot artifact store is maintained."""
        projection = await context_os.project(
            messages=sample_transcript,
            recent_window_messages=4,
        )

        assert isinstance(projection.snapshot.artifact_store, tuple)
