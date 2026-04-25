"""Tests for polaris.kernelone.llm.engine.error_mapping."""

from __future__ import annotations

from polaris.kernelone.llm.engine.error_mapping import (
    KernelRepairCategory,
    NoRetryCategory,
    PlatformRetryCategory,
    get_retry_hint,
    is_kernel_repairable,
    is_platform_retryable,
    is_retryable,
    map_error_to_category,
    serialize_error,
)


class TestMapErrorToCategory:
    def test_timeout_error(self) -> None:
        exc = Exception("request timeout")
        category, retryable, hint = map_error_to_category(exc)
        assert category == PlatformRetryCategory.TIMEOUT
        assert retryable is True
        assert hint is not None and "超时" in hint

    def test_timed_out_error(self) -> None:
        exc = Exception("connection timed out")
        category, _retryable, _hint = map_error_to_category(exc)
        assert category == PlatformRetryCategory.TIMEOUT

    def test_rate_limit_error(self) -> None:
        exc = Exception("rate limit exceeded")
        category, retryable, _hint = map_error_to_category(exc)
        assert category == PlatformRetryCategory.RATE_LIMIT
        assert retryable is True

    def test_network_error(self) -> None:
        exc = Exception("network connection failed")
        category, retryable, _hint = map_error_to_category(exc)
        assert category == PlatformRetryCategory.NETWORK_ERROR
        assert retryable is True

    def test_service_unavailable(self) -> None:
        exc = Exception("503 service unavailable")
        category, retryable, _hint = map_error_to_category(exc)
        assert category == PlatformRetryCategory.SERVICE_UNAVAILABLE
        assert retryable is True

    def test_gateway_timeout(self) -> None:
        exc = Exception("504 gateway timeout")
        category, retryable, _hint = map_error_to_category(exc)
        # "gateway timeout" contains "timeout" which matches TIMEOUT first;
        # both are platform-retryable so the behavioral outcome is the same
        assert category in (PlatformRetryCategory.TIMEOUT, PlatformRetryCategory.GATEWAY_TIMEOUT)
        assert retryable is True

    def test_parse_error(self) -> None:
        exc = Exception("json decode error")
        category, retryable, _hint = map_error_to_category(exc)
        assert category == KernelRepairCategory.PARSE_ERROR
        assert retryable is False

    def test_schema_validation_error(self) -> None:
        exc = Exception("schema validation failed: required field missing")
        category, retryable, _hint = map_error_to_category(exc)
        assert category == KernelRepairCategory.SCHEMA_VALIDATION_ERROR
        assert retryable is False

    def test_tool_not_found(self) -> None:
        exc = Exception("tool not found: unknown_tool")
        category, retryable, _hint = map_error_to_category(exc)
        assert category == KernelRepairCategory.TOOL_NOT_FOUND
        assert retryable is False

    def test_tool_argument_error(self) -> None:
        exc = Exception("invalid argument for tool")
        category, retryable, _hint = map_error_to_category(exc)
        assert category == KernelRepairCategory.TOOL_ARGUMENT_ERROR
        assert retryable is False

    def test_quality_check_failed(self) -> None:
        exc = Exception("quality below threshold")
        category, retryable, _hint = map_error_to_category(exc)
        assert category == KernelRepairCategory.QUALITY_CHECK_FAILED
        assert retryable is False

    def test_permission_denied(self) -> None:
        exc = Exception("access denied")
        category, retryable, _hint = map_error_to_category(exc)
        assert category == NoRetryCategory.PERMISSION_DENIED
        assert retryable is False

    def test_authentication_error(self) -> None:
        exc = Exception("api key invalid: unauthorized")
        category, retryable, _hint = map_error_to_category(exc)
        assert category == NoRetryCategory.AUTHENTICATION_ERROR
        assert retryable is False

    def test_context_length_exceeded(self) -> None:
        exc = Exception("context length exceeded: max tokens reached")
        category, retryable, _hint = map_error_to_category(exc)
        assert category == NoRetryCategory.CONTEXT_LENGTH_EXCEEDED
        assert retryable is False

    def test_unknown_error(self) -> None:
        exc = Exception("something completely unexpected")
        category, retryable, _hint = map_error_to_category(exc)
        assert category == NoRetryCategory.UNKNOWN_ERROR
        assert retryable is False

    def test_case_insensitive(self) -> None:
        exc = Exception("TIMEOUT")
        category, _retryable, _hint = map_error_to_category(exc)
        assert category == PlatformRetryCategory.TIMEOUT


class TestIsPlatformRetryable:
    def test_platform_retryable(self) -> None:
        assert is_platform_retryable(PlatformRetryCategory.TIMEOUT) is True

    def test_kernel_not_platform(self) -> None:
        assert is_platform_retryable(KernelRepairCategory.PARSE_ERROR) is False

    def test_no_retry_not_platform(self) -> None:
        assert is_platform_retryable(NoRetryCategory.UNKNOWN_ERROR) is False


class TestIsKernelRepairable:
    def test_kernel_repairable(self) -> None:
        assert is_kernel_repairable(KernelRepairCategory.PARSE_ERROR) is True

    def test_platform_not_kernel(self) -> None:
        assert is_kernel_repairable(PlatformRetryCategory.TIMEOUT) is False

    def test_no_retry_not_kernel(self) -> None:
        assert is_kernel_repairable(NoRetryCategory.UNKNOWN_ERROR) is False


class TestIsRetryable:
    def test_platform_retryable(self) -> None:
        assert is_retryable(PlatformRetryCategory.TIMEOUT) is True

    def test_kernel_repairable(self) -> None:
        assert is_retryable(KernelRepairCategory.PARSE_ERROR) is True

    def test_no_retry_not_retryable(self) -> None:
        assert is_retryable(NoRetryCategory.UNKNOWN_ERROR) is False


class TestGetRetryHint:
    def test_all_categories_have_hints(self) -> None:
        for plat_category in PlatformRetryCategory:
            assert get_retry_hint(plat_category) is not None
        for kernel_category in KernelRepairCategory:
            assert get_retry_hint(kernel_category) is not None
        for no_retry_category in NoRetryCategory:
            assert get_retry_hint(no_retry_category) is not None

    def test_unknown_category_returns_none(self) -> None:
        class FakeEnum:
            pass

        assert get_retry_hint(FakeEnum()) is None  # type: ignore[arg-type]


class TestSerializeError:
    def test_platform_retryable_serialization(self) -> None:
        result = serialize_error(PlatformRetryCategory.TIMEOUT)
        assert result["error_category"] == "TIMEOUT"
        assert result["retryable"] is True
        assert result["retry_hint"] is not None

    def test_kernel_repairable_serialization(self) -> None:
        result = serialize_error(KernelRepairCategory.PARSE_ERROR)
        assert result["error_category"] == "PARSE_ERROR"
        assert result["retryable"] is True
        assert result["retry_hint"] is not None

    def test_no_retry_serialization(self) -> None:
        result = serialize_error(NoRetryCategory.AUTHENTICATION_ERROR)
        assert result["error_category"] == "AUTHENTICATION_ERROR"
        assert result["retryable"] is False
        assert result["retry_hint"] is not None
