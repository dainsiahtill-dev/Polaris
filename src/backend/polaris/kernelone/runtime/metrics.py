"""KernelOne runtime metrics collection.

Provides Prometheus-compatible metrics for execution monitoring.

Example:
    from polaris.kernelone.runtime.metrics import get_metrics, ExecutionMetrics

    metrics = get_metrics()
    metrics.record_start("subprocess")
    metrics.record_end("subprocess", "success", 1.5)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Final

# =============================================================================
# Metric constants
# =============================================================================

EXECUTION_LANES: Final[tuple[str, ...]] = (
    "async_task",
    "blocking_io",
    "subprocess",
)
"""Unified execution lane identifiers."""

EXECUTION_STATUSES: Final[tuple[str, ...]] = (
    "queued",
    "running",
    "success",
    "failed",
    "timed_out",
    "cancelled",
)
"""Valid execution status values for metrics."""


# =============================================================================
# Metrics collector
# =============================================================================


@dataclass
class ExecutionMetrics:
    """Execution metrics collector.

    Aggregates execution-related metrics for Prometheus export and
    health monitoring.

    Attributes:
        active_executions: Current count of active executions per lane.
        completed_count: Total completed executions per status.
        total_duration: Cumulative execution duration in seconds per lane.
        messages_dropped: Count of dropped messages.
        processes_killed: Count of forcefully killed processes.
        states_retained: Current number of retained state objects.
        states_active: Current number of active (non-terminal) states.
    """

    active_executions: dict[str, int] = field(default_factory=lambda: dict.fromkeys(EXECUTION_LANES, 0))
    completed_count: dict[str, int] = field(default_factory=lambda: dict.fromkeys(EXECUTION_STATUSES, 0))
    total_duration: dict[str, float] = field(default_factory=lambda: dict.fromkeys(EXECUTION_LANES, 0.0))
    messages_dropped: int = 0
    processes_killed: int = 0
    states_retained: int = 0
    states_active: int = 0

    def record_start(self, lane: str) -> None:
        """Record execution start.

        Args:
            lane: The execution lane identifier.
        """
        if lane in self.active_executions:
            self.active_executions[lane] += 1
        else:
            self.active_executions[lane] = 1

    def record_end(
        self,
        lane: str,
        status: str,
        duration: float,
    ) -> None:
        """Record execution completion.

        Args:
            lane: The execution lane identifier.
            status: The final execution status.
            duration: Execution duration in seconds.
        """
        if lane in self.active_executions:
            self.active_executions[lane] = max(0, self.active_executions[lane] - 1)

        if status in self.completed_count:
            self.completed_count[status] += 1
        else:
            self.completed_count[status] = 1

        if lane in self.total_duration:
            self.total_duration[lane] += duration
        else:
            self.total_duration[lane] = duration

    def record_message_drop(self) -> None:
        """Record a dropped message."""
        self.messages_dropped += 1

    def record_process_kill(self) -> None:
        """Record a forcefully killed process."""
        self.processes_killed += 1

    def update_states(self, total: int, active: int) -> None:
        """Update state retention statistics.

        Args:
            total: Total number of retained states.
            active: Number of active (non-terminal) states.
        """
        self.states_retained = total
        self.states_active = active

    def to_prometheus_text(self) -> str:
        """Generate Prometheus text format metrics output.

        Returns:
            Multi-line string in Prometheus text exposition format.
        """
        lines: list[str] = []

        # Active executions gauge
        lines.append("# HELP kernelone_execution_active_current Current number of active executions")
        lines.append("# TYPE kernelone_execution_active_current gauge")
        for lane, count in self.active_executions.items():
            lines.append(f'kernelone_execution_active_current{{lane="{lane}"}} {count}')

        # Completed executions counter
        lines.append("# HELP kernelone_execution_completed_total Total number of completed executions")
        lines.append("# TYPE kernelone_execution_completed_total counter")
        for status, count in self.completed_count.items():
            lines.append(f'kernelone_execution_completed_total{{status="{status}"}} {count}')

        # Total execution time
        lines.append("# HELP kernelone_execution_duration_seconds_total Total execution time in seconds")
        lines.append("# TYPE kernelone_execution_duration_seconds_total counter")
        for lane, duration in self.total_duration.items():
            lines.append(f'kernelone_execution_duration_seconds_total{{lane="{lane}"}} {duration}')

        # Messages dropped
        lines.append("# HELP kernelone_messages_dropped_total Total number of dropped messages")
        lines.append("# TYPE kernelone_messages_dropped_total counter")
        lines.append(f"kernelone_messages_dropped_total {self.messages_dropped}")

        # Processes killed
        lines.append("# HELP kernelone_processes_killed_total Total number of forcefully killed processes")
        lines.append("# TYPE kernelone_processes_killed_total counter")
        lines.append(f"kernelone_processes_killed_total {self.processes_killed}")

        # States retained
        lines.append("# HELP kernelone_states_retained_current Current number of retained states")
        lines.append("# TYPE kernelone_states_retained_current gauge")
        lines.append(f"kernelone_states_retained_current {self.states_retained}")

        lines.append("# HELP kernelone_states_active_current Current number of active (non-terminal) states")
        lines.append("# TYPE kernelone_states_active_current gauge")
        lines.append(f"kernelone_states_active_current {self.states_active}")

        return "\n".join(lines)


# =============================================================================
# Global metrics instance
# =============================================================================

_metrics: ExecutionMetrics | None = None


def get_metrics() -> ExecutionMetrics:
    """Get the global metrics instance.

    Returns:
        The singleton ExecutionMetrics instance.
    """
    global _metrics
    if _metrics is None:
        _metrics = ExecutionMetrics()
    return _metrics


def reset_metrics() -> None:
    """Reset global metrics state (for testing purposes)."""
    global _metrics
    _metrics = ExecutionMetrics()
