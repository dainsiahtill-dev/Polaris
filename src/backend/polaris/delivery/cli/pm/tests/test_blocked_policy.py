"""Tests for blocked task policy engine."""

import pytest
from polaris.delivery.cli.pm.blocked_policy import (
    BlockedDecision,
    BlockedStrategy,
    _classify_error,
    consume_degrade_settings,
    evaluate_blocked_policy,
    get_blocked_policy_from_env,
    normalize_director_status,
    should_apply_degrade_settings,
)


class TestNormalizeDirectorStatus:
    """Test status normalization."""

    def test_success_statuses(self) -> None:
        """Test that success-like statuses are normalized to 'success'."""
        for status in ["success", "done", "completed", "pass", "passed"]:
            assert normalize_director_status(status) == "success"

    def test_failed_statuses(self) -> None:
        """Test that failure-like statuses are normalized to 'failed'."""
        for status in ["fail", "failed", "error", "cancelled", "timeout"]:
            assert normalize_director_status(status) == "failed"

    def test_blocked_statuses(self) -> None:
        """Test that blocked-like statuses are normalized to 'blocked'."""
        for status in ["blocked", "block"]:
            assert normalize_director_status(status) == "blocked"

    def test_needs_continue_statuses(self) -> None:
        """Test that continue-like statuses are normalized to 'needs_continue'."""
        for status in ["needs_continue", "need_continue", "continue", "deferred"]:
            assert normalize_director_status(status) == "needs_continue"

    def test_unknown_status(self) -> None:
        """Test that unknown statuses are preserved."""
        assert normalize_director_status("unknown") == "unknown"
        assert normalize_director_status("random") == "random"

    def test_none_and_empty(self) -> None:
        """Test that None and empty strings return 'unknown'."""
        assert normalize_director_status(None) == "unknown"
        assert normalize_director_status("") == "unknown"


class TestErrorClassification:
    """Test error classification for auto strategy."""

    def test_rate_limit_detection(self) -> None:
        """Test rate limit error classification."""
        error_class, confidence = _classify_error("rate limit exceeded, please retry")
        assert error_class == "llm_rate_limit"
        assert confidence > 0.5

    def test_context_length_detection(self) -> None:
        """Test context length error classification."""
        error_class, confidence = _classify_error("context length too long, max tokens exceeded")
        assert error_class == "llm_context_length"
        assert confidence > 0.5

    def test_permission_denied_detection(self) -> None:
        """Test permission denied error classification."""
        error_class, confidence = _classify_error("permission denied, access unauthorized")
        assert error_class == "permission_denied"
        assert confidence > 0.5

    def test_unknown_error(self) -> None:
        """Test that unknown errors return 'unknown'."""
        error_class, confidence = _classify_error("some random error message")
        assert error_class == "unknown"
        assert confidence == 0.0


class TestSkipStrategy:
    """Test SKIP strategy behavior."""

    def test_skip_returns_continue_decision(self) -> None:
        """Test that skip strategy returns SKIP_AND_CONTINUE decision."""
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
        """Test that skip strategy increments blocked_skip_count."""
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
        """Test that skip strategy includes task status update."""
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
        """Test that skip strategy resets consecutive_blocked counter."""
        result = evaluate_blocked_policy(
            strategy=BlockedStrategy.SKIP,
            task={"task_id": "T1"},
            director_result={"status": "blocked"},
            pm_state={"consecutive_blocked": 5},
            retry_count=1,
            max_retries=3,
        )
        assert result.pm_state_patch.get("consecutive_blocked") == 0


class TestManualStrategy:
    """Test MANUAL strategy behavior."""

    def test_manual_returns_stop_decision(self) -> None:
        """Test that manual strategy returns MANUAL_STOP decision."""
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
        """Test that manual strategy sets awaiting_manual_intervention flag."""
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
        assert "T1" in result.pm_state_patch["manual_intervention_reason"]


class TestDegradeRetryStrategy:
    """Test DEGRADE_RETRY strategy behavior."""

    def test_degrade_retry_with_budget(self) -> None:
        """Test that degrade retry works when budget remains."""
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
        """Test that degrade settings include expected fields."""
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
        """Test that degrade retry falls back to manual when budget exhausted."""
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


class TestAutoStrategy:
    """Test AUTO strategy behavior."""

    def test_auto_rate_limit_uses_degrade(self) -> None:
        """Test that rate limit errors trigger degrade retry."""
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
        """Test that permission errors trigger manual stop."""
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
        """Test that context length errors trigger skip."""
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
        """Test that critical tasks (auth/security) prefer manual stop."""
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


class TestDegradeSettingsHelpers:
    """Test degrade settings helper functions."""

    def test_should_apply_degrade_settings(self) -> None:
        """Test should_apply_degrade_settings function."""
        pm_state = {"degrade_settings": {"serial_mode": True}}
        should_apply, settings = should_apply_degrade_settings(pm_state)
        assert should_apply is True
        assert settings["serial_mode"] is True

    def test_should_not_apply_empty_settings(self) -> None:
        """Test that empty settings are not applied."""
        pm_state = {"degrade_settings": {}}
        should_apply, _settings = should_apply_degrade_settings(pm_state)
        assert should_apply is False

    def test_should_not_apply_missing_settings(self) -> None:
        """Test that missing settings are not applied."""
        pm_state = {}
        should_apply, _settings = should_apply_degrade_settings(pm_state)
        assert should_apply is False

    def test_consume_degrade_settings(self) -> None:
        """Test that consume_degrade_settings removes the settings."""
        pm_state = {"degrade_settings": {"serial_mode": True}, "other_key": "value"}
        new_state = consume_degrade_settings(pm_state)
        assert "degrade_settings" not in new_state
        assert new_state["other_key"] == "value"


class TestEnvConfiguration:
    """Test environment variable configuration."""

    def test_get_blocked_policy_from_env_defaults(self, monkeypatch) -> None:
        """Test that default values are returned when env vars are not set."""
        monkeypatch.delenv("POLARIS_PM_BLOCKED_STRATEGY", raising=False)
        monkeypatch.delenv("POLARIS_PM_BLOCKED_DEGRADE_RETRIES", raising=False)
        strategy, retries = get_blocked_policy_from_env()
        assert strategy == "auto"
        assert retries == 1

    def test_get_blocked_policy_from_env_custom(self, monkeypatch) -> None:
        """Test that custom values are read from env vars."""
        monkeypatch.setenv("POLARIS_PM_BLOCKED_STRATEGY", "skip")
        monkeypatch.setenv("POLARIS_PM_BLOCKED_DEGRADE_RETRIES", "3")
        strategy, retries = get_blocked_policy_from_env()
        assert strategy == "skip"
        assert retries == 3


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
