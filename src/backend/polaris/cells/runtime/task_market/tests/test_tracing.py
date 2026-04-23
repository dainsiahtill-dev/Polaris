"""Tests for TaskMarketTracer — OTel span creation and NoOp fallback."""

from __future__ import annotations

from polaris.cells.runtime.task_market.internal.tracing import (
    NoOpSpan,
    TaskMarketTracer,
    reset_task_market_tracer_for_testing,
)


def test_tracing_disabled_is_noop() -> None:
    tracer = TaskMarketTracer(enabled=False)
    assert tracer.enabled is False
    span = tracer.start_span("task_market.publish", {"task_id": "t-1"})
    assert isinstance(span, NoOpSpan)
    # NoOpSpan should be usable as context manager.
    with span:
        span.set_attribute("key", "value")
        span.add_event("event_name")


def test_noop_span_is_context_manager() -> None:
    span = NoOpSpan()
    with span as s:
        s.set_attribute("test", 42)
        s.add_event("test_event", {"detail": "ok"})
    # No exception raised.


def test_tracer_default_disabled_without_env() -> None:
    """When KERNELONE_TASK_MARKET_TRACING_ENABLED is not set, tracer is disabled."""
    import os

    env_key = "KERNELONE_TASK_MARKET_TRACING_ENABLED"
    original = os.environ.pop(env_key, None)
    try:
        tracer = TaskMarketTracer()
        assert tracer.enabled is False
    finally:
        if original is not None:
            os.environ[env_key] = original


def test_tracer_enabled_via_env(monkeypatch) -> None:
    """When env var is set to true and OTel SDK is available, tracer is enabled."""
    monkeypatch.setenv("KERNELONE_TASK_MARKET_TRACING_ENABLED", "true")
    tracer = TaskMarketTracer()
    # The enabled flag depends on whether opentelemetry is importable.
    # In a test environment without OTel SDK, it gracefully falls back to disabled.
    # In CI with OTel SDK installed, it would be enabled.
    # We just verify it doesn't crash.
    span = tracer.start_span("test_span", {"key": "value"})
    # Should be either a real span or NoOpSpan, both are valid.
    assert span is not None


def test_reset_tracer_for_testing() -> None:
    tracer = reset_task_market_tracer_for_testing(enabled=False)
    assert tracer.enabled is False

    tracer_enabled = reset_task_market_tracer_for_testing(enabled=True)
    # If OTel SDK is not installed, enabled will be False despite request.
    # Just verify it doesn't crash.
    assert isinstance(tracer_enabled, TaskMarketTracer)


def test_tracer_start_span_returns_context_manager() -> None:
    tracer = TaskMarketTracer(enabled=False)
    span = tracer.start_span("task_market.claim", {"task_id": "t-1", "stage": "pending_exec"})
    # Should be usable as a context manager without error.
    with span:
        pass
