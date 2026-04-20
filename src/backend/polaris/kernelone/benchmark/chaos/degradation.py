"""Graceful Degradation Benchmark for Chaos Testing.

This module provides capabilities for measuring system behavior
under degradation conditions including reduced throughput,
increased latency, and partial failures.

Example
-------
    benchmark = GracefulDegradationBenchmark()
    results = await benchmark.run_staged_degradation(
        target_throughput=100.0,
        stages=[1.0, 0.8, 0.5, 0.2],
    )
"""

from __future__ import annotations

import asyncio
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

# ------------------------------------------------------------------
# Enums and Models
# ------------------------------------------------------------------


class DegradationStage(Enum):
    """Stage of system degradation."""

    HEALTHY = "healthy"
    DEGRADED = "degraded"
    CRITICAL = "critical"
    FAILOVER = "failover"
    RECOVERED = "recovered"


@dataclass(frozen=True, kw_only=True)
class DegradationMetrics:
    """Metrics describing system degradation state.

    Attributes:
        success_rate: Ratio of successful requests (0.0 - 1.0).
        average_latency_ms: Average response latency in milliseconds.
        p99_latency_ms: 99th percentile latency in milliseconds.
        timeout_count: Number of timeouts.
        rate_limit_count: Number of rate-limited requests.
        error_distribution: Count of errors by type.
        mttr_seconds: Mean Time To Recovery in seconds.
        stage: Current degradation stage.
    """

    success_rate: float
    average_latency_ms: float
    p99_latency_ms: float
    timeout_count: int
    rate_limit_count: int
    error_distribution: dict[str, int]
    mttr_seconds: float
    stage: DegradationStage = DegradationStage.HEALTHY
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    total_requests: int = 0
    failed_requests: int = 0

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "success_rate": round(self.success_rate, 4),
            "average_latency_ms": round(self.average_latency_ms, 2),
            "p99_latency_ms": round(self.p99_latency_ms, 2),
            "timeout_count": self.timeout_count,
            "rate_limit_count": self.rate_limit_count,
            "error_distribution": dict(self.error_distribution),
            "mttr_seconds": round(self.mttr_seconds, 2),
            "stage": self.stage.value,
            "timestamp": self.timestamp.isoformat(),
            "total_requests": self.total_requests,
            "failed_requests": self.failed_requests,
        }


@dataclass
class DegradationStageConfig:
    """Configuration for a degradation stage."""

    stage: DegradationStage
    throughput_multiplier: float  # 0.0 - 1.0
    latency_multiplier: float  # 1.0 means normal
    error_rate: float  # 0.0 - 1.0
    duration_seconds: float


@dataclass
class RequestResult:
    """Result of a single request."""

    success: bool
    latency_ms: float
    error_type: str | None = None
    error_message: str | None = None
    timestamp: float = field(default_factory=time.time)


# ------------------------------------------------------------------
# Degradation Benchmark
# ------------------------------------------------------------------


class GracefulDegradationBenchmark:
    """Benchmark for measuring graceful degradation behavior.

    This benchmark runs the system under various load conditions
    and measures how well it degrades gracefully.
    """

    __slots__ = (
        "_errors",
        "_in_degraded_state",
        "_last_recovery",
        "_latencies",
        "_recovery_times",
        "_request_func",
    )

    def __init__(
        self,
        request_func: Callable[..., Awaitable[Any]] | None = None,
    ) -> None:
        self._request_func = request_func
        self._latencies: list[float] = []
        self._errors: Counter[str] = Counter()
        self._recovery_times: list[float] = []
        self._last_recovery = time.monotonic()
        self._in_degraded_state = False

    def set_request_func(self, func: Callable[..., Awaitable[Any]]) -> None:
        """Set the request function to benchmark."""
        self._request_func = func

    async def run_staged_degradation(
        self,
        target_throughput: float,
        stages: list[float],
        requests_per_stage: int = 100,
        stage_duration_seconds: float = 10.0,
    ) -> list[DegradationMetrics]:
        """Run benchmark across multiple degradation stages.

        Args:
            target_throughput: Target requests per second.
            stages: List of throughput multipliers (1.0 = full, 0.5 = half).
            requests_per_stage: Minimum requests to run per stage.
            stage_duration_seconds: Maximum duration per stage.

        Returns:
            List of metrics for each stage.
        """
        results = []

        for stage_multiplier in stages:
            self._reset_metrics()

            effective_rate = target_throughput * stage_multiplier
            interval = 1.0 / effective_rate if effective_rate > 0 else 0.0

            # Determine stage based on multiplier
            stage = self._determine_stage(stage_multiplier)

            # Run requests at controlled rate
            start_time = time.monotonic()
            tasks: list[asyncio.Task[RequestResult]] = []

            while time.monotonic() - start_time < stage_duration_seconds or len(tasks) < requests_per_stage:
                if len(tasks) < requests_per_stage:
                    task = asyncio.create_task(self._execute_request(stage_multiplier))
                    tasks.append(task)

                await asyncio.sleep(min(interval, 0.01))

                # Check for completed tasks
                done, pending = await asyncio.wait(tasks, timeout=0, return_when=asyncio.FIRST_COMPLETED)
                for d in done:
                    result = d.result()
                    self._record_result(result)
                    tasks.remove(d)

                # Also wait a bit to collect more results
                if len(pending) < 10:
                    break

            # Wait for remaining tasks
            if tasks:
                remaining = await asyncio.gather(*tasks, return_exceptions=True)
                for r in remaining:
                    if isinstance(r, RequestResult):
                        self._record_result(r)

            # Calculate metrics for this stage
            metrics = self._calculate_metrics(stage)
            results.append(metrics)

        return results

    async def _execute_request(self, error_rate: float) -> RequestResult:
        """Execute a single request and record result."""
        start = time.monotonic()

        try:
            if self._request_func:
                await self._request_func()
                latency_ms = (time.monotonic() - start) * 1000

                # Simulate errors based on degradation level
                import random

                if random.random() < error_rate:
                    raise RuntimeError(f"Simulated degradation error (rate={error_rate})")

                return RequestResult(
                    success=True,
                    latency_ms=latency_ms,
                )
            else:
                # Default: simulate a request
                await asyncio.sleep(0.01)
                latency_ms = (time.monotonic() - start) * 1000
                return RequestResult(success=True, latency_ms=latency_ms)

        except asyncio.TimeoutError:
            return RequestResult(
                success=False,
                latency_ms=(time.monotonic() - start) * 1000,
                error_type="TimeoutError",
                error_message="Request timed out",
            )
        except (RuntimeError, ValueError) as e:
            return RequestResult(
                success=False,
                latency_ms=(time.monotonic() - start) * 1000,
                error_type=type(e).__name__,
                error_message=str(e),
            )

    def _record_result(self, result: RequestResult) -> None:
        """Record a request result."""
        self._latencies.append(result.latency_ms)
        if result.error_type:
            self._errors[result.error_type] += 1

    def _reset_metrics(self) -> None:
        """Reset metrics for new stage."""
        self._latencies.clear()
        self._errors.clear()

    def _determine_stage(self, multiplier: float) -> DegradationStage:
        """Determine degradation stage from throughput multiplier."""
        if multiplier >= 1.0:
            return DegradationStage.HEALTHY
        elif multiplier >= 0.7:
            return DegradationStage.DEGRADED
        elif multiplier >= 0.4:
            return DegradationStage.CRITICAL
        else:
            return DegradationStage.FAILOVER

    def _calculate_metrics(self, stage: DegradationStage) -> DegradationMetrics:
        """Calculate degradation metrics from recorded data."""
        total = len(self._latencies)
        if total == 0:
            return DegradationMetrics(
                success_rate=1.0,
                average_latency_ms=0.0,
                p99_latency_ms=0.0,
                timeout_count=0,
                rate_limit_count=0,
                error_distribution=dict(self._errors),
                mttr_seconds=0.0,
                stage=stage,
            )

        # Calculate statistics
        sorted_latencies = sorted(self._latencies)
        avg_latency = sum(self._latencies) / total
        p99_index = min(int(total * 0.99), total - 1)
        p99_latency = sorted_latencies[p99_index]

        success_count = total - sum(self._errors.values())
        success_rate = success_count / total

        # Calculate MTTR
        mttr = time.monotonic() - self._last_recovery

        return DegradationMetrics(
            success_rate=success_rate,
            average_latency_ms=avg_latency,
            p99_latency_ms=p99_latency,
            timeout_count=self._errors.get("TimeoutError", 0),
            rate_limit_count=self._errors.get("RateLimitExceeded", 0),
            error_distribution=dict(self._errors),
            mttr_seconds=mttr,
            stage=stage,
            total_requests=total,
            failed_requests=sum(self._errors.values()),
        )


# ------------------------------------------------------------------
# Load Test
# ------------------------------------------------------------------


@dataclass
class LoadTestConfig:
    """Configuration for load testing."""

    concurrent_users: int = 10
    requests_per_user: int = 100
    think_time_ms: float = 100.0
    ramp_up_seconds: float = 5.0


@dataclass(frozen=True, kw_only=True)
class LoadTestResult:
    """Result of a load test."""

    total_requests: int
    successful_requests: int
    failed_requests: int
    duration_seconds: float
    throughput_rps: float
    average_latency_ms: float
    p50_latency_ms: float
    p95_latency_ms: float
    p99_latency_ms: float
    error_rate: float

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "total_requests": self.total_requests,
            "successful_requests": self.successful_requests,
            "failed_requests": self.failed_requests,
            "duration_seconds": round(self.duration_seconds, 2),
            "throughput_rps": round(self.throughput_rps, 2),
            "average_latency_ms": round(self.average_latency_ms, 2),
            "p50_latency_ms": round(self.p50_latency_ms, 2),
            "p95_latency_ms": round(self.p95_latency_ms, 2),
            "p99_latency_ms": round(self.p99_latency_ms, 2),
            "error_rate": round(self.error_rate, 4),
        }


class LoadTester:
    """Load testing utility.

    Simulates concurrent users making requests.
    """

    __slots__ = ("_request_func", "_results")

    def __init__(self, request_func: Callable[..., Awaitable[Any]]) -> None:
        self._request_func = request_func
        self._results: list[RequestResult] = []

    async def run(
        self,
        config: LoadTestConfig,
    ) -> LoadTestResult:
        """Run load test.

        Args:
            config: Load test configuration.

        Returns:
            Aggregated load test results.
        """
        self._results.clear()
        start_time = time.monotonic()

        # Create user tasks
        tasks = []
        for user_id in range(config.concurrent_users):
            task = asyncio.create_task(
                self._simulate_user(
                    user_id=user_id,
                    requests=config.requests_per_user,
                    think_time_ms=config.think_time_ms,
                    ramp_up_seconds=config.ramp_up_seconds,
                )
            )
            tasks.append(task)

        # Wait for all users to complete
        await asyncio.gather(*tasks, return_exceptions=True)

        duration = time.monotonic() - start_time
        return self._aggregate_results(duration)

    async def _simulate_user(
        self,
        user_id: int,
        requests: int,
        think_time_ms: float,
        ramp_up_seconds: float,
    ) -> None:
        """Simulate a single user making requests."""
        # Ramp up delay
        await asyncio.sleep((user_id / requests) * ramp_up_seconds)

        for _ in range(requests):
            result = await self._execute_request()
            self._results.append(result)

            # Think time between requests
            await asyncio.sleep(think_time_ms / 1000.0)

    async def _execute_request(self) -> RequestResult:
        """Execute a request and record result."""
        start = time.monotonic()

        try:
            await self._request_func()
            return RequestResult(
                success=True,
                latency_ms=(time.monotonic() - start) * 1000,
            )
        except (RuntimeError, ValueError) as e:
            return RequestResult(
                success=False,
                latency_ms=(time.monotonic() - start) * 1000,
                error_type=type(e).__name__,
                error_message=str(e),
            )

    def _aggregate_results(self, duration: float) -> LoadTestResult:
        """Aggregate results into LoadTestResult."""
        total = len(self._results)
        if total == 0:
            return LoadTestResult(
                total_requests=0,
                successful_requests=0,
                failed_requests=0,
                duration_seconds=duration,
                throughput_rps=0.0,
                average_latency_ms=0.0,
                p50_latency_ms=0.0,
                p95_latency_ms=0.0,
                p99_latency_ms=0.0,
                error_rate=0.0,
            )

        successful = sum(1 for r in self._results if r.success)
        failed = total - successful
        latencies = sorted(r.latency_ms for r in self._results)

        def percentile(p: float) -> float:
            idx = min(int(total * p), total - 1)
            return latencies[idx]

        return LoadTestResult(
            total_requests=total,
            successful_requests=successful,
            failed_requests=failed,
            duration_seconds=duration,
            throughput_rps=total / duration,
            average_latency_ms=sum(latencies) / total,
            p50_latency_ms=percentile(0.50),
            p95_latency_ms=percentile(0.95),
            p99_latency_ms=percentile(0.99),
            error_rate=failed / total,
        )
