"""Tests for AIExecutor core functionality.

Tests cover:
  - Basic invoke flow
  - Timeout handling
  - Error classification
  - Resilience policy construction
  - WorkspaceExecutorManager singleton behavior
  - invoke_stream deprecation warning
"""

from __future__ import annotations

import warnings
from unittest.mock import MagicMock, patch

import pytest
from polaris.kernelone.llm.engine.contracts import (
    AIRequest,
    AIResponse,
    ErrorCategory,
    TaskType,
)
from polaris.kernelone.llm.engine.executor import (
    AIExecutor,
    WorkspaceExecutorManager,
    _ExecutorEntry,
    get_executor,
    reset_executor_manager,
    set_executor,
)


class TestAIExecutorBasic:
    """Tests for AIExecutor basic invoke functionality."""

    @pytest.mark.asyncio
    async def test_executor_initialization(self) -> None:
        """AIExecutor must initialize with default values."""
        executor = AIExecutor()

        assert executor.workspace is None
        assert executor.resilience is not None
        assert executor.model_catalog is not None
        assert executor.token_budget is not None

    @pytest.mark.asyncio
    async def test_executor_with_workspace(self) -> None:
        """AIExecutor must accept workspace parameter."""
        executor = AIExecutor(workspace="/tmp/test")

        assert executor.workspace == "/tmp/test"

    @pytest.mark.asyncio
    async def test_invoke_with_stream_option_raises(self) -> None:
        """invoke() with stream=True must raise NotImplementedError."""
        executor = AIExecutor()
        request = AIRequest(
            task_type=TaskType.DIALOGUE,
            role="test",
            input="test",
            options={"stream": True},
        )

        with pytest.raises(NotImplementedError) as exc_info:
            await executor.invoke(request)

        assert "invoke_stream" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_invoke_failure_when_provider_not_resolved(self) -> None:
        """invoke() must return failure when provider cannot be resolved."""
        executor = AIExecutor()

        request = AIRequest(
            task_type=TaskType.DIALOGUE,
            role="unknown_role_no_provider",
            input="test",
        )

        response = await executor.invoke(request)

        assert response.ok is False
        assert response.error_category == ErrorCategory.CONFIG_ERROR

    @pytest.mark.asyncio
    async def test_invoke_failure_when_provider_not_found(self) -> None:
        """invoke() must return failure when provider type is unknown."""
        executor = AIExecutor()

        request = AIRequest(
            task_type=TaskType.DIALOGUE,
            role="test",
            input="test",
            provider_id="nonexistent_provider",
            model="gpt-4",
        )

        response = await executor.invoke(request)

        assert response.ok is False
        assert response.error_category == ErrorCategory.CONFIG_ERROR

    @pytest.mark.asyncio
    async def test_invoke_exception_returns_failure(self) -> None:
        """invoke() must catch exceptions and return failure response."""
        # Create executor without telemetry
        executor = AIExecutor(telemetry=None)

        # Mock _resolve_provider_model to raise - this tests the exception handling
        with patch.object(
            executor,
            "_resolve_provider_model",
            side_effect=RuntimeError("unexpected error"),
        ):
            request = AIRequest(
                task_type=TaskType.DIALOGUE,
                role="test",
                input="test",
            )
            response = await executor.invoke(request)

        assert response.ok is False
        assert response.error_category == ErrorCategory.UNKNOWN
        assert "unexpected error" in response.error


class TestAIExecutorTimeout:
    """Tests for timeout handling in AIExecutor."""

    @pytest.mark.asyncio
    async def test_timeout_config_from_options(self) -> None:
        """TimeoutConfig must be constructable from request options."""
        from polaris.kernelone.llm.engine.resilience import TimeoutConfig

        options = {
            "timeout": 30.0,
            "total_timeout": 120.0,
            "connect_timeout": 5.0,
        }

        config = TimeoutConfig.from_options(options)

        assert config.request_timeout == 30.0
        assert config.total_timeout == 120.0
        assert config.connect_timeout == 5.0

    @pytest.mark.asyncio
    async def test_build_request_resilience_uses_options(self) -> None:
        """_build_request_resilience must use timeout from request options."""
        executor = AIExecutor()

        request = AIRequest(
            task_type=TaskType.DIALOGUE,
            role="test",
            input="test",
            options={
                "timeout": 10.0,
                "max_retries": 2,
            },
        )

        resilience = executor._build_request_resilience(request)

        assert resilience.timeout_config.request_timeout == 10.0
        assert resilience.retry_config.max_attempts == 3  # 2 + 1

    @pytest.mark.asyncio
    async def test_build_request_resilience_fallback_on_invalid_options(self) -> None:
        """_build_request_resilience must fallback on invalid options."""
        executor = AIExecutor()

        request = AIRequest(
            task_type=TaskType.DIALOGUE,
            role="test",
            input="test",
            options={"timeout": "invalid"},  # Invalid type
        )

        resilience = executor._build_request_resilience(request)

        # Should fallback to default resilience
        assert resilience.timeout_config.request_timeout == 60.0

    @pytest.mark.asyncio
    async def test_invoke_with_timeout_via_resilience(self) -> None:
        """invoke() must respect timeout through resilience manager."""
        from polaris.kernelone.llm.engine.resilience import AIResponse

        executor = AIExecutor(telemetry=None)

        # Mock _invoke_with_resilience to return timeout error
        timeout_response = AIResponse.failure(
            error="Request timeout",
            category=ErrorCategory.TIMEOUT,
        )

        async def mock_invoke_with_resilience(request, trace_id):
            return timeout_response

        with (
            patch.object(
                executor,
                "_resolve_provider_model",
                return_value=("mock_provider", "gpt-4"),
            ),
            patch.object(
                executor,
                "_invoke_with_resilience",
                side_effect=mock_invoke_with_resilience,
            ),
        ):
            request = AIRequest(
                task_type=TaskType.DIALOGUE,
                role="test",
                input="test",
                options={"timeout": 0.001},
            )

            response = await executor.invoke(request)

        assert response.ok is False
        assert response.error_category == ErrorCategory.TIMEOUT


class TestWorkspaceExecutorManager:
    """Tests for WorkspaceExecutorManager singleton behavior."""

    def test_workspace_key_default(self) -> None:
        """_workspace_key must return _default_ for None/empty."""
        manager = WorkspaceExecutorManager()

        assert manager._workspace_key(None) == "_default_"
        assert manager._workspace_key("") == "_default_"
        assert manager._workspace_key("   ") == "_default_"

    def test_workspace_key_normal(self) -> None:
        """_workspace_key must preserve non-empty workspace."""
        manager = WorkspaceExecutorManager()

        assert manager._workspace_key("/tmp/workspace") == "/tmp/workspace"
        assert manager._workspace_key("C:\\Users\\test") == "C:\\Users\\test"

    @pytest.mark.asyncio
    async def test_get_executor_sync_creates_new(self) -> None:
        """get_executor_sync must create new executor for new workspace."""
        manager = WorkspaceExecutorManager()

        executor1 = manager.get_executor_sync(None)
        executor2 = manager.get_executor_sync("/tmp/workspace")

        assert isinstance(executor1, AIExecutor)
        assert isinstance(executor2, AIExecutor)
        assert executor1 is not executor2

    @pytest.mark.asyncio
    async def test_get_executor_sync_returns_same(self) -> None:
        """get_executor_sync must return same executor for same workspace."""
        manager = WorkspaceExecutorManager()

        executor1 = manager.get_executor_sync("/tmp/workspace")
        executor2 = manager.get_executor_sync("/tmp/workspace")

        assert executor1 is executor2

    @pytest.mark.asyncio
    async def test_get_executor_async(self) -> None:
        """get_executor_async must return executor."""
        manager = WorkspaceExecutorManager()

        executor = await manager.get_executor("/tmp/test")

        assert isinstance(executor, AIExecutor)

    @pytest.mark.asyncio
    async def test_set_executor(self) -> None:
        """set_executor must replace executor for workspace."""
        manager = WorkspaceExecutorManager()

        original = manager.get_executor_sync("/tmp/workspace")
        mock_executor = MagicMock(spec=AIExecutor)
        manager.set_executor(mock_executor, "/tmp/workspace")

        replacement = manager.get_executor_sync("/tmp/workspace")
        assert replacement is mock_executor
        assert replacement is not original


class TestExecutorManagerGlobals:
    """Tests for global executor manager functions."""

    def test_get_executor_returns_executor(self) -> None:
        """get_executor must return AIExecutor instance."""
        reset_executor_manager()

        executor = get_executor()

        assert isinstance(executor, AIExecutor)

    def test_set_executor_replaces_global(self) -> None:
        """set_executor must replace global executor."""
        reset_executor_manager()

        original = get_executor()
        mock_executor = MagicMock(spec=AIExecutor)
        set_executor(mock_executor)

        replaced = get_executor()
        assert replaced is mock_executor
        assert replaced is not original


class TestAIExecutorDeprecations:
    """Tests for deprecated methods."""

    @pytest.mark.asyncio
    async def test_execute_deprecated(self) -> None:
        """execute() must emit DeprecationWarning."""
        executor = AIExecutor()

        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")

            request = AIRequest(
                task_type=TaskType.DIALOGUE,
                role="test",
                input="test",
            )
            # Mock to prevent actual invocation
            with patch.object(executor, "invoke", return_value=AIResponse.success("ok")):
                await executor.execute(request)

        # Check for deprecation warning
        deprecation_warnings = [warning for warning in w if issubclass(warning.category, DeprecationWarning)]
        assert len(deprecation_warnings) >= 1
        assert "deprecated" in str(deprecation_warnings[0].message).lower()

    @pytest.mark.asyncio
    async def test_invoke_stream_deprecated(self) -> None:
        """invoke_stream() must emit DeprecationWarning."""
        executor = AIExecutor()

        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")

            request = AIRequest(
                task_type=TaskType.DIALOGUE,
                role="test",
                input="test",
            )

            # invoke_stream is a generator, iterate to trigger warning
            gen = executor.invoke_stream(request)
            await gen.__anext__()  # Trigger the warning

        deprecation_warnings = [warning for warning in w if issubclass(warning.category, DeprecationWarning)]
        assert len(deprecation_warnings) >= 1
        assert "experimental" in str(deprecation_warnings[0].message).lower()


class TestAIExecutorInvokeStream:
    """Tests for invoke_stream functionality."""

    @pytest.mark.asyncio
    async def test_invoke_stream_yields_error_on_exception(self) -> None:
        """invoke_stream must yield error event on exception."""
        # Test relies on provider resolution failure path
        # Use an invalid provider to trigger error event
        executor = AIExecutor()

        request = AIRequest(
            task_type=TaskType.DIALOGUE,
            role="test",
            input="test",
            provider_id="nonexistent_provider",
            model="gpt-4",
        )

        events = []
        async for event in executor.invoke_stream(request):
            events.append(event)

        assert len(events) >= 1
        error_events = [e for e in events if e.get("event_type") == "error"]
        assert len(error_events) >= 1


class TestAIExecutorWithRepair:
    """Tests for invoke_with_repair functionality."""

    @pytest.mark.asyncio
    async def test_invoke_with_repair_no_truncation(self) -> None:
        """invoke_with_repair must not attempt repair on normal output."""
        # Create executor with mocked resilience
        mock_resilience = MagicMock()
        mock_resilience.should_attempt_repair.return_value = False
        # Return a fresh ResilienceManager with proper behavior for other calls
        mock_resilience.truncation_config.max_repair_tokens = 1200

        executor = AIExecutor(resilience=mock_resilience)

        request = AIRequest(
            task_type=TaskType.DIALOGUE,
            role="test",
            input="test",
            provider_id="mock",
            model="gpt-4",
        )

        # Mock the invoke method directly on the executor
        with patch.object(
            AIExecutor,
            "invoke",
            return_value=AIResponse.success("normal output"),
        ):
            response = await executor.invoke_with_repair(request)

        assert response.ok is True
        assert response.output == "normal output"

    @pytest.mark.asyncio
    async def test_invoke_with_repair_returns_failure_on_truncation(self) -> None:
        """invoke_with_repair must return failure when invoke fails."""
        # Create executor with mocked resilience
        mock_resilience = MagicMock()
        mock_resilience.should_attempt_repair.return_value = True
        mock_resilience.build_repair_prompt.return_value = "repair prompt"

        executor = AIExecutor(resilience=mock_resilience)

        request = AIRequest(
            task_type=TaskType.DIALOGUE,
            role="test",
            input="test",
            provider_id="mock",
            model="gpt-4",
        )

        # Mock invoke to return failure
        with patch.object(
            AIExecutor,
            "invoke",
            return_value=AIResponse.failure(
                error="provider unavailable",
                category=ErrorCategory.PROVIDER_ERROR,
            ),
        ):
            response = await executor.invoke_with_repair(request)

        assert response.ok is False


class TestAIResponseFactory:
    """Tests for AIResponse factory methods."""

    def test_ai_response_success_factory(self) -> None:
        """AIResponse.success must create successful response."""
        response = AIResponse.success(
            output="test output",
            latency_ms=100,
            model="gpt-4",
        )

        assert response.ok is True
        assert response.output == "test output"
        assert response.latency_ms == 100
        assert response.model == "gpt-4"

    def test_ai_response_failure_factory(self) -> None:
        """AIResponse.failure must create failed response."""
        response = AIResponse.failure(
            error="test error",
            category=ErrorCategory.TIMEOUT,
            latency_ms=50,
        )

        assert response.ok is False
        assert response.error == "test error"
        assert response.error_category == ErrorCategory.TIMEOUT
        assert response.latency_ms == 50

    def test_ai_response_usage_estimation(self) -> None:
        """AIResponse must estimate usage when not provided."""
        response = AIResponse.success(output="hello world")

        assert response.usage is not None
        assert response.usage.total_tokens > 0


class TestExecutorEntry:
    """Tests for _ExecutorEntry dataclass."""

    def test_executor_entry_defaults(self) -> None:
        """_ExecutorEntry must have default ref_count of 0."""
        mock_executor = MagicMock()
        entry = _ExecutorEntry(executor=mock_executor)

        assert entry.executor is mock_executor
        assert entry.ref_count == 0

    def test_executor_entry_custom_ref_count(self) -> None:
        """_ExecutorEntry must accept custom ref_count."""
        mock_executor = MagicMock()
        entry = _ExecutorEntry(executor=mock_executor, ref_count=5)

        assert entry.ref_count == 5
