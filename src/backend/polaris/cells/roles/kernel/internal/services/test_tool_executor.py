"""Tests for ToolExecutor service layer.

Test coverage:
- Normal execution (success and failure)
- Timeout handling
- Error classification and retryable flags
- Batch execution
- Retry with fallback
- Edge cases (empty input, invalid args, etc.)
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from polaris.cells.roles.kernel.internal.services.tool_executor import (
    MAX_CONCURRENT_TOOL_EXECUTIONS,
    TIMEOUT_DEFAULT_SECONDS,
    TIMEOUT_DIRECTOR_SECONDS,
    ErrorCategory,
    ToolAuthorizationError,
    ToolCall,
    ToolError,
    ToolExecutionBackend,
    ToolExecutor,
    ToolTimeoutError,
    create_tool_executor,
)

# ═══════════════════════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════════════════════


@pytest.fixture
def mock_profile():
    """Create a mock RoleProfile for testing."""
    profile = MagicMock()
    profile.role_id = "test_role"
    profile.tool_policy = MagicMock()
    profile.tool_policy.tool_timeout_seconds = 30
    return profile


@pytest.fixture
def director_profile():
    """Create a mock Director RoleProfile for testing."""
    profile = MagicMock()
    profile.role_id = "director"
    profile.tool_policy = MagicMock()
    profile.tool_policy.tool_timeout_seconds = 60
    return profile


@pytest.fixture
def mock_backend():
    """Create a mock ToolExecutionBackend."""
    backend = AsyncMock(spec=ToolExecutionBackend)
    return backend


@pytest.fixture
def executor(mock_backend):
    """Create a ToolExecutor with mock backend."""
    return ToolExecutor(
        workspace=".",
        backend=mock_backend,
        default_timeout=TIMEOUT_DEFAULT_SECONDS,
        director_timeout=TIMEOUT_DIRECTOR_SECONDS,
    )


# ═══════════════════════════════════════════════════════════════════════════
# Normal Execution Tests
# ═══════════════════════════════════════════════════════════════════════════


class TestExecuteSingleNormal:
    """Tests for normal execute_single behavior."""

    @pytest.mark.asyncio
    async def test_execute_single_success(self, executor, mock_backend, mock_profile) -> None:
        """Test successful tool execution."""
        # Arrange
        mock_backend.execute.return_value = {
            "success": True,
            "result": {"content": "file content"},
        }
        call = ToolCall(tool="read_file", args={"path": "test.py"})

        # Act
        result = await executor.execute_single(call, mock_profile)

        # Assert
        assert result.success is True
        assert result.tool == "read_file"
        assert result.error is None
        assert result.result == {"content": "file content"}
        assert result.retryable is False
        mock_backend.execute.assert_called_once_with(
            "read_file",
            {"path": "test.py"},
            workspace=".",
        )

    @pytest.mark.asyncio
    async def test_execute_single_failure(self, executor, mock_backend, mock_profile) -> None:
        """Test failed tool execution."""
        # Arrange
        mock_backend.execute.return_value = {
            "success": False,
            "error": "File not found",
        }
        call = ToolCall(tool="read_file", args={"path": "nonexistent.py"})

        # Act
        result = await executor.execute_single(call, mock_profile)

        # Assert
        assert result.success is False
        assert result.tool == "read_file"
        assert result.error == "File not found"
        assert result.result is None

    @pytest.mark.asyncio
    async def test_execute_single_with_call_id(self, executor, mock_backend, mock_profile) -> None:
        """Test that call_id is preserved in result."""
        # Arrange
        mock_backend.execute.return_value = {"success": True, "result": "data"}
        call = ToolCall(tool="test_tool", args={}, call_id="call-123")

        # Act
        result = await executor.execute_single(call, mock_profile)

        # Assert
        assert result.call_id == "call-123"

    @pytest.mark.asyncio
    async def test_execute_single_non_dict_result(self, executor, mock_backend, mock_profile) -> None:
        """Test handling of non-dict result from backend."""
        # Arrange
        mock_backend.execute.return_value = "raw string result"
        call = ToolCall(tool="test_tool", args={})

        # Act
        result = await executor.execute_single(call, mock_profile)

        # Assert
        assert result.success is True
        assert result.result == "raw string result"


# ═══════════════════════════════════════════════════════════════════════════
# Timeout Tests
# ═══════════════════════════════════════════════════════════════════════════


class TestExecuteSingleTimeout:
    """Tests for timeout handling."""

    @pytest.mark.asyncio
    async def test_execute_single_timeout(self, executor, mock_backend, mock_profile) -> None:
        """Test timeout handling."""
        # Arrange
        mock_backend.execute.side_effect = asyncio.TimeoutError()
        call = ToolCall(tool="slow_tool", args={})

        # Act
        result = await executor.execute_single(call, mock_profile)

        # Assert
        assert result.success is False
        assert result.error_category == ErrorCategory.TIMEOUT
        assert result.retryable is True
        assert "timed out" in result.error.lower()

    @pytest.mark.asyncio
    async def test_director_longer_timeout(self, mock_backend) -> None:
        """Test that director role gets longer timeout."""
        # Arrange
        executor = ToolExecutor(workspace=".", backend=mock_backend)
        director_profile = MagicMock()
        director_profile.role_id = "director"
        director_profile.tool_policy = None

        # Mock to track timeout value
        timeout_captured = []

        async def capture_wait_for(coro, timeout):
            timeout_captured.append(timeout)
            return await coro

        mock_backend.execute.return_value = {"success": True}
        call = ToolCall(tool="test", args={})

        # Act
        with patch("asyncio.wait_for", side_effect=capture_wait_for):
            await executor.execute_single(call, director_profile)

        # Assert
        assert timeout_captured[0] == TIMEOUT_DIRECTOR_SECONDS

    @pytest.mark.asyncio
    async def test_custom_timeout_from_profile(self, mock_backend) -> None:
        """Test custom timeout from profile configuration."""
        # Arrange
        executor = ToolExecutor(workspace=".", backend=mock_backend)
        profile = MagicMock()
        profile.role_id = "custom"
        profile.tool_policy = MagicMock()
        profile.tool_policy.tool_timeout_seconds = 120

        timeout_captured = []

        async def capture_wait_for(coro, timeout):
            timeout_captured.append(timeout)
            return await coro

        mock_backend.execute.return_value = {"success": True}
        call = ToolCall(tool="test", args={})

        # Act
        with patch("asyncio.wait_for", side_effect=capture_wait_for):
            await executor.execute_single(call, profile)

        # Assert
        assert timeout_captured[0] == 120


# ═══════════════════════════════════════════════════════════════════════════
# Error Classification Tests
# ═══════════════════════════════════════════════════════════════════════════


class TestErrorClassification:
    """Tests for error classification and retryable flags."""

    @pytest.mark.asyncio
    async def test_rate_limit_error(self, executor, mock_backend, mock_profile) -> None:
        """Test rate limit error classification."""
        # Arrange
        mock_backend.execute.side_effect = Exception("Rate limit exceeded: 429")
        call = ToolCall(tool="api_call", args={})

        # Act
        result = await executor.execute_single(call, mock_profile)

        # Assert
        assert result.success is False
        assert result.error_category == ErrorCategory.RATE_LIMIT
        assert result.retryable is True

    @pytest.mark.asyncio
    async def test_network_error(self, executor, mock_backend, mock_profile) -> None:
        """Test network error classification."""
        # Arrange
        mock_backend.execute.side_effect = Exception("Network connection refused")
        call = ToolCall(tool="api_call", args={})

        # Act
        result = await executor.execute_single(call, mock_profile)

        # Assert
        assert result.error_category == ErrorCategory.NETWORK_ERROR
        assert result.retryable is True

    @pytest.mark.asyncio
    async def test_authorization_error(self, executor, mock_backend, mock_profile) -> None:
        """Test authorization error classification."""
        # Arrange
        mock_backend.execute.side_effect = Exception("Unauthorized: 403")
        call = ToolCall(tool="restricted_tool", args={})

        # Act
        result = await executor.execute_single(call, mock_profile)

        # Assert
        assert result.error_category == ErrorCategory.AUTHORIZATION
        assert result.retryable is False

    @pytest.mark.asyncio
    async def test_validation_error(self, executor, mock_backend, mock_profile) -> None:
        """Test validation error classification."""
        # Arrange
        mock_backend.execute.side_effect = Exception("Validation failed: missing required field")
        call = ToolCall(tool="test", args={})

        # Act
        result = await executor.execute_single(call, mock_profile)

        # Assert
        assert result.error_category == ErrorCategory.VALIDATION
        assert result.retryable is False

    @pytest.mark.asyncio
    async def test_not_found_error(self, executor, mock_backend, mock_profile) -> None:
        """Test not found error classification."""
        # Arrange
        mock_backend.execute.side_effect = Exception("File not found: 404")
        call = ToolCall(tool="read_file", args={"path": "missing.txt"})

        # Act
        result = await executor.execute_single(call, mock_profile)

        # Assert
        assert result.error_category == ErrorCategory.NOT_FOUND
        assert result.retryable is False


# ═══════════════════════════════════════════════════════════════════════════
# Batch Execution Tests
# ═══════════════════════════════════════════════════════════════════════════


class TestExecuteBatch:
    """Tests for batch execution."""

    @pytest.mark.asyncio
    async def test_execute_batch_success(self, executor, mock_backend, mock_profile) -> None:
        """Test successful batch execution."""
        # Arrange
        mock_backend.execute.side_effect = [
            {"success": True, "result": "content1"},
            {"success": True, "result": "content2"},
            {"success": True, "result": "content3"},
        ]
        calls = [
            ToolCall(tool="read_file", args={"path": "a.py"}),
            ToolCall(tool="read_file", args={"path": "b.py"}),
            ToolCall(tool="read_file", args={"path": "c.py"}),
        ]

        # Act
        results = await executor.execute_batch(calls, mock_profile)

        # Assert
        assert len(results) == 3
        assert all(r.success for r in results)
        assert results[0].result == "content1"
        assert results[1].result == "content2"
        assert results[2].result == "content3"

    @pytest.mark.asyncio
    async def test_execute_batch_empty(self, executor, mock_profile) -> None:
        """Test batch execution with empty list."""
        # Act
        results = await executor.execute_batch([], mock_profile)

        # Assert
        assert results == []

    @pytest.mark.asyncio
    async def test_execute_batch_partial_failure(self, executor, mock_backend, mock_profile) -> None:
        """Test batch execution with partial failures."""
        # Arrange
        mock_backend.execute.side_effect = [
            {"success": True, "result": "content1"},
            {"success": False, "error": "Permission denied"},
            {"success": True, "result": "content3"},
        ]
        calls = [
            ToolCall(tool="read_file", args={"path": "a.py"}),
            ToolCall(tool="restricted", args={}),
            ToolCall(tool="read_file", args={"path": "c.py"}),
        ]

        # Act
        results = await executor.execute_batch(calls, mock_profile)

        # Assert
        assert len(results) == 3
        assert results[0].success is True
        assert results[1].success is False
        assert results[2].success is True

    @pytest.mark.asyncio
    async def test_execute_batch_preserves_order(self, executor, mock_backend, mock_profile) -> None:
        """Test that batch execution preserves call order."""
        # Arrange
        execution_order = []

        async def track_execution(tool_name, args, **kwargs):
            execution_order.append(tool_name)
            return {"success": True, "result": tool_name}

        mock_backend.execute.side_effect = track_execution
        calls = [
            ToolCall(tool="first", args={}),
            ToolCall(tool="second", args={}),
            ToolCall(tool="third", args={}),
        ]

        # Act
        results = await executor.execute_batch(calls, mock_profile)

        # Assert
        assert execution_order == ["first", "second", "third"]
        assert [r.tool for r in results] == ["first", "second", "third"]


# ═══════════════════════════════════════════════════════════════════════════
# Retry with Fallback Tests
# ═══════════════════════════════════════════════════════════════════════════


class TestExecuteWithFallback:
    """Tests for execute_with_fallback retry mechanism."""

    @pytest.mark.asyncio
    async def test_success_no_retry(self, executor, mock_backend, mock_profile) -> None:
        """Test that successful execution doesn't retry."""
        # Arrange
        mock_backend.execute.return_value = {"success": True, "result": "data"}
        call = ToolCall(tool="test", args={})

        # Act
        result = await executor.execute_with_fallback(call, mock_profile, max_retries=2)

        # Assert
        assert result.success is True
        assert mock_backend.execute.call_count == 1

    @pytest.mark.asyncio
    async def test_retry_on_retryable_error(self, executor, mock_backend, mock_profile) -> None:
        """Test retry on retryable error."""
        # Arrange
        mock_backend.execute.side_effect = [
            Exception("Network timeout"),
            {"success": True, "result": "data"},
        ]
        call = ToolCall(tool="test", args={})

        # Act
        result = await executor.execute_with_fallback(call, mock_profile, max_retries=2)

        # Assert
        assert result.success is True
        assert mock_backend.execute.call_count == 2

    @pytest.mark.asyncio
    async def test_no_retry_on_non_retryable_error(self, executor, mock_backend, mock_profile) -> None:
        """Test no retry on non-retryable error."""
        # Arrange
        mock_backend.execute.side_effect = Exception("Permission denied: 403")
        call = ToolCall(tool="test", args={})

        # Act
        result = await executor.execute_with_fallback(call, mock_profile, max_retries=2)

        # Assert
        assert result.success is False
        assert mock_backend.execute.call_count == 1  # No retry

    @pytest.mark.asyncio
    async def test_max_retries_exceeded(self, executor, mock_backend, mock_profile) -> None:
        """Test behavior when max retries exceeded."""
        # Arrange
        mock_backend.execute.side_effect = Exception("Network timeout")
        call = ToolCall(tool="test", args={})

        # Act
        result = await executor.execute_with_fallback(call, mock_profile, max_retries=2)

        # Assert
        assert result.success is False
        assert mock_backend.execute.call_count == 3  # Initial + 2 retries
        assert "Max retries exceeded" in result.error or "timeout" in result.error.lower()


# ═══════════════════════════════════════════════════════════════════════════
# Edge Case Tests
# ═══════════════════════════════════════════════════════════════════════════


class TestEdgeCases:
    """Tests for edge cases."""

    @pytest.mark.asyncio
    async def test_empty_tool_name(self, executor, mock_profile) -> None:
        """Test handling of empty tool name."""
        # Arrange
        call = ToolCall(tool="", args={})

        # Act
        result = await executor.execute_single(call, mock_profile)

        # Assert
        assert result.success is False
        assert result.error == "Tool name is empty"
        assert result.error_category == ErrorCategory.VALIDATION

    @pytest.mark.asyncio
    async def test_none_args_defaults_to_empty_dict(self, executor, mock_backend, mock_profile) -> None:
        """Test that None args defaults to empty dict."""
        # Arrange
        mock_backend.execute.return_value = {"success": True}
        call = ToolCall(tool="test", args=None)  # type: ignore[arg-type]

        # Act
        await executor.execute_single(call, mock_profile)

        # Assert
        call_args = mock_backend.execute.call_args
        assert call_args[0][1] == {}  # Second positional arg should be empty dict

    @pytest.mark.asyncio
    async def test_execution_time_tracking(self, executor, mock_backend, mock_profile) -> None:
        """Test that execution time is tracked."""
        # Arrange
        mock_backend.execute.return_value = {"success": True}
        call = ToolCall(tool="test", args={})

        # Act
        result = await executor.execute_single(call, mock_profile)

        # Assert
        assert result.execution_time_ms >= 0

    @pytest.mark.asyncio
    async def test_tool_result_to_dict(self, executor, mock_backend, mock_profile) -> None:
        """Test ToolResult.to_dict method."""
        # Arrange
        mock_backend.execute.return_value = {"success": True, "result": "data"}
        call = ToolCall(tool="test", args={})

        # Act
        result = await executor.execute_single(call, mock_profile)
        result_dict = result.to_dict()

        # Assert
        assert result_dict["success"] is True
        assert result_dict["tool"] == "test"
        assert result_dict["error_category"] == ErrorCategory.UNKNOWN.value
        assert "execution_time_ms" in result_dict


# ═══════════════════════════════════════════════════════════════════════════
# ToolError Exception Tests
# ═══════════════════════════════════════════════════════════════════════════


class TestToolErrorExceptions:
    """Tests for ToolError exception classes."""

    def test_tool_error_basic(self) -> None:
        """Test basic ToolError creation."""
        error = ToolError("Something went wrong")
        assert str(error) == "Something went wrong"
        assert error.error_code == "tool_error"
        assert error.retryable is False

    def test_tool_error_with_context(self) -> None:
        """Test ToolError with context."""
        error = ToolError(
            "Failed",
            error_code="custom_error",
            error_category=ErrorCategory.NETWORK_ERROR,
            retryable=True,
            context={"tool": "test", "attempt": 1},
        )
        assert error.error_code == "custom_error"
        assert error.error_category == ErrorCategory.NETWORK_ERROR
        assert error.retryable is True
        assert error.context == {"tool": "test", "attempt": 1}

    def test_tool_error_to_dict(self) -> None:
        """Test ToolError.to_dict method."""
        error = ToolError(
            "Failed",
            error_code="test_error",
            error_category=ErrorCategory.VALIDATION,
            retryable=False,
            context={"field": "name"},
        )
        error_dict = error.to_dict()
        assert error_dict["code"] == "test_error"
        assert error_dict["message"] == "Failed"
        assert error_dict["category"] == ErrorCategory.VALIDATION.value
        assert error_dict["retryable"] is False
        assert error_dict["details"] == {"field": "name"}

    def test_tool_timeout_error(self) -> None:
        """Test ToolTimeoutError creation."""
        error = ToolTimeoutError("Timeout!", timeout_seconds=30.0)
        assert error.error_code == "tool_timeout"
        assert error.error_category == ErrorCategory.TIMEOUT
        assert error.retryable is True
        assert error.timeout_seconds == 30.0
        assert error.context["timeout_seconds"] == 30.0

    def test_tool_authorization_error(self) -> None:
        """Test ToolAuthorizationError creation."""
        error = ToolAuthorizationError("Access denied", tool_name="restricted")
        assert error.error_code == "tool_unauthorized"
        assert error.error_category == ErrorCategory.AUTHORIZATION
        assert error.retryable is False
        assert error.tool_name == "restricted"


# ═══════════════════════════════════════════════════════════════════════════
# Factory Function Tests
# ═══════════════════════════════════════════════════════════════════════════


class TestFactoryFunctions:
    """Tests for factory functions."""

    def test_create_tool_executor(self) -> None:
        """Test create_tool_executor factory."""
        executor = create_tool_executor(workspace="/test")
        assert isinstance(executor, ToolExecutor)
        assert executor._workspace == "/test"

    def test_create_tool_executor_with_backend(self, mock_backend) -> None:
        """Test create_tool_executor with backend."""
        executor = create_tool_executor(workspace=".", backend=mock_backend)
        assert executor._backend is mock_backend


# ═══════════════════════════════════════════════════════════════════════════
# Constants Tests
# ═══════════════════════════════════════════════════════════════════════════


def test_timeout_constants() -> None:
    """Test that timeout constants are defined correctly."""
    assert TIMEOUT_DIRECTOR_SECONDS == 660.0
    assert TIMEOUT_DEFAULT_SECONDS == 60
    assert MAX_CONCURRENT_TOOL_EXECUTIONS == 5


def test_error_category_values() -> None:
    """Test ErrorCategory enum values."""
    assert ErrorCategory.TIMEOUT.value == "timeout"
    assert ErrorCategory.RATE_LIMIT.value == "rate_limit"
    assert ErrorCategory.AUTHORIZATION.value == "authorization"
    assert ErrorCategory.UNKNOWN.value == "unknown"
