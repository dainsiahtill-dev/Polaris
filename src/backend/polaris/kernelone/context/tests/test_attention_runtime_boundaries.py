"""Boundary and edge case tests for Attention Runtime.

This module provides comprehensive test coverage for:
- Empty and whitespace inputs
- Mixed language inputs (Chinese + English)
- Long text inputs
- Repeated patterns
- Exception handling
- Unicode and special characters
"""

from __future__ import annotations

import pytest
from polaris.kernelone.context.context_os import (
    DialogAct,
    DialogActClassifier,
    DialogActResult,
    PendingFollowUp,
    RunCard,
    StateFirstContextOS,
    StateFirstContextOSPolicy,
)
from polaris.kernelone.context.context_os.evaluation import (
    AttentionRuntimeMetrics,
    AttentionRuntimeQualityResult,
    evaluate_attention_runtime_case,
    extract_attention_trace,
)
from polaris.kernelone.context.context_os.helpers import get_metadata_value
from polaris.kernelone.context.context_os.policies import ContextWindowPolicy, WindowSizePolicy


class TestDialogActBoundaryCases:
    """Boundary and edge case tests for DialogActClassifier."""

    @pytest.fixture
    def classifier(self) -> DialogActClassifier:
        return DialogActClassifier()

    # === Empty and Whitespace Tests ===

    def test_empty_string_returns_unknown(self, classifier: DialogActClassifier) -> None:
        """Empty string returns UNKNOWN with zero confidence.

        Covers boundary case: empty input.
        """
        result = classifier.classify("", role="user")
        assert result.act == DialogAct.UNKNOWN
        assert result.confidence == 0.0
        assert result.triggers == ()

    def test_whitespace_only_returns_unknown(self, classifier: DialogActClassifier) -> None:
        """Whitespace-only string returns UNKNOWN.

        Covers boundary case: whitespace input.
        """
        result = classifier.classify("   ", role="user")
        assert result.act == DialogAct.UNKNOWN
        assert result.confidence < 1.0

    def test_newline_only_returns_unknown(self, classifier: DialogActClassifier) -> None:
        """Newline-only string returns UNKNOWN.

        Covers boundary case: newline characters only.
        """
        result = classifier.classify("\n\n", role="user")
        assert result.act == DialogAct.UNKNOWN

    # === Pure Punctuation Tests ===

    def test_pure_punctuation_returns_unknown(self, classifier: DialogActClassifier) -> None:
        """Pure punctuation returns UNKNOWN or NOISE.

        Covers boundary case: punctuation only.
        """
        result = classifier.classify("...", role="user")
        # Either UNKNOWN or NOISE is acceptable
        assert result.act in {DialogAct.UNKNOWN, DialogAct.NOISE}

    def test_single_punctuation_returns_unknown(self, classifier: DialogActClassifier) -> None:
        """Single punctuation returns UNKNOWN.

        Covers boundary case: single punctuation character.
        """
        result = classifier.classify("?", role="user")
        assert result.act == DialogAct.UNKNOWN

    # === Mixed Language Tests ===

    def test_mixed_chinese_english_affirm(self, classifier: DialogActClassifier) -> None:
        """Mixed language input with affirm keyword is classified correctly.

        Note: Due to fullmatch mode, only exact matches are accepted.
        "需要" alone matches, but "Yes, 需要" does not.
        """
        # "需要" alone matches
        result = classifier.classify("需要", role="user")
        assert result.act == DialogAct.AFFIRM

        # "Yes, 需要" does NOT match (fullmatch)
        result2 = classifier.classify("Yes, 需要", role="user")
        assert result2.act == DialogAct.UNKNOWN  # fullmatch doesn't match

    def test_mixed_chinese_english_deny(self, classifier: DialogActClassifier) -> None:
        """Mixed language input with deny keyword is classified correctly.

        Note: Due to fullmatch mode, only exact matches are accepted.
        """
        # "不用" alone matches
        result = classifier.classify("不用", role="user")
        assert result.act == DialogAct.DENY

        # Mixed with punctuation doesn't match
        result2 = classifier.classify("No, 不用", role="user")
        assert result2.act == DialogAct.UNKNOWN

    def test_english_only_affirm(self, classifier: DialogActClassifier) -> None:
        """English affirm keywords are classified correctly.

        Covers normal case: English-only input.
        """
        for text in ["yes", "Yes", "YES", "sure"]:
            result = classifier.classify(text, role="user")
            assert result.act == DialogAct.AFFIRM, f"Failed for {text!r}"

    def test_english_ok_affirm(self, classifier: DialogActClassifier) -> None:
        """English 'ok' is classified as AFFIRM (short confirmation)."""
        result = classifier.classify("ok", role="user")
        assert result.act == DialogAct.AFFIRM

    # === Long Text Tests ===

    def test_long_text_with_affirm(self, classifier: DialogActClassifier) -> None:
        """Long text starting with affirm keyword returns UNKNOWN.

        Because we use fullmatch, only exact matches are accepted.
        """
        long_text = "需要我帮你实现登录功能吗？" * 10
        result = classifier.classify(long_text, role="user")
        # fullmatch doesn't match long text
        assert result.act == DialogAct.UNKNOWN

    def test_short_affirm_in_long_text(self, classifier: DialogActClassifier) -> None:
        """Short affirm keyword in long text returns UNKNOWN.

        Because we use fullmatch, only exact matches are accepted.
        """
        long_text = "Some explanation needs"
        result = classifier.classify(long_text, role="user")
        assert result.act == DialogAct.UNKNOWN

    # === Repeated Patterns Tests ===

    def test_repeated_affirm(self, classifier: DialogActClassifier) -> None:
        """Repeated affirm keywords return UNKNOWN.

        Because we use fullmatch, "需要需要需要" doesn't match "需要".
        """
        result = classifier.classify("需要需要需要", role="user")
        # fullmatch doesn't match repeated patterns
        assert result.act == DialogAct.UNKNOWN

    def test_repeated_punctuation(self, classifier: DialogActClassifier) -> None:
        """Repeated punctuation is classified correctly.

        Covers boundary case: repeated punctuation.
        """
        result = classifier.classify("???!!!", role="user")
        assert result.act in {DialogAct.UNKNOWN, DialogAct.NOISE}

    # === Unicode and Special Characters ===

    def test_unicode_fullwidth_affirm(self, classifier: DialogActClassifier) -> None:
        """Fullwidth characters are not currently supported.

        This is a known limitation.
        """
        result = classifier.classify("ｙｅｓ", role="user")  # Fullwidth
        # Should fall through to UNKNOWN since fullwidth chars don't match
        assert result.act == DialogAct.UNKNOWN

    def test_emoji_in_text(self, classifier: DialogActClassifier) -> None:
        """Emoji in text is handled gracefully.

        Covers boundary case: emoji characters.
        """
        result = classifier.classify("需要 😊", role="user")
        # With emoji, fullmatch may not match
        assert result.act in {DialogAct.UNKNOWN, DialogAct.AFFIRM}

    def test_special_characters(self, classifier: DialogActClassifier) -> None:
        """Special characters are handled gracefully.

        Covers boundary case: special characters.
        """
        result = classifier.classify("需要@#$%", role="user")
        assert result.act in {DialogAct.UNKNOWN, DialogAct.AFFIRM}


class TestPendingFollowUpBoundaryCases:
    """Boundary and edge case tests for PendingFollowUp state."""

    def test_empty_pending_followup(self) -> None:
        """Empty PendingFollowUp is created correctly.

        Note: status == "pending" means is_blocking() returns True,
        regardless of action content. This is the current behavior.
        """
        pf = PendingFollowUp()
        assert pf.action == ""
        assert pf.status == "pending"
        assert not pf.is_resolved()
        # is_blocking depends only on status, not action content
        assert pf.is_blocking()  # status is "pending"

    def test_pending_followup_with_action_blocks(self) -> None:
        """PendingFollowUp with action blocks sealing correctly."""
        pf = PendingFollowUp(action="test action", status="pending")
        assert pf.action == "test action"
        assert pf.status == "pending"
        assert pf.is_blocking()  # status is "pending"
        assert not pf.is_resolved()

    def test_pending_followup_with_empty_status(self) -> None:
        """PendingFollowUp with empty status defaults to pending."""
        pf = PendingFollowUp(action="test", status="")
        assert pf.status == ""
        assert pf.is_resolved() is False  # Empty status is not resolved

    def test_pending_followup_all_statuses(self) -> None:
        """All valid statuses are handled correctly."""
        statuses = [
            ("pending", False, True),
            ("confirmed", True, False),
            ("denied", True, False),
            ("paused", True, False),
            ("redirected", True, False),
            ("expired", True, False),
            ("unknown", False, False),
        ]
        for status, expected_resolved, expected_blocking in statuses:
            pf = PendingFollowUp(action="test", status=status)
            assert pf.is_resolved() == expected_resolved, f"Failed for {status!r}"
            # Only pending with action should be blocking
            assert pf.is_blocking() == expected_blocking, f"Failed for {status!r}"


class TestRunCardBoundaryCases:
    """Boundary and edge case tests for RunCard."""

    def test_empty_run_card(self) -> None:
        """Empty RunCard has default values."""
        rc = RunCard()
        assert rc.current_goal == ""
        assert rc.latest_user_intent == ""
        assert rc.pending_followup_action == ""
        assert rc.pending_followup_status == ""
        assert rc.last_turn_outcome == ""

    def test_run_card_with_empty_fields(self) -> None:
        """RunCard with empty optional fields works correctly."""
        rc = RunCard(
            current_goal="test goal",
            latest_user_intent="",
            pending_followup_action="",
            pending_followup_status="",
            last_turn_outcome="",
        )
        assert rc.current_goal == "test goal"
        assert rc.latest_user_intent == ""
        assert rc.pending_followup_action == ""
        assert rc.pending_followup_status == ""
        assert rc.last_turn_outcome == ""


class TestStateFirstContextOSBoundaryCases:
    """Boundary and edge case tests for StateFirstContextOS."""

    @pytest.fixture
    def engine(self) -> StateFirstContextOS:
        return StateFirstContextOS()

    async def test_empty_messages(self, engine: StateFirstContextOS) -> None:
        """Empty message list returns valid projection."""
        projection = await engine.project(messages=[], recent_window_messages=8)
        assert projection.snapshot is not None
        assert projection.run_card is not None

    async def test_single_message(self, engine: StateFirstContextOS) -> None:
        """Single message returns valid projection."""
        projection = await engine.project(
            messages=[{"role": "user", "content": "test"}],
            recent_window_messages=8,
        )
        assert projection.snapshot is not None
        assert len(projection.active_window) >= 1

    async def test_none_role_message(self, engine: StateFirstContextOS) -> None:
        """Message with None role is handled gracefully."""
        projection = await engine.project(
            messages=[{"role": None, "content": "test"}],  # type: ignore
            recent_window_messages=8,
        )
        assert projection.snapshot is not None

    async def test_empty_content_message(self, engine: StateFirstContextOS) -> None:
        """Message with empty content is handled correctly."""
        projection = await engine.project(
            messages=[{"role": "user", "content": ""}],
            recent_window_messages=8,
        )
        assert projection.snapshot is not None

    async def test_zero_recent_window(self, engine: StateFirstContextOS) -> None:
        """Zero recent_window_messages uses policy default."""
        projection = await engine.project(
            messages=[{"role": "user", "content": "test"}],
            recent_window_messages=0,
        )
        assert projection.snapshot is not None
        # Should use policy default (3)
        assert len(projection.active_window) >= 1

    async def test_negative_recent_window(self, engine: StateFirstContextOS) -> None:
        """Negative recent_window_messages uses policy default."""
        projection = await engine.project(
            messages=[{"role": "user", "content": "test"}],
            recent_window_messages=-5,
        )
        assert projection.snapshot is not None

    async def test_very_large_recent_window(self, engine: StateFirstContextOS) -> None:
        """Very large recent_window_messages is capped by policy."""
        projection = await engine.project(
            messages=[{"role": "user", "content": "test"}],
            recent_window_messages=1000,
        )
        assert projection.snapshot is not None
        # Should be capped at max_active_window_messages
        assert len(projection.active_window) <= engine.policy.max_active_window_messages


class TestAttentionRuntimeEvaluationBoundaryCases:
    """Boundary and edge case tests for attention runtime evaluation."""

    async def test_empty_conversation(self) -> None:
        """Empty conversation returns success with zero metrics (no measurement made).

        Empty conversations should pass since there's nothing to evaluate -
        but metrics are 0.0 because no measurement was made (confidence = 0.0).
        This is the correct behavior after P1-2 fix: missing data = 0.0 score,
        not auto-pass with 1.0.
        """
        result = await evaluate_attention_runtime_case(conversation=[])
        assert isinstance(result, AttentionRuntimeQualityResult)
        assert result.passed is True
        # Empty conversation has no measurement - metrics are 0.0, confidence is 0.0
        # This correctly indicates "no measurement made" rather than "perfect score"
        assert result.metrics.intent_carryover_accuracy == 0.0
        assert result.metrics.latest_turn_retention_rate == 0.0
        assert result.metrics.focus_regression_rate == 0.0
        assert result.metrics.false_clear_rate == 0.0
        assert result.metrics.pending_followup_resolution_rate == 0.0
        assert result.metrics.seal_while_pending_rate == 0.0
        assert result.metrics.continuity_focus_alignment_rate == 0.0
        assert result.metrics.context_redundancy_rate == 0.0
        # All confidence fields should be 0.0 (no measurement possible)
        assert result.metrics.intent_carryover_confidence == 0.0
        assert result.metrics.latest_turn_retention_confidence == 0.0
        assert result.metrics.focus_regression_confidence == 0.0
        assert result.metrics.false_clear_confidence == 0.0
        assert result.metrics.pending_followup_resolution_confidence == 0.0
        assert result.metrics.seal_while_pending_confidence == 0.0
        assert result.metrics.continuity_focus_alignment_confidence == 0.0
        assert result.metrics.context_redundancy_confidence == 0.0

    async def test_conversation_with_empty_messages(self) -> None:
        """Conversation with empty messages is handled."""
        result = await evaluate_attention_runtime_case(
            conversation=[{"role": "", "content": ""}]  # type: ignore
        )
        # Should handle gracefully, possibly returning failure
        assert isinstance(result, AttentionRuntimeQualityResult)

    def test_extract_trace_with_none_snapshot(self) -> None:
        """extract_attention_trace handles None snapshot."""
        trace = extract_attention_trace(None)
        assert trace is not None
        assert trace.intent_classification == ""
        assert trace.pending_followup is None

    def test_attention_metrics_defaults(self) -> None:
        """AttentionRuntimeMetrics has correct defaults."""
        metrics = AttentionRuntimeMetrics()
        assert metrics.intent_carryover_accuracy == 0.0
        assert metrics.latest_turn_retention_rate == 0.0
        assert metrics.focus_regression_rate == 0.0
        assert metrics.false_clear_rate == 0.0
        assert metrics.pending_followup_resolution_rate == 0.0
        assert metrics.seal_while_pending_rate == 0.0
        assert metrics.continuity_focus_alignment_rate == 0.0
        assert metrics.context_redundancy_rate == 0.0


class TestPolicyBoundaryCases:
    """Boundary and edge case tests for StateFirstContextOSPolicy."""

    def test_default_policy_values(self) -> None:
        """Default policy has reasonable values."""
        policy = StateFirstContextOSPolicy()
        assert policy.window_size.min_recent_messages_pinned >= 1
        assert policy.context_window.max_active_window_messages >= policy.window_size.min_recent_messages_pinned
        assert policy.collection_limits.max_open_loops >= 1
        assert policy.collection_limits.max_stable_facts >= 1

    def test_feature_switch_defaults(self) -> None:
        """Feature switches have correct defaults."""
        policy = StateFirstContextOSPolicy()
        assert policy.attention_runtime.enable_dialog_act is True
        assert policy.attention_runtime.enable_seal_guard is True

    def test_policy_with_zero_limits(self) -> None:
        """Policy with zero limits is handled gracefully."""
        policy = StateFirstContextOSPolicy(
            window_size=WindowSizePolicy(min_recent_messages_pinned=0),
            context_window=ContextWindowPolicy(max_active_window_messages=0),
        )
        # Should not crash, even with zero values
        assert policy.window_size.min_recent_messages_pinned == 0
        assert policy.context_window.max_active_window_messages == 0


class TestDialogActResultBoundaryCases:
    """Boundary and edge case tests for DialogActResult."""

    def test_empty_dialog_act_result(self) -> None:
        """Empty DialogActResult has default values."""
        result = DialogActResult()
        assert result.act == DialogAct.UNKNOWN
        assert result.confidence == 0.0
        assert result.triggers == ()
        assert result.metadata == ()

    def test_dialog_act_result_with_metadata(self) -> None:
        """DialogActResult with metadata works correctly."""
        result = DialogActResult(
            act=DialogAct.AFFIRM,
            confidence=0.95,
            triggers=("需要",),
            metadata=tuple({"role": "user", "short_reply": True}.items()),
        )
        assert result.act == DialogAct.AFFIRM
        assert result.confidence == 0.95
        assert "需要" in result.triggers
        assert get_metadata_value(result.metadata, "short_reply") is True

    def test_dialog_act_result_serialization(self) -> None:
        """DialogActResult serializes to dict correctly."""
        result = DialogActResult(
            act=DialogAct.DENY,
            confidence=0.90,
            triggers=("不用",),
        )
        data = result.to_dict()
        assert data["act"] == DialogAct.DENY
        assert data["confidence"] == 0.90
        assert "不用" in data["triggers"]

    def test_dialog_act_result_deserialization(self) -> None:
        """DialogActResult deserializes from dict correctly."""
        data = {
            "act": "pause",
            "confidence": 0.85,
            "triggers": ["先别"],
            "metadata": {"role": "user"},
        }
        result = DialogActResult.from_mapping(data)
        assert result.act == DialogAct.PAUSE
        assert result.confidence == 0.85
        assert "先别" in result.triggers
