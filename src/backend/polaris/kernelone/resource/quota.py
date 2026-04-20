"""Resource quota management for Agent/Cell-level resource allocation.

This module provides the resource quota system for Polaris AGI skeleton,
enabling per-agent resource tracking and enforcement.

Core types:
- ResourceQuota: Immutable resource limits for an agent
- ResourceUsage: Current resource consumption
- AgentResources: Combines quota and usage for an agent
- QuotaStatus: Enum for quota check results
- ResourceQuotaManager: Central manager for quota allocation and checking

Design constraints:
- ResourceQuota is frozen to prevent accidental modification
- All quota checks are thread-safe via RLock
- System-level quotas provide hard limits independent of agent quotas
"""

from __future__ import annotations

import threading
from contextlib import contextmanager, suppress
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, ClassVar

# No direct import of metrics to avoid circular deps - use lazy import


@dataclass(frozen=True, slots=True)
class ResourceQuota:
    """Immutable resource quota limits for an agent or cell.

    Attributes:
        cpu_quota_ns: CPU time quota in nanoseconds (default: 60s)
        memory_bytes: Memory limit in bytes (default: 2GB)
        max_concurrent_tools: Maximum concurrent tool executions (default: 10)
        max_turns: Maximum number of conversation turns (default: 50)
        max_wall_time_seconds: Maximum wall-clock time in seconds (default: 300s)

    Class variables:
        SYSTEM_CPU_QUOTA_NS: System-wide CPU quota (600s)
        SYSTEM_MEMORY_BYTES: System-wide memory limit (16GB)
    """

    cpu_quota_ns: int = 60_000_000_000  # 60 seconds in nanoseconds
    memory_bytes: int = 2 * 1024 * 1024 * 1024  # 2GB
    max_concurrent_tools: int = 10
    max_turns: int = 50
    max_wall_time_seconds: int = 300  # 5 minutes

    SYSTEM_CPU_QUOTA_NS: ClassVar[int] = 600_000_000_000  # 600 seconds
    SYSTEM_MEMORY_BYTES: ClassVar[int] = 16 * 1024 * 1024 * 1024  # 16GB


@dataclass(slots=True)
class ResourceUsage:
    """Tracks current resource consumption for an agent.

    Attributes:
        cpu_used_ns: CPU time used in nanoseconds
        memory_used_bytes: Memory currently used in bytes
        concurrent_tools: Number of currently active tool executions
        turns: Number of turns consumed
        wall_time_seconds: Elapsed wall-clock time in seconds
    """

    cpu_used_ns: int = 0
    memory_used_bytes: int = 0
    concurrent_tools: int = 0
    turns: int = 0
    wall_time_seconds: int = 0

    def is_within_quota(self, quota: ResourceQuota) -> bool:
        """Check if current usage is within the specified quota limits.

        Args:
            quota: The ResourceQuota limits to check against.

        Returns:
            True if all resource metrics are within quota, False otherwise.
        """
        if self.cpu_used_ns > quota.cpu_quota_ns:
            return False
        if self.memory_used_bytes > quota.memory_bytes:
            return False
        if self.concurrent_tools > quota.max_concurrent_tools:
            return False
        if self.turns > quota.max_turns:
            return False
        return self.wall_time_seconds <= quota.max_wall_time_seconds


@dataclass(slots=True)
class AgentResources:
    """Represents resources allocated to an agent, including quota and usage.

    Attributes:
        agent_id: Unique identifier for the agent
        quota: The allocated ResourceQuota for this agent
        usage: Current ResourceUsage for this agent
        acquired_at: Timestamp when resources were allocated
    """

    agent_id: str
    quota: ResourceQuota
    usage: ResourceUsage = field(default_factory=ResourceUsage)
    acquired_at: datetime = field(default_factory=datetime.now)


class QuotaStatus(Enum):
    """Enumeration of quota check results.

    ALLOWED: All resources are within acceptable limits
    DENIED_SINGLE: Single resource type exceeded its quota
    DENIED_MULTIPLE: Multiple resource types exceeded their quotas
    SYSTEM_OVERLOADED: System-wide resource limits are exceeded
    """

    ALLOWED = "allowed"
    DENIED_SINGLE = "denied_single"
    DENIED_MULTIPLE = "denied_multiple"
    SYSTEM_OVERLOADED = "system_overloaded"


# ═══════════════════════════════════════════════════════════════════════════════
# Global Quota Manager Singleton
# ═══════════════════════════════════════════════════════════════════════════════

# Module-level global manager instance
_GLOBAL_MANAGER: ResourceQuotaManager | None = None
_GLOBAL_MANAGER_LOCK = threading.Lock()


def get_global_quota_manager() -> ResourceQuotaManager:
    """Get the global ResourceQuotaManager singleton.

    Returns:
        The global ResourceQuotaManager instance.
    """
    global _GLOBAL_MANAGER
    if _GLOBAL_MANAGER is None:
        with _GLOBAL_MANAGER_LOCK:
            if _GLOBAL_MANAGER is None:
                _GLOBAL_MANAGER = ResourceQuotaManager()
    return _GLOBAL_MANAGER


def reset_global_quota_manager() -> None:
    """Reset the global manager (for testing only)."""
    global _GLOBAL_MANAGER
    with _GLOBAL_MANAGER_LOCK:
        _GLOBAL_MANAGER = None


@contextmanager
def agent_quota_context(
    agent_id: str,
    quota: ResourceQuota | None = None,
    manager: ResourceQuotaManager | None = None,
) -> Any:
    """Context manager for agent-scoped quota management.

    Allocates resources on entry and releases on exit.

    Args:
        agent_id: Unique identifier for the agent.
        quota: Optional custom ResourceQuota. If None, uses default.
        manager: Optional quota manager. If None, uses global manager.

    Yields:
        The allocated AgentResources object.

    Example:
        with agent_quota_context("agent-123") as resources:
            # Agent is running with allocated quota
            ...
        # Resources automatically released
    """
    _manager = manager if manager is not None else get_global_quota_manager()
    resources = _manager.allocate(agent_id, quota)
    try:
        yield resources
    finally:
        with suppress(KeyError):
            _manager.release(agent_id)


# ═══════════════════════════════════════════════════════════════════════════════
# Quota Metrics (Prometheus-compatible)
# ═══════════════════════════════════════════════════════════════════════════════


class QuotaMetrics:
    """Prometheus-compatible quota metrics.

    Provides gauges for quota usage monitoring.

    Attributes:
        agent_turns: Gauge for agent turn count
        agent_concurrent_tools: Gauge for concurrent tool executions
        agent_cpu_ns: Gauge for CPU time used in nanoseconds
        agent_memory_bytes: Gauge for memory usage in bytes
        agent_wall_time_seconds: Gauge for wall-clock time in seconds
    """

    def __init__(self) -> None:
        """Initialize quota metrics with zero values."""
        self._lock = threading.Lock()
        self._metrics: dict[str, dict[str, float]] = {}
        self._enabled = False

    def enable(self) -> None:
        """Enable metrics collection."""
        self._enabled = True

    def disable(self) -> None:
        """Disable metrics collection."""
        self._enabled = False

    def record_usage(self, agent_id: str, usage: ResourceUsage) -> None:
        """Record resource usage for an agent.

        Args:
            agent_id: Unique identifier for the agent.
            usage: The ResourceUsage to record.
        """
        if not self._enabled:
            return
        with self._lock:
            self._metrics[agent_id] = {
                "turns": float(usage.turns),
                "concurrent_tools": float(usage.concurrent_tools),
                "cpu_ns": float(usage.cpu_used_ns),
                "memory_bytes": float(usage.memory_used_bytes),
                "wall_time_seconds": float(usage.wall_time_seconds),
            }

    def get_metrics(self) -> dict[str, dict[str, float]]:
        """Get all recorded metrics.

        Returns:
            Dictionary mapping agent_id to metric name to value.
        """
        with self._lock:
            return dict(self._metrics)

    def clear(self) -> None:
        """Clear all recorded metrics."""
        with self._lock:
            self._metrics.clear()


# Global metrics instance
_GLOBAL_METRICS: QuotaMetrics | None = None
_GLOBAL_METRICS_LOCK = threading.Lock()


def get_quota_metrics() -> QuotaMetrics:
    """Get the global QuotaMetrics singleton.

    Returns:
        The global QuotaMetrics instance.
    """
    global _GLOBAL_METRICS
    if _GLOBAL_METRICS is None:
        with _GLOBAL_METRICS_LOCK:
            if _GLOBAL_METRICS is None:
                _GLOBAL_METRICS = QuotaMetrics()
    return _GLOBAL_METRICS


# ═══════════════════════════════════════════════════════════════════════════════
# Resource Quota Manager Implementation
# ═══════════════════════════════════════════════════════════════════════════════


class ResourceQuotaManager:
    """Thread-safe manager for agent resource quota allocation and checking.

    This manager provides:
    - Allocation: Assign resources to agents with optional custom quotas
    - Release: Free resources when an agent finishes
    - Check: Verify if an agent's resource usage is within quota
    - System limits: Enforce overall system resource constraints
    - Concurrent tool tracking: Increment/decrement concurrent tool count

    Thread safety is achieved via RLock to protect the internal agent
    resources dictionary.
    """

    def __init__(self) -> None:
        """Initialize the ResourceQuotaManager with an empty agent registry."""
        self._lock = threading.RLock()
        self._agent_resources: dict[str, AgentResources] = {}
        self._metrics = get_quota_metrics()

    def _allocate_agent_unlocked(self, agent_id: str, quota: ResourceQuota | None = None) -> AgentResources:
        """Allocate resources to an agent (assumes lock is already held).

        Internal method for use when lock is already held by caller.
        If no quota is provided, the default ResourceQuota will be used.

        Args:
            agent_id: Unique identifier for the agent.
            quota: Optional custom ResourceQuota. If None, uses default.

        Returns:
            The newly allocated AgentResources object.
        """
        effective_quota = quota if quota is not None else ResourceQuota()
        agent_res = AgentResources(
            agent_id=agent_id,
            quota=effective_quota,
        )
        self._agent_resources[agent_id] = agent_res
        self._metrics.record_usage(agent_id, agent_res.usage)
        return agent_res

    def allocate(self, agent_id: str, quota: ResourceQuota | None = None) -> AgentResources:
        """Allocate resources to an agent.

        If no quota is provided, the default ResourceQuota will be used.

        Args:
            agent_id: Unique identifier for the agent.
            quota: Optional custom ResourceQuota. If None, uses default.

        Returns:
            The newly allocated AgentResources object.

        Raises:
            ValueError: If agent_id is already allocated.
        """
        with self._lock:
            if agent_id in self._agent_resources:
                msg = f"Agent {agent_id} is already allocated"
                raise ValueError(msg)

            return self._allocate_agent_unlocked(agent_id, quota)

    def release(self, agent_id: str) -> None:
        """Release resources allocated to an agent.

        Args:
            agent_id: Unique identifier for the agent.

        Raises:
            KeyError: If agent_id is not found in the registry.
        """
        with self._lock:
            if agent_id not in self._agent_resources:
                msg = f"Agent {agent_id} not found"
                raise KeyError(msg)
            del self._agent_resources[agent_id]

    def check_quota(self, agent_id: str) -> QuotaStatus:
        """Check the quota status for an agent.

        This method checks both the agent's individual quota and the
        system-wide resource limits.

        Args:
            agent_id: Unique identifier for the agent.

        Returns:
            QuotaStatus indicating the current resource status.

        Raises:
            KeyError: If agent_id is not found in the registry.
        """
        with self._lock:
            if agent_id not in self._agent_resources:
                msg = f"Agent {agent_id} not found"
                raise KeyError(msg)

            agent_res = self._agent_resources[agent_id]
            usage = agent_res.usage
            quota = agent_res.quota

            # Check system-wide limits first
            system_quota = ResourceQuota()
            violations: list[str] = []

            if usage.cpu_used_ns > system_quota.SYSTEM_CPU_QUOTA_NS:
                violations.append("system_cpu")
            if usage.memory_used_bytes > system_quota.SYSTEM_MEMORY_BYTES:
                violations.append("system_memory")

            if violations:
                return QuotaStatus.SYSTEM_OVERLOADED

            # Check agent-specific quota violations
            agent_violations: list[str] = []

            if usage.cpu_used_ns > quota.cpu_quota_ns:
                agent_violations.append("cpu")
            if usage.memory_used_bytes > quota.memory_bytes:
                agent_violations.append("memory")
            if usage.concurrent_tools > quota.max_concurrent_tools:
                agent_violations.append("concurrent_tools")
            if usage.turns > quota.max_turns:
                agent_violations.append("turns")
            if usage.wall_time_seconds > quota.max_wall_time_seconds:
                agent_violations.append("wall_time")

            if not agent_violations:
                return QuotaStatus.ALLOWED
            if len(agent_violations) == 1:
                return QuotaStatus.DENIED_SINGLE
            return QuotaStatus.DENIED_MULTIPLE

    def check_tool_quota(self, agent_id: str) -> tuple[bool, str]:
        """Check if an agent can execute a new concurrent tool.

        This method specifically checks the concurrent_tools limit.

        Args:
            agent_id: Unique identifier for the agent.

        Returns:
            Tuple of (allowed, reason). If allowed is True, the tool can proceed.
            If allowed is False, reason contains the denial message.

        Raises:
            KeyError: If agent_id is not found in the registry.
        """
        with self._lock:
            if agent_id not in self._agent_resources:
                msg = f"Agent {agent_id} not found"
                raise KeyError(msg)

            agent_res = self._agent_resources[agent_id]
            usage = agent_res.usage
            quota = agent_res.quota

            if usage.concurrent_tools >= quota.max_concurrent_tools:
                return (
                    False,
                    f"Concurrent tool limit reached: {usage.concurrent_tools}/{quota.max_concurrent_tools}",
                )
            return (True, "")

    def acquire_concurrent_tool(self, agent_id: str) -> bool:
        """Acquire a concurrent tool slot for an agent.

        Atomically increments the concurrent_tools count if under quota.
        Auto-allocates the agent if not registered (lazy allocation).

        Args:
            agent_id: Unique identifier for the agent.

        Returns:
            True if the slot was acquired, False if at quota limit.
        """
        with self._lock:
            # Auto-allocate if not registered (lazy allocation)
            if agent_id not in self._agent_resources:
                self._allocate_agent_unlocked(agent_id)

            agent_res = self._agent_resources[agent_id]
            if agent_res.usage.concurrent_tools >= agent_res.quota.max_concurrent_tools:
                return False

            agent_res.usage.concurrent_tools += 1
            self._metrics.record_usage(agent_id, agent_res.usage)
            return True

    def release_concurrent_tool(self, agent_id: str) -> None:
        """Release a concurrent tool slot for an agent.

        Atomically decrements the concurrent_tools count.
        Silently ignores if agent is not registered (handles edge cases where
        acquire was called but agent was auto-allocated).

        Args:
            agent_id: Unique identifier for the agent.
        """
        with self._lock:
            # Silently ignore if agent not registered - it was either never
            # allocated or was auto-allocated and already released
            if agent_id not in self._agent_resources:
                return

            agent_res = self._agent_resources[agent_id]
            if agent_res.usage.concurrent_tools <= 0:
                msg = f"Agent {agent_id} concurrent tools already 0"
                raise ValueError(msg)

            agent_res.usage.concurrent_tools -= 1
            self._metrics.record_usage(agent_id, agent_res.usage)

    def get_usage(self, agent_id: str) -> ResourceUsage:
        """Get the current resource usage for an agent.

        Args:
            agent_id: Unique identifier for the agent.

        Returns:
            The current ResourceUsage for the agent.

        Raises:
            KeyError: If agent_id is not found in the registry.
        """
        with self._lock:
            if agent_id not in self._agent_resources:
                msg = f"Agent {agent_id} not found"
                raise KeyError(msg)
            return self._agent_resources[agent_id].usage

    def update_usage(self, agent_id: str, usage: ResourceUsage) -> None:
        """Update the resource usage for an agent.

        Args:
            agent_id: Unique identifier for the agent.
            usage: The new ResourceUsage to set.

        Raises:
            KeyError: If agent_id is not found in the registry.
        """
        with self._lock:
            if agent_id not in self._agent_resources:
                msg = f"Agent {agent_id} not found"
                raise KeyError(msg)
            self._agent_resources[agent_id].usage = usage
            self._metrics.record_usage(agent_id, usage)

    def increment_turn(self, agent_id: str) -> None:
        """Increment the turn count for an agent.

        Args:
            agent_id: Unique identifier for the agent.

        Raises:
            KeyError: If agent_id is not found in the registry.
        """
        with self._lock:
            if agent_id not in self._agent_resources:
                msg = f"Agent {agent_id} not found"
                raise KeyError(msg)

            agent_res = self._agent_resources[agent_id]
            agent_res.usage.turns += 1
            self._metrics.record_usage(agent_id, agent_res.usage)

    def add_wall_time(self, agent_id: str, seconds: float) -> None:
        """Add wall-clock time to an agent's usage.

        Args:
            agent_id: Unique identifier for the agent.
            seconds: Time in seconds to add.

        Raises:
            KeyError: If agent_id is not found in the registry.
        """
        with self._lock:
            if agent_id not in self._agent_resources:
                msg = f"Agent {agent_id} not found"
                raise KeyError(msg)

            agent_res = self._agent_resources[agent_id]
            agent_res.usage.wall_time_seconds += int(seconds)
            self._metrics.record_usage(agent_id, agent_res.usage)

    def add_cpu_time(self, agent_id: str, cpu_ns: int) -> None:
        """Add CPU time to an agent's usage.

        Args:
            agent_id: Unique identifier for the agent.
            cpu_ns: CPU time in nanoseconds to add.

        Raises:
            KeyError: If agent_id is not found in the registry.
        """
        with self._lock:
            if agent_id not in self._agent_resources:
                msg = f"Agent {agent_id} not found"
                raise KeyError(msg)

            agent_res = self._agent_resources[agent_id]
            agent_res.usage.cpu_used_ns += cpu_ns
            self._metrics.record_usage(agent_id, agent_res.usage)

    def set_memory_usage(self, agent_id: str, memory_bytes: int) -> None:
        """Set the memory usage for an agent.

        Args:
            agent_id: Unique identifier for the agent.
            memory_bytes: Memory usage in bytes.

        Raises:
            KeyError: If agent_id is not found in the registry.
        """
        with self._lock:
            if agent_id not in self._agent_resources:
                msg = f"Agent {agent_id} not found"
                raise KeyError(msg)

            agent_res = self._agent_resources[agent_id]
            agent_res.usage.memory_used_bytes = memory_bytes
            self._metrics.record_usage(agent_id, agent_res.usage)

    def get_all_agents(self) -> list[str]:
        """Get list of all registered agent IDs.

        Returns:
            List of agent IDs.
        """
        with self._lock:
            return list(self._agent_resources.keys())

    def get_quota_status_summary(self) -> dict[str, Any]:
        """Get a summary of quota status for all agents.

        Returns:
            Dictionary with quota status summary.
        """
        with self._lock:
            summary: dict[str, Any] = {
                "total_agents": len(self._agent_resources),
                "by_status": {
                    "allowed": 0,
                    "denied_single": 0,
                    "denied_multiple": 0,
                    "system_overloaded": 0,
                },
                "agents": {},
            }

            for agent_id, resources in self._agent_resources.items():
                status = self.check_quota(agent_id)
                summary["by_status"][status.value] = summary["by_status"].get(status.value, 0) + 1
                summary["agents"][agent_id] = {
                    "status": status.value,
                    "turns": resources.usage.turns,
                    "concurrent_tools": resources.usage.concurrent_tools,
                    "max_concurrent_tools": resources.quota.max_concurrent_tools,
                    "wall_time_seconds": resources.usage.wall_time_seconds,
                }

            return summary
