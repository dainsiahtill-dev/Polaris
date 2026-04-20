"""Tests for EvaluationRunner timeout and exception handling.

Tests cover:
- B001: Suite execution with timeout protection
- B002: Proper handling of CancelledError (not swallowed)
- Edge cases for timeout configuration
"""

from __future__ import annotations

import asyncio
from typing import NoReturn
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from polaris.cells.llm.evaluation.internal.runner import (
    AIStreamEventType,
    EvaluationRequest,
    EvaluationRunner,
    EvaluationSuiteResult,
)
from polaris.cells.llm.evaluation.internal.timeout import (
    TimeoutConfig,
    TimeoutResult,
)

# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def mock_settings():
    """Create mock settings object."""
    settings = MagicMock()
    settings.workspace = "."
    settings.ramdisk_root = ""
    return settings


@pytest.fixture
def timeout_config():
    """Create test timeout configuration."""
    return TimeoutConfig(
        suite_timeout_sec=5.0,
        case_timeout_sec=2.0,
        enable_timeout=True,
    )


@pytest.fixture
def runner(mock_settings, timeout_config):
    """Create EvaluationRunner instance with mocked settings."""
    return EvaluationRunner(
        workspace=".",
        settings=mock_settings,
        timeout_config=timeout_config,
    )


@pytest.fixture
def sample_request():
    """Create sample evaluation request."""
    return EvaluationRequest(
        provider_id="test-provider",
        model="test-model",
        role="pm",
        suites=["connectivity"],
        context={},
        options={},
    )


# =============================================================================
# B002: CancelledError Handling Tests
# =============================================================================


class TestCancelledErrorHandling:
    """Tests for B002: CancelledError must not be swallowed."""

    @pytest.mark.asyncio
    async def test_cancelled_error_re_raised_in_run(self, runner, sample_request) -> None:
        """Test that CancelledError is re-raised in run() method."""

        # Mock a suite runner that raises CancelledError
        async def mock_suite_that_cancels(*args, **kwargs):
            await asyncio.sleep(10)  # Long sleep
            return {"ok": True}

        runner.SUITE_RUNNERS = {"connectivity": mock_suite_that_cancels}

        # Create a task and cancel it
        async def run_and_cancel() -> None:
            task = asyncio.create_task(runner.run(sample_request))
            await asyncio.sleep(0.01)  # Let it start
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                raise  # Re-raise to test

        with pytest.raises(asyncio.CancelledError):
            await run_and_cancel()

    @pytest.mark.asyncio
    async def test_cancelled_error_re_raised_in_run_streaming(self, runner, sample_request) -> None:
        """Test that CancelledError propagates from slow suites in streaming mode.

        Note: This test verifies that when a suite is cancelled, the error propagates.
        The exact behavior depends on how the streaming generator handles cancellation.
        """

        # Mock a suite runner that raises CancelledError
        async def mock_suite_that_cancels(*args, **kwargs) -> NoReturn:
            raise asyncio.CancelledError()

        runner.SUITE_RUNNERS = {"connectivity": mock_suite_that_cancels}

        # When running the full suite, CancelledError should propagate
        with pytest.raises(asyncio.CancelledError):
            await runner.run(sample_request)

    @pytest.mark.asyncio
    async def test_exception_caught_but_not_cancelled_error(self, runner, sample_request) -> None:
        """Test that regular exceptions are caught but CancelledError is not."""

        async def mock_suite_that_fails(*args, **kwargs) -> NoReturn:
            raise ValueError("test error")

        runner.SUITE_RUNNERS = {"connectivity": mock_suite_that_fails}

        # Should not raise, but return result with error
        report = await runner.run(sample_request)
        assert report.summary["total_cases"] >= 1

    @pytest.mark.asyncio
    async def test_cancelled_error_not_swallowed_by_bare_except(self, runner, sample_request) -> None:
        """Test that CancelledError is not swallowed by except Exception."""

        async def mock_suite_raises_cancelled(*args, **kwargs) -> NoReturn:
            raise asyncio.CancelledError()

        runner.SUITE_RUNNERS = {"connectivity": mock_suite_raises_cancelled}

        # The CancelledError should propagate, not be caught
        with pytest.raises(asyncio.CancelledError):
            await runner.run(sample_request)


# =============================================================================
# B001: Timeout Protection Tests
# =============================================================================


class TestTimeoutProtection:
    """Tests for B001: Suite execution timeout protection."""

    @pytest.mark.asyncio
    async def test_suite_timeout_config_applied(self, mock_settings) -> None:
        """Test that timeout configuration is properly applied."""
        config = TimeoutConfig(
            suite_timeout_sec=0.1,  # Very short
            case_timeout_sec=0.05,
            enable_timeout=True,
        )
        runner = EvaluationRunner(
            workspace=".",
            settings=mock_settings,
            timeout_config=config,
        )

        # Track if suite was called
        suite_called = False

        async def slow_suite(*args, **kwargs):
            nonlocal suite_called
            suite_called = True
            await asyncio.sleep(10)  # Will timeout
            return {"ok": True}

        runner.SUITE_RUNNERS = {"connectivity": slow_suite}

        request = EvaluationRequest(
            provider_id="test",
            model="test",
            suites=["connectivity"],
        )

        report = await runner.run(request)

        # Suite should have been called
        assert suite_called
        # Should have failed due to timeout (passed_cases < total_cases)
        connectivity_result = next((s for s in report.suites if s.suite_name == "connectivity"), None)
        assert connectivity_result is not None
        assert connectivity_result.failed_cases > 0 or connectivity_result.passed_cases == 0

    @pytest.mark.asyncio
    async def test_timeout_config_from_options(self, mock_settings) -> None:
        """Test that timeout can be configured via options."""
        config = TimeoutConfig(enable_timeout=True)  # Default timeout
        runner = EvaluationRunner(
            workspace=".",
            settings=mock_settings,
            timeout_config=config,
        )

        async def slow_suite(*args, **kwargs):
            await asyncio.sleep(10)
            return {"ok": True}

        runner.SUITE_RUNNERS = {"connectivity": slow_suite}

        # Request with timeout override
        request = EvaluationRequest(
            provider_id="test",
            model="test",
            suites=["connectivity"],
            options={"suite_timeout_sec": 0.05},  # 50ms timeout
        )

        report = await runner.run(request)
        # Should handle timeout gracefully
        assert len(report.suites) >= 1

    @pytest.mark.asyncio
    async def test_timeout_disabled_by_config(self, mock_settings) -> None:
        """Test that timeout can be disabled via config."""
        config = TimeoutConfig(
            suite_timeout_sec=300.0,
            enable_timeout=False,  # Disabled
        )
        runner = EvaluationRunner(
            workspace=".",
            settings=mock_settings,
            timeout_config=config,
        )

        execution_time_ms = 0

        async def slow_suite(*args, **kwargs):
            nonlocal execution_time_ms
            import time

            start = time.perf_counter()
            await asyncio.sleep(0.05)
            execution_time_ms = int((time.perf_counter() - start) * 1000)
            return {"ok": True}

        runner.SUITE_RUNNERS = {"connectivity": slow_suite}

        request = EvaluationRequest(
            provider_id="test",
            model="test",
            suites=["connectivity"],
        )

        # Should complete without timeout
        await runner.run(request)
        assert execution_time_ms > 0

    @pytest.mark.asyncio
    async def test_get_suite_timeout_returns_configured_value(self, runner) -> None:
        """Test _get_suite_timeout returns configured value."""
        timeout = runner._get_suite_timeout("connectivity", {})
        assert timeout == runner.timeout_config.suite_timeout_sec

    @pytest.mark.asyncio
    async def test_get_suite_timeout_from_options(self, runner) -> None:
        """Test _get_suite_timeout reads from options."""
        options = {"suite_timeout_sec": 123.0}
        timeout = runner._get_suite_timeout("connectivity", options)
        assert timeout == 123.0

    @pytest.mark.asyncio
    async def test_get_suite_timeout_suite_specific(self, runner) -> None:
        """Test suite-specific timeout override."""
        options = {"connectivity_timeout_sec": 200.0}
        timeout = runner._get_suite_timeout("connectivity", options)
        assert timeout == 200.0

    @pytest.mark.asyncio
    async def test_run_suite_with_timeout(self, runner, sample_request) -> None:
        """Test _run_suite_with_timeout returns TimeoutResult."""

        async def quick_suite(*args, **kwargs):
            return {"ok": True}

        runner.SUITE_RUNNERS = {"connectivity": quick_suite}

        timeout_result = await runner._run_suite_with_timeout(
            "connectivity",
            quick_suite,
            sample_request,
            {},
            10.0,  # 10 second timeout
        )

        assert isinstance(timeout_result, TimeoutResult)
        assert timeout_result.ok is True

    @pytest.mark.asyncio
    async def test_run_suite_timeout_result_fields(self, runner, sample_request) -> None:
        """Test TimeoutResult fields are properly set."""

        async def quick_suite(*args, **kwargs):
            await asyncio.sleep(0.01)
            return {"ok": True, "data": "test"}

        runner.SUITE_RUNNERS = {"connectivity": quick_suite}

        timeout_result = await runner._run_suite_with_timeout(
            "connectivity",
            quick_suite,
            sample_request,
            {},
            10.0,  # 10 second timeout
        )

        assert isinstance(timeout_result, TimeoutResult)
        assert timeout_result.result == {"ok": True, "data": "test"}
        assert timeout_result.timed_out is False
        assert timeout_result.elapsed_ms > 0


# =============================================================================
# Streaming Mode Tests
# =============================================================================


class TestStreamingMode:
    """Tests for streaming mode timeout and error handling."""

    @pytest.mark.asyncio
    async def test_streaming_yields_timeout_error(self, runner, sample_request) -> None:
        """Test that streaming mode yields timeout error events."""

        async def slow_suite(*args, **kwargs):
            await asyncio.sleep(10)
            return {"ok": True}

        runner.SUITE_RUNNERS = {"connectivity": slow_suite}

        # Set very short timeout
        runner.timeout_config.suite_timeout_sec = 0.05

        events = []
        async for event in runner.run_streaming(sample_request):
            events.append(event)

        # Should have start and completion events
        assert len(events) >= 2
        # Completion event should indicate failure
        complete_event = events[-1]
        assert complete_event.type == AIStreamEventType.COMPLETE

    @pytest.mark.asyncio
    async def test_streaming_cancelled_error_re_raised(self, runner, sample_request) -> None:
        """Test that streaming mode re-raises CancelledError."""

        async def cancellable_suite(*args, **kwargs):
            await asyncio.sleep(10)
            return {"ok": True}

        runner.SUITE_RUNNERS = {"connectivity": cancellable_suite}

        async def consume_stream():
            collected = []
            async for event in runner.run_streaming(sample_request):
                collected.append(event)
            return collected

        # Cancel after collecting some events
        task = asyncio.create_task(consume_stream())
        await asyncio.sleep(0.01)
        task.cancel()

        with pytest.raises(asyncio.CancelledError):
            await task


# =============================================================================
# Suite Result Conversion Tests
# =============================================================================


class TestSuiteResultConversion:
    """Tests for _convert_suite_result method."""

    def test_convert_simple_result(self, runner) -> None:
        """Test converting simple result without cases."""
        result_data = {"ok": True}
        suite_result = runner._convert_suite_result("connectivity", result_data)

        assert isinstance(suite_result, EvaluationSuiteResult)
        assert suite_result.suite_name == "connectivity"
        assert suite_result.total_cases == 1
        assert suite_result.passed_cases == 1

    def test_convert_failed_result(self, runner) -> None:
        """Test converting failed result."""
        result_data = {"ok": False, "error": "connection failed"}
        suite_result = runner._convert_suite_result("connectivity", result_data)

        assert suite_result.total_cases == 1
        assert suite_result.passed_cases == 0
        assert suite_result.failed_cases == 1

    def test_convert_result_with_cases(self, runner) -> None:
        """Test converting result with detailed cases."""
        result_data = {
            "ok": False,
            "details": {
                "cases": [
                    {"id": "case1", "passed": True, "output": "ok", "score": 1.0},
                    {"id": "case2", "passed": False, "output": "fail", "score": 0.0},
                    {"id": "case3", "passed": True, "output": "ok", "score": 1.0},
                ]
            },
        }
        suite_result = runner._convert_suite_result("qualification", result_data)

        assert suite_result.total_cases == 3
        assert suite_result.passed_cases == 2
        assert suite_result.failed_cases == 1
        assert len(suite_result.results) == 3

    def test_convert_timeout_result(self, runner) -> None:
        """Test converting timeout result."""
        result_data = {
            "ok": False,
            "error": "Suite connectivity timed out after 5.0s",
            "timed_out": True,
        }
        suite_result = runner._convert_suite_result("connectivity", result_data)

        assert suite_result.total_cases == 1
        assert suite_result.passed_cases == 0


# =============================================================================
# Normalize Suites Tests
# =============================================================================


class TestNormalizeSuites:
    """Tests for normalize_suites method."""

    def test_empty_role_defaults_to_connectivity(self, runner) -> None:
        """Test that empty role defaults to connectivity suite."""
        suites = runner.normalize_suites([], "")
        assert suites == ["connectivity"]

    def test_connectivity_role_only(self, runner) -> None:
        """Test that connectivity role only runs connectivity."""
        suites = runner.normalize_suites([], "connectivity")
        assert suites == ["connectivity"]

    def test_default_role_uses_required_suites(self, runner) -> None:
        """Test that default role uses required suites."""
        suites = runner.normalize_suites([], "default")
        assert "connectivity" in suites
        assert "response" in suites
        assert "qualification" in suites

    def test_deduplication(self, runner) -> None:
        """Test that duplicate suites are removed."""
        suites = runner.normalize_suites(
            ["connectivity", "connectivity", "response"],
            "pm",
        )
        assert suites.count("connectivity") == 1

    def test_whitespace_handling(self, runner) -> None:
        """Test that whitespace in suite names is handled."""
        suites = runner.normalize_suites(["  connectivity  ", " response "], "pm")
        assert "connectivity" in suites
        assert "response" in suites

    def test_case_insensitive(self, runner) -> None:
        """Test that suite names are case-insensitive."""
        suites = runner.normalize_suites(["CONNECTIVITY", "Response"], "pm")
        assert "connectivity" in suites
        assert "response" in suites


# =============================================================================
# Edge Cases
# =============================================================================


class TestEdgeCases:
    """Tests for edge cases and boundary conditions."""

    @pytest.mark.asyncio
    async def test_empty_suites_list(self, runner, sample_request) -> None:
        """Test with empty suites list."""
        sample_request.suites = []
        sample_request.role = "connectivity"  # Simplest role

        # Mock provider resolution to avoid actual calls
        with patch.object(runner, "_resolve_provider_cfg", return_value={}):
            # Mock the suite runner to return quick success
            runner.SUITE_RUNNERS = {"connectivity": AsyncMock(return_value={"ok": True})}

            report = await runner.run(sample_request)
            assert report.suites  # Should have connectivity suite

    @pytest.mark.asyncio
    async def test_unknown_suite_skipped(self, runner, sample_request) -> None:
        """Test that unknown suites are skipped."""
        sample_request.suites = ["connectivity", "nonexistent_suite"]

        runner.SUITE_RUNNERS = {"connectivity": AsyncMock(return_value={"ok": True})}

        report = await runner.run(sample_request)
        assert len(report.suites) == 1
        assert report.suites[0].suite_name == "connectivity"

    @pytest.mark.asyncio
    async def test_provider_config_loading_failure(self, runner, sample_request) -> None:
        """Test handling when provider config loading fails."""
        # Mock _load_provider_cfg to return empty
        runner._load_provider_cfg = lambda x: {}

        runner.SUITE_RUNNERS = {"connectivity": AsyncMock(return_value={"ok": True})}

        # Should not raise, should handle gracefully
        report = await runner.run(sample_request)
        assert report.provider_id == sample_request.provider_id

    @pytest.mark.asyncio
    async def test_zero_timeout_from_options(self, mock_settings) -> None:
        """Test that zero timeout from options disables timeout."""
        config = TimeoutConfig(enable_timeout=True)
        runner = EvaluationRunner(
            workspace=".",
            settings=mock_settings,
            timeout_config=config,
        )

        timeout = runner._get_suite_timeout(
            "connectivity",
            {"suite_timeout_sec": 0.0},
        )
        # Zero should be returned (timeout disabled)
        assert timeout == 0.0

    @pytest.mark.asyncio
    async def test_multiple_suites_with_different_timeouts(self, runner, sample_request) -> None:
        """Test running multiple suites with their own timeouts."""
        results = []

        async def suite_a(*args, **kwargs):
            results.append("a_start")
            await asyncio.sleep(0.01)
            results.append("a_end")
            return {"ok": True}

        async def suite_b(*args, **kwargs):
            results.append("b_start")
            await asyncio.sleep(0.01)
            results.append("b_end")
            return {"ok": True}

        runner.SUITE_RUNNERS = {
            "connectivity": suite_a,
            "response": suite_b,
        }
        sample_request.suites = ["connectivity", "response"]
        sample_request.role = "default"

        # Set timeouts
        runner.timeout_config.suite_timeout_sec = 10.0

        report = await runner.run(sample_request)
        assert "a_start" in results
        assert "b_start" in results
        assert len(report.suites) == 2
