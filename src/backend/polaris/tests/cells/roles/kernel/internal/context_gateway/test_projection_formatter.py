"""Tests for ProjectionFormatter — context projection to LLM message formatting.

Coverage targets:
- All 9 public methods (7 static/classmethod + 2 class methods)
- Pure string assertions on formatter output
- Edge cases: empty input, missing fields, deduplication, priority sorting
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest
from polaris.cells.roles.kernel.internal.context_gateway.projection_formatter import (
    ProjectionFormatter,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def sample_snapshot() -> dict[str, Any]:
    """Return a sample ContextOS snapshot dict."""
    return {
        "transcript_log": [
            {
                "role": "user",
                "content": "Hello there",
                "event_id": "evt_001_abc123",
                "sequence": 1,
                "metadata": {"route": "patch", "dialog_act": "affirm"},
            },
            {
                "role": "assistant",
                "content": "General Kenobi",
                "event_id": "evt_002_def456",
                "sequence": 2,
                "metadata": {"route": "summarize", "dialog_act": "clarify"},
            },
            {
                "role": "tool_result",
                "content": '{"result": 42}',
                "event_id": "evt_003_ghi789",
                "sequence": 3,
                "metadata": {"route": "archive", "dialog_act": "deny"},
            },
        ],
        "working_state": {"current_task": "test-formatter"},
        "artifact_store": [{"id": "art1"}, {"id": "art2"}],
        "pending_followup": {"description": "Follow up on formatter tests"},
    }


@pytest.fixture
def minimal_snapshot() -> dict[str, Any]:
    """Return a snapshot with minimal / empty fields."""
    return {
        "transcript_log": [],
        "working_state": {},
        "artifact_store": [],
        "pending_followup": {},
    }


@pytest.fixture
def mock_receipt() -> MagicMock:
    """Return a mock StrategyReceipt."""
    receipt = MagicMock()
    receipt.bundle_id = "bundle-42"
    receipt.profile_id = "profile-7"
    receipt.turn_index = 3
    receipt.budget_decisions = []
    receipt.tool_sequence = ["read", "write"]
    receipt.exploration_phase_reached = "phase_2"
    receipt.cache_hits = ("hit1",)
    receipt.cache_misses = ("miss1", "miss2")
    receipt.compaction_triggered = True
    return receipt


@pytest.fixture
def mock_budget_decision() -> MagicMock:
    """Return a mock BudgetDecision."""
    bd = MagicMock()
    bd.kind.value = "tool"
    bd.decision = "allow"
    bd.estimated_tokens = 150
    bd.headroom_after = 850
    return bd


# ---------------------------------------------------------------------------
# 1. format_strategy_receipt_style
# ---------------------------------------------------------------------------


def test_format_strategy_receipt_none() -> None:
    """format_strategy_receipt_style(None) must emit the unavailable marker."""
    result = ProjectionFormatter.format_strategy_receipt_style(None)
    assert result == "【Strategy Context】\n(receipt unavailable)"


def test_format_strategy_receipt_basic(mock_receipt: MagicMock) -> None:
    """format_strategy_receipt_style must include bundle, profile, and turn."""
    result = ProjectionFormatter.format_strategy_receipt_style(mock_receipt)
    assert "bundle: bundle-42" in result
    assert "profile: profile-7" in result
    assert "turn: 3" in result


def test_format_strategy_receipt_tool_sequence(mock_receipt: MagicMock) -> None:
    """format_strategy_receipt_style must render the tool sequence arrow."""
    result = ProjectionFormatter.format_strategy_receipt_style(mock_receipt)
    assert "tool_sequence: read → write" in result


def test_format_strategy_receipt_exploration_phase(mock_receipt: MagicMock) -> None:
    """format_strategy_receipt_style must show the exploration phase when present."""
    result = ProjectionFormatter.format_strategy_receipt_style(mock_receipt)
    assert "exploration_phase: phase_2" in result


def test_format_strategy_receipt_cache_stats(mock_receipt: MagicMock) -> None:
    """format_strategy_receipt_style must render cache hit/miss counts."""
    result = ProjectionFormatter.format_strategy_receipt_style(mock_receipt)
    assert "cache_hits: 1, misses: 2" in result


def test_format_strategy_receipt_compaction(mock_receipt: MagicMock) -> None:
    """format_strategy_receipt_style must note compaction when triggered."""
    result = ProjectionFormatter.format_strategy_receipt_style(mock_receipt)
    assert "compaction: triggered this turn" in result


def test_format_strategy_receipt_budget_decisions(mock_receipt: MagicMock, mock_budget_decision: MagicMock) -> None:
    """format_strategy_receipt_style must list budget decisions up to the first 3."""
    mock_receipt.budget_decisions = [mock_budget_decision]
    result = ProjectionFormatter.format_strategy_receipt_style(mock_receipt)
    assert "budget_decisions: 1 decision(s)" in result
    assert "tool: allow (tokens=150, headroom=850)" in result


# ---------------------------------------------------------------------------
# 2. format_context_os_snapshot
# ---------------------------------------------------------------------------


def test_format_snapshot_summary(sample_snapshot: dict[str, Any]) -> None:
    """format_context_os_snapshot(summary) must include transcript count and working state."""
    result = ProjectionFormatter.format_context_os_snapshot(sample_snapshot, verbosity="summary")
    assert result.startswith("【Context OS State】")
    assert "transcript_events: 3 event(s)" in result
    assert "current_task: test-formatter" in result
    assert "artifacts: 2 record(s)" in result
    assert "pending_followup: Follow up on formatter tests" in result


def test_format_snapshot_debug_shows_full_transcript(sample_snapshot: dict[str, Any]) -> None:
    """format_context_os_snapshot(debug) must emit per-event metadata lines."""
    result = ProjectionFormatter.format_context_os_snapshot(sample_snapshot, verbosity="debug")
    assert "[seq=1] user" in result
    assert "route=patch" in result
    assert "act=affirm" in result
    assert "content: Hello there" in result


def test_format_snapshot_empty_transcript(minimal_snapshot: dict[str, Any]) -> None:
    """format_context_os_snapshot with empty transcript must emit the empty marker."""
    result = ProjectionFormatter.format_context_os_snapshot(minimal_snapshot)
    assert "transcript_events: (empty)" in result


def test_format_snapshot_no_artifacts(sample_snapshot: dict[str, Any]) -> None:
    """format_context_os_snapshot must not mention artifacts when the store is empty."""
    sample_snapshot["artifact_store"] = []
    result = ProjectionFormatter.format_context_os_snapshot(sample_snapshot)
    assert "artifacts:" not in result


def test_format_snapshot_summary_last_five_only(sample_snapshot: dict[str, Any]) -> None:
    """In summary mode, only the last 5 transcript events should appear."""
    sample_snapshot["transcript_log"] = [
        {"role": "user", "content": f"msg{i}", "event_id": f"evt{i}", "sequence": i, "metadata": {}} for i in range(10)
    ]
    result = ProjectionFormatter.format_context_os_snapshot(sample_snapshot, verbosity="summary")
    assert "msg5" in result
    assert "msg9" in result
    assert "msg0" not in result


# ---------------------------------------------------------------------------
# 3. expand_transcript_to_messages
# ---------------------------------------------------------------------------


def test_expand_transcript_basic(sample_snapshot: dict[str, Any]) -> None:
    """expand_transcript_to_messages must convert transcript events to role/content dicts."""
    messages = ProjectionFormatter.expand_transcript_to_messages(sample_snapshot)
    assert len(messages) == 3
    assert messages[0] == {"role": "user", "content": "Hello there"}
    assert messages[1] == {"role": "assistant", "content": "General Kenobi"}


def test_expand_transcript_normalizes_tool_result(sample_snapshot: dict[str, Any]) -> None:
    """expand_transcript_to_messages must map 'tool_result' role to 'tool'."""
    messages = ProjectionFormatter.expand_transcript_to_messages(sample_snapshot)
    assert messages[2] == {"role": "tool", "content": '{"result": 42}'}


def test_expand_transcript_skips_empty_events() -> None:
    """expand_transcript_to_messages must skip events with missing role or content."""
    snapshot = {
        "transcript_log": [
            {"role": "", "content": "no-role"},
            {"role": "user", "content": ""},
            {"role": "user", "content": "valid"},
            {"content": "no-role-key"},
        ]
    }
    messages = ProjectionFormatter.expand_transcript_to_messages(snapshot)
    assert len(messages) == 1
    assert messages[0] == {"role": "user", "content": "valid"}


def test_expand_transcript_empty_input() -> None:
    """expand_transcript_to_messages with no transcript_log must return []."""
    assert ProjectionFormatter.expand_transcript_to_messages({}) == []
    assert ProjectionFormatter.expand_transcript_to_messages({"transcript_log": []}) == []


# ---------------------------------------------------------------------------
# 4. dedupe_messages
# ---------------------------------------------------------------------------


def test_dedupe_messages_removes_duplicates() -> None:
    """dedupe_messages must keep the first occurrence and drop later duplicates."""
    messages = [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "hi"},
        {"role": "user", "content": "hello"},
    ]
    result = ProjectionFormatter.dedupe_messages(messages)
    assert len(result) == 2
    assert result[0] == {"role": "user", "content": "hello"}
    assert result[1] == {"role": "assistant", "content": "hi"}


def test_dedupe_messages_distinguishes_different_roles() -> None:
    """dedupe_messages must treat same content with different roles as unique."""
    messages = [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "hello"},
    ]
    result = ProjectionFormatter.dedupe_messages(messages)
    assert len(result) == 2


def test_dedupe_messages_empty() -> None:
    """dedupe_messages with an empty list must return []."""
    assert ProjectionFormatter.dedupe_messages([]) == []


# ---------------------------------------------------------------------------
# 5. dialog_act_priority
# ---------------------------------------------------------------------------


def test_dialog_act_priority_high_value() -> None:
    """dialog_act_priority must return 2 for high-priority dialog acts."""
    event = MagicMock()
    event.metadata = (("dialog_act", "affirm"),)
    assert ProjectionFormatter.dialog_act_priority(event) == 2


def test_dialog_act_priority_no_metadata() -> None:
    """dialog_act_priority must return 0 when metadata is absent."""
    event = MagicMock()
    event.metadata = None
    assert ProjectionFormatter.dialog_act_priority(event) == 0


def test_dialog_act_priority_low_value() -> None:
    """dialog_act_priority must return 0 for non-high-priority acts."""
    event = MagicMock()
    event.metadata = (("dialog_act", "greeting"),)
    assert ProjectionFormatter.dialog_act_priority(event) == 0


# ---------------------------------------------------------------------------
# 6. sort_events_by_routing_priority
# ---------------------------------------------------------------------------


def test_sort_events_by_routing_priority_empty() -> None:
    """sort_events_by_routing_priority with an empty tuple must return []."""
    assert ProjectionFormatter.sort_events_by_routing_priority(()) == []


def test_sort_events_patch_before_clear() -> None:
    """PATCH events must sort before CLEAR events."""
    events = (
        MagicMock(sequence=1, route="clear", metadata=()),
        MagicMock(sequence=2, route="patch", metadata=()),
    )
    sorted_events = ProjectionFormatter.sort_events_by_routing_priority(events)
    assert [e.route for e in sorted_events] == ["clear", "patch"]


def test_sort_events_confidence_tiebreaker() -> None:
    """Within the same route, higher confidence must sort earlier."""
    events = (
        MagicMock(sequence=1, route="patch", metadata=(("routing_confidence", 0.3),)),
        MagicMock(sequence=1, route="patch", metadata=(("routing_confidence", 0.9),)),
    )
    sorted_events = ProjectionFormatter.sort_events_by_routing_priority(events)
    confidences = [float(e.metadata[0][1]) for e in sorted_events]
    assert confidences == [0.9, 0.3]


# ---------------------------------------------------------------------------
# 7. messages_from_projection
# ---------------------------------------------------------------------------


def test_messages_from_projection_head_tail_anchors() -> None:
    """messages_from_projection must include head_anchor and tail_anchor as system messages."""
    projection = MagicMock()
    projection.head_anchor = "HEAD"
    projection.tail_anchor = "TAIL"
    projection.active_window = ()
    projection.run_card = None
    messages = ProjectionFormatter.messages_from_projection(projection)
    roles = [m["role"] for m in messages]
    assert roles.count("system") == 2
    contents = [m["content"] for m in messages]
    assert "HEAD" in contents
    assert "TAIL" in contents


def test_messages_from_projection_run_card() -> None:
    """messages_from_projection must append the run card as a system message."""
    projection = MagicMock()
    projection.head_anchor = ""
    projection.tail_anchor = ""
    projection.active_window = ()
    run_card = MagicMock()
    run_card.current_goal = "Finish tests"
    run_card.open_loops = ("loop1",)
    run_card.latest_user_intent = "run formatter tests"
    run_card.pending_followup_action = "assert output"
    run_card.last_turn_outcome = "pass"
    projection.run_card = run_card
    messages = ProjectionFormatter.messages_from_projection(projection)
    run_card_msg = [m for m in messages if m.get("name") == "run_card"]
    assert len(run_card_msg) == 1
    assert "Finish tests" in run_card_msg[0]["content"]


def test_messages_from_projection_dedupes() -> None:
    """messages_from_projection must deduplicate messages by content hash."""
    projection = MagicMock()
    projection.head_anchor = ""
    projection.tail_anchor = ""
    projection.run_card = None
    evt = MagicMock(
        sequence=1,
        route="patch",
        role="user",
        content="hello",
        metadata=(),
        artifact_id=None,
        event_id="e1",
    )
    projection.active_window = (evt, evt)  # duplicate
    messages = ProjectionFormatter.messages_from_projection(projection)
    assert len(messages) == 1
    assert messages[0]["content"] == "hello"


def test_messages_from_projection_skips_old_clear_events() -> None:
    """messages_from_projection must skip CLEAR events that are not recent or forced."""
    projection = MagicMock()
    projection.head_anchor = ""
    projection.tail_anchor = ""
    projection.run_card = None
    events = (
        MagicMock(sequence=1, route="clear", role="user", content="old", metadata=(), artifact_id=None, event_id="e1"),
        MagicMock(
            sequence=10, route="clear", role="user", content="recent", metadata=(), artifact_id=None, event_id="e2"
        ),
    )
    projection.active_window = events
    messages = ProjectionFormatter.messages_from_projection(projection)
    contents = [m["content"] for m in messages]
    assert "old" not in contents
    assert "recent" in contents


def test_messages_from_projection_archive_stub_for_old() -> None:
    """ARCHIVE events that are not recent must use a stub content."""
    projection = MagicMock()
    projection.head_anchor = ""
    projection.tail_anchor = ""
    projection.run_card = None
    events = (
        MagicMock(
            sequence=1,
            route="archive",
            role="user",
            content="big content",
            metadata=(),
            artifact_id="art1",
            event_id="e1",
        ),
        MagicMock(
            sequence=10,
            route="archive",
            role="user",
            content="recent content",
            metadata=(),
            artifact_id="art2",
            event_id="e2",
        ),
    )
    projection.active_window = events
    messages = ProjectionFormatter.messages_from_projection(projection)
    contents = [m["content"] for m in messages]
    assert "big content" not in contents
    assert "[Artifact stored: art1]" in contents
    assert "recent content" in contents


def test_messages_from_projection_preserves_patch_content() -> None:
    """PATCH events must retain full content and metadata."""
    projection = MagicMock()
    projection.head_anchor = ""
    projection.tail_anchor = ""
    projection.run_card = None
    evt = MagicMock(
        sequence=1,
        route="patch",
        role="assistant",
        content="patch content",
        metadata=(("route", "patch"),),
        artifact_id=None,
        event_id="e1",
    )
    projection.active_window = (evt,)
    messages = ProjectionFormatter.messages_from_projection(projection)
    assert len(messages) == 1
    assert messages[0]["content"] == "patch content"
    assert messages[0]["metadata"]["route"] == "patch"
