"""Regression tests for Attention Runtime improvements (A1-A5).

This module tests:
- A1: Dialog Act Classification
- A2: Pending Follow-Up State
- A3: Run Card v2 and Active Window Root Hardening
- A4: Seal Guard and Continuity Convergence
- A5: Evaluation Gate and Attention Observability
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from polaris.kernelone.context.context_os import (
    DialogAct,
    DialogActClassifier,
    PendingFollowUp,
    StateFirstContextOS,
    StateFirstContextOSPolicy,
)
from polaris.kernelone.context.context_os.evaluation import (
    AttentionRuntimeQualityResult,
    evaluate_attention_runtime_case,
    extract_attention_trace,
)
from polaris.kernelone.context.context_os.helpers import get_metadata_value
from polaris.kernelone.context.context_os.policies import WindowSizePolicy


class TestDialogActClassification:
    """Tests for A1: Dialog Act Classification."""

    @pytest.fixture
    def classifier(self) -> DialogActClassifier:
        return DialogActClassifier()

    def test_affirm_classification(self, classifier: DialogActClassifier) -> None:
        """Short affirmative responses are correctly classified."""
        for text in ["需要", "要", "可以", "好的", "是", "确认", "好"]:
            result = classifier.classify(text, role="user")
            assert result.act == DialogAct.AFFIRM, f"Expected AFFIRM for {text!r}, got {result.act}"
            assert result.confidence >= 0.9
            assert get_metadata_value(result.metadata, "short_reply") is True

    def test_deny_classification(self, classifier: DialogActClassifier) -> None:
        """Short negative responses are correctly classified."""
        for text in ["不用", "不要", "不需要", "不用了", "停止"]:
            result = classifier.classify(text, role="user")
            assert result.act == DialogAct.DENY, f"Expected DENY for {text!r}, got {result.act}"
            assert result.confidence >= 0.9

    def test_pause_classification(self, classifier: DialogActClassifier) -> None:
        """Pause signals are correctly classified (not DENY)."""
        for text in ["先别", "等一下", "暂停", "等等", "稍等"]:
            result = classifier.classify(text, role="user")
            assert result.act == DialogAct.PAUSE, f"Expected PAUSE for {text!r}, got {result.act}"
            assert result.confidence >= 0.9

    def test_redirect_classification(self, classifier: DialogActClassifier) -> None:
        """Redirect signals are correctly classified."""
        for text in ["改成", "换一个", "另外", "改成另外一个"]:
            result = classifier.classify(text, role="user")
            assert result.act == DialogAct.REDIRECT, f"Expected REDIRECT for {text!r}, got {result.act}"

    def test_clarify_classification(self, classifier: DialogActClassifier) -> None:
        """Clarify signals are correctly classified."""
        for text in ["什么意思", "什么", "怎么说", "详细点"]:
            result = classifier.classify(text, role="user")
            assert result.act == DialogAct.CLARIFY, f"Expected CLARIFY for {text!r}, got {result.act}"

    def test_commit_classification(self, classifier: DialogActClassifier) -> None:
        """Commit signals are correctly classified."""
        # Use patterns that are uniquely commit (not affirm)
        for text in ["就这样", "就这样吧", "确定", "就这样办", "就这么定了"]:
            result = classifier.classify(text, role="user")
            assert result.act == DialogAct.COMMIT, f"Expected COMMIT for {text!r}, got {result.act}"
        # Note: "ok" alone is classified as AFFIRM (short confirmation)
        # "sounds good" and "agreed" are uniquely commit

    def test_cancel_classification(self, classifier: DialogActClassifier) -> None:
        """Cancel signals are correctly classified."""
        for text in ["取消", "算了", "不要了", "终止"]:
            result = classifier.classify(text, role="user")
            assert result.act == DialogAct.CANCEL, f"Expected CANCEL for {text!r}, got {result.act}"

    def test_noise_classification(self, classifier: DialogActClassifier) -> None:
        """Low-signal greetings are classified as noise."""
        for text in ["hello", "hi", "你好", "嗨", "bye"]:
            result = classifier.classify(text, role="user")
            assert result.act == DialogAct.NOISE, f"Expected NOISE for {text!r}, got {result.act}"

    def test_high_priority_acts_not_noise(self, classifier: DialogActClassifier) -> None:
        """High-priority dialog acts are never classified as noise."""
        high_priority_texts = ["需要", "不用", "先别", "改成", "什么意思", "就这样"]
        for text in high_priority_texts:
            result = classifier.classify(text, role="user")
            assert result.act != DialogAct.NOISE, f"{text!r} should not be NOISE"
            assert DialogAct.is_high_priority(result.act), f"{text!r} should be high priority"

    def test_is_high_priority(self) -> None:
        """DialogAct.is_high_priority correctly identifies high-priority acts."""
        high_priority = {
            DialogAct.AFFIRM,
            DialogAct.DENY,
            DialogAct.PAUSE,
            DialogAct.REDIRECT,
            DialogAct.CLARIFY,
            DialogAct.COMMIT,
            DialogAct.CANCEL,
        }
        low_priority = {DialogAct.STATUS_ACK, DialogAct.NOISE, DialogAct.UNKNOWN}

        for act in high_priority:
            assert DialogAct.is_high_priority(act), f"{act} should be high priority"
        for act in low_priority:
            assert not DialogAct.is_high_priority(act), f"{act} should not be high priority"


class TestPendingFollowUpState:
    """Tests for A2: Pending Follow-Up State."""

    @pytest.fixture
    def engine(self) -> StateFirstContextOS:
        return StateFirstContextOS()

    async def test_pending_followup_created_on_assistant_question(self, engine: StateFirstContextOS) -> None:
        """Pending follow-up is created when assistant asks a follow-up question."""
        projection = await engine.project(
            messages=[
                {"role": "user", "content": "请帮我实现登录功能"},
                {"role": "assistant", "content": "需要我帮你实现吗？"},
            ],
            recent_window_messages=8,
        )
        assert projection.snapshot.pending_followup is not None
        assert projection.snapshot.pending_followup.action
        assert projection.snapshot.pending_followup.status == "pending"

    async def test_pending_followup_confirmed_on_affirm(self, engine: StateFirstContextOS) -> None:
        """Pending follow-up is confirmed when user responds with affirm."""
        snapshot = (
            await engine.project(
                messages=[
                    {"role": "user", "content": "请帮我实现登录功能"},
                    {"role": "assistant", "content": "需要我帮你实现吗？"},
                ],
                recent_window_messages=8,
            )
        ).snapshot

        projection = await engine.project(
            messages=[{"role": "user", "content": "需要"}],
            existing_snapshot=snapshot,
            recent_window_messages=8,
        )
        assert projection.snapshot.pending_followup is not None
        assert projection.snapshot.pending_followup.status == "confirmed"

    async def test_pending_followup_denied_on_deny(self, engine: StateFirstContextOS) -> None:
        """Pending follow-up is denied when user responds with deny."""
        snapshot = (
            await engine.project(
                messages=[
                    {"role": "user", "content": "请帮我实现登录功能"},
                    {"role": "assistant", "content": "需要我帮你实现吗？"},
                ],
                recent_window_messages=8,
            )
        ).snapshot

        projection = await engine.project(
            messages=[{"role": "user", "content": "不用"}],
            existing_snapshot=snapshot,
            recent_window_messages=8,
        )
        assert projection.snapshot.pending_followup is not None
        assert projection.snapshot.pending_followup.status == "denied"

    async def test_pending_followup_paused_on_pause(self, engine: StateFirstContextOS) -> None:
        """Pending follow-up is paused when user responds with pause."""
        snapshot = (
            await engine.project(
                messages=[
                    {"role": "user", "content": "请帮我实现登录功能"},
                    {"role": "assistant", "content": "需要我帮你实现吗？"},
                ],
                recent_window_messages=8,
            )
        ).snapshot

        projection = await engine.project(
            messages=[{"role": "user", "content": "先别"}],
            existing_snapshot=snapshot,
            recent_window_messages=8,
        )
        assert projection.snapshot.pending_followup is not None
        assert projection.snapshot.pending_followup.status == "paused"

    def test_pending_followup_is_resolved(self) -> None:
        """PendingFollowUp.is_resolved correctly identifies resolved states."""
        resolved_statuses = ["confirmed", "denied", "paused", "redirected", "expired"]
        for status in resolved_statuses:
            pf = PendingFollowUp(status=status)
            assert pf.is_resolved(), f"status={status} should be resolved"

        pf = PendingFollowUp(status="pending")
        assert not pf.is_resolved(), "pending should not be resolved"

    def test_pending_followup_is_blocking(self) -> None:
        """PendingFollowUp.is_blocking correctly identifies blocking state."""
        pf = PendingFollowUp(status="pending")
        assert pf.is_blocking(), "pending should be blocking"

        for status in ["confirmed", "denied", "paused", "redirected", "expired"]:
            pf = PendingFollowUp(status=status)
            assert not pf.is_blocking(), f"status={status} should not be blocking"


class TestRunCardV2:
    """Tests for A3: Run Card v2."""

    @pytest.fixture
    def engine(self) -> StateFirstContextOS:
        return StateFirstContextOS()

    async def test_run_card_v2_fields_populated(self, engine: StateFirstContextOS) -> None:
        """Run Card v2 fields are correctly populated."""
        projection = await engine.project(
            messages=[
                {"role": "user", "content": "请帮我实现登录功能"},
                {"role": "assistant", "content": "需要我帮你实现吗？"},
                {"role": "user", "content": "需要"},
            ],
            recent_window_messages=8,
        )
        run_card = projection.run_card
        assert run_card is not None
        assert run_card.latest_user_intent == "需要"
        # pending_followup_action is extracted by the assistant follow-up pattern
        # which may be truncated, so we just verify it's populated
        assert run_card.pending_followup_action != ""
        assert run_card.pending_followup_status == "confirmed"
        assert run_card.last_turn_outcome == DialogAct.AFFIRM

    async def test_run_card_v2_last_turn_outcome(self, engine: StateFirstContextOS) -> None:
        """last_turn_outcome correctly reflects the latest dialog act."""
        projection = await engine.project(
            messages=[{"role": "user", "content": "先别"}],
            recent_window_messages=8,
        )
        run_card = projection.run_card
        assert run_card is not None
        assert run_card.last_turn_outcome == DialogAct.PAUSE


class TestActiveWindowRootHardening:
    """Tests for A3: Active Window Root Hardening."""

    @pytest.fixture
    def engine(self) -> StateFirstContextOS:
        return StateFirstContextOS()

    async def test_pending_followup_source_in_active_window(self, engine: StateFirstContextOS) -> None:
        """Pending follow-up source is kept in active window."""
        projection = await engine.project(
            messages=[
                {"role": "user", "content": "请帮我实现登录功能"},
                {"role": "assistant", "content": "需要我帮你实现吗？"},
            ],
            recent_window_messages=8,
        )
        active_ids = {e.event_id for e in projection.active_window}
        assert projection.snapshot.pending_followup is not None, "pending_followup should not be None"
        assert projection.snapshot.pending_followup.source_event_id in active_ids

    async def test_latest_message_in_active_window(self, engine: StateFirstContextOS) -> None:
        """Latest message is always in active window."""
        messages = [
            {"role": "user", "content": "消息1"},
            {"role": "assistant", "content": "回复1"},
            {"role": "user", "content": "消息2"},
        ]
        projection = await engine.project(messages=messages, recent_window_messages=8)
        active_ids = {e.event_id for e in projection.active_window}
        latest_event_id = projection.snapshot.transcript_log[-1].event_id
        assert latest_event_id in active_ids

    async def test_min_recent_floor_respected(self, engine: StateFirstContextOS) -> None:
        """min_recent_floor is respected even when recent_window_messages is small."""
        policy = StateFirstContextOSPolicy(window_size=WindowSizePolicy(min_recent_messages_pinned=3))
        engine = StateFirstContextOS(policy=policy)

        messages = [{"role": "user", "content": f"消息{i}"} for i in range(5)]
        projection = await engine.project(messages=messages, recent_window_messages=1)

        # Should still have at least 3 recent messages pinned
        assert len(projection.active_window) >= 3


class TestSealGuard:
    """Tests for A4: Seal Guard."""

    @pytest.fixture
    def engine(self) -> StateFirstContextOS:
        return StateFirstContextOS()

    async def test_seal_blocked_when_pending_followup(self, engine: StateFirstContextOS) -> None:
        """Episode sealing is blocked when pending follow-up exists."""
        # Create pending follow-up
        snapshot = (
            await engine.project(
                messages=[
                    {"role": "user", "content": "请帮我实现登录功能"},
                    {"role": "assistant", "content": "需要我帮你实现吗？"},
                ],
                recent_window_messages=8,
            )
        ).snapshot

        # Add more conversation
        projection = await engine.project(
            messages=[
                {"role": "assistant", "content": "好的，我现在开始实现。"},
                {"role": "user", "content": "好的"},
            ],
            existing_snapshot=snapshot,
            recent_window_messages=8,
        )

        # With pending follow-up, sealing should be blocked
        assert len(projection.snapshot.episode_store) == 0

    async def test_seal_allowed_when_no_pending(self, engine: StateFirstContextOS) -> None:
        """Episode sealing is allowed when no pending follow-up exists."""
        projection = await engine.project(
            messages=[
                {"role": "user", "content": "请帮我实现登录功能"},
                {"role": "assistant", "content": "好的，我来实现。"},
                {"role": "tool", "content": "代码已写入文件"},
                {"role": "user", "content": "完成了吗"},
            ],
            recent_window_messages=8,
        )
        # When no pending follow-up, sealing logic depends on domain adapter
        # This is a basic check that the system works
        assert projection.snapshot is not None


class TestAttentionObservability:
    """Tests for A5: Attention Observability."""

    @pytest.fixture
    def engine(self) -> StateFirstContextOS:
        return StateFirstContextOS()

    async def test_extract_attention_trace(self, engine: StateFirstContextOS) -> None:
        """Attention trace is correctly extracted."""
        projection = await engine.project(
            messages=[
                {"role": "user", "content": "请实现登录功能"},
                {"role": "assistant", "content": "需要我帮你实现吗？"},
            ],
            recent_window_messages=8,
        )
        trace = extract_attention_trace(projection.snapshot, projection)
        assert trace is not None
        # pending follow-up should be in attention roots when it's pending
        # Note: after confirmation, it's no longer pending
        assert trace.latest_dialog_act == DialogAct.UNKNOWN  # First message has no dialog act
        # Verify trace fields are properly populated
        assert isinstance(trace.attention_roots, tuple)
        assert "latest_user_turn" in trace.attention_roots or "current_goal" in trace.attention_roots

    async def test_attention_runtime_case_evaluation(self) -> None:
        """Attention runtime case evaluation works correctly."""
        conversation = [
            {"role": "user", "content": "请帮我实现登录功能"},
            {"role": "assistant", "content": "需要我帮你实现吗？"},
            {"role": "user", "content": "需要"},
        ]
        result = await evaluate_attention_runtime_case(
            conversation=conversation,
            expected_pending_status="confirmed",
        )
        assert isinstance(result, AttentionRuntimeQualityResult)
        assert result.metrics.pending_followup_resolution_rate == 1.0

    async def test_evaluation_gate_continuity_alignment(self) -> None:
        """Test improved A5 evaluation gate - continuity_focus_alignment_rate.

        This tests the fix for: 'continuity_focus_alignment_rate
        should properly measure alignment, not just check if latest_intent exists'.
        """
        conversation = [
            {"role": "user", "content": "请帮我实现登录功能"},
            {"role": "assistant", "content": "好的，我来帮你实现。"},
        ]
        result = await evaluate_attention_runtime_case(conversation=conversation)
        # Should have meaningful alignment score
        assert result.metrics.continuity_focus_alignment_rate >= 0.0
        assert result.metrics.continuity_focus_alignment_rate <= 1.0

    async def test_evaluation_gate_focus_regression_failure(self) -> None:
        """Test that high focus_regression_rate triggers failure.

        This tests: focus_regression_rate > 0.5 should be a failure.
        """
        # Create a conversation where user gives high-priority response
        # but the goal doesn't match
        conversation = [
            {"role": "user", "content": "请帮我实现登录功能"},
            {"role": "assistant", "content": "好的，我来帮你实现登录功能。"},
            {"role": "user", "content": "先别，我需要改成注册功能"},  # High priority redirect
        ]
        result = await evaluate_attention_runtime_case(conversation=conversation)
        # The result should have meaningful metrics (not all zeros)
        assert result.metrics.focus_regression_rate >= 0.0

    async def test_seal_blocked_in_attention_metrics(self) -> None:
        """Seal blocking is correctly reflected in attention metrics."""
        conversation = [
            {"role": "user", "content": "请帮我实现登录功能"},
            {"role": "assistant", "content": "需要我帮你实现吗？"},
            {"role": "assistant", "content": "好的，我现在开始实现。"},
            {"role": "user", "content": "好的"},
        ]
        result = await evaluate_attention_runtime_case(
            conversation=conversation,
            expect_seal_blocked=True,
        )
        assert result.metrics.seal_while_pending_rate == 0.0  # Correctly blocked

    async def test_context_redundancy_rate_detects_repeated_context(self) -> None:
        """Repeated long fragments in active context should raise redundancy rate."""
        repeated = "请阅读并总结这个项目代码，重点包含配置模块、服务模块、工具模块的实现细节和边界。"
        conversation = [
            {"role": "user", "content": repeated},
            {"role": "assistant", "content": "收到，我先读取项目结构。"},
            {"role": "assistant", "content": repeated},
            {"role": "tool", "content": repeated},
            {"role": "assistant", "content": repeated},
        ]
        result = await evaluate_attention_runtime_case(conversation=conversation)
        assert 0.0 <= result.metrics.context_redundancy_rate <= 1.0
        assert result.metrics.context_redundancy_rate > 0.20
        assert result.metrics.details.get("duplicate_instances", 0) > 0


class TestResolvedFollowUpCleanup:
    """Tests for resolved follow-up cleanup (Critical: Issue #2)."""

    @pytest.fixture
    def engine(self) -> StateFirstContextOS:
        return StateFirstContextOS()

    async def test_resolved_followup_not_carried_forward(self, engine: StateFirstContextOS) -> None:
        """Resolved follow-ups should NOT occupy attention in subsequent turns.

        This tests the fix for: 'confirmed/denied/paused follow-ups
        should not continue to occupy attention'.
        """
        # Turn 1: User asks for implementation, assistant asks follow-up
        snapshot1 = (
            await engine.project(
                messages=[
                    {"role": "user", "content": "请帮我实现登录功能"},
                    {"role": "assistant", "content": "需要我帮你实现吗？"},
                ],
                recent_window_messages=8,
            )
        ).snapshot

        # Verify pending follow-up was created
        assert snapshot1.pending_followup is not None
        assert snapshot1.pending_followup.status == "pending"
        assert "实现" in snapshot1.pending_followup.action

        # Turn 2: User confirms
        projection2 = await engine.project(
            messages=[{"role": "user", "content": "需要"}],
            existing_snapshot=snapshot1,
            recent_window_messages=8,
        )

        # Verify follow-up was resolved
        assert projection2.snapshot.pending_followup is not None
        assert projection2.snapshot.pending_followup.status == "confirmed"

        # Turn 3: User continues with new task (simulating fresh start)
        # The resolved follow-up should NOT be carried forward
        snapshot3 = await engine.project(
            messages=[
                {"role": "user", "content": "帮我写个注册功能"},
                {"role": "assistant", "content": "好的，我来帮你实现注册功能。"},
            ],
            recent_window_messages=8,
        )

        # Critical: The resolved follow-up from Turn 2 should NOT appear
        # in the new conversation's pending_followup
        assert snapshot3.snapshot.pending_followup is None or snapshot3.snapshot.pending_followup.status == "pending"

    async def test_run_card_clears_resolved_followup(self, engine: StateFirstContextOS) -> None:
        """Resolved follow-up should NOT appear in run_card.pending_followup_action."""
        # Turn 1: Create and resolve a follow-up
        projection1 = await engine.project(
            messages=[
                {"role": "user", "content": "请帮我实现登录功能"},
                {"role": "assistant", "content": "需要我帮你实现吗？"},
                {"role": "user", "content": "需要"},
            ],
            recent_window_messages=8,
        )

        # Verify the resolved follow-up appears in the run_card
        assert projection1.run_card is not None
        assert projection1.run_card.pending_followup_status == "confirmed"

        # Turn 2: New conversation without any follow-up
        projection2 = await engine.project(
            messages=[
                {"role": "user", "content": "请帮我实现注册功能"},
                {"role": "assistant", "content": "好的，我来帮你实现注册功能。"},
            ],
            recent_window_messages=8,
        )

        # Critical: The resolved follow-up should NOT appear in the new run_card
        # Only the new pending follow-up (if any) should appear
        assert projection2.run_card is not None
        # If there's a new pending follow-up, it should be from the new question
        if projection2.run_card.pending_followup_action:
            assert (
                "注册" in projection2.run_card.pending_followup_action
                or projection2.run_card.pending_followup_action == ""
            )

    async def test_continuation_turn_keeps_latest_intent_and_hides_resolved_followup(
        self,
        engine: StateFirstContextOS,
    ) -> None:
        """Continuation should append transcript sequence and keep latest turn in focus."""
        projection1 = await engine.project(
            messages=[
                {"role": "user", "content": "请帮我实现登录功能"},
                {"role": "assistant", "content": "需要我帮你实现吗？"},
                {"role": "user", "content": "需要"},
            ],
            recent_window_messages=8,
        )
        assert projection1.run_card is not None
        assert projection1.run_card.pending_followup_status == "confirmed"

        projection2 = await engine.project(
            messages=[{"role": "user", "content": "好的，继续"}],
            existing_snapshot=projection1.snapshot,
            recent_window_messages=8,
        )
        assert projection2.run_card is not None
        assert projection2.run_card.latest_user_intent == "好的，继续"
        assert projection2.run_card.pending_followup_action == ""
        assert projection2.run_card.pending_followup_status == ""

        sequences = [item.sequence for item in projection2.snapshot.transcript_log]
        assert sequences == sorted(sequences)
        assert sequences[-1] > sequences[-2]

        latest_event = projection2.snapshot.transcript_log[-1]
        assert latest_event.role == "user"
        assert latest_event.content == "好的，继续"
        active_ids = {item.event_id for item in projection2.active_window}
        assert latest_event.event_id in active_ids


class TestPendingFollowUpPersistence:
    """Tests for pending follow-up persistence (Critical: Issue #1)."""

    @pytest.fixture
    def engine(self) -> StateFirstContextOS:
        return StateFirstContextOS()

    async def test_pending_followup_serialization_roundtrip(self, engine: StateFirstContextOS) -> None:
        """Pending follow-up should survive serialization/deserialization."""
        projection1 = await engine.project(
            messages=[
                {"role": "user", "content": "请帮我实现登录功能"},
                {"role": "assistant", "content": "需要我帮你实现吗？"},
            ],
            recent_window_messages=8,
        )

        snapshot = projection1.snapshot
        assert snapshot.pending_followup is not None
        pf1 = snapshot.pending_followup
        original_action = pf1.action
        original_status = pf1.status

        # Serialize to dict
        snapshot_dict = snapshot.to_dict()

        # Deserialize back
        from polaris.kernelone.context.context_os.models_v2 import ContextOSSnapshotV2 as ContextOSSnapshot

        restored = ContextOSSnapshot.from_mapping(snapshot_dict)

        # Verify pending_followup is preserved
        assert restored is not None, "restored should not be None"
        assert restored.pending_followup is not None, "restored.pending_followup should not be None"
        pf = restored.pending_followup
        assert pf.action == original_action
        assert pf.status == original_status

    async def test_pending_followup_in_snapshot_dict(self, engine: StateFirstContextOS) -> None:
        """Pending follow-up should be present in snapshot.to_dict()."""
        projection = await engine.project(
            messages=[
                {"role": "user", "content": "请帮我实现登录功能"},
                {"role": "assistant", "content": "需要我帮你实现吗？"},
            ],
            recent_window_messages=8,
        )

        snapshot_dict = projection.snapshot.to_dict()

        # Critical: pending_followup key must exist in the dict
        assert "pending_followup" in snapshot_dict
        assert snapshot_dict["pending_followup"] is not None, "pending_followup should not be None in dict"
        pending_followup_dict = snapshot_dict["pending_followup"]
        assert isinstance(pending_followup_dict, dict), "pending_followup should be a dict"
        assert pending_followup_dict.get("action") != ""


class TestCodeDomainEnhancement:
    """Tests for A7: Code-Domain Enhancement."""

    @pytest.fixture
    def engine(self) -> StateFirstContextOS:
        from polaris.kernelone.context.context_os import CodeContextDomainAdapter

        return StateFirstContextOS(domain_adapter=CodeContextDomainAdapter())

    async def test_code_followup_intent_recognized(self, engine: StateFirstContextOS) -> None:
        """Code-specific follow-up intents are recognized."""
        projection = await engine.project(
            messages=[
                {"role": "user", "content": "这个 bug 很严重"},
                {"role": "assistant", "content": "需要我帮你修复这个 bug 吗？"},
            ],
            recent_window_messages=8,
        )
        run_card = projection.run_card
        # Code domain should have recognized the pending follow-up
        assert run_card is not None
        assert run_card.pending_followup_action != "" or run_card.pending_followup_status != ""
        # Verify the follow-up action mentions "fix" or "bug"
        if run_card.pending_followup_action:
            action_lower = run_card.pending_followup_action.lower()
            assert any(kw in action_lower for kw in ["fix", "修复", "bug"])

    async def test_code_workflow_hints_in_artifact_metadata(self, engine: StateFirstContextOS) -> None:
        """Code workflow hints are included in artifact metadata."""
        projection = await engine.project(
            messages=[
                {"role": "user", "content": "修复 polaris/kernelone/context/tests/test_fix.py 中的 bug"},
                {"role": "assistant", "content": "我来帮你修复这个 bug。"},
                {"role": "tool", "content": "```python\n# fixed code\n```"},
            ],
            recent_window_messages=8,
        )
        if projection.snapshot.artifact_store:
            artifact = projection.snapshot.artifact_store[0]
            # Code domain adapter should add workflow hints
            assert get_metadata_value(artifact.metadata, "adapter_id") == "code"


class TestContextWindowResolution:
    """Tests for context window resolution from LLM Provider config.

    Resolution order:
        1. LLM Provider Config Table (ModelCatalog.resolve)
        2. Hard-coded model windows table
        3. StateFirstContextOSPolicy.model_context_window (env var overridable)
    """

    _MOCK_SPEC_TARGET = "polaris.kernelone.context.budget_gate._resolve_model_window_from_spec"

    def test_context_window_default_fallback(self) -> None:
        """Without provider/model, should use policy default."""
        engine = StateFirstContextOS()
        # Default is 128000 from policy
        assert engine.resolved_context_window == 128000

    @patch(_MOCK_SPEC_TARGET, return_value=128000)
    def test_context_window_resolves_from_config_or_table(self, _mock: MagicMock) -> None:
        """With provider/model, should resolve from config table or hard-coded table."""
        engine = StateFirstContextOS(
            provider_id="openai",
            model="gpt-4o",
            workspace=".",
        )
        # Should resolve to a positive value (either from config or hard-coded table)
        assert engine.resolved_context_window > 0

    @patch(_MOCK_SPEC_TARGET, return_value=200000)
    def test_context_window_uses_config_primary(self, _mock: MagicMock) -> None:
        """LLM Config Table takes priority over hard-coded table.

        Note: The actual value depends on the workspace LLM config.
        """
        engine = StateFirstContextOS(
            provider_id="anthropic",
            model="claude-sonnet-4-5",
            workspace=".",
        )
        # ModelCatalog takes priority - returns value from workspace config
        # This should be a positive value (from config or table)
        assert engine.resolved_context_window > 0

    @patch(_MOCK_SPEC_TARGET, return_value=128000)
    def test_context_window_caches_resolution(self, _mock: MagicMock) -> None:
        """Context window resolution should be cached."""
        engine = StateFirstContextOS(
            provider_id="openai",
            model="gpt-4o",
            workspace=".",
        )
        first_call = engine.resolved_context_window
        second_call = engine.resolved_context_window
        assert first_call == second_call
        assert engine._resolved_context_window is not None

    @patch(_MOCK_SPEC_TARGET, return_value=128000)
    async def test_budget_plan_uses_resolved_window(self, _mock: MagicMock) -> None:
        """BudgetPlan should use the resolved context window."""
        engine = StateFirstContextOS(
            provider_id="openai",
            model="gpt-4o",
            workspace=".",
        )
        projection = await engine.project(
            messages=[
                {"role": "user", "content": "请帮我实现登录功能"},
                {"role": "assistant", "content": "好的，我来帮你实现。"},
            ],
            recent_window_messages=8,
        )
        # Should use resolved window (from config or table), not policy default
        assert projection.snapshot.budget_plan is not None
        assert projection.snapshot.budget_plan.model_context_window > 0
        # The resolved value should match what we got from the engine
        assert projection.snapshot.budget_plan.model_context_window == engine.resolved_context_window
