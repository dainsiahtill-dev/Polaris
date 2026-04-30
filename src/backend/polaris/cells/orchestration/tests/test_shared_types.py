"""Tests for polaris.cells.orchestration.shared_types."""

from __future__ import annotations

import warnings
from datetime import datetime, timezone

import pytest
from polaris.cells.orchestration.shared_types import (
    ErrorCategory,
    ErrorClassifier,
    ErrorRecord,
    RecoveryRecommendation,
)
from polaris.kernelone.errors import ErrorCategory as CanonicalErrorCategory


class TestErrorCategory:
    """Tests for ErrorCategory type alias and re-export."""

    def test_error_category_is_canonical(self) -> None:
        assert ErrorCategory is CanonicalErrorCategory

    def test_error_category_values(self) -> None:
        assert ErrorCategory.TRANSIENT_NETWORK.value == "transient_network"
        assert ErrorCategory.PERMANENT_AUTH.value == "permanent_auth"
        assert ErrorCategory.SYSTEM_TIMEOUT.value == "system_timeout"

    def test_error_category_is_str_enum(self) -> None:
        assert issubclass(ErrorCategory, str)
        assert issubclass(ErrorCategory, CanonicalErrorCategory)

    def test_deprecation_warning_on_direct_import(self) -> None:
        # Direct import from module triggers deprecation
        with warnings.catch_warnings(record=True):
            warnings.simplefilter("always")
            from polaris.cells.orchestration import shared_types

            # The actual deprecation happens when accessing ErrorCategory
            # through the module's __getattr__
            with pytest.warns(DeprecationWarning):
                cat = shared_types.__getattr__("ErrorCategory")
                assert cat is CanonicalErrorCategory

    def test_attribute_error_for_unknown(self) -> None:
        from polaris.cells.orchestration import shared_types

        with pytest.raises(AttributeError):
            shared_types.__getattr__("NonExistent")


class TestErrorRecord:
    """Tests for ErrorRecord dataclass."""

    def test_create_minimal(self) -> None:
        record = ErrorRecord(
            category=ErrorCategory.TRANSIENT_NETWORK,
            message="connection refused",
        )
        assert record.category == ErrorCategory.TRANSIENT_NETWORK
        assert record.message == "connection refused"
        assert isinstance(record.timestamp, datetime)
        assert record.context == {}
        assert record.retry_count == 0

    def test_create_with_context(self) -> None:
        record = ErrorRecord(
            category=ErrorCategory.PERMANENT_AUTH,
            message="auth failed",
            context={"endpoint": "/api/v1"},
        )
        assert record.context == {"endpoint": "/api/v1"}

    def test_create_with_retry_count(self) -> None:
        record = ErrorRecord(
            category=ErrorCategory.SYSTEM_TIMEOUT,
            message="timeout",
            retry_count=3,
        )
        assert record.retry_count == 3

    def test_create_with_custom_timestamp(self) -> None:
        ts = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        record = ErrorRecord(
            category=ErrorCategory.UNKNOWN,
            message="test",
            timestamp=ts,
        )
        assert record.timestamp == ts

    def test_timestamp_default_is_utc(self) -> None:
        record = ErrorRecord(
            category=ErrorCategory.UNKNOWN,
            message="test",
        )
        assert record.timestamp.tzinfo is not None

    def test_default_context_is_empty_dict(self) -> None:
        record = ErrorRecord(
            category=ErrorCategory.UNKNOWN,
            message="test",
        )
        assert record.context == {}

    def test_repr(self) -> None:
        record = ErrorRecord(
            category=ErrorCategory.UNKNOWN,
            message="test",
        )
        assert "ErrorRecord" in repr(record)


class TestRecoveryRecommendation:
    """Tests for RecoveryRecommendation dataclass."""

    def test_create(self) -> None:
        rec = RecoveryRecommendation(
            can_retry=True,
            retry_delay_seconds=1.0,
            max_retries=3,
            strategy="backoff",
            reason="Network issues are usually transient",
        )
        assert rec.can_retry is True
        assert rec.retry_delay_seconds == 1.0
        assert rec.max_retries == 3
        assert rec.strategy == "backoff"
        assert rec.reason == "Network issues are usually transient"

    def test_create_no_retry(self) -> None:
        rec = RecoveryRecommendation(
            can_retry=False,
            retry_delay_seconds=0.0,
            max_retries=0,
            strategy="abort",
            reason="Cannot retry",
        )
        assert rec.can_retry is False
        assert rec.max_retries == 0

    def test_mutable(self) -> None:
        rec = RecoveryRecommendation(
            can_retry=True,
            retry_delay_seconds=1.0,
            max_retries=3,
            strategy="backoff",
            reason="r",
        )
        rec.max_retries = 5
        assert rec.max_retries == 5

    def test_various_strategies(self) -> None:
        for strategy in ["immediate", "backoff", "manual", "abort"]:
            rec = RecoveryRecommendation(
                can_retry=True,
                retry_delay_seconds=0.0,
                max_retries=0,
                strategy=strategy,
                reason="test",
            )
            assert rec.strategy == strategy


class TestErrorClassifierClassify:
    """Tests for ErrorClassifier.classify method."""

    def test_classify_transient_network_connection_refused(self) -> None:
        error = Exception("connection refused")
        assert ErrorClassifier.classify(error) == ErrorCategory.TRANSIENT_NETWORK

    def test_classify_transient_network_broken_pipe(self) -> None:
        error = Exception("broken pipe")
        assert ErrorClassifier.classify(error) == ErrorCategory.TRANSIENT_NETWORK

    def test_classify_transient_rate_limit(self) -> None:
        error = Exception("rate limit exceeded")
        assert ErrorClassifier.classify(error) == ErrorCategory.TRANSIENT_RATE_LIMIT

    def test_classify_transient_rate_limit_429(self) -> None:
        error = Exception("429 too many requests")
        assert ErrorClassifier.classify(error) == ErrorCategory.TRANSIENT_RATE_LIMIT

    def test_classify_permanent_auth(self) -> None:
        error = Exception("unauthorized access")
        assert ErrorClassifier.classify(error) == ErrorCategory.PERMANENT_AUTH

    def test_classify_permanent_auth_permission_denied(self) -> None:
        error = Exception("permission denied")
        assert ErrorClassifier.classify(error) == ErrorCategory.PERMANENT_AUTH

    def test_classify_permanent_validation(self) -> None:
        error = Exception("validation failed")
        assert ErrorClassifier.classify(error) == ErrorCategory.PERMANENT_VALIDATION

    def test_classify_permanent_not_found(self) -> None:
        error = Exception("resource not found")
        assert ErrorClassifier.classify(error) == ErrorCategory.PERMANENT_NOT_FOUND

    def test_classify_system_timeout(self) -> None:
        error = Exception("operation timeout")
        assert ErrorClassifier.classify(error) == ErrorCategory.SYSTEM_TIMEOUT

    def test_classify_workflow_deadlock(self) -> None:
        error = Exception("deadlock detected")
        assert ErrorClassifier.classify(error) == ErrorCategory.WORKFLOW_DEADLOCK

    def test_classify_timeout_error_instance(self) -> None:
        error = TimeoutError("operation timed out")
        assert ErrorClassifier.classify(error) == ErrorCategory.SYSTEM_TIMEOUT

    def test_classify_permission_error_instance(self) -> None:
        error = PermissionError("access denied")
        assert ErrorClassifier.classify(error) == ErrorCategory.PERMANENT_AUTH

    def test_classify_file_not_found_error_instance(self) -> None:
        error = FileNotFoundError("file missing")
        assert ErrorClassifier.classify(error) == ErrorCategory.PERMANENT_NOT_FOUND

    def test_classify_value_error_instance(self) -> None:
        error = ValueError("bad input")
        assert ErrorClassifier.classify(error) == ErrorCategory.PERMANENT_VALIDATION

    def test_classify_unknown_error(self) -> None:
        error = Exception("something completely unexpected")
        assert ErrorClassifier.classify(error) == ErrorCategory.SYSTEM_UNKNOWN

    def test_classify_empty_message(self) -> None:
        error = Exception("")
        assert ErrorClassifier.classify(error) == ErrorCategory.SYSTEM_UNKNOWN


class TestErrorClassifierGetRecoveryRecommendation:
    """Tests for ErrorClassifier.get_recovery_recommendation method."""

    def test_transient_network_recommendation(self) -> None:
        rec = ErrorClassifier.get_recovery_recommendation(ErrorCategory.TRANSIENT_NETWORK)
        assert rec.can_retry is True
        assert rec.strategy == "backoff"
        assert rec.max_retries == 3

    def test_transient_rate_limit_recommendation(self) -> None:
        rec = ErrorClassifier.get_recovery_recommendation(ErrorCategory.TRANSIENT_RATE_LIMIT)
        assert rec.can_retry is True
        assert rec.strategy == "backoff"
        assert rec.max_retries == 5

    def test_permanent_auth_recommendation(self) -> None:
        rec = ErrorClassifier.get_recovery_recommendation(ErrorCategory.PERMANENT_AUTH)
        assert rec.can_retry is False
        assert rec.strategy == "manual"
        assert rec.max_retries == 0

    def test_permanent_not_found_recommendation(self) -> None:
        rec = ErrorClassifier.get_recovery_recommendation(ErrorCategory.PERMANENT_NOT_FOUND)
        assert rec.can_retry is False
        assert rec.strategy == "abort"

    def test_unknown_category_fallback(self) -> None:
        rec = ErrorClassifier.get_recovery_recommendation(ErrorCategory.UNKNOWN)
        assert rec.can_retry is False
        assert rec.strategy == "abort"
        assert rec.max_retries == 0
        assert rec.reason == "Unknown error type"

    def test_workflow_canceled_recommendation(self) -> None:
        rec = ErrorClassifier.get_recovery_recommendation(ErrorCategory.WORKFLOW_CANCELED)
        assert rec.can_retry is False
        assert rec.strategy == "abort"

    def test_system_capacity_recommendation(self) -> None:
        rec = ErrorClassifier.get_recovery_recommendation(ErrorCategory.SYSTEM_CAPACITY)
        assert rec.can_retry is True
        assert rec.retry_delay_seconds == 30.0


class TestErrorClassifierAnalyze:
    """Tests for ErrorClassifier.analyze method."""

    def test_analyze_returns_tuple(self) -> None:
        error = Exception("connection refused")
        result = ErrorClassifier.analyze(error)
        assert isinstance(result, tuple)
        assert len(result) == 2

    def test_analyze_transient_network(self) -> None:
        error = Exception("connection refused")
        category, rec = ErrorClassifier.analyze(error)
        assert category == ErrorCategory.TRANSIENT_NETWORK
        assert rec.can_retry is True

    def test_analyze_permanent_auth(self) -> None:
        error = Exception("unauthorized")
        category, rec = ErrorClassifier.analyze(error)
        assert category == ErrorCategory.PERMANENT_AUTH
        assert rec.can_retry is False


class TestErrorClassifierClassifyFromMessage:
    """Tests for ErrorClassifier.classify_from_message method."""

    def test_classify_from_message_returns_tuple(self) -> None:
        result = ErrorClassifier.classify_from_message("connection refused")
        assert isinstance(result, tuple)
        assert len(result) == 2

    def test_classify_from_message_transient_network(self) -> None:
        category, rec = ErrorClassifier.classify_from_message("connection refused")
        assert category == ErrorCategory.TRANSIENT_NETWORK
        assert rec.can_retry is True

    def test_classify_from_message_unknown(self) -> None:
        category, rec = ErrorClassifier.classify_from_message("random text")
        assert category == ErrorCategory.SYSTEM_UNKNOWN
        assert rec.can_retry is True

    def test_classify_from_message_empty(self) -> None:
        category, _rec = ErrorClassifier.classify_from_message("")
        assert category == ErrorCategory.SYSTEM_UNKNOWN


class TestModuleExports:
    """Tests for module __all__ exports."""

    def test_all_exports_present(self) -> None:
        from polaris.cells.orchestration import shared_types as mod

        assert hasattr(mod, "__all__")
        assert "ErrorCategory" in mod.__all__
        assert "ErrorClassifier" in mod.__all__
        assert "ErrorRecord" in mod.__all__
        assert "RecoveryRecommendation" in mod.__all__
        assert len(mod.__all__) == 4
