"""Tests for ContinuationPolicy."""

import pytest
from polaris.cells.roles.kernel.public.turn_contracts import (
    TurnContinuationMode,
    TurnOutcomeEnvelope,
    TurnResult,
)
from polaris.cells.roles.runtime.internal.continuation_policy import (
    ContinuationPolicy,
    OrchestratorSessionState,
    SessionPatch,
    apply_session_patch,
    get_active_findings,
)


class TestContinuationPolicyCanContinue:
    """测试 ContinuationPolicy.can_continue 的各种场景。"""

    @pytest.fixture
    def policy(self):
        return ContinuationPolicy(max_auto_turns=5, speculative_hit_threshold=0.7)

    @pytest.fixture
    def base_state(self):
        return OrchestratorSessionState(session_id="test-session", goal="test")

    def _make_envelope(self, mode: TurnContinuationMode, **overrides) -> TurnOutcomeEnvelope:
        turn_result = TurnResult(turn_id="t1", kind="final_answer", visible_content="", decision={})
        defaults = {
            "turn_result": turn_result,
            "continuation_mode": mode,
            "next_intent": None,
            "session_patch": {},
            "artifacts_to_persist": [],
            "speculative_hints": {},
        }
        defaults.update(overrides)
        return TurnOutcomeEnvelope(**defaults)

    def test_auto_continue_allowed(self, policy, base_state):
        envelope = self._make_envelope(TurnContinuationMode.AUTO_CONTINUE)
        can, reason = policy.can_continue(base_state, envelope)
        assert can is True
        assert reason is None

    def test_end_session_blocked(self, policy, base_state):
        envelope = self._make_envelope(TurnContinuationMode.END_SESSION)
        can, reason = policy.can_continue(base_state, envelope)
        assert can is False
        assert reason == "mode=end_session"

    def test_waiting_human_blocked(self, policy, base_state):
        envelope = self._make_envelope(TurnContinuationMode.WAITING_HUMAN)
        can, reason = policy.can_continue(base_state, envelope)
        assert can is False
        assert reason == "mode=waiting_human"

    def test_handoff_development_blocked(self, policy, base_state):
        envelope = self._make_envelope(TurnContinuationMode.HANDOFF_DEVELOPMENT)
        can, reason = policy.can_continue(base_state, envelope)
        assert can is False
        assert reason == "mode=handoff_development"

    def test_handoff_exploration_blocked(self, policy, base_state):
        envelope = self._make_envelope(TurnContinuationMode.HANDOFF_EXPLORATION)
        can, reason = policy.can_continue(base_state, envelope)
        assert can is False
        assert reason == "mode=handoff_exploration"

    def test_max_turns_exceeded(self, policy, base_state):
        base_state.turn_count = 5
        envelope = self._make_envelope(TurnContinuationMode.AUTO_CONTINUE)
        can, reason = policy.can_continue(base_state, envelope)
        assert can is False
        assert reason == "max_turns_exceeded"

    def test_repetitive_failure_detected(self, policy, base_state):
        base_state.turn_history = [
            {"turn_index": 1, "continuation_mode": "auto_continue", "error": "same error"},
            {"turn_index": 2, "continuation_mode": "auto_continue", "error": "same error"},
            {"turn_index": 3, "continuation_mode": "auto_continue", "error": "same error"},
        ]
        envelope = self._make_envelope(TurnContinuationMode.AUTO_CONTINUE)
        can, reason = policy.can_continue(base_state, envelope)
        assert can is False
        assert reason == "repetitive_failure"

    def test_stagnation_detected(self, policy, base_state):
        base_state.recent_artifact_hashes = ["abc123", "abc123"]
        envelope = self._make_envelope(TurnContinuationMode.AUTO_CONTINUE)
        can, reason = policy.can_continue(base_state, envelope)
        # Phase 5.1: stagnation 检测后会尝试 recovery strategy
        assert can is True
        assert "recovery_strategy=" in reason

    def test_stagnation_with_speculative_hints_allowed(self, policy, base_state):
        base_state.recent_artifact_hashes = ["abc123", "abc123"]
        envelope = self._make_envelope(TurnContinuationMode.AUTO_CONTINUE, speculative_hints={"hint": "value"})
        can, reason = policy.can_continue(base_state, envelope)
        assert can is True
        assert reason is None

    def test_speculative_continue_worthwhile(self, policy, base_state):
        envelope = self._make_envelope(
            TurnContinuationMode.SPECULATIVE_CONTINUE,
            speculative_hints={"shadow_engine_hit_rate": 0.8},
            session_patch={"file.py": "new content"},
        )
        can, reason = policy.can_continue(base_state, envelope)
        assert can is True
        assert reason is None

    def test_speculative_continue_not_worthwhile_low_hit_rate(self, policy, base_state):
        envelope = self._make_envelope(
            TurnContinuationMode.SPECULATIVE_CONTINUE,
            speculative_hints={"shadow_engine_hit_rate": 0.5},
            session_patch={"file.py": "new content"},
        )
        can, reason = policy.can_continue(base_state, envelope)
        assert can is False
        assert reason == "speculative_not_worthwhile"

    def test_speculative_continue_not_worthwhile_no_artifact_change(self, policy, base_state):
        envelope = self._make_envelope(
            TurnContinuationMode.SPECULATIVE_CONTINUE,
            speculative_hints={"shadow_engine_hit_rate": 0.9},
            session_patch={},
        )
        can, reason = policy.can_continue(base_state, envelope)
        assert can is False
        assert reason == "speculative_not_worthwhile"


class TestContinuationPolicyDetectRepetitiveFailure:
    """测试 _detect_repetitive_failure 静态方法。"""

    def test_not_enough_history(self):
        state = OrchestratorSessionState(session_id="s1")
        state.turn_history = [
            {"turn_index": 1, "error": "err"},
            {"turn_index": 2, "error": "err"},
        ]
        assert ContinuationPolicy._detect_repetitive_failure(state) is False

    def test_all_same_error(self):
        state = OrchestratorSessionState(session_id="s1")
        state.turn_history = [
            {"turn_index": 1, "error": "timeout"},
            {"turn_index": 2, "error": "timeout"},
            {"turn_index": 3, "error": "timeout"},
        ]
        assert ContinuationPolicy._detect_repetitive_failure(state) is True

    def test_different_errors(self):
        state = OrchestratorSessionState(session_id="s1")
        state.turn_history = [
            {"turn_index": 1, "error": "timeout"},
            {"turn_index": 2, "error": "timeout"},
            {"turn_index": 3, "error": "not found"},
        ]
        assert ContinuationPolicy._detect_repetitive_failure(state) is False

    def test_all_none_errors_not_failure(self):
        state = OrchestratorSessionState(session_id="s1")
        state.turn_history = [
            {"turn_index": 1, "error": None},
            {"turn_index": 2, "error": None},
            {"turn_index": 3, "error": None},
        ]
        # None means no error, so it's not a repetitive failure
        assert ContinuationPolicy._detect_repetitive_failure(state) is False


class TestContinuationPolicyDetectStagnation:
    """测试 _detect_stagnation_v2 静态方法。"""

    def test_not_enough_hashes(self):
        state = OrchestratorSessionState(session_id="s1")
        state.recent_artifact_hashes = ["hash1"]
        envelope = TurnOutcomeEnvelope(
            turn_result=TurnResult(turn_id="t1", kind="final_answer", visible_content="", decision={}),
            continuation_mode=TurnContinuationMode.AUTO_CONTINUE,
        )
        assert ContinuationPolicy._detect_stagnation_v2(state, envelope) is False

    def test_hashes_differ(self):
        state = OrchestratorSessionState(session_id="s1")
        state.recent_artifact_hashes = ["hash1", "hash2"]
        envelope = TurnOutcomeEnvelope(
            turn_result=TurnResult(turn_id="t1", kind="final_answer", visible_content="", decision={}),
            continuation_mode=TurnContinuationMode.AUTO_CONTINUE,
        )
        assert ContinuationPolicy._detect_stagnation_v2(state, envelope) is False

    def test_same_hashes_with_hints(self):
        state = OrchestratorSessionState(session_id="s1")
        state.recent_artifact_hashes = ["hash1", "hash1"]
        envelope = TurnOutcomeEnvelope(
            turn_result=TurnResult(turn_id="t1", kind="final_answer", visible_content="", decision={}),
            continuation_mode=TurnContinuationMode.AUTO_CONTINUE,
            speculative_hints={"hint": "value"},
        )
        assert ContinuationPolicy._detect_stagnation_v2(state, envelope) is False

    def test_same_hashes_no_hints(self):
        state = OrchestratorSessionState(session_id="s1")
        state.recent_artifact_hashes = ["hash1", "hash1"]
        envelope = TurnOutcomeEnvelope(
            turn_result=TurnResult(turn_id="t1", kind="final_answer", visible_content="", decision={}),
            continuation_mode=TurnContinuationMode.AUTO_CONTINUE,
        )
        assert ContinuationPolicy._detect_stagnation_v2(state, envelope) is True

    def test_semantic_stagnation_detected(self):
        """连续 4 个 turn 的 task_progress 未推进，应判定为语义停滞。"""
        state = OrchestratorSessionState(session_id="s1")
        state.structured_findings["_findings_trajectory"] = [
            {"task_progress": "exploring"},
            {"task_progress": "exploring"},
            {"task_progress": "exploring"},
            {"task_progress": "exploring"},
        ]
        envelope = TurnOutcomeEnvelope(
            turn_result=TurnResult(turn_id="t1", kind="final_answer", visible_content="", decision={}),
            continuation_mode=TurnContinuationMode.AUTO_CONTINUE,
        )
        assert ContinuationPolicy._detect_stagnation_v2(state, envelope) is True

    def test_semantic_stagnation_progress_changes(self):
        """task_progress 有变化时不应判定为语义停滞。"""
        state = OrchestratorSessionState(session_id="s1")
        state.structured_findings["_findings_trajectory"] = [
            {"task_progress": "exploring"},
            {"task_progress": "exploring"},
            {"task_progress": "investigating"},
            {"task_progress": "investigating"},
        ]
        envelope = TurnOutcomeEnvelope(
            turn_result=TurnResult(turn_id="t1", kind="final_answer", visible_content="", decision={}),
            continuation_mode=TurnContinuationMode.AUTO_CONTINUE,
        )
        assert ContinuationPolicy._detect_stagnation_v2(state, envelope) is False

    def test_semantic_stagnation_not_enough_trajectory(self):
        """轨迹不足 4 条时不应触发语义停滞检测。"""
        state = OrchestratorSessionState(session_id="s1")
        state.structured_findings["_findings_trajectory"] = [
            {"task_progress": "exploring"},
            {"task_progress": "exploring"},
            {"task_progress": "exploring"},
        ]
        envelope = TurnOutcomeEnvelope(
            turn_result=TurnResult(turn_id="t1", kind="final_answer", visible_content="", decision={}),
            continuation_mode=TurnContinuationMode.AUTO_CONTINUE,
        )
        assert ContinuationPolicy._detect_stagnation_v2(state, envelope) is False


class TestSessionPatchConfidenceAndSuperseded:
    """测试 SessionPatch confidence/superseded 字段（Step 9）。"""

    def test_get_confidence_defaults_to_hypothesis(self):
        patch = SessionPatch({"task_progress": "exploring"})
        assert patch.get_confidence() == "hypothesis"

    def test_get_confidence_returns_actual_value(self):
        patch = SessionPatch({"confidence": "confirmed"})
        assert patch.get_confidence() == "confirmed"

    def test_get_superseded_defaults_to_false(self):
        patch = SessionPatch({"task_progress": "exploring"})
        assert patch.get_superseded() is False

    def test_get_superseded_returns_true_when_set(self):
        patch = SessionPatch({"superseded": True, "error_summary": "wrong"})
        assert patch.get_superseded() is True


class TestApplySessionPatchConfidenceAware:
    """测试 apply_session_patch 置信度感知合并（Step 9）。"""

    def test_superseded_marks_fields(self):
        state = OrchestratorSessionState(session_id="s1")
        state.structured_findings = {"error_summary": "db timeout", "suspected_files": ["db.py"]}
        patch = SessionPatch({"superseded": True, "suspected_files": []})
        apply_session_patch(state, patch)
        # superseded=True 时，patch 中的字段名（suspected_files）进入 _superseded_keys
        assert "suspected_files" in state.structured_findings.get("_superseded_keys", [])

    def test_confidence_hypothesis_does_not_override_confirmed(self):
        state = OrchestratorSessionState(session_id="s1")
        state.structured_findings = {
            "error_summary": "auth.py broken",
            "_confidence_error_summary": "confirmed",
        }
        patch = SessionPatch({"confidence": "hypothesis", "error_summary": "db.py broken"})
        apply_session_patch(state, patch)
        # confirmed 不会被 hypothesis 覆盖
        assert state.structured_findings["error_summary"] == "auth.py broken"
        assert state.structured_findings["_confidence_error_summary"] == "confirmed"

    def test_confidence_likely_overrides_hypothesis(self):
        state = OrchestratorSessionState(session_id="s1")
        state.structured_findings = {
            "error_summary": "auth.py broken",
            "_confidence_error_summary": "hypothesis",
        }
        patch = SessionPatch({"confidence": "likely", "error_summary": "db.py broken"})
        apply_session_patch(state, patch)
        assert state.structured_findings["error_summary"] == "db.py broken"
        assert state.structured_findings["_confidence_error_summary"] == "likely"

    def test_confidence_confirmed_overrides_likely(self):
        state = OrchestratorSessionState(session_id="s1")
        state.structured_findings = {
            "error_summary": "auth.py broken",
            "_confidence_error_summary": "likely",
        }
        patch = SessionPatch({"confidence": "confirmed", "error_summary": "db.py broken"})
        apply_session_patch(state, patch)
        assert state.structured_findings["error_summary"] == "db.py broken"
        assert state.structured_findings["_confidence_error_summary"] == "confirmed"

    def test_first_patch_sets_confidence(self):
        state = OrchestratorSessionState(session_id="s1")
        patch = SessionPatch({"confidence": "likely", "error_summary": "auth broken"})
        apply_session_patch(state, patch)
        assert state.structured_findings["error_summary"] == "auth broken"
        assert state.structured_findings["_confidence_error_summary"] == "likely"


class TestStagnationIntegration:
    """Stagnation detection integration tests generated by Director CLI live-fire."""

    @pytest.fixture
    def policy(self):
        return ContinuationPolicy(max_auto_turns=5, speculative_hit_threshold=0.7)

    @pytest.fixture
    def base_state(self):
        return OrchestratorSessionState(session_id="test-session", goal="test")

    def _make_envelope(self, **overrides) -> TurnOutcomeEnvelope:
        turn_result = TurnResult(turn_id="t1", kind="final_answer", visible_content="", decision={})
        defaults = {
            "turn_result": turn_result,
            "continuation_mode": TurnContinuationMode.AUTO_CONTINUE,
            "next_intent": None,
            "session_patch": {},
            "artifacts_to_persist": [],
            "speculative_hints": {},
        }
        defaults.update(overrides)
        return TurnOutcomeEnvelope(**defaults)

    def test_semantic_stagnation_three_same_progress(self, policy, base_state):
        """连续 4 次 task_progress 不变触发语义停滞（max_trajectory_size=10）。"""
        for _ in range(4):
            patch = SessionPatch({"task_progress": "exploring"})
            apply_session_patch(base_state, patch)
        envelope = self._make_envelope()
        assert policy._detect_stagnation_v2(base_state, envelope) is True

    def test_hash_stagnation_repeated_hashes(self, policy, base_state):
        """recent_artifact_hashes 连续两次相同触发哈希停滞。"""
        base_state.recent_artifact_hashes = ["hash_A", "hash_A"]
        envelope = self._make_envelope()
        assert policy._detect_stagnation_v2(base_state, envelope) is True

    def test_speculative_hints_prevent_hash_stagnation(self, policy, base_state):
        """speculative_hints 非空时豁免哈希停滞。"""
        base_state.recent_artifact_hashes = ["hash_A", "hash_A"]
        envelope = self._make_envelope(speculative_hints={"predicted_approach": "refactoring"})
        assert policy._detect_stagnation_v2(base_state, envelope) is False

    def test_progress_changes_no_semantic_stagnation(self, policy, base_state):
        """task_progress 有变化时不触发语义停滞。"""
        for progress in ("exploring", "investigating", "implementing", "verifying"):
            patch = SessionPatch({"task_progress": progress})
            apply_session_patch(base_state, patch)
        envelope = self._make_envelope()
        assert policy._detect_stagnation_v2(base_state, envelope) is False

    def test_combined_stagnation_progress_and_hash(self, policy, base_state):
        """语义停滞 + 哈希停滞同时满足时仍然返回 True。"""
        for _ in range(4):
            patch = SessionPatch({"task_progress": "exploring"})
            apply_session_patch(base_state, patch)
        base_state.recent_artifact_hashes = ["hash_X", "hash_X"]
        envelope = self._make_envelope()
        assert policy._detect_stagnation_v2(base_state, envelope) is True


class TestGetActiveFindings:
    """测试 get_active_findings 过滤 superseded 发现物（Step 9）。"""

    def test_returns_all_when_no_superseded(self):
        findings = {"task_progress": "exploring", "error_summary": "bug found"}
        active = get_active_findings(findings)
        assert active == findings

    def test_filters_superseded_keys(self):
        findings = {
            "task_progress": "exploring",
            "error_summary": "bug found",
            "_superseded_keys": ["error_summary"],
        }
        active = get_active_findings(findings)
        assert "error_summary" not in active
        assert "task_progress" in active

    def test_filters_confidence_metadata(self):
        findings = {
            "error_summary": "bug found",
            "_confidence_error_summary": "confirmed",
            "_superseded_keys": [],
        }
        active = get_active_findings(findings)
        assert "error_summary" in active
        assert "_confidence_error_summary" not in active
        assert "_superseded_keys" not in active

    def test_preserves_findings_trajectory(self):
        findings = {
            "_findings_trajectory": [{"task_progress": "exploring"}],
            "_superseded_keys": [],
        }
        active = get_active_findings(findings)
        assert "_findings_trajectory" in active
