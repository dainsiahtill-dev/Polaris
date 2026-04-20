"""Retry Policy 综合测试。"""

from polaris.cells.roles.kernel.internal.error_recovery.retry_policy import (
    RetryConfig,
    RetryPolicy,
    ToolError,
)


class TestRetryPolicy:
    """测试重试策略。"""

    def test_max_retries_exceeded(self) -> None:
        policy = RetryPolicy(RetryConfig(max_retries=3))
        error = ToolError("test", "any error", {})
        assert policy.should_retry(error, 0) is True
        assert policy.should_retry(error, 2) is True
        assert policy.should_retry(error, 3) is False

    def test_permission_error_no_retry(self) -> None:
        policy = RetryPolicy()
        error = ToolError("write", "Permission denied", {})
        assert policy.should_retry(error, 0) is False

    def test_file_not_found_can_retry(self) -> None:
        policy = RetryPolicy()
        error = ToolError("read", "File not found: test.py", {})
        assert policy.should_retry(error, 0) is True

    def test_exponential_backoff(self) -> None:
        policy = RetryPolicy(RetryConfig(exponential_backoff=True))
        assert policy.compute_delay(0) == 0.5
        assert policy.compute_delay(1) == 1.0
        assert policy.compute_delay(2) == 2.0

    def test_linear_backoff(self) -> None:
        policy = RetryPolicy(RetryConfig(exponential_backoff=False))
        assert policy.compute_delay(0) == 0.5
        assert policy.compute_delay(1) == 0.5
        assert policy.compute_delay(2) == 0.5

    def test_build_error_context(self) -> None:
        policy = RetryPolicy()
        error = ToolError("read_file", "File not found: test.py", {"path": "test.py"})
        ctx = policy.build_error_context(error)
        assert "read_file" in ctx
        assert "File not found" in ctx
        assert "test.py" in ctx

    def test_custom_config(self) -> None:
        policy = RetryPolicy(RetryConfig(max_retries=5, base_delay=1.0))
        error = ToolError("test", "error", {})
        assert policy.should_retry(error, 4) is True
        assert policy.should_retry(error, 5) is False
        assert policy.compute_delay(0) == 1.0
