"""边界情况测试：roles.kernel Cell

目标覆盖率：60%
补充测试：预算超限处理、工具调用失败重试、并发Turn执行、错误恢复
"""

from __future__ import annotations

from polaris.cells.roles.kernel.internal.error_recovery.context_injector import ErrorContextInjector
from polaris.cells.roles.kernel.internal.error_recovery.retry_policy import RetryConfig, RetryPolicy, ToolError
from polaris.cells.roles.kernel.internal.token_budget import TokenBudget


class TestTokenBudgetEdgeCases:
    """Token预算边界情况测试"""

    def test_budget_default_values(self) -> None:
        """测试默认预算值"""
        budget = TokenBudget()
        assert budget.system_context == 4000
        assert budget.task_context == 2000
        assert budget.conversation == 4000
        assert budget.override == 1000
        assert budget.safety_margin == 500

    def test_budget_custom_values(self) -> None:
        """测试自定义预算值"""
        budget = TokenBudget(system_context=1000, task_context=500, conversation=2000, override=200, safety_margin=100)
        assert budget.system_context == 1000
        assert budget.task_context == 500
        assert budget.conversation == 2000
        assert budget.override == 200
        assert budget.safety_margin == 100

    def test_budget_minimum_positive_values(self) -> None:
        """测试最小正值预算"""
        # 使用最小正值，因为零值会触发验证错误
        budget = TokenBudget(system_context=1, task_context=1, conversation=1, override=1, safety_margin=1)
        assert budget.total == 5

    def test_budget_negative_values(self) -> None:
        """测试负预算值"""
        budget = TokenBudget(
            system_context=-100,
            task_context=-50,
        )
        # 负值应该被接受
        assert budget.system_context == -100
        assert budget.task_context == -50

    def test_budget_total_calculation(self) -> None:
        """测试总预算计算"""
        budget = TokenBudget(system_context=1000, task_context=500, conversation=2000, override=200, safety_margin=100)
        expected_total = 1000 + 500 + 2000 + 200 + 100
        assert budget.total == expected_total


class TestRetryPolicyEdgeCases:
    """重试策略边界情况测试"""

    def test_retry_policy_default_config(self) -> None:
        """测试默认重试配置"""
        policy = RetryPolicy()
        assert policy._config.max_retries == 3
        assert policy._config.base_delay == 0.5
        assert policy._config.exponential_backoff is True

    def test_retry_policy_custom_config(self) -> None:
        """测试自定义重试配置"""
        config = RetryConfig(max_retries=5, base_delay=1.0, exponential_backoff=False)
        policy = RetryPolicy(config)
        assert policy._config.max_retries == 5
        assert policy._config.base_delay == 1.0
        assert policy._config.exponential_backoff is False

    def test_retry_policy_zero_max_retries(self) -> None:
        """测试零最大重试次数"""
        config = RetryConfig(max_retries=0)
        policy = RetryPolicy(config)

        error = ToolError(tool_name="test", error_message="error", args={})
        assert policy.should_retry(error, 0) is False

    def test_retry_policy_attempt_boundary(self) -> None:
        """测试尝试次数边界"""
        config = RetryConfig(max_retries=3)
        policy = RetryPolicy(config)
        error = ToolError(tool_name="test", error_message="error", args={})

        assert policy.should_retry(error, 0) is True
        assert policy.should_retry(error, 1) is True
        assert policy.should_retry(error, 2) is True
        assert policy.should_retry(error, 3) is False

    def test_retry_policy_no_retry_patterns(self) -> None:
        """测试不可重试的错误模式"""
        policy = RetryPolicy()

        # 权限错误不应该重试
        permission_error = ToolError(tool_name="test", error_message="Permission denied", args={})
        assert policy.should_retry(permission_error, 0) is False

        access_denied = ToolError(tool_name="test", error_message="Access Denied", args={})
        assert policy.should_retry(access_denied, 0) is False

        forbidden = ToolError(tool_name="test", error_message="Forbidden", args={})
        assert policy.should_retry(forbidden, 0) is False

    def test_retry_policy_retryable_error(self) -> None:
        """测试可重试的错误"""
        policy = RetryPolicy()

        # 普通错误应该重试
        error = ToolError(tool_name="test", error_message="Connection timeout", args={})
        assert policy.should_retry(error, 0) is True

        error2 = ToolError(tool_name="test", error_message="Network error", args={})
        assert policy.should_retry(error2, 0) is True

    def test_compute_delay_exponential(self) -> None:
        """测试指数退避延迟计算"""
        config = RetryConfig(base_delay=1.0, exponential_backoff=True)
        policy = RetryPolicy(config)

        # 指数退避: 2^attempt * base_delay
        assert policy.compute_delay(0) == 1.0
        assert policy.compute_delay(1) == 2.0
        assert policy.compute_delay(2) == 4.0
        assert policy.compute_delay(3) == 8.0

    def test_compute_delay_linear(self) -> None:
        """测试线性延迟计算"""
        config = RetryConfig(base_delay=1.0, exponential_backoff=False)
        policy = RetryPolicy(config)

        # 线性延迟: 始终是base_delay
        assert policy.compute_delay(0) == 1.0
        assert policy.compute_delay(1) == 1.0
        assert policy.compute_delay(5) == 1.0


class TestErrorContextInjector:
    """错误上下文注入器测试"""

    def test_inject_error_context_basic(self) -> None:
        """测试基本错误上下文注入"""
        history = [{"role": "user", "content": "Hello"}]
        result = ErrorContextInjector.inject_error_context(
            history=history, tool_name="test_tool", error_message="Something went wrong", args={"arg1": "value1"}
        )

        assert len(result) == 2
        assert result[0] == {"role": "user", "content": "Hello"}
        assert result[1]["role"] == "system"
        assert "test_tool" in result[1]["content"]
        assert "Something went wrong" in result[1]["content"]

    def test_inject_error_context_empty_history(self) -> None:
        """测试空历史注入"""
        result = ErrorContextInjector.inject_error_context(
            history=[], tool_name="test_tool", error_message="Error", args={}
        )

        assert len(result) == 1
        assert result[0]["role"] == "system"

    def test_inject_error_context_none_history(self) -> None:
        """测试None历史注入"""
        result = ErrorContextInjector.inject_error_context(
            history=None, tool_name="test_tool", error_message="Error", args={}
        )

        assert len(result) == 1
        assert result[0]["role"] == "system"

    def test_inject_error_context_with_args(self) -> None:
        """测试带参数的错误注入"""
        result = ErrorContextInjector.inject_error_context(
            history=[], tool_name="complex_tool", error_message="Complex error", args={"key": "value", "number": 123}
        )

        assert "complex_tool" in result[0]["content"]
        assert "Complex error" in result[0]["content"]

    def test_inject_error_context_preserves_original(self) -> None:
        """测试保留原始历史"""
        original = [{"role": "user", "content": "Test"}]
        result = ErrorContextInjector.inject_error_context(
            history=original, tool_name="tool", error_message="error", args={}
        )

        # 原始历史应该被保留
        assert result[0] == {"role": "user", "content": "Test"}
