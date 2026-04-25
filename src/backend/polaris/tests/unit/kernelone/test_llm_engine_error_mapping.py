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


class TestMapErrorToCategoryPlatformRetryable:
    def test_timeout(self) -> None:
        category, retryable, hint = map_error_to_category(Exception("request timeout"))
        assert category == PlatformRetryCategory.TIMEOUT
        assert retryable is True
        assert hint == "请求超时，请稍后重试"

    def test_timed_out(self) -> None:
        category, retryable, hint = map_error_to_category(Exception("operation timed out"))
        assert category == PlatformRetryCategory.TIMEOUT

    def test_deadline_exceeded(self) -> None:
        category, retryable, hint = map_error_to_category(Exception("deadline exceeded"))
        assert category == PlatformRetryCategory.TIMEOUT

    def test_rate_limit(self) -> None:
        category, retryable, hint = map_error_to_category(Exception("rate limit exceeded"))
        assert category == PlatformRetryCategory.RATE_LIMIT
        assert retryable is True

    def test_too_many_requests(self) -> None:
        category, retryable, hint = map_error_to_category(Exception("too many requests"))
        assert category == PlatformRetryCategory.RATE_LIMIT

    def test_quota_exceeded(self) -> None:
        category, retryable, hint = map_error_to_category(Exception("quota exceeded"))
        assert category == PlatformRetryCategory.RATE_LIMIT

    def test_network_error(self) -> None:
        category, retryable, hint = map_error_to_category(Exception("network unreachable"))
        assert category == PlatformRetryCategory.NETWORK_ERROR
        assert retryable is True

    def test_connection_refused(self) -> None:
        category, retryable, hint = map_error_to_category(Exception("connection refused"))
        assert category == PlatformRetryCategory.NETWORK_ERROR

    def test_dns_failure(self) -> None:
        category, retryable, hint = map_error_to_category(Exception("dns resolution failed"))
        assert category == PlatformRetryCategory.NETWORK_ERROR

    def test_service_unavailable_503(self) -> None:
        category, retryable, hint = map_error_to_category(Exception("503 service unavailable"))
        assert category == PlatformRetryCategory.SERVICE_UNAVAILABLE
        assert retryable is True

    def test_gateway_timeout_504(self) -> None:
        category, retryable, hint = map_error_to_category(Exception("504 gateway timeout"))
        assert category == PlatformRetryCategory.GATEWAY_TIMEOUT
        assert retryable is True


class TestMapErrorToCategoryKernelRepairable:
    def test_parse_error(self) -> None:
        category, retryable, hint = map_error_to_category(Exception("json parse error"))
        assert category == KernelRepairCategory.PARSE_ERROR
        assert retryable is False
        assert hint == "输出解析失败，请调整输出格式"

    def test_json_decode(self) -> None:
        category, retryable, hint = map_error_to_category(Exception("json decode error"))
        assert category == KernelRepairCategory.PARSE_ERROR

    def test_schema_validation(self) -> None:
        category, retryable, hint = map_error_to_category(Exception("schema validation failed"))
        assert category == KernelRepairCategory.SCHEMA_VALIDATION_ERROR
        assert retryable is False

    def test_required_field(self) -> None:
        category, retryable, hint = map_error_to_category(Exception("required field missing"))
        assert category == KernelRepairCategory.SCHEMA_VALIDATION_ERROR

    def test_tool_not_found(self) -> None:
        category, retryable, hint = map_error_to_category(Exception("tool not found"))
        assert category == KernelRepairCategory.TOOL_NOT_FOUND
        assert retryable is False

    def test_unknown_tool(self) -> None:
        category, retryable, hint = map_error_to_category(Exception("unknown tool"))
        assert category == KernelRepairCategory.TOOL_NOT_FOUND

    def test_tool_argument_error(self) -> None:
        category, retryable, hint = map_error_to_category(Exception("invalid argument"))
        assert category == KernelRepairCategory.TOOL_ARGUMENT_ERROR
        assert retryable is False

    def test_param_error(self) -> None:
        category, retryable, hint = map_error_to_category(Exception("param error"))
        assert category == KernelRepairCategory.TOOL_ARGUMENT_ERROR

    def test_quality_check_failed(self) -> None:
        category, retryable, hint = map_error_to_category(Exception("quality below threshold"))
        assert category == KernelRepairCategory.QUALITY_CHECK_FAILED
        assert retryable is False


class TestMapErrorToCategoryNoRetry:
    def test_permission_denied(self) -> None:
        category, retryable, hint = map_error_to_category(Exception("permission denied"))
        assert category == NoRetryCategory.PERMISSION_DENIED
        assert retryable is False

    def test_access_denied(self) -> None:
        category, retryable, hint = map_error_to_category(Exception("access denied"))
        assert category == NoRetryCategory.PERMISSION_DENIED

    def test_authentication(self) -> None:
        category, retryable, hint = map_error_to_category(Exception("authentication failed"))
        assert category == NoRetryCategory.AUTHENTICATION_ERROR
        assert retryable is False

    def test_api_key(self) -> None:
        category, retryable, hint = map_error_to_category(Exception("invalid api key"))
        assert category == NoRetryCategory.AUTHENTICATION_ERROR

    def test_401(self) -> None:
        category, retryable, hint = map_error_to_category(Exception("401 unauthorized"))
        assert category == NoRetryCategory.AUTHENTICATION_ERROR

    def test_context_length(self) -> None:
        category, retryable, hint = map_error_to_category(Exception("context length exceeded"))
        assert category == NoRetryCategory.CONTEXT_LENGTH_EXCEEDED
        assert retryable is False

    def test_max_tokens(self) -> None:
        category, retryable, hint = map_error_to_category(Exception("max tokens exceeded"))
        assert category == NoRetryCategory.CONTEXT_LENGTH_EXCEEDED

    def test_unknown_error(self) -> None:
        category, retryable, hint = map_error_to_category(Exception("something weird"))
        assert category == NoRetryCategory.UNKNOWN_ERROR
        assert retryable is False
        assert hint == "发生未知错误"


class TestIsPlatformRetryable:
    def test_true_for_platform(self) -> None:
        assert is_platform_retryable(PlatformRetryCategory.TIMEOUT) is True

    def test_false_for_kernel(self) -> None:
        assert is_platform_retryable(KernelRepairCategory.PARSE_ERROR) is False

    def test_false_for_no_retry(self) -> None:
        assert is_platform_retryable(NoRetryCategory.UNKNOWN_ERROR) is False


class TestIsKernelRepairable:
    def test_true_for_kernel(self) -> None:
        assert is_kernel_repairable(KernelRepairCategory.TOOL_NOT_FOUND) is True

    def test_false_for_platform(self) -> None:
        assert is_kernel_repairable(PlatformRetryCategory.RATE_LIMIT) is False

    def test_false_for_no_retry(self) -> None:
        assert is_kernel_repairable(NoRetryCategory.AUTHENTICATION_ERROR) is False


class TestIsRetryable:
    def test_true_for_platform(self) -> None:
        assert is_retryable(PlatformRetryCategory.NETWORK_ERROR) is True

    def test_true_for_kernel(self) -> None:
        assert is_retryable(KernelRepairCategory.SCHEMA_VALIDATION_ERROR) is True

    def test_false_for_no_retry(self) -> None:
        assert is_retryable(NoRetryCategory.PERMISSION_DENIED) is False


class TestGetRetryHint:
    def test_all_platform_hints(self) -> None:
        for cat in PlatformRetryCategory:
            assert get_retry_hint(cat) is not None

    def test_all_kernel_hints(self) -> None:
        for cat in KernelRepairCategory:
            assert get_retry_hint(cat) is not None

    def test_all_no_retry_hints(self) -> None:
        for cat in NoRetryCategory:
            assert get_retry_hint(cat) is not None

    def test_unknown_category_returns_none(self) -> None:
        class FakeEnum:
            pass

        assert get_retry_hint(FakeEnum()) is None  # type: ignore[arg-type]


class TestSerializeError:
    def test_structure(self) -> None:
        result = serialize_error(PlatformRetryCategory.TIMEOUT)
        assert result["error_category"] == "TIMEOUT"
        assert result["retryable"] is True
        assert result["retry_hint"] == "请求超时，请稍后重试"

    def test_no_retry(self) -> None:
        result = serialize_error(NoRetryCategory.AUTHENTICATION_ERROR)
        assert result["retryable"] is False
        assert result["error_category"] == "AUTHENTICATION_ERROR"
