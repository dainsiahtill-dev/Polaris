"""Phoenix resilience benchmark executors (TC-PHX-001..005).

Circuit-breaker transitions, multi-provider fallback, backoff jitter,
retry-policy parity, and dead-letter-queue throughput.
"""

from __future__ import annotations

import asyncio
import contextlib
import math
import time

from polaris.kernelone.benchmark.holographic.stats import _now_iso, _perf_ms, _seed_random
from polaris.kernelone.benchmark.holographic_models import HolographicCase
from polaris.kernelone.benchmark.holographic_stats import ks_uniform_statistic, summarize_samples
from polaris.kernelone.llm.engine.resilience import (
    CircuitBreaker,
    CircuitBreakerConfig,
    MultiProviderFallbackManager,
    ProviderEndpoint,
    calculate_backoff_with_jitter,
)
from polaris.kernelone.resilience.retry_policy import RetryPolicy, compute_delay
from polaris.kernelone.workflow.dlq import DeadLetterItem, InMemoryDeadLetterQueue


async def _exec_tc_phx_001(case: HolographicCase) -> dict[str, float]:
    _seed_random()
    chain_samples_us: list[float] = []
    open_fast_fail_us: list[float] = []
    iterations = max(100, min(case.min_samples, 1000))
    config = CircuitBreakerConfig(
        failure_threshold=5,
        recovery_timeout=0.0001,
        half_open_max_calls=1,
        success_threshold=1,
    )

    async def _ok_call() -> str:
        return "ok"

    async def _fail_call() -> str:
        raise RuntimeError("injected failure")

    for _ in range(iterations):
        breaker = CircuitBreaker(name="phx", config=config)
        chain_start = time.perf_counter_ns()
        for _attempt in range(config.failure_threshold):
            with contextlib.suppress(RuntimeError):
                await breaker.call(_fail_call)
        open_start = time.perf_counter_ns()
        with contextlib.suppress(Exception):
            await breaker.call(_ok_call)
        open_fast_fail_us.append((time.perf_counter_ns() - open_start) / 1000.0)
        await asyncio.sleep(config.recovery_timeout + 0.0001)
        await breaker.call(_ok_call)
        chain_samples_us.append((time.perf_counter_ns() - chain_start) / 1000.0)

    chain_stats = summarize_samples(chain_samples_us, warmup_rounds=case.warmup_rounds)
    open_stats = summarize_samples(open_fast_fail_us, warmup_rounds=case.warmup_rounds)
    return {
        "transition_p50_us": chain_stats.p50,
        "transition_p90_us": chain_stats.p90,
        "transition_p99_us": chain_stats.p99,
        "open_fast_fail_p50_us": open_stats.p50,
        "open_fast_fail_p90_us": open_stats.p90,
        "open_fast_fail_p99_us": open_stats.p99,
    }


async def _exec_tc_phx_002(case: HolographicCase) -> dict[str, float]:
    fallback_samples_ms: list[float] = []
    baseline_samples_ms: list[float] = []
    iterations = max(200, min(case.min_samples, 500))

    async def provider_a_rate_limited() -> str:
        raise RuntimeError("HTTP 429 rate limit")

    async def provider_a_success() -> str:
        await asyncio.sleep(0.005)
        return "provider-a"

    async def provider_b_success() -> str:
        await asyncio.sleep(0.005)
        return "provider-b"

    fallback_manager = MultiProviderFallbackManager(
        [
            ProviderEndpoint(name="provider_a", invoke=provider_a_rate_limited),
            ProviderEndpoint(name="provider_b", invoke=provider_b_success),
        ]
    )
    baseline_manager = MultiProviderFallbackManager(
        [
            ProviderEndpoint(name="provider_a", invoke=provider_a_success),
            ProviderEndpoint(name="provider_b", invoke=provider_b_success),
        ]
    )

    for _ in range(iterations):
        started = time.perf_counter_ns()
        result = await fallback_manager.invoke()
        _ = result.provider
        fallback_samples_ms.append(_perf_ms(started))

        started = time.perf_counter_ns()
        baseline = await baseline_manager.invoke()
        _ = baseline.provider
        baseline_samples_ms.append(_perf_ms(started))

    fallback_stats = summarize_samples(fallback_samples_ms, warmup_rounds=case.warmup_rounds)
    baseline_stats = summarize_samples(baseline_samples_ms, warmup_rounds=case.warmup_rounds)
    overhead_p99_percent = (
        ((fallback_stats.p99 - baseline_stats.p99) / baseline_stats.p99) * 100.0 if baseline_stats.p99 > 0 else 0.0
    )
    return {
        "fallback_p50_ms": fallback_stats.p50,
        "fallback_p90_ms": fallback_stats.p90,
        "fallback_p99_ms": fallback_stats.p99,
        "baseline_p99_ms": baseline_stats.p99,
        "overhead_p99_percent": overhead_p99_percent,
    }


async def _exec_tc_phx_003(case: HolographicCase) -> dict[str, float]:
    _seed_random()
    samples = [
        calculate_backoff_with_jitter(
            attempt=4,
            base_delay=1.0,
            max_delay=60.0,
            jitter_percent=0.1,
        )
        for _ in range(max(200, case.min_samples))
    ]
    stats = summarize_samples(samples, warmup_rounds=case.warmup_rounds)
    return {
        "delay_mean_s": stats.mean,
        "delay_std_s": stats.std_dev,
        "cv": stats.coefficient_of_variation,
        "ks_stat": ks_uniform_statistic(samples),
    }


async def _exec_tc_phx_004(case: HolographicCase) -> dict[str, float]:
    combos = 0
    diffs_ms: list[float] = []
    repeats = max(1, case.min_samples // 28)
    for base_delay in (0.1, 0.5, 1.0, 2.0):
        policy = RetryPolicy(base_delay_seconds=base_delay)
        for attempt in range(1, 8):
            for _ in range(repeats):
                left = calculate_backoff_with_jitter(
                    attempt=attempt,
                    base_delay=base_delay,
                    max_delay=1_000_000.0,
                    jitter_percent=0.0,
                )
                right = compute_delay(policy, attempt)
                combos += 1
                diffs_ms.append(abs(left - right) * 1000.0)
    diff_count = sum(1 for value in diffs_ms if not math.isclose(value, 0.0, abs_tol=1e-9))
    return {
        "combos": float(combos),
        "diff_rate_percent": (diff_count / combos) * 100.0 if combos else 0.0,
        "max_abs_diff_ms": max(diffs_ms) if diffs_ms else 0.0,
    }


def _build_dlq_item(index: int) -> DeadLetterItem:
    now = _now_iso()
    return DeadLetterItem(
        task_id=f"task-{index}",
        workflow_id="wf-bench",
        handler_name="handler",
        input_payload={"i": index},
        error="failure",
        failed_at=now,
        dlq_at=now,
        attempt=3,
        max_attempts=3,
    )


async def _exec_tc_phx_005(case: HolographicCase) -> dict[str, float]:
    queue = InMemoryDeadLetterQueue(maxsize=50000)
    prefill = 1000
    workers = 20
    operations_per_worker = max(100, case.min_samples)
    for index in range(prefill):
        await queue.enqueue(_build_dlq_item(index))

    enqueue_latencies_ms: list[float] = []
    dequeue_latencies_ms: list[float] = []
    start_ns = time.perf_counter_ns()

    async def _worker(worker_id: int) -> None:
        for offset in range(operations_per_worker):
            item = _build_dlq_item(worker_id * operations_per_worker + offset)
            begin = time.perf_counter_ns()
            await queue.enqueue(item)
            enqueue_latencies_ms.append(_perf_ms(begin))

            begin = time.perf_counter_ns()
            _ = await queue.dequeue(timeout=0.0)
            dequeue_latencies_ms.append(_perf_ms(begin))

    await asyncio.gather(*(_worker(worker_id) for worker_id in range(workers)))
    total_ms = _perf_ms(start_ns)
    enqueue_stats = summarize_samples(enqueue_latencies_ms, warmup_rounds=case.warmup_rounds)
    dequeue_stats = summarize_samples(dequeue_latencies_ms, warmup_rounds=case.warmup_rounds)
    total_ops = workers * operations_per_worker
    ops_s = (total_ops / (total_ms / 1000.0)) if total_ms > 0 else 0.0
    return {
        "enqueue_p50_ms": enqueue_stats.p50,
        "enqueue_p99_ms": enqueue_stats.p99,
        "dequeue_p50_ms": dequeue_stats.p50,
        "dequeue_p99_ms": dequeue_stats.p99,
        "enqueue_ops_s": ops_s,
        "dequeue_ops_s": ops_s,
    }
