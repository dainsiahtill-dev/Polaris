"""Tests for shared_types module in orchestration cells."""

from __future__ import annotations

import warnings
from datetime import datetime, timezone

import pytest
from polaris.cells.orchestration.shared_types import (
    ErrorClassifier,
    ErrorRecord,
    RecoveryRecommendation,
)
from polaris.kernelone.errors import ErrorCategory


# =============================================================================
# ErrorRecord
# =============================================================================
def test_error_record_defaults():
    record = ErrorRecord(category=ErrorCategory.SYSTEM_TIMEOUT, message="timeout")
    assert record.category == ErrorCategory.SYSTEM_TIMEOUT
    assert record.message == "timeout"
    assert isinstance(record.timestamp, datetime)
    assert record.timestamp.tzinfo is not None
    assert record.context == {}
    assert record.retry_count == 0


def test_error_record_explicit_values():
    ts = datetime(2025, 1, 1, tzinfo=timezone.utc)
    record = ErrorRecord(
        category=ErrorCategory.PERMANENT_AUTH,
        message="auth failed",
        timestamp=ts,
        context={"user": "alice"},
        retry_count=2,
    )
    assert record.timestamp == ts
    assert record.context == {"user": "alice"}
    assert record.retry_count == 2


# =============================================================================
# RecoveryRecommendation
# =============================================================================
def test_recovery_recommendation_creation():
    rec = RecoveryRecommendation(
        can_retry=True,
        retry_delay_seconds=5.0,
        max_retries=3,
        strategy="backoff",
        reason="transient",
    )
    assert rec.can_retry is True
    assert rec.retry_delay_seconds == 5.0
    assert rec.max_retries == 3
    assert rec.strategy == "backoff"
    assert rec.reason == "transient"


# =============================================================================
# ErrorClassifier.classify
# =============================================================================
class TestErrorClassifierClassify:
    def test_classify_transient_network(self):
        err = Exception("Connection refused by peer")
        assert ErrorClassifier.classify(err) == ErrorCategory.TRANSIENT_NETWORK

    def test_classify_transient_rate_limit(self):
        err = Exception("Too many requests, 429")
        assert ErrorClassifier.classify(err) == ErrorCategory.TRANSIENT_RATE_LIMIT

    def test_classify_transient_resource(self):
        err = Exception("Out of memory on node")
        assert ErrorClassifier.classify(err) == ErrorCategory.TRANSIENT_RESOURCE

    def test_classify_permanent_auth(self):
        err = Exception("Authentication failed for user")
        assert ErrorClassifier.classify(err) == ErrorCategory.PERMANENT_AUTH

    def test_classify_permanent_validation(self):
        err = Exception("Validation failed: bad request")
        assert ErrorClassifier.classify(err) == ErrorCategory.PERMANENT_VALIDATION

    def test_classify_permanent_not_found(self):
        err = Exception("Resource does not exist")
        assert ErrorClassifier.classify(err) == ErrorCategory.PERMANENT_NOT_FOUND

    def test_classify_system_timeout(self):
        err = Exception("Deadline exceeded for operation")
        assert ErrorClassifier.classify(err) == ErrorCategory.SYSTEM_TIMEOUT

    def test_classify_workflow_deadlock(self):
        err = Exception("Deadlock detected in dependency graph")
        assert ErrorClassifier.classify(err) == ErrorCategory.WORKFLOW_DEADLOCK

    def test_classify_isinstance_timeout_error(self):
        err = TimeoutError("something")
        assert ErrorClassifier.classify(err) == ErrorCategory.SYSTEM_TIMEOUT

    def test_classify_isinstance_permission_error(self):
        err = PermissionError("denied")
        assert ErrorClassifier.classify(err) == ErrorCategory.PERMANENT_AUTH

    def test_classify_isinstance_file_not_found(self):
        err = FileNotFoundError("missing")
        assert ErrorClassifier.classify(err) == ErrorCategory.PERMANENT_NOT_FOUND

    def test_classify_isinstance_value_error(self):
        err = ValueError("bad input")
        assert ErrorClassifier.classify(err) == ErrorCategory.PERMANENT_VALIDATION

    def test_classify_unknown(self):
        err = Exception("Something completely unrelated")
        assert ErrorClassifier.classify(err) == ErrorCategory.SYSTEM_UNKNOWN

    def test_classify_empty_message(self):
        err = Exception("")
        assert ErrorClassifier.classify(err) == ErrorCategory.SYSTEM_UNKNOWN

    def test_classify_pattern_priority_order(self):
        # First matching pattern wins based on dict order
        # "timeout" is in SYSTEM_TIMEOUT patterns, but also test a unique one
        err = Exception("rate limit throttled")
        # This matches "rate limit" before "throttled"
        assert ErrorClassifier.classify(err) == ErrorCategory.TRANSIENT_RATE_LIMIT


# =============================================================================
# ErrorClassifier.get_recovery_recommendation
# =============================================================================
class TestErrorClassifierRecovery:
    def test_recovery_transient_network(self):
        rec = ErrorClassifier.get_recovery_recommendation(ErrorCategory.TRANSIENT_NETWORK)
        assert rec.can_retry is True
        assert rec.strategy == "backoff"
        assert rec.max_retries == 3

    def test_recovery_permanent_auth(self):
        rec = ErrorClassifier.get_recovery_recommendation(ErrorCategory.PERMANENT_AUTH)
        assert rec.can_retry is False
        assert rec.strategy == "manual"
        assert rec.max_retries == 0

    def test_recovery_permanent_not_found(self):
        rec = ErrorClassifier.get_recovery_recommendation(ErrorCategory.PERMANENT_NOT_FOUND)
        assert rec.can_retry is False
        assert rec.strategy == "abort"

    def test_recovery_system_timeout(self):
        rec = ErrorClassifier.get_recovery_recommendation(ErrorCategory.SYSTEM_TIMEOUT)
        assert rec.can_retry is True
        assert rec.retry_delay_seconds == 2.0
        assert rec.max_retries == 2

    def test_recovery_unknown_category(self):
        rec = ErrorClassifier.get_recovery_recommendation(ErrorCategory.PROVIDER_ERROR)
        assert rec.can_retry is False
        assert rec.strategy == "abort"
        assert rec.reason == "Unknown error type"


# =============================================================================
# ErrorClassifier.analyze
# =============================================================================
def test_analyze_full():
    category, rec = ErrorClassifier.analyze(Exception("Connection refused"))
    assert category == ErrorCategory.TRANSIENT_NETWORK
    assert rec.can_retry is True
    assert rec.strategy == "backoff"


# =============================================================================
# ErrorClassifier.classify_from_message
# =============================================================================
def test_classify_from_message():
    category, rec = ErrorClassifier.classify_from_message("Disk full, cannot write")
    assert category == ErrorCategory.TRANSIENT_RESOURCE
    assert rec.can_retry is True


def test_classify_from_message_unknown():
    category, rec = ErrorClassifier.classify_from_message("foobar")
    assert category == ErrorCategory.SYSTEM_UNKNOWN
    # SYSTEM_UNKNOWN is explicitly mapped to can_retry=True in _RECOVERY_STRATEGIES
    assert rec.can_retry is True
    assert rec.strategy == "backoff"


# =============================================================================
# Deprecation re-export via __getattr__
# =============================================================================
def test_error_category_deprecation_warning():
    import polaris.cells.orchestration.shared_types as _st

    # Remove the runtime attribute so __getattr__ is invoked
    old = _st.ErrorCategory
    object.__delattr__(_st, "ErrorCategory")
    try:
        with pytest.warns(DeprecationWarning, match="has been moved to polaris.kernelone.errors"):
            _ = _st.ErrorCategory
    finally:
        _st.ErrorCategory = old


def test_error_category_is_same_enum():
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        from polaris.cells.orchestration import shared_types

        assert shared_types.ErrorCategory is ErrorCategory


def test_invalid_attr_raises():
    from polaris.cells.orchestration import shared_types

    with pytest.raises(AttributeError, match="has no attribute"):
        _ = shared_types.NonExistentThing
