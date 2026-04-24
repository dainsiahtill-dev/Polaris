"""Tests for polaris.delivery.cli.pm.blocked_policy.

Covers enums, dataclasses, error classification, policy evaluation,
and helper functions with normal, boundary, and edge cases.
"""

from __future__ import annotations

from typing import Any

import pytest
from polaris.delivery.cli.pm.blocked_policy import (
    BlockedDecision,
    BlockedPolicyResult,
    BlockedStrategy,
    _classify_error,
    _get_task_signature,
    consume_degrade_settings,
    evaluate_blocked_policy,
    get_blocked_policy_from_env,
    normalize_director_status,
    should_apply_degrade_settings,
)


class TestBlockedStrategyEnum:
    """Tests for BlockedStrategy enum."""

    def test_enum_values(self) -> None:
        assert BlockedStrategy.SKIP == "skip"
        assert BlockedStrategy.MANUAL == "manual"
        assert BlockedStrategy.DEGRADE_RETRY == "degrade_retry"
        assert BlockedStrategy.AUTO == "auto"

    def test_enum_membership(self) -> None:
        assert BlockedStrategy("skip") is BlockedStrategy.SKIP
        assert BlockedStrategy("auto") is BlockedStrategy.AUTO

    def test_invalid_enum_value(self) -> None:
        with pytest.raises(ValueError):
            BlockedStrategy("invalid")


class TestBlockedDecisionEnum:
    """Tests for BlockedDecision enum."""

    def test_enum_values(self) -> None:
        assert BlockedDecision.CONTINUE == "continue"
        assert BlockedDecision.MANUAL_STOP == "manual_stop"
        assert BlockedDecision.DEGRADE_AND_CONTINUE == "degrade_and_continue"
        assert BlockedDecision.SKIP_AND_CONTINUE == "skip_and_continue"


class TestBlockedPolicyResult:
    """Tests for BlockedPolicyResult dataclass."""

    def test_defaults(self) -> None:
        result = BlockedPolicyResult(decision=BlockedDecision.CONTINUE, exit_code=0)
        assert result.pm_state_patch == {}
        assert result.audit_payload == {}
        assert result.strategy == ""
        assert result.reason == ""
        assert result.task_status_update is None

    def test_custom_values(self) -> None:
        result = BlockedPolicyResult(
            decision=BlockedDecision.SKIP_AND_CONTINUE,
            exit_code=0,
            strategy="skip",
            reason="test",
            task_status_update={"status": "skipped"},
        )
        assert result.task_status_update == {"status": "skipped"}


class TestNormalizeDirectorStatus:
    """Tests for normalize_director_status function."""

    def test_success_statuses(self) -> None:
        for status in ("success", "done", "completed", "pass", "passed"):
            assert normalize_director_status(status) == "success"

    def test_failed_statuses(self) -> None:
        for status in ("fail", "failed", "error", "cancelled", "timeout"):
            assert normalize_director_status(status) == "failed"

    def test_blocked_statuses(self) -> None:
        for status in ("blocked", "block"):
            assert normalize_director_status(status) == "blocked"

    def test_needs_continue_statuses(self) -> None:
        for status in ("needs_continue", "need_continue", "continue", "deferred"):
            assert normalize_director_status(status) == "needs_continue"

    def test_unknown_status(self) -> None:
        assert normalize_director_status("unknown") == "unknown"
        assert normalize_director_status("random") == "random"

    def test_none_and_empty(self) -> None:
        assert normalize_director_status(None) == "unknown"
        assert normalize_director_status("") == "unknown"

    def test_whitespace_handling(self) -> None:
        assert normalize_director_status("  SUCCESS  ") == "success"
        assert normalize_director_status(" Failed ") == "failed"


class TestClassifyError:
    """Tests for _classify_error function."""

    def test_rate_limit_detection(self) -> None:
        error_class, confidence = _classify_error("rate limit exceeded, please retry")
        assert error_class == "llm_rate_limit"
        assert confidence > 0.5

    def test_quota_exceeded_detection(self) -> None:
        error_class, confidence = _classify_error("insufficient quota for billing")
        assert error_class == "llm_quota_exceeded"
        assert confidence > 0.5

    def test_timeout_detection(self) -> None:
        error_class, _confidence = _classify_error("request timeout, deadline exceeded")
        assert error_class == "llm_timeout"

    def test_context_length_detection(self) -> None:
        error_class, _confidence = _classify_error("context length too long, max tokens exceeded")
        assert error_class == "llm_context_length"

    def test_permission_denied_detection(self) -> None:
        error_class, _confidence = _classify_error("permission denied, access unauthorized 403")
        assert error_class == "permission_denied"

    def test_resource_not_found_detection(self) -> None:
        error_class, _confidence = _classify_error("file not found, 404 error, no such path")
        assert error_class == "resource_not_found"

    def test_transient_network_detection(self) -> None:
        error_class, _confidence = _classify_error("network unreachable, dns error, econnrefused")
        assert error_class == "transient_network"

    def test_syntax_validation_detection(self) -> None:
        error_class, _confidence = _classify_error("syntax error, parse failed, schema validation")
        assert error_class == "syntax_validation"

    def test_tool_execution_fail_detection(self) -> None:
        error_class, _confidence = _classify_error("tool execution failed, command exit code 1")
        assert error_class == "tool_execution_fail"

    def test_unknown_error(self) -> None:
        error_class, confidence = _classify_error("some random error message")
        assert error_class == "unknown"
        assert confidence == 0.0

    def test_empty_error(self) -> None:
        error_class, confidence = _classify_error("")
        assert error_class == "unknown"
        assert confidence == 0.0

    def test_multiple_matches_returns_highest_confidence(self) -> None:
        # "rate limit" and "timeout" both might match; should return one with highest confidence
        error_class, confidence = _classify_error("rate limit timeout")
        assert error_class in ("llm_rate_limit", "llm_timeout")
        assert confidence > 0.5


class TestGetTaskSignature:
    """Tests for _get_task_signature function."""

    def test_with_task_id(self) -> None:
        assert _get_task_signature({"task_id": "T1", "title": "Test"}) == "task:T1"

    def test_with_id_fallback(self) -> None:
        assert _get_task_signature({"id": "ID1"}) == "task:ID1"

    def test_with_title_only(self) -> None:
        sig = _get_task_signature({"title": "A very long title that should be hashed"})
        assert sig.startswith("title:")
        assert len(sig) == 22  # "title:" + 16 hex chars

    def test_with_subject_fallback(self) -> None:
        sig = _get_task_signature({"subject": "Test Subject"})
        assert sig.startswith("title:")

    def test_unknown_task(self) -> None:
        assert _get_task_signature({}) == "unknown"
        assert _get_task_signature({"foo": "bar"}) == "unknown"

    def test_none_values(self) -> None:
        assert _get_task_signature({"task_id": None, "title": None}) == "unknown"


class TestEvaluateBlockedPolicySkip:
    """Tests for SKIP strategy in evaluate_blocked_policy."""

    def test_skip_returns_continue_decision(self) -> None:
        result = evaluate_blocked_policy(
            strategy=BlockedStrategy.SKIP,
            task={"task_id": "T1", "title": "Test Task"},
            director_result={"status": "blocked", "error": "some error"},
            pm_state={"blocked_skip_count": 0},
            retry_count=3,
            max_retries=3,
        )
        assert result.decision == BlockedDecision.SKIP_AND_CONTINUE
        assert result.exit_code == 0
        assert result.strategy == "skip"

    def test_skip_increments_counter(self) -> None:
        result = evaluate_blocked_policy(
            strategy=BlockedStrategy.SKIP,
            task={"task_id": "T1"},
            director_result={"status": "blocked"},
            pm_state={"blocked_skip_count": 5},
            retry_count=1,
            max_retries=3,
        )
        assert result.pm_state_patch["blocked_skip_count"] == 6

    def test_skip_includes_task_status_update(self) -> None:
        result = evaluate_blocked_policy(
            strategy=BlockedStrategy.SKIP,
            task={"task_id": "T1"},
            director_result={"status": "blocked"},
            pm_state={},
            retry_count=1,
            max_retries=3,
        )
        assert result.task_status_update is not None
        assert result.task_status_update["status"] == "skipped"
        assert result.task_status_update["blocked_handle_action"] == "skip"

    def test_skip_resets_blocked_counter(self) -> None:
        result = evaluate_blocked_policy(
            strategy=BlockedStrategy.SKIP,
            task={"task_id": "T1"},
            director_result={"status": "blocked"},
            pm_state={"consecutive_blocked": 5},
            retry_count=1,
            max_retries=3,
        )
        assert result.pm_state_patch.get("consecutive_blocked") == 0

    def test_skip_with_string_strategy(self) -> None:
        result = evaluate_blocked_policy(
            strategy="skip",
            task={"task_id": "T1"},
            director_result={"status": "blocked"},
            pm_state={},
            retry_count=1,
            max_retries=3,
        )
        assert result.decision == BlockedDecision.SKIP_AND_CONTINUE


class TestEvaluateBlockedPolicyManual:
    """Tests for MANUAL strategy in evaluate_blocked_policy."""

    def test_manual_returns_stop_decision(self) -> None:
        result = evaluate_blocked_policy(
            strategy=BlockedStrategy.MANUAL,
            task={"task_id": "T1"},
            director_result={"status": "blocked"},
            pm_state={},
            retry_count=1,
            max_retries=3,
        )
        assert result.decision == BlockedDecision.MANUAL_STOP
        assert result.exit_code == 3
        assert result.strategy == "manual"

    def test_manual_sets_intervention_flag(self) -> None:
        result = evaluate_blocked_policy(
            strategy=BlockedStrategy.MANUAL,
            task={"task_id": "T1"},
            director_result={"status": "blocked"},
            pm_state={},
            retry_count=1,
            max_retries=3,
        )
        assert result.pm_state_patch["awaiting_manual_intervention"] is True
        assert "blocked_task:" in result.pm_state_patch["manual_intervention_reason"]


class TestEvaluateBlockedPolicyDegradeRetry:
    """Tests for DEGRADE_RETRY strategy in evaluate_blocked_policy."""

    def test_degrade_retry_with_budget(self) -> None:
        result = evaluate_blocked_policy(
            strategy=BlockedStrategy.DEGRADE_RETRY,
            task={"task_id": "T1"},
            director_result={"status": "blocked"},
            pm_state={"degrade_retry_count": 0},
            retry_count=1,
            max_retries=3,
            degrade_retry_budget=2,
        )
        assert result.decision == BlockedDecision.DEGRADE_AND_CONTINUE
        assert result.exit_code == 0
        assert result.pm_state_patch["degrade_retry_count"] == 1
        assert "degrade_settings" in result.pm_state_patch

    def test_degrade_settings_content(self) -> None:
        result = evaluate_blocked_policy(
            strategy=BlockedStrategy.DEGRADE_RETRY,
            task={"task_id": "T1"},
            director_result={"status": "blocked"},
            pm_state={"degrade_retry_count": 0},
            retry_count=1,
            max_retries=3,
            degrade_retry_budget=2,
        )
        settings = result.pm_state_patch["degrade_settings"]
        assert settings["serial_mode"] is True
        assert settings["max_parallel"] == 1
        assert settings["integration_qa"] is False
        assert settings["max_verification_retries"] == 0

    def test_degrade_exhausted_fallback_to_manual(self) -> None:
        result = evaluate_blocked_policy(
            strategy=BlockedStrategy.DEGRADE_RETRY,
            task={"task_id": "T1"},
            director_result={"status": "blocked"},
            pm_state={"degrade_retry_count": 2},
            retry_count=1,
            max_retries=3,
            degrade_retry_budget=2,
        )
        assert result.decision == BlockedDecision.MANUAL_STOP
        assert result.exit_code == 3

    def test_degrade_budget_zero(self) -> None:
        result = evaluate_blocked_policy(
            strategy=BlockedStrategy.DEGRADE_RETRY,
            task={"task_id": "T1"},
            director_result={"status": "blocked"},
            pm_state={"degrade_retry_count": 0},
            retry_count=1,
            max_retries=3,
            degrade_retry_budget=0,
        )
        assert result.decision == BlockedDecision.MANUAL_STOP


class TestEvaluateBlockedPolicyAuto:
    """Tests for AUTO strategy in evaluate_blocked_policy."""

    def test_auto_rate_limit_uses_degrade(self) -> None:
        result = evaluate_blocked_policy(
            strategy=BlockedStrategy.AUTO,
            task={"task_id": "T1"},
            director_result={"status": "blocked", "error": "rate limit exceeded"},
            pm_state={"degrade_retry_count": 0},
            retry_count=1,
            max_retries=3,
            degrade_retry_budget=2,
        )
        assert result.decision == BlockedDecision.DEGRADE_AND_CONTINUE
        assert "degrade_settings" in result.pm_state_patch

    def test_auto_permission_denied_uses_manual(self) -> None:
        result = evaluate_blocked_policy(
            strategy=BlockedStrategy.AUTO,
            task={"task_id": "T1"},
            director_result={"status": "blocked", "error": "permission denied"},
            pm_state={},
            retry_count=1,
            max_retries=3,
        )
        assert result.decision == BlockedDecision.MANUAL_STOP
        assert result.pm_state_patch["awaiting_manual_intervention"] is True

    def test_auto_context_length_uses_skip(self) -> None:
        result = evaluate_blocked_policy(
            strategy=BlockedStrategy.AUTO,
            task={"task_id": "T1"},
            director_result={"status": "blocked", "error": "context length too long"},
            pm_state={},
            retry_count=1,
            max_retries=3,
        )
        assert result.decision == BlockedDecision.SKIP_AND_CONTINUE

    def test_auto_critical_task_prefers_manual(self) -> None:
        result = evaluate_blocked_policy(
            strategy=BlockedStrategy.AUTO,
            task={"task_id": "T1", "title": "auth module implementation"},
            director_result={"status": "blocked", "error": "some error"},
            pm_state={},
            retry_count=5,
            max_retries=5,
            degrade_retry_budget=1,
        )
        assert result.decision == BlockedDecision.MANUAL_STOP

    def test_auto_security_critical_task_prefers_manual(self) -> None:
        result = evaluate_blocked_policy(
            strategy=BlockedStrategy.AUTO,
            task={"task_id": "T1", "title": "security core patch"},
            director_result={"status": "blocked", "error": "some error"},
            pm_state={},
            retry_count=5,
            max_retries=5,
            degrade_retry_budget=1,
        )
        assert result.decision == BlockedDecision.MANUAL_STOP

    def test_auto_unclassified_fallback_to_skip(self) -> None:
        result = evaluate_blocked_policy(
            strategy=BlockedStrategy.AUTO,
            task={"task_id": "T1"},
            director_result={"status": "blocked", "error": "totally unknown bizarre issue"},
            pm_state={},
            retry_count=1,
            max_retries=3,
        )
        assert result.decision == BlockedDecision.SKIP_AND_CONTINUE

    def test_auto_degrade_budget_exhausted_fallback_to_skip(self) -> None:
        result = evaluate_blocked_policy(
            strategy=BlockedStrategy.AUTO,
            task={"task_id": "T1"},
            director_result={"status": "blocked", "error": "rate limit exceeded"},
            pm_state={"degrade_retry_count": 5},
            retry_count=1,
            max_retries=3,
            degrade_retry_budget=1,
        )
        assert result.decision == BlockedDecision.SKIP_AND_CONTINUE

    def test_auto_no_error_text_uses_fallback(self) -> None:
        result = evaluate_blocked_policy(
            strategy=BlockedStrategy.AUTO,
            task={"task_id": "T1"},
            director_result={"status": "blocked"},
            pm_state={},
            retry_count=1,
            max_retries=3,
        )
        assert result.decision == BlockedDecision.SKIP_AND_CONTINUE


class TestEvaluateBlockedPolicyUnknownStrategy:
    """Tests for unknown strategy fallback."""

    def test_unknown_strategy_fallback_to_skip(self) -> None:
        result = evaluate_blocked_policy(
            strategy="nonexistent",
            task={"task_id": "T1"},
            director_result={"status": "blocked"},
            pm_state={},
            retry_count=1,
            max_retries=3,
        )
        assert result.decision == BlockedDecision.SKIP_AND_CONTINUE
        assert result.exit_code == 0
        assert "unknown_strategy" in result.reason


class TestShouldApplyDegradeSettings:
    """Tests for should_apply_degrade_settings helper."""

    def test_should_apply_degrade_settings(self) -> None:
        pm_state: dict[str, Any] = {"degrade_settings": {"serial_mode": True}}
        should_apply, settings = should_apply_degrade_settings(pm_state)
        assert should_apply is True
        assert settings["serial_mode"] is True

    def test_should_not_apply_empty_settings(self) -> None:
        pm_state: dict[str, Any] = {"degrade_settings": {}}
        should_apply, _settings = should_apply_degrade_settings(pm_state)
        assert should_apply is False

    def test_should_not_apply_missing_settings(self) -> None:
        pm_state: dict[str, Any] = {}
        should_apply, _settings = should_apply_degrade_settings(pm_state)
        assert should_apply is False

    def test_should_not_apply_none_settings(self) -> None:
        pm_state: dict[str, Any] = {"degrade_settings": None}
        should_apply, _settings = should_apply_degrade_settings(pm_state)
        assert should_apply is False


class TestConsumeDegradeSettings:
    """Tests for consume_degrade_settings helper."""

    def test_consume_degrade_settings(self) -> None:
        pm_state: dict[str, Any] = {"degrade_settings": {"serial_mode": True}, "other_key": "value"}
        new_state = consume_degrade_settings(pm_state)
        assert "degrade_settings" not in new_state
        assert new_state["other_key"] == "value"

    def test_consume_missing_settings(self) -> None:
        pm_state: dict[str, Any] = {"other_key": "value"}
        new_state = consume_degrade_settings(pm_state)
        assert new_state == {"other_key": "value"}

    def test_does_not_mutate_original(self) -> None:
        pm_state: dict[str, Any] = {"degrade_settings": {"serial_mode": True}, "other_key": "value"}
        new_state = consume_degrade_settings(pm_state)
        assert "degrade_settings" in pm_state
        assert "degrade_settings" not in new_state


class TestGetBlockedPolicyFromEnv:
    """Tests for get_blocked_policy_from_env helper."""

    def test_defaults(self, monkeypatch: Any) -> None:
        monkeypatch.delenv("KERNELONE_PM_BLOCKED_STRATEGY", raising=False)
        monkeypatch.delenv("KERNELONE_PM_BLOCKED_DEGRADE_RETRIES", raising=False)
        strategy, retries = get_blocked_policy_from_env()
        assert strategy == "auto"
        assert retries == 1

    def test_custom_values(self, monkeypatch: Any) -> None:
        monkeypatch.setenv("KERNELONE_PM_BLOCKED_STRATEGY", "skip")
        monkeypatch.setenv("KERNELONE_PM_BLOCKED_DEGRADE_RETRIES", "3")
        strategy, retries = get_blocked_policy_from_env()
        assert strategy == "skip"
        assert retries == 3

    def test_invalid_retries_fallback(self, monkeypatch: Any) -> None:
        monkeypatch.setenv("KERNELONE_PM_BLOCKED_DEGRADE_RETRIES", "invalid")
        _strategy, retries = get_blocked_policy_from_env()
        assert retries == 1

    def test_zero_retries(self, monkeypatch: Any) -> None:
        monkeypatch.setenv("KERNELONE_PM_BLOCKED_DEGRADE_RETRIES", "0")
        _strategy, retries = get_blocked_policy_from_env()
        assert retries == 0
