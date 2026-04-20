"""Metrics Module - 可观测性指标导出

提供 Prometheus 风格的指标导出，用于监控：
- 缓存命中率 (L1/L2/L3)
- LLM 调用延迟
- 质量评分
- 执行统计

使用 KernelOne Trace 功能进行分布式追踪。
"""

from __future__ import annotations

import logging
import random
import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

# ============================================================================
# Metrics Definitions (Prometheus-style, no external dependencies)
# ============================================================================


class Counter:
    """Simple thread-safe counter metric."""

    def __init__(self, name: str, description: str, labelnames: tuple[str, ...] | None = None) -> None:
        self._name = name
        self._description = description
        self._labelnames = labelnames or ()
        self._values: dict[tuple, float] = {}
        self._lock = threading.RLock()

    def labels(self, **label_values: str) -> _LabeledCounter:
        return _LabeledCounter(self, tuple(sorted(label_values.items())))

    def inc(self, value: float = 1, label_key: tuple | None = None) -> None:
        with self._lock:
            key = label_key or ()
            self._values[key] = self._values.get(key, 0) + value

    def get(self, label_key: tuple | None = None) -> float:
        with self._lock:
            return self._values.get(label_key or (), 0)

    def collect(self) -> dict[str, Any]:
        """Collect all metrics in Prometheus format."""
        with self._lock:
            return {
                "name": self._name,
                "description": self._description,
                "type": "counter",
                "values": dict(self._values),
            }


class _LabeledCounter:
    """Labeled counter for chaining."""

    __slots__ = ("_label_key", "_parent")

    def __init__(self, parent: Counter, label_key: tuple) -> None:
        self._parent = parent
        self._label_key = label_key

    def inc(self, value: float = 1) -> None:
        self._parent.inc(value, self._label_key)

    def get(self) -> float:
        """Get the counter value for this label combination."""
        return self._parent.get(self._label_key)


class Histogram:
    """Simple thread-safe histogram metric for latency tracking.

    Note:
        使用固定窗口采样防止内存无限增长。
        默认保留最近 10000 条记录。
    """

    # 固定窗口大小，防止内存无限增长
    _MAX_SAMPLES = 10000

    def __init__(
        self,
        name: str,
        description: str,
        buckets: tuple[float, ...] | None = None,
        max_samples: int | None = None,
    ) -> None:
        self._name = name
        self._description = description
        self._buckets = buckets or (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0)
        self._values: list[float] = []
        self._sum: float = 0
        self._count: int = 0
        self._lock = threading.RLock()
        self._max_samples = max_samples or self._MAX_SAMPLES

    def observe(self, value: float) -> None:
        """Record an observation with fixed-window sampling.

        Note:
            当样本数超过 max_samples 时，使用 reservoir sampling 策略，
            保持内存使用可控。
        """
        with self._lock:
            n = len(self._values)
            if n < self._max_samples:
                # 窗口未满，正常添加
                self._values.append(value)
            else:
                # Reservoir sampling: 随机替换
                idx = random.randint(0, self._count)
                if idx < self._max_samples:
                    # 替换（但不更新 sum，因为是随机替换）
                    # 为了简化，这里使用滑动窗口策略：移除最旧的
                    self._values.pop(0)
                    self._values.append(value)
            self._sum += value
            self._count += 1

    def get_stats(self) -> dict[str, Any]:
        """Get histogram statistics."""
        with self._lock:
            if not self._values:
                return {
                    "count": 0,
                    "sum": 0,
                    "avg": 0,
                    "min": 0,
                    "max": 0,
                    "p50": 0,
                    "p95": 0,
                    "p99": 0,
                }
            sorted_values = sorted(self._values)
            n = len(sorted_values)
            return {
                "count": self._count,
                "sum": self._sum,
                "avg": self._sum / self._count,
                "min": sorted_values[0],
                "max": sorted_values[-1],
                "p50": sorted_values[int(n * 0.5)],
                "p95": sorted_values[int(n * 0.95)] if n > 1 else sorted_values[0],
                "p99": sorted_values[int(n * 0.99)] if n > 1 else sorted_values[0],
            }

    def collect(self) -> dict[str, Any]:
        """Collect histogram in Prometheus format."""
        with self._lock:
            return {
                "name": self._name,
                "description": self._description,
                "type": "histogram",
                "buckets": self._buckets,
                **self.get_stats(),
            }


class Gauge:
    """Simple thread-safe gauge metric."""

    def __init__(self, name: str, description: str) -> None:
        self._name = name
        self._description = description
        self._value: float = 0
        self._lock = threading.RLock()

    def set(self, value: float) -> None:
        with self._lock:
            self._value = value

    def get(self) -> float:
        with self._lock:
            return self._value

    def inc(self, value: float = 1) -> None:
        with self._lock:
            self._value += value

    def dec(self, value: float = 1) -> None:
        with self._lock:
            self._value -= value

    def collect(self) -> dict[str, Any]:
        with self._lock:
            return {
                "name": self._name,
                "description": self._description,
                "type": "gauge",
                "value": self._value,
            }


# ============================================================================
# Pre-defined Metrics
# ============================================================================

# Cache hit counters (by level)
CACHE_HIT = Counter(
    "role_kernel_cache_hit",
    "Cache hit count by level",
    labelnames=("level",),
)

# Cache miss counters (by level)
CACHE_MISS = Counter(
    "role_kernel_cache_miss",
    "Cache miss count by level",
    labelnames=("level",),
)

# LLM latency histogram (in seconds)
LLM_LATENCY = Histogram(
    "role_kernel_llm_latency_seconds",
    "LLM call latency in seconds",
    buckets=(0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0, 120.0),
)

# Quality score gauge
QUALITY_SCORE = Gauge(
    "role_kernel_quality_score",
    "Quality score from last validation",
)

# Active roles gauge
ACTIVE_ROLES = Gauge(
    "role_kernel_active_roles",
    "Number of active roles",
)

# Execution counters
EXECUTION_COUNT = Counter(
    "role_kernel_execution_total",
    "Total execution count",
    labelnames=("role", "status"),
)

# Retry counter
RETRY_COUNT = Counter(
    "role_kernel_retry_total",
    "Total retry count",
    labelnames=("role", "reason"),
)

# Phase 7 transaction monitoring metrics
TRANSACTION_KERNEL_VIOLATION_COUNT = Counter(
    "transaction_kernel_violation_count_total",
    "Total transaction kernel violation count",
)

TURN_SINGLE_BATCH_RATIO = Gauge(
    "turn_single_batch_ratio",
    "Ratio of turns with a single tool batch (1.0 if <=1 batch, else 0.0)",
)

WORKFLOW_HANDOFF_RATE = Gauge(
    "workflow_handoff_rate",
    "Rate of turns handed off to workflow (1.0 if handoff, else 0.0)",
)

KERNEL_GUARD_ASSERT_FAIL_RATE = Gauge(
    "kernel_guard_assert_fail_rate",
    "Rate of kernel guard assertion failures",
)

SPECULATIVE_HIT_RATE = Gauge(
    "speculative_hit_rate",
    "Rate of speculative tool call hits",
)

SPECULATIVE_FALSE_POSITIVE_RATE = Gauge(
    "speculative_false_positive_rate",
    "Rate of speculative tool call false positives",
)


# ============================================================================
# Metrics Collector (singleton)
# ============================================================================


@dataclass
class MetricsSnapshot:
    """Snapshot of all metrics for export."""

    timestamp: float = field(default_factory=time.time)
    cache_stats: dict[str, Any] = field(default_factory=dict)
    llm_stats: dict[str, Any] = field(default_factory=dict)
    quality_stats: dict[str, Any] = field(default_factory=dict)
    execution_stats: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "cache": self.cache_stats,
            "llm": self.llm_stats,
            "quality": self.quality_stats,
            "execution": self.execution_stats,
        }


class MetricsCollector:
    """Singleton metrics collector with Prometheus-compatible export."""

    _instance: MetricsCollector | None = None
    _lock = threading.Lock()
    _initialized: bool = False

    def __new__(cls) -> MetricsCollector:
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                object.__setattr__(cls._instance, "_initialized", False)
            return cls._instance

    def __init__(self) -> None:
        if self._initialized:
            return
        self._initialized = True
        self._last_quality_score: float = 0
        self._last_llm_latency: float = 0
        self._start_time = time.time()
        self._collect_lock = threading.RLock()

    @classmethod
    def get_instance(cls) -> MetricsCollector:
        """Get singleton instance."""
        return cls()

    @classmethod
    def reset_for_testing(cls) -> None:
        """Reset singleton state for testing isolation."""
        cls._instance = None

    def record_cache_hit(self, level: str) -> None:
        """Record cache hit for given level."""
        CACHE_HIT.labels(level=level).inc()

    def record_cache_miss(self, level: str) -> None:
        """Record cache miss for given level."""
        CACHE_MISS.labels(level=level).inc()

    def record_llm_latency(self, latency_seconds: float) -> None:
        """Record LLM call latency."""
        self._last_llm_latency = latency_seconds
        LLM_LATENCY.observe(latency_seconds)

    def record_quality_score(self, score: float) -> None:
        """Record quality score."""
        self._last_quality_score = score
        QUALITY_SCORE.set(score)

    def record_execution(self, role: str, status: str) -> None:
        """Record execution completion."""
        EXECUTION_COUNT.labels(role=role, status=status).inc()

    def record_retry(self, role: str, reason: str) -> None:
        """Record retry event."""
        RETRY_COUNT.labels(role=role, reason=reason).inc()

    def record_transaction_metrics(self, metrics: Mapping[str, float]) -> None:
        """Record Phase 7 transaction monitoring metrics."""
        violation_count = float(metrics.get("transaction_kernel.violation_count", 0))
        TRANSACTION_KERNEL_VIOLATION_COUNT.inc(violation_count)
        TURN_SINGLE_BATCH_RATIO.set(float(metrics.get("turn.single_batch_ratio", 0)))
        WORKFLOW_HANDOFF_RATE.set(float(metrics.get("workflow.handoff_rate", 0)))
        KERNEL_GUARD_ASSERT_FAIL_RATE.set(float(metrics.get("kernel_guard.assert_fail_rate", 0)))
        SPECULATIVE_HIT_RATE.set(float(metrics.get("speculative.hit_rate", 0)))
        SPECULATIVE_FALSE_POSITIVE_RATE.set(float(metrics.get("speculative.false_positive_rate", 0)))

    def get_prometheus_format(self) -> str:
        """Export all collected metrics in Prometheus text format."""
        lines: list[str] = []
        for metric in self.collect_all():
            name = metric["name"]
            description = metric["description"]
            metric_type = metric["type"]
            lines.append(f"# HELP {name} {description}")
            lines.append(f"# TYPE {name} {metric_type}")
            if metric_type == "counter":
                for label_key, value in metric.get("values", {}).items():
                    if label_key:
                        label_str = ",".join(f'{k}="{v}"' for k, v in label_key)
                        lines.append(f"{name}{{{label_str}}} {value}")
                    else:
                        lines.append(f"{name} {value}")
            elif metric_type == "gauge":
                lines.append(f"{name} {metric.get('value', 0)}")
            elif metric_type == "histogram":
                # Current histogram implementation only provides summary stats,
                # not per-bucket counts. Export count and sum.
                count = metric.get("count", 0)
                sum_val = metric.get("sum", 0)
                lines.append(f"{name}_count {count}")
                lines.append(f"{name}_sum {sum_val}")
            lines.append("")
        return "\n".join(lines)

    def get_snapshot(self) -> MetricsSnapshot:
        """Get current metrics snapshot."""
        with self._collect_lock:
            return MetricsSnapshot(
                timestamp=time.time(),
                cache_stats={
                    "l1_hits": CACHE_HIT.labels(level="l1").get(),
                    "l2_hits": CACHE_HIT.labels(level="l2").get(),
                    "l3_hits": CACHE_HIT.labels(level="l3").get(),
                    "l1_misses": CACHE_MISS.labels(level="l1").get(),
                    "l2_misses": CACHE_MISS.labels(level="l2").get(),
                    "l3_misses": CACHE_MISS.labels(level="l3").get(),
                },
                llm_stats={
                    "last_latency": self._last_llm_latency,
                    "latency_stats": LLM_LATENCY.get_stats(),
                },
                quality_stats={
                    "last_score": self._last_quality_score,
                },
                execution_stats={
                    "uptime_seconds": time.time() - self._start_time,
                },
            )

    def collect_all(self) -> list[dict[str, Any]]:
        """Collect all metrics in Prometheus format."""
        return [
            CACHE_HIT.collect(),
            CACHE_MISS.collect(),
            LLM_LATENCY.collect(),
            QUALITY_SCORE.collect(),
            ACTIVE_ROLES.collect(),
            EXECUTION_COUNT.collect(),
            RETRY_COUNT.collect(),
            # Phase 7 transaction metrics
            TRANSACTION_KERNEL_VIOLATION_COUNT.collect(),
            TURN_SINGLE_BATCH_RATIO.collect(),
            WORKFLOW_HANDOFF_RATE.collect(),
            KERNEL_GUARD_ASSERT_FAIL_RATE.collect(),
            SPECULATIVE_HIT_RATE.collect(),
            SPECULATIVE_FALSE_POSITIVE_RATE.collect(),
        ]

    @staticmethod
    def reset() -> None:
        """Reset all metrics (for testing)."""
        global CACHE_HIT, CACHE_MISS, LLM_LATENCY, QUALITY_SCORE
        global ACTIVE_ROLES, EXECUTION_COUNT, RETRY_COUNT
        global TRANSACTION_KERNEL_VIOLATION_COUNT, TURN_SINGLE_BATCH_RATIO
        global WORKFLOW_HANDOFF_RATE, KERNEL_GUARD_ASSERT_FAIL_RATE
        global SPECULATIVE_HIT_RATE, SPECULATIVE_FALSE_POSITIVE_RATE

        # Recreate metrics to reset state
        CACHE_HIT = Counter(
            "role_kernel_cache_hit",
            "Cache hit count by level",
            labelnames=("level",),
        )
        CACHE_MISS = Counter(
            "role_kernel_cache_miss",
            "Cache miss count by level",
            labelnames=("level",),
        )
        LLM_LATENCY = Histogram(
            "role_kernel_llm_latency_seconds",
            "LLM call latency in seconds",
            buckets=(0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0, 120.0),
        )
        QUALITY_SCORE = Gauge(
            "role_kernel_quality_score",
            "Quality score from last validation",
        )
        ACTIVE_ROLES = Gauge(
            "role_kernel_active_roles",
            "Number of active roles",
        )
        EXECUTION_COUNT = Counter(
            "role_kernel_execution_total",
            "Total execution count",
            labelnames=("role", "status"),
        )
        RETRY_COUNT = Counter(
            "role_kernel_retry_total",
            "Total retry count",
            labelnames=("role", "reason"),
        )
        TRANSACTION_KERNEL_VIOLATION_COUNT = Counter(
            "transaction_kernel_violation_count_total",
            "Total transaction kernel violation count",
        )
        TURN_SINGLE_BATCH_RATIO = Gauge(
            "turn_single_batch_ratio",
            "Ratio of turns with a single tool batch (1.0 if <=1 batch, else 0.0)",
        )
        WORKFLOW_HANDOFF_RATE = Gauge(
            "workflow_handoff_rate",
            "Rate of turns handed off to workflow (1.0 if handoff, else 0.0)",
        )
        KERNEL_GUARD_ASSERT_FAIL_RATE = Gauge(
            "kernel_guard_assert_fail_rate",
            "Rate of kernel guard assertion failures",
        )
        SPECULATIVE_HIT_RATE = Gauge(
            "speculative_hit_rate",
            "Rate of speculative tool call hits",
        )
        SPECULATIVE_FALSE_POSITIVE_RATE = Gauge(
            "speculative_false_positive_rate",
            "Rate of speculative tool call false positives",
        )


def get_metrics_collector() -> MetricsCollector:
    """Get the singleton metrics collector."""
    return MetricsCollector.get_instance()


def record_cache_stats(stats: dict[str, Any]) -> None:
    """Convenience function to record cache stats from PromptBuilder."""
    for level in ("l1", "l2", "l3"):
        hits = stats.get(f"{level}_hits", 0)
        misses = stats.get(f"{level}_misses", 0)
        # Increment by actual count (not just presence check)
        if hits > 0:
            CACHE_HIT.labels(level=level).inc(hits)
        if misses > 0:
            CACHE_MISS.labels(level=level).inc(misses)


# ============================================================================
# Dead Loop Prevention Metrics (ADR-0068)
# ============================================================================

# Circuit breaker triggers by type
CIRCUIT_BREAKER_TRIGGERS = Counter(
    "role_kernel_circuit_breaker_total",
    "Circuit breaker triggers by type",
    labelnames=("breaker_type",),
)

# Intent switches detected
INTENT_SWITCHES = Counter(
    "role_kernel_intent_switches_total",
    "Intent switch detections (view->write transitions)",
)

# Thinking tag violations
THINKING_VIOLATIONS = Counter(
    "role_kernel_thinking_violations_total",
    "Thinking tag violations by type",
    labelnames=("violation_type",),
)

# Emergency context compactions
EMERGENCY_COMPACTIONS = Counter(
    "role_kernel_emergency_compactions_total",
    "Emergency context compactions triggered by event count",
)

# Read-only streak length histogram
READ_ONLY_STREAK_HISTOGRAM = Histogram(
    "role_kernel_read_only_streak_length",
    "Length of read-only streaks before reset or circuit breaker",
    buckets=(1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 15, 20),
)

# Tool call counts by category
TOOL_CALLS_BY_CATEGORY = Counter(
    "role_kernel_tool_calls_category_total",
    "Tool calls by category (read/write)",
    labelnames=("category",),
)


class DeadLoopMetricsCollector:
    """Convenience collector for dead loop prevention metrics.

    Provides type-safe methods for recording ADR-0068 metrics.
    """

    def __init__(self) -> None:
        self._recent_breakers: list[dict[str, Any]] = []
        self._max_records = 100

    def record_circuit_breaker(
        self,
        breaker_type: str,
        tool_name: str = "",
        details: dict[str, str] | None = None,
    ) -> None:
        """Record a circuit breaker trigger.

        Args:
            breaker_type: same_tool | cross_tool | stagnation | thinking
            tool_name: Tool involved
            details: Additional context
        """
        CIRCUIT_BREAKER_TRIGGERS.labels(breaker_type=breaker_type).inc()

        # Keep recent records for debugging
        record = {
            "breaker_type": breaker_type,
            "tool_name": tool_name,
            "details": details or {},
            "timestamp": time.time(),
        }
        self._recent_breakers.append(record)
        if len(self._recent_breakers) > self._max_records:
            self._recent_breakers = self._recent_breakers[-self._max_records :]

    def record_intent_switch(self, old_intent: str, new_intent: str) -> None:
        """Record an intent switch detection."""
        INTENT_SWITCHES.inc()
        logger.debug(f"[METRICS] Intent switch: '{old_intent[:30]}...' -> '{new_intent[:30]}...'")

    def record_thinking_violation(self, violation_type: str) -> None:
        """Record a thinking tag violation."""
        THINKING_VIOLATIONS.labels(violation_type=violation_type).inc()

    def record_emergency_compaction(self, event_count: int) -> None:
        """Record an emergency context compaction."""
        EMERGENCY_COMPACTIONS.inc()
        logger.warning(f"[METRICS] Emergency compaction triggered by {event_count} events")

    def record_read_only_streak(self, streak_length: int) -> None:
        """Record the final length of a read-only streak."""
        READ_ONLY_STREAK_HISTOGRAM.observe(streak_length)

    def record_tool_call(self, is_read_only: bool) -> None:
        """Record a tool call by category."""
        category = "read" if is_read_only else "write"
        TOOL_CALLS_BY_CATEGORY.labels(category=category).inc()

    def get_recent_breakers(self, limit: int = 10) -> list[dict[str, Any]]:
        """Get recent circuit breaker records for debugging."""
        return self._recent_breakers[-limit:]


# Global dead loop metrics instance
_dead_loop_metrics: DeadLoopMetricsCollector | None = None


def get_dead_loop_metrics() -> DeadLoopMetricsCollector:
    """Get the global dead loop metrics collector."""
    global _dead_loop_metrics
    if _dead_loop_metrics is None:
        _dead_loop_metrics = DeadLoopMetricsCollector()
    return _dead_loop_metrics


def reset_dead_loop_metrics() -> None:
    """Reset dead loop metrics (for testing)."""
    global _dead_loop_metrics, CIRCUIT_BREAKER_TRIGGERS, INTENT_SWITCHES
    global THINKING_VIOLATIONS, EMERGENCY_COMPACTIONS
    global READ_ONLY_STREAK_HISTOGRAM, TOOL_CALLS_BY_CATEGORY

    _dead_loop_metrics = None

    # Recreate metrics
    CIRCUIT_BREAKER_TRIGGERS = Counter(
        "role_kernel_circuit_breaker_total",
        "Circuit breaker triggers by type",
        labelnames=("breaker_type",),
    )
    INTENT_SWITCHES = Counter(
        "role_kernel_intent_switches_total",
        "Intent switch detections",
    )
    THINKING_VIOLATIONS = Counter(
        "role_kernel_thinking_violations_total",
        "Thinking tag violations by type",
        labelnames=("violation_type",),
    )
    EMERGENCY_COMPACTIONS = Counter(
        "role_kernel_emergency_compactions_total",
        "Emergency context compactions",
    )
    READ_ONLY_STREAK_HISTOGRAM = Histogram(
        "role_kernel_read_only_streak_length",
        "Length of read-only streaks",
        buckets=(1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 15, 20),
    )
    TOOL_CALLS_BY_CATEGORY = Counter(
        "role_kernel_tool_calls_category_total",
        "Tool calls by category",
        labelnames=("category",),
    )


__all__ = [
    # Dead loop prevention exports (ADR-0068)
    "CIRCUIT_BREAKER_TRIGGERS",
    "EMERGENCY_COMPACTIONS",
    "INTENT_SWITCHES",
    # Phase 7 transaction metrics
    "KERNEL_GUARD_ASSERT_FAIL_RATE",
    "READ_ONLY_STREAK_HISTOGRAM",
    "SPECULATIVE_FALSE_POSITIVE_RATE",
    "SPECULATIVE_HIT_RATE",
    "THINKING_VIOLATIONS",
    "TOOL_CALLS_BY_CATEGORY",
    "TRANSACTION_KERNEL_VIOLATION_COUNT",
    "TURN_SINGLE_BATCH_RATIO",
    "WORKFLOW_HANDOFF_RATE",
    # Original exports
    "Counter",
    "DeadLoopMetricsCollector",
    "Gauge",
    "Histogram",
    "MetricsCollector",
    "MetricsSnapshot",
    "get_dead_loop_metrics",
    "get_metrics_collector",
    "record_cache_stats",
    "reset_dead_loop_metrics",
]
