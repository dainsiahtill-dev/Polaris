"""Tests for polaris.kernelone.errors exception hierarchy.

Covers KernelOneError base class, all major subclasses, ErrorCategory,
and the classify_error function.
"""

from __future__ import annotations

import asyncio

import pytest
from polaris.kernelone.errors import (
    AuthenticationError,
    BudgetExceededError,
    CircuitBreakerOpenError,
    ConfigLoadError,
    ConfigMigrationError,
    ConfigurationError,
    ConfigValidationError,
    DatabaseConnectionError,
    DatabaseDriverNotAvailableError,
    DatabaseError,
    ErrorCategory,
    FileNotFoundError,
    KernelOneError,
    NetworkError,
    PathTraversalError,
    RateLimitError,
    RetryableError,
    ShellDisallowedError,
    StateError,
    TimeoutError,
    ToolAuthorizationError,
    ToolExecutionError,
    ValidationError,
    classify_error,
)


class TestKernelOneError:
    def test_basic_exception(self) -> None:
        with pytest.raises(KernelOneError, match="Something broke"):
            raise KernelOneError("Something broke")

    def test_code_attribute(self) -> None:
        err = KernelOneError("msg", code="CUSTOM_CODE")
        assert err.code == "CUSTOM_CODE"

    def test_retryable_attribute(self) -> None:
        err = KernelOneError("msg", retryable=True)
        assert err.retryable is True

    def test_cause_attribute(self) -> None:
        cause = ValueError("original")
        err = KernelOneError("wrapped", cause=cause)
        assert err.__cause__ is cause

    def test_details_attribute(self) -> None:
        err = KernelOneError("msg", details={"key": "value"})
        assert err.details == {"key": "value"}

    def test_to_dict_basic(self) -> None:
        err = KernelOneError("msg", code="C", retryable=True)
        d = err.to_dict()
        assert d["type"] == "KernelOneError"
        assert d["code"] == "C"
        assert d["message"] == "msg"
        assert d["retryable"] is True

    def test_to_dict_with_cause(self) -> None:
        err = KernelOneError("wrapped", cause=ValueError("original"))
        d = err.to_dict()
        assert d["cause"]["type"] == "ValueError"
        assert d["cause"]["message"] == "original"

    def test_to_dict_with_details(self) -> None:
        err = KernelOneError("msg", details={"a": 1})
        d = err.to_dict()
        assert d["details"] == {"a": 1}

    def test_str_returns_message(self) -> None:
        err = KernelOneError("hello")
        assert str(err) == "hello"


class TestConfigurationError:
    def test_field_attribute(self) -> None:
        err = ConfigLoadError("failed", config_path="/etc/config.yaml")
        assert err.config_path == "/etc/config.yaml"
        assert err.details["config_path"] == "/etc/config.yaml"

    def test_config_validation_error(self) -> None:
        err = ConfigValidationError("invalid", validation_errors=["missing key", "bad type"])
        assert err.validation_errors == ["missing key", "bad type"]
        assert err.details["validation_errors"] == ["missing key", "bad type"]

    def test_config_migration_error(self) -> None:
        err = ConfigMigrationError("failed", from_version="1.0", to_version="2.0")
        assert err.details["from_version"] == "1.0"
        assert err.details["to_version"] == "2.0"

    def test_default_retryable_false(self) -> None:
        err = ConfigurationError("bad config")
        assert err.retryable is False


class TestValidationError:
    def test_field_and_constraint(self) -> None:
        err = ValidationError("bad", field="name", constraint="required")
        assert err.field == "name"
        assert err.constraint == "required"
        assert err.details["field"] == "name"
        assert err.details["constraint"] == "required"

    def test_path_traversal_error(self) -> None:
        err = PathTraversalError("not allowed", path="/../etc", allowed_root="/home")
        assert err.details["path"] == "/../etc"
        assert err.details["allowed_root"] == "/home"


class TestExecutionError:
    def test_operation_and_tool_name(self) -> None:
        err = ToolExecutionError("failed", tool_name="git", exit_code=1)
        assert err.tool_name == "git"
        assert err.exit_code == 1
        assert err.details["exit_code"] == 1

    def test_shell_disallowed_error(self) -> None:
        err = ShellDisallowedError(command="rm -rf /")
        assert err.details["command"] == "rm -rf /"
        assert err.retryable is False

    def test_budget_exceeded_error(self) -> None:
        err = BudgetExceededError("too much", limit=1000, requested=2000, current=500)
        assert err.limit == 1000
        assert err.requested == 2000
        assert err.current == 500
        assert err.retryable is True

    def test_tool_authorization_error(self) -> None:
        err = ToolAuthorizationError("denied", tool_name="rm", role="agent", reason="policy")
        assert err.details["role"] == "agent"
        assert err.details["reason"] == "policy"
        assert err.retryable is False


class TestResourceError:
    def test_file_not_found_error(self) -> None:
        err = FileNotFoundError("missing", file_path="/tmp/x")
        assert err.file_path == "/tmp/x"
        assert err.details["resource_type"] == "file"
        assert err.retryable is False

    def test_database_error(self) -> None:
        err = DatabaseError("fail", database_name="main", operation="select")
        assert err.database_name == "main"
        assert err.operation == "select"

    def test_database_connection_error_retryable(self) -> None:
        err = DatabaseConnectionError("cannot connect")
        assert err.retryable is True

    def test_database_driver_not_available(self) -> None:
        err = DatabaseDriverNotAvailableError("missing driver", driver_name="psycopg2")
        assert err.details["driver_name"] == "psycopg2"
        assert err.retryable is False


class TestCommunicationError:
    def test_network_error(self) -> None:
        err = NetworkError("down", url="https://example.com")
        assert err.url == "https://example.com"
        assert err.retryable is True

    def test_timeout_error(self) -> None:
        err = TimeoutError("slow", timeout_seconds=30.0, operation="fetch")
        assert err.timeout_seconds == 30.0
        assert err.operation == "fetch"

    def test_rate_limit_error(self) -> None:
        err = RateLimitError("too fast", retry_after=60.0)
        assert err.retry_after == 60.0
        assert err.limit_type == "requests"

    def test_circuit_breaker_open(self) -> None:
        err = CircuitBreakerOpenError(circuit_name="db", retry_after=10.0)
        assert "db" in str(err)
        assert err.circuit_name == "db"

    def test_authentication_error(self) -> None:
        err = AuthenticationError("bad creds", provider="oauth")
        assert err.provider == "oauth"
        assert err.retryable is False


class TestStateError:
    def test_invalid_state_transition(self) -> None:
        err = StateError("bad", current_state="idle", target_state="running")
        assert err.current_state == "idle"
        assert err.target_state == "running"
        assert err.retryable is False


class TestRetryableErrors:
    def test_retryable_error(self) -> None:
        err = RetryableError("try again")
        assert err.retryable is True
        assert err.code == "RETRYABLE_ERROR"

    def test_non_retryable_error(self) -> None:
        from polaris.kernelone.errors import NonRetryableError

        err = NonRetryableError("never")
        assert err.retryable is False
        assert err.code == "NON_RETRYABLE_ERROR"


class TestErrorCategory:
    def test_known_categories(self) -> None:
        assert ErrorCategory.UNKNOWN.value == "unknown"
        assert ErrorCategory.TIMEOUT.value == "timeout"
        assert ErrorCategory.NOT_FOUND.value == "not_found"

    def test_all_are_strings(self) -> None:
        for cat in ErrorCategory:
            assert isinstance(cat.value, str)


class TestClassifyError:
    def test_timeout_error(self) -> None:
        err = asyncio.TimeoutError("timed out")
        assert classify_error(err) == ErrorCategory.TIMEOUT

    def test_connection_error(self) -> None:
        err = ConnectionError("refused")
        assert classify_error(err) == ErrorCategory.NETWORK_ERROR

    def test_keyword_timeout(self) -> None:
        err = Exception("Socket timeout occurred")
        assert classify_error(err) == ErrorCategory.TIMEOUT

    def test_keyword_rate_limit(self) -> None:
        err = Exception("Rate limit exceeded: 429")
        assert classify_error(err) == ErrorCategory.RATE_LIMIT

    def test_keyword_connection(self) -> None:
        err = Exception("Network connection failed")
        assert classify_error(err) == ErrorCategory.NETWORK_ERROR

    def test_keyword_config(self) -> None:
        err = Exception("Configuration is invalid")
        assert classify_error(err) == ErrorCategory.CONFIG_ERROR

    def test_keyword_json(self) -> None:
        err = Exception("JSON parse failure")
        assert classify_error(err) == ErrorCategory.JSON_PARSE

    def test_unknown_fallback(self) -> None:
        err = Exception("Something completely different")
        assert classify_error(err) == ErrorCategory.UNKNOWN
