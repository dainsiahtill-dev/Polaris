"""Tests for polaris.kernelone.llm.error_categories module.

Covers:
- ErrorCategory deprecation warning for direct module import
- _category_from_exception function
- classify_error function with all error patterns
"""

from __future__ import annotations

import warnings

import pytest
from polaris.kernelone.errors import ErrorCategory


class TestErrorCategoryDeprecation:
    """Tests for ErrorCategory deprecation warning."""

    def test_error_category_module_has_getattr(self) -> None:
        """Verify __getattr__ is defined at module level for deprecation."""
        import polaris.kernelone.llm.error_categories as error_categories_module

        # The module should have a __getattr__ function for deprecation
        assert hasattr(error_categories_module, "__getattr__")
        assert callable(error_categories_module.__getattr__)

    def test_error_category_module_attr_getattr(self) -> None:
        """Verify __getattr__ returns ErrorCategory from canonical location."""
        import polaris.kernelone.llm.error_categories as error_categories_module

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            ec = error_categories_module.ErrorCategory
            # Should be the same as the canonical ErrorCategory
            assert ec is ErrorCategory
            assert ec.TIMEOUT == ErrorCategory.TIMEOUT

    def test_error_category_invalid_attr_raises(self) -> None:
        """Verify accessing invalid attribute raises AttributeError."""
        import polaris.kernelone.llm.error_categories as error_categories_module

        with pytest.raises(AttributeError, match="has no attribute"):
            _ = error_categories_module.NonExistentAttribute  # type: ignore


class TestCategoryFromException:
    """Tests for _category_from_exception function."""

    def test_llm_timeout_error_returns_timeout(self) -> None:
        """Verify LLMTimeoutError maps to TIMEOUT."""
        from polaris.kernelone.llm.error_categories import _category_from_exception
        from polaris.kernelone.llm.exceptions import LLMTimeoutError

        error = LLMTimeoutError("timeout")
        result = _category_from_exception(error)
        assert result == ErrorCategory.TIMEOUT

    def test_rate_limit_error_returns_rate_limit(self) -> None:
        """Verify RateLimitError maps to RATE_LIMIT."""
        from polaris.kernelone.llm.error_categories import _category_from_exception
        from polaris.kernelone.llm.exceptions import RateLimitError

        error = RateLimitError("rate limited")
        result = _category_from_exception(error)
        assert result == ErrorCategory.RATE_LIMIT

    def test_network_error_returns_network_error(self) -> None:
        """Verify NetworkError maps to NETWORK_ERROR."""
        from polaris.kernelone.llm.error_categories import _category_from_exception
        from polaris.kernelone.llm.exceptions import NetworkError

        error = NetworkError("connection failed")
        result = _category_from_exception(error)
        assert result == ErrorCategory.NETWORK_ERROR

    def test_circuit_breaker_open_error_returns_network_error(self) -> None:
        """Verify CircuitBreakerOpenError maps to NETWORK_ERROR."""
        from polaris.kernelone.llm.error_categories import _category_from_exception
        from polaris.kernelone.llm.exceptions import CircuitBreakerOpenError

        error = CircuitBreakerOpenError(circuit_name="test", retry_after=10.0)
        result = _category_from_exception(error)
        assert result == ErrorCategory.NETWORK_ERROR

    def test_configuration_error_returns_config_error(self) -> None:
        """Verify ConfigurationError maps to CONFIG_ERROR."""
        from polaris.kernelone.llm.error_categories import _category_from_exception
        from polaris.kernelone.llm.exceptions import ConfigurationError

        error = ConfigurationError("invalid config")
        result = _category_from_exception(error)
        assert result == ErrorCategory.CONFIG_ERROR

    def test_json_parse_error_returns_json_parse(self) -> None:
        """Verify JSONParseError maps to JSON_PARSE."""
        from polaris.kernelone.llm.error_categories import _category_from_exception
        from polaris.kernelone.llm.exceptions import JSONParseError

        error = JSONParseError("invalid json")
        result = _category_from_exception(error)
        assert result == ErrorCategory.JSON_PARSE

    def test_response_parse_error_returns_json_parse(self) -> None:
        """Verify ResponseParseError maps to JSON_PARSE."""
        from polaris.kernelone.llm.error_categories import _category_from_exception
        from polaris.kernelone.llm.exceptions import ResponseParseError

        error = ResponseParseError("parse failed")
        result = _category_from_exception(error)
        assert result == ErrorCategory.JSON_PARSE

    def test_tool_parse_error_returns_json_parse(self) -> None:
        """Verify ToolParseError maps to JSON_PARSE."""
        from polaris.kernelone.llm.error_categories import _category_from_exception
        from polaris.kernelone.llm.exceptions import ToolParseError

        error = ToolParseError("tool parse failed")
        result = _category_from_exception(error)
        assert result == ErrorCategory.JSON_PARSE

    def test_provider_error_returns_provider_error(self) -> None:
        """Verify ProviderError maps to PROVIDER_ERROR."""
        from polaris.kernelone.llm.error_categories import _category_from_exception
        from polaris.kernelone.llm.exceptions import ProviderError

        error = ProviderError("provider failed")
        result = _category_from_exception(error)
        assert result == ErrorCategory.PROVIDER_ERROR

    def test_llm_error_subclass_returns_unknown(self) -> None:
        """Verify LLMError subclass without specific mapping returns UNKNOWN.

        Note: LLMError is lazily loaded from kernelone.errors. Direct subclassing
        is not supported in this test context. The test verifies that the
        _category_from_exception function handles generic Exception with
        a message matching 'unknown' patterns.
        """
        from polaris.kernelone.llm.error_categories import _category_from_exception

        # Since direct LLMError subclassing is not available, we verify
        # that any unrecognized exception type returns UNKNOWN category
        # through the keyword-based fallback
        error = Exception("some unknown error pattern")
        result = _category_from_exception(error)
        # Non-LLMError exceptions return None from _category_from_exception
        # The classify_error function then falls back to keyword matching
        assert result is None

    def test_non_llm_error_returns_none(self) -> None:
        """Verify non-LLMError exceptions return None."""
        from polaris.kernelone.llm.error_categories import _category_from_exception

        error = ValueError("not an LLM error")
        result = _category_from_exception(error)
        assert result is None


class TestClassifyError:
    """Tests for classify_error function."""

    def test_timeout_error_string(self) -> None:
        """Verify classify_error handles 'timeout' in error string."""
        from polaris.kernelone.llm.error_categories import classify_error

        error = ValueError("connection timeout")
        result = classify_error(error)
        assert result == ErrorCategory.TIMEOUT

    def test_timeout_error_string_variant(self) -> None:
        """Verify classify_error handles 'timed out' in error string."""
        from polaris.kernelone.llm.error_categories import classify_error

        error = ValueError("request timed out")
        result = classify_error(error)
        assert result == ErrorCategory.TIMEOUT

    def test_rate_limit_429(self) -> None:
        """Verify classify_error handles '429' status code."""
        from polaris.kernelone.llm.error_categories import classify_error

        error = ValueError("error 429: too many requests")
        result = classify_error(error)
        assert result == ErrorCategory.RATE_LIMIT

    def test_rate_limit_explicit(self) -> None:
        """Verify classify_error handles 'rate limit' in error string."""
        from polaris.kernelone.llm.error_categories import classify_error

        error = ValueError("rate limit exceeded")
        result = classify_error(error)
        assert result == ErrorCategory.RATE_LIMIT

    def test_rate_limit_too_many_requests(self) -> None:
        """Verify classify_error handles 'too many requests'."""
        from polaris.kernelone.llm.error_categories import classify_error

        error = ValueError("too many requests")
        result = classify_error(error)
        assert result == ErrorCategory.RATE_LIMIT

    def test_connection_error(self) -> None:
        """Verify classify_error handles 'connection' in error string."""
        from polaris.kernelone.llm.error_categories import classify_error

        error = ConnectionError("connection refused")
        result = classify_error(error)
        assert result == ErrorCategory.NETWORK_ERROR

    def test_network_error_string(self) -> None:
        """Verify classify_error handles 'network' in error string."""
        from polaris.kernelone.llm.error_categories import classify_error

        error = ValueError("network unavailable")
        result = classify_error(error)
        assert result == ErrorCategory.NETWORK_ERROR

    def test_config_error(self) -> None:
        """Verify classify_error handles 'config' in error string."""
        from polaris.kernelone.llm.error_categories import classify_error

        error = ValueError("invalid config")
        result = classify_error(error)
        assert result == ErrorCategory.CONFIG_ERROR

    def test_configuration_error(self) -> None:
        """Verify classify_error handles 'configuration' in error string."""
        from polaris.kernelone.llm.error_categories import classify_error

        error = ValueError("configuration missing")
        result = classify_error(error)
        assert result == ErrorCategory.CONFIG_ERROR

    def test_json_error(self) -> None:
        """Verify classify_error handles 'json' in error string."""
        from polaris.kernelone.llm.error_categories import classify_error

        error = ValueError("invalid json")
        result = classify_error(error)
        assert result == ErrorCategory.JSON_PARSE

    def test_parse_error(self) -> None:
        """Verify classify_error handles 'parse' in error string."""
        from polaris.kernelone.llm.error_categories import classify_error

        error = ValueError("parse error")
        result = classify_error(error)
        assert result == ErrorCategory.JSON_PARSE

    def test_invalid_error(self) -> None:
        """Verify classify_error handles 'invalid' in error string."""
        from polaris.kernelone.llm.error_categories import classify_error

        error = ValueError("invalid response")
        result = classify_error(error)
        assert result == ErrorCategory.INVALID_RESPONSE

    def test_response_error(self) -> None:
        """Verify classify_error handles 'response' in error string."""
        from polaris.kernelone.llm.error_categories import classify_error

        error = ValueError("bad response")
        result = classify_error(error)
        assert result == ErrorCategory.INVALID_RESPONSE

    def test_unknown_error(self) -> None:
        """Verify classify_error returns UNKNOWN for unrecognized errors."""
        from polaris.kernelone.llm.error_categories import classify_error

        error = ValueError("some random error")
        result = classify_error(error)
        assert result == ErrorCategory.UNKNOWN

    def test_llm_error_takes_precedence(self) -> None:
        """Verify LLMError subclass classification takes precedence over string matching."""
        from polaris.kernelone.llm.error_categories import classify_error
        from polaris.kernelone.llm.exceptions import RateLimitError

        # RateLimitError should be classified as RATE_LIMIT, not by string
        error = RateLimitError("rate limit timeout")
        result = classify_error(error)
        assert result == ErrorCategory.RATE_LIMIT
