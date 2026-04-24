"""Latency baseline benchmarks for core Polaris components.

Measures performance of:
- TurnTransactionController single execution
- LLM Provider mock call latency
- ContextOS read/write operations

Uses simple time.perf_counter() timing (no pytest-benchmark dependency).
"""

from __future__ import annotations

import contextlib
import time
from datetime import datetime, timezone
from typing import Any

import pytest
from polaris.cells.roles.kernel.internal.transaction.ledger import (
    TurnLedger,
)
from polaris.cells.roles.kernel.internal.turn_state_machine import (
    TurnState,
    TurnStateMachine,
)
from polaris.cells.roles.kernel.internal.turn_transaction_controller import (
    TurnTransactionController,
)

from .conftest import (
    BenchmarkResult,
    BenchmarkSuite,
    LatencyThresholds,
    calculate_stats,
    save_baseline,
)

# =============================================================================
# Mock Factories
# =============================================================================


def _create_mock_llm_provider() -> Any:
    """Create a mock LLM provider that returns immediately."""

    async def _mock_call(*args: Any, **kwargs: Any) -> dict[str, Any]:
        return {
            "content": '{"kind": "FINAL_ANSWER", "visible_message": "Mock response"}',
            "model": "mock-model",
            "provider": "mock",
            "usage": {"prompt_tokens": 10, "completion_tokens": 20},
        }

    return _mock_call


def _create_mock_tool_runtime() -> Any:
    """Create a mock tool runtime that returns immediately."""

    async def _mock_execute(*args: Any, **kwargs: Any) -> dict[str, Any]:
        return {"status": "success", "output": "mock output"}

    return _mock_execute


def _create_minimal_context() -> list[dict[str, Any]]:
    """Create minimal conversation context for benchmarking."""
    return [
        {"role": "system", "content": "You are a helpful assistant."},
        {"role": "user", "content": "Hello, analyze this code."},
    ]


def _create_minimal_tool_definitions() -> list[dict[str, Any]]:
    """Create minimal tool definitions for benchmarking."""
    return [
        {
            "name": "read_file",
            "description": "Read a file",
            "parameters": {"type": "object", "properties": {"path": {"type": "string"}}},
        },
    ]


# =============================================================================
# TurnTransactionController Benchmarks
# =============================================================================


class TestTurnTransactionLatency:
    """Benchmark TurnTransactionController single execution latency."""

    def test_controller_initialization_latency(
        self,
        latency_thresholds: type[LatencyThresholds],
    ) -> BenchmarkResult:
        """Benchmark controller initialization time."""
        llm_provider = _create_mock_llm_provider()
        tool_runtime = _create_mock_tool_runtime()

        times_ms: list[float] = []
        for _ in range(10):  # warmup
            TurnTransactionController(
                llm_provider=llm_provider,
                tool_runtime=tool_runtime,
            )

        for _ in range(100):
            start = time.perf_counter()
            TurnTransactionController(
                llm_provider=llm_provider,
                tool_runtime=tool_runtime,
            )
            elapsed_ms = (time.perf_counter() - start) * 1000.0
            times_ms.append(elapsed_ms)

        stats = calculate_stats(times_ms)
        result = BenchmarkResult(
            name="controller_init",
            iterations=100,
            total_ms=sum(times_ms),
            **stats,
        )

        assert result.p95_ms < latency_thresholds.STATE_MACHINE_TRANSITION_MS * 2, (
            f"Controller init p95 ({result.p95_ms:.2f}ms) exceeds threshold"
        )

        return result

    @pytest.mark.asyncio
    async def test_turn_execute_latency(
        self,
        latency_thresholds: type[LatencyThresholds],
    ) -> BenchmarkResult:
        """Benchmark single turn execution with mock LLM."""
        llm_provider = _create_mock_llm_provider()
        tool_runtime = _create_mock_tool_runtime()

        controller = TurnTransactionController(
            llm_provider=llm_provider,
            tool_runtime=tool_runtime,
        )

        context = _create_minimal_context()
        tool_defs = _create_minimal_tool_definitions()

        times_ms: list[float] = []
        for i in range(50):
            turn_id = f"bench-turn-{i}"
            start = time.perf_counter()
            with contextlib.suppress(Exception):
                await controller.execute(
                    turn_id=turn_id,
                    context=context,
                    tool_definitions=tool_defs,
                )
            elapsed_ms = (time.perf_counter() - start) * 1000.0
            times_ms.append(elapsed_ms)

        stats = calculate_stats(times_ms)
        result = BenchmarkResult(
            name="turn_execute",
            iterations=50,
            total_ms=sum(times_ms),
            **stats,
        )

        assert result.p95_ms < latency_thresholds.TURN_EXECUTE_P95_MS, (
            f"Turn execute p95 ({result.p95_ms:.2f}ms) exceeds threshold ({latency_thresholds.TURN_EXECUTE_P95_MS}ms)"
        )

        return result

    def test_state_machine_transition_latency(
        self,
        latency_thresholds: type[LatencyThresholds],
    ) -> BenchmarkResult:
        """Benchmark state machine transitions."""
        times_ms: list[float] = []

        for _ in range(5):  # warmup
            sm = TurnStateMachine(turn_id="warmup")
            sm.transition_to(TurnState.CONTEXT_BUILT)

        for _ in range(1000):
            sm = TurnStateMachine(turn_id="bench")
            start = time.perf_counter()
            sm.transition_to(TurnState.CONTEXT_BUILT)
            sm.transition_to(TurnState.DECISION_REQUESTED)
            sm.transition_to(TurnState.DECISION_RECEIVED)
            elapsed_ms = (time.perf_counter() - start) * 1000.0
            times_ms.append(elapsed_ms)

        stats = calculate_stats(times_ms)
        result = BenchmarkResult(
            name="state_machine_transitions",
            iterations=1000,
            total_ms=sum(times_ms),
            **stats,
        )

        assert result.p95_ms < latency_thresholds.STATE_MACHINE_TRANSITION_MS, (
            f"State transition p95 ({result.p95_ms:.3f}ms) exceeds threshold"
        )

        return result

    def test_ledger_record_latency(
        self,
        latency_thresholds: type[LatencyThresholds],
    ) -> BenchmarkResult:
        """Benchmark ledger recording operations."""
        times_ms: list[float] = []

        for _ in range(1000):
            ledger = TurnLedger(turn_id="bench-ledger")
            start = time.perf_counter()
            ledger.state_history.append(("CONTEXT_BUILT", int(time.time() * 1000)))
            ledger.anomaly_flags.append({"type": "TEST", "data": "bench"})
            elapsed_ms = (time.perf_counter() - start) * 1000.0
            times_ms.append(elapsed_ms)

        stats = calculate_stats(times_ms)
        result = BenchmarkResult(
            name="ledger_record",
            iterations=1000,
            total_ms=sum(times_ms),
            **stats,
        )

        assert result.p95_ms < latency_thresholds.LEDGER_RECORD_MS, (
            f"Ledger record p95 ({result.p95_ms:.3f}ms) exceeds threshold"
        )

        return result


# =============================================================================
# LLM Provider Mock Latency
# =============================================================================


class TestLLMProviderLatency:
    """Benchmark LLM provider call latency in mock mode."""

    @pytest.mark.asyncio
    async def test_mock_provider_call_latency(
        self,
        latency_thresholds: type[LatencyThresholds],
    ) -> BenchmarkResult:
        """Benchmark mock LLM provider invocation."""
        provider = _create_mock_llm_provider()

        times_ms: list[float] = []
        for _ in range(100):
            start = time.perf_counter()
            await provider(
                messages=[{"role": "user", "content": "test"}],
                model="mock-model",
            )
            elapsed_ms = (time.perf_counter() - start) * 1000.0
            times_ms.append(elapsed_ms)

        stats = calculate_stats(times_ms)
        result = BenchmarkResult(
            name="llm_provider_mock_call",
            iterations=100,
            total_ms=sum(times_ms),
            **stats,
        )

        assert result.p95_ms < latency_thresholds.LLM_PROVIDER_P95_MS, (
            f"LLM mock call p95 ({result.p95_ms:.3f}ms) exceeds threshold"
        )

        return result

    @pytest.mark.asyncio
    async def test_provider_with_streaming_latency(
        self,
        latency_thresholds: type[LatencyThresholds],
    ) -> BenchmarkResult:
        """Benchmark streaming LLM provider response."""

        async def _mock_stream(*args: Any, **kwargs: Any) -> Any:
            for chunk in ["Mock ", "streaming ", "response"]:
                yield {"delta": chunk, "model": "mock-model"}

        times_ms: list[float] = []
        for _ in range(50):
            start = time.perf_counter()
            chunks = []
            async for chunk in _mock_stream():
                chunks.append(chunk)
            elapsed_ms = (time.perf_counter() - start) * 1000.0
            times_ms.append(elapsed_ms)

        stats = calculate_stats(times_ms)
        result = BenchmarkResult(
            name="llm_provider_streaming",
            iterations=50,
            total_ms=sum(times_ms),
            **stats,
        )

        return result


# =============================================================================
# ContextOS Read/Write Latency
# =============================================================================


class TestContextOSLatency:
    """Benchmark ContextOS read/write latency."""

    def test_context_read_latency(
        self,
        latency_thresholds: type[LatencyThresholds],
    ) -> BenchmarkResult:
        """Benchmark context reading operations."""
        context = _create_minimal_context()

        times_ms: list[float] = []
        for _ in range(1000):
            start = time.perf_counter()
            _ = [msg for msg in context if msg.get("role") == "user"]
            elapsed_ms = (time.perf_counter() - start) * 1000.0
            times_ms.append(elapsed_ms)

        stats = calculate_stats(times_ms)
        result = BenchmarkResult(
            name="context_read",
            iterations=1000,
            total_ms=sum(times_ms),
            **stats,
        )

        assert result.p95_ms < latency_thresholds.CONTEXT_OS_READ_P95_MS, (
            f"Context read p95 ({result.p95_ms:.3f}ms) exceeds threshold"
        )

        return result

    def test_context_write_latency(
        self,
        latency_thresholds: type[LatencyThresholds],
    ) -> BenchmarkResult:
        """Benchmark context mutation operations."""
        context = _create_minimal_context()

        times_ms: list[float] = []
        for i in range(1000):
            start = time.perf_counter()
            context.append({"role": "assistant", "content": f"Response {i}"})
            elapsed_ms = (time.perf_counter() - start) * 1000.0
            times_ms.append(elapsed_ms)

        stats = calculate_stats(times_ms)
        result = BenchmarkResult(
            name="context_write",
            iterations=1000,
            total_ms=sum(times_ms),
            **stats,
        )

        assert result.p95_ms < latency_thresholds.CONTEXT_OS_WRITE_P95_MS, (
            f"Context write p95 ({result.p95_ms:.3f}ms) exceeds threshold"
        )

        return result

    def test_context_serialization_latency(
        self,
        latency_thresholds: type[LatencyThresholds],
    ) -> BenchmarkResult:
        """Benchmark context JSON serialization."""
        import json

        context = _create_minimal_context()
        context.extend([{"role": "assistant", "content": f"Response {i}", "metadata": {"turn": i}} for i in range(10)])

        times_ms: list[float] = []
        for _ in range(500):
            start = time.perf_counter()
            json.dumps(context, ensure_ascii=False)
            elapsed_ms = (time.perf_counter() - start) * 1000.0
            times_ms.append(elapsed_ms)

        stats = calculate_stats(times_ms)
        result = BenchmarkResult(
            name="context_serialization",
            iterations=500,
            total_ms=sum(times_ms),
            **stats,
        )

        return result


# =============================================================================
# Integration Benchmark
# =============================================================================


@pytest.mark.asyncio
async def test_full_benchmark_suite() -> None:
    """Run full benchmark suite and save results."""
    results: list[BenchmarkResult] = []

    thresholds = LatencyThresholds

    controller_bench = TestTurnTransactionLatency()
    results.append(controller_bench.test_controller_initialization_latency(thresholds))
    results.append(controller_bench.test_state_machine_transition_latency(thresholds))
    results.append(controller_bench.test_ledger_record_latency(thresholds))

    llm_bench = TestLLMProviderLatency()
    results.append(await llm_bench.test_mock_provider_call_latency(thresholds))

    context_bench = TestContextOSLatency()
    results.append(context_bench.test_context_read_latency(thresholds))
    results.append(context_bench.test_context_write_latency(thresholds))
    results.append(context_bench.test_context_serialization_latency(thresholds))

    suite = BenchmarkSuite(
        suite_name="latency_baseline",
        timestamp=datetime.now(timezone.utc).isoformat(),
        results=results,
        environment={
            "platform": "benchmark",
            "iteration_note": "Warmup + measured iterations per test",
        },
    )

    filepath = save_baseline(suite)
    assert filepath.exists(), f"Baseline file not created: {filepath}"
