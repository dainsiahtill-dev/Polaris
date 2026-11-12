"""A stalled provider request must not end the engineering run.

A request timeout is a transport failure. The platform contract says
transport failures are retried inside the attempt budget, but the timeout
path returned a failure response immediately, so one stalled upstream
request ended the whole run. The retry budget also has to cover the
configured attempts: a request that stalls for the full request timeout
otherwise leaves no room for the attempt the transport contract allows.
"""

from __future__ import annotations

import asyncio

import pytest
from polaris.kernelone.llm.engine.contracts import AIRequest, AIResponse, TaskType
from polaris.kernelone.llm.engine.executor import AIExecutor
from polaris.kernelone.llm.engine.resilience import (
    ErrorCategory,
    ResilienceManager,
    RetryConfig,
    TimeoutConfig,
)


@pytest.mark.asyncio
async def test_request_timeout_is_retried_inside_the_attempt_budget() -> None:
    attempts = 0

    async def operation() -> None:
        nonlocal attempts
        attempts += 1
        await asyncio.sleep(3600)

    manager = ResilienceManager(
        timeout_config=TimeoutConfig(request_timeout=0.05, total_timeout=5.0),
        retry_config=RetryConfig(max_attempts=2, base_delay=0.0),
    )

    response = await manager.execute_with_resilience(operation, operation_name="probe")

    assert attempts == 2
    assert response.ok is False
    assert response.error_category == ErrorCategory.TIMEOUT
    assert "All 2 attempts failed" in str(response.error)
    assert "Request timeout" in str(response.error)
    assert response.platform_retry_count == 1
    assert response.platform_retry_exhausted is True


@pytest.mark.asyncio
async def test_a_timeout_that_recovers_returns_the_successful_response() -> None:
    attempts = 0

    async def operation():
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            await asyncio.sleep(3600)
        return AIResponse.success(output="ok")

    manager = ResilienceManager(
        timeout_config=TimeoutConfig(request_timeout=0.05, total_timeout=5.0),
        retry_config=RetryConfig(max_attempts=2, base_delay=0.0),
    )

    response = await manager.execute_with_resilience(operation, operation_name="probe")

    assert attempts == 2
    assert response.ok is True


def test_request_local_total_budget_covers_the_configured_attempts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executor = AIExecutor()
    monkeypatch.setattr(executor, "_get_provider_config", lambda _provider: {"timeout": 300})
    request = AIRequest(
        task_type=TaskType.DIALOGUE,
        role="director",
        provider_id="synthetic",
        input="x",
    )

    policy = executor._build_request_resilience(request)

    assert policy.retry_config.max_attempts == 2
    # One attempt may use the full request timeout, so the total budget has to
    # cover the second attempt as well.
    assert policy.timeout_config.request_timeout == 300.0
    assert policy.timeout_config.total_timeout >= 600.0


def test_request_local_total_budget_keeps_a_larger_host_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    executor = AIExecutor()
    monkeypatch.setattr(executor, "_get_provider_config", lambda _provider: {"timeout": 30})
    request = AIRequest(
        task_type=TaskType.DIALOGUE,
        role="director",
        provider_id="synthetic",
        input="x",
        options={"total_timeout": 900},
    )

    policy = executor._build_request_resilience(request)

    assert policy.timeout_config.request_timeout == 30.0
    assert policy.timeout_config.total_timeout == 900.0
