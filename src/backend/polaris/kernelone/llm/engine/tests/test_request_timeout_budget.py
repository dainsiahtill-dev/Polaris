"""Finite timeout budgets across canonical config, resilience and native codecs."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from polaris.infrastructure.llm.providers.anthropic_provider import AnthropicProvider
from polaris.infrastructure.llm.providers.openai_provider import OpenAIProvider
from polaris.infrastructure.llm.providers.provider_helpers import _bounded_http
from polaris.kernelone.llm.engine._executor_base import build_invoke_config
from polaris.kernelone.llm.engine.contracts import AIRequest, TaskType
from polaris.kernelone.llm.engine.executor import AIExecutor
from polaris.kernelone.llm.engine.invocation_budget import bind_provider_invocation_budget
from polaris.kernelone.shared.text_utils import normalize_int, normalize_timeout_seconds


@pytest.mark.parametrize("value", [299.4205446243286, 360, 360.0, "360.0", 0.25])
def test_native_timeout_keeps_finite_fractional_seconds(value: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    # The old integer parser silently reduced all float inputs to 60 seconds.
    expected = float(value)
    observed: list[tuple[float | None, float | None]] = []

    class StopBeforeTransport(BaseException):
        """Stop the synthetic capture before a physical HTTP session opens."""

    class CaptureSession:
        def __init__(self, *, timeout: Any, **_kwargs: Any) -> None:
            observed.append((timeout.total, timeout.sock_read))

        async def __aenter__(self) -> None:
            raise StopBeforeTransport

        async def __aexit__(self, *_args: Any) -> bool:
            return False

    monkeypatch.setattr(_bounded_http.aiohttp, "ClientSession", CaptureSession)
    monkeypatch.setattr(_bounded_http, "_proxy_and_ssl", lambda _url: (None, None))
    config = {"base_url": "http://synthetic.invalid", "timeout": value, "retries": 0}
    for provider in (OpenAIProvider(), AnthropicProvider()):
        with pytest.raises(StopBeforeTransport):
            provider.invoke("synthetic", "gpt-4o", config)
    assert observed == [(expected, expected), (expected, expected)]


@pytest.mark.parametrize("value", [None, "invalid", float("nan"), float("inf"), True, False])
def test_invalid_timeout_uses_default(value: Any) -> None:
    assert normalize_timeout_seconds(value, default=17) == 17


@pytest.mark.parametrize("value", [0, -1, -0.25, "0", "-0.25"])
def test_nonpositive_timeout_still_disables(value: Any) -> None:
    assert normalize_timeout_seconds(value, default=17) == 0


def test_timeout_fix_does_not_change_generic_integer_coercion() -> None:
    assert normalize_int(360.0, default=7) == 7


@pytest.mark.parametrize("run_remaining, expected", [(None, 360), (5400.0, 360), (30.0, 30)])
def test_merged_provider_budget_drives_resilience_and_native(
    run_remaining: float | None, expected: float, monkeypatch: pytest.MonkeyPatch
) -> None:
    executor = AIExecutor()
    monkeypatch.setattr(executor, "_get_provider_config", lambda _provider_id: {"timeout": 360})
    request = AIRequest(task_type=TaskType.DIALOGUE, role="synthetic", input="x", provider_id="synthetic")

    def check() -> None:
        config = build_invoke_config({"timeout": 360}, request.options)
        resilience = executor._build_request_resilience(request)
        assert config["timeout"] == pytest.approx(expected, abs=0.05)
        assert resilience.timeout_config.request_timeout == pytest.approx(expected, abs=0.05)

    if run_remaining is None:
        check()
    else:
        with bind_provider_invocation_budget(run_remaining):
            check()


def test_explicit_per_call_timeout_stays_distinct_from_parent_deadline() -> None:
    with bind_provider_invocation_budget(30):
        assert build_invoke_config({"timeout": 360}, {"timeout": 900})["timeout"] == pytest.approx(30, abs=0.05)
    assert build_invoke_config({"timeout": 360}, {"timeout": 900})["timeout"] == 900


@pytest.mark.asyncio
async def test_earlier_parent_budget_cannot_be_extended() -> None:
    with bind_provider_invocation_budget(0.03):
        config = build_invoke_config({"timeout": 360}, {"timeout": 5400})
        assert 0 < config["timeout"] <= 0.03
        await asyncio.sleep(0.04)
        with pytest.raises(TimeoutError, match="deadline"):
            build_invoke_config({"timeout": 360}, {})
