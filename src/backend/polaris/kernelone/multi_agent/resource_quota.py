"""Resource Quota Manager for Multi-Agent Coordination.

This module provides resource quota management for supporting 100+ concurrent
agents. It handles:

1. Per-agent quota allocation and tracking
2. Global resource quota limits
3. Quota exceeded error handling
4. Concurrent agent limit enforcement

Usage:
    quota_manager = ResourceQuotaManager(
        max_concurrent_agents=100,
        default_agent_quota={"cpu": 1.0, "memory": "256MB"},
    )

    # Allocate quota for an agent
    quota_manager.allocate("agent-1", {"cpu": 1.0, "memory": "256MB"})

    # Check if agent can be allocated
    can_allocate = quota_manager.check("agent-2", {"cpu": 2.0, "memory": "512MB"})

    # Release quota when agent finishes
    quota_manager.release("agent-1")
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════
# Quota Constants
# ═══════════════════════════════════════════════════════════════════════════

#: Default maximum concurrent agents supported
DEFAULT_MAX_CONCURRENT_AGENTS: int = 100

#: Default agent quota in CPU cores
DEFAULT_AGENT_CPU_QUOTA: float = 1.0

#: Default agent quota in memory (MB)
DEFAULT_AGENT_MEMORY_QUOTA_MB: int = 256

#: Default maximum quota per agent
DEFAULT_MAX_QUOTA_PER_AGENT: int = 4


# ═══════════════════════════════════════════════════════════════════════════
# Quota Exceptions
# ═══════════════════════════════════════════════════════════════════════════


class QuotaExceededError(Exception):
    """Raised when resource quota is exceeded.

    Attributes:
        agent_id: The agent ID that failed allocation
        requested: The requested quota that exceeded limits
        available: The available quota at time of failure
        limit_type: The type of limit that was exceeded
    """

    def __init__(
        self,
        message: str,
        agent_id: str | None = None,
        requested: dict[str, Any] | None = None,
        available: dict[str, Any] | None = None,
        limit_type: str | None = None,
    ) -> None:
        """Initialize QuotaExceededError.

        Args:
            message: Error message
            agent_id: Agent ID that failed allocation
            requested: Requested quota resources
            available: Available quota resources
            limit_type: Type of limit exceeded (global, per_agent, total)
        """
        super().__init__(message)
        self.agent_id = agent_id
        self.requested = requested or {}
        self.available = available or {}
        self.limit_type = limit_type or "unknown"


class QuotaNotFoundError(Exception):
    """Raised when trying to release or check a non-existent quota allocation."""

    def __init__(self, agent_id: str, message: str | None = None) -> None:
        """Initialize QuotaNotFoundError.

        Args:
            agent_id: The agent ID that was not found
            message: Optional error message
        """
        self.agent_id = agent_id
        super().__init__(message or f"Quota not found for agent: {agent_id}")


# ═══════════════════════════════════════════════════════════════════════════
# Quota Statistics
# ═══════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class QuotaStats:
    """Statistics for resource quota manager.

    Attributes:
        total_allocated: Total number of allocated quotas
        total_released: Total number of released quotas
        current_agents: Current number of active agents
        peak_agents: Peak number of concurrent agents reached
        denied_count: Number of allocation requests denied
        limit_type: Type of limit currently active (global, per_agent, total)
    """

    total_allocated: int = 0
    total_released: int = 0
    current_agents: int = 0
    peak_agents: int = 0
    denied_count: int = 0
    limit_type: str = "global"


@dataclass
class AgentQuota:
    """Quota allocation for a single agent.

    Attributes:
        agent_id: Unique agent identifier
        quota: Resource quota dict (cpu, memory, etc.)
        allocated_at: Timestamp of allocation
        correlation_id: Optional correlation ID for tracking
    """

    agent_id: str
    quota: dict[str, Any]
    allocated_at: str = field(default_factory=lambda: str(uuid.uuid4()))
    correlation_id: str | None = None


# ═══════════════════════════════════════════════════════════════════════════
# Quota Limits
# ═══════════════════════════════════════════════════════════════════════════


class QuotaLimitType(StrEnum):
    """Types of quota limits."""

    GLOBAL = "global"  # Total system-wide limit
    PER_AGENT = "per_agent"  # Limit per individual agent
    TOTAL = "total"  # Total resource pool limit


@dataclass
class QuotaLimits:
    """Container for quota limits.

    Attributes:
        max_concurrent_agents: Maximum concurrent agents allowed
        max_quota_per_agent: Maximum quota that can be allocated to a single agent
        max_total_cpu: Maximum total CPU cores across all agents
        max_total_memory_mb: Maximum total memory in MB across all agents
    """

    max_concurrent_agents: int = DEFAULT_MAX_CONCURRENT_AGENTS
    max_quota_per_agent: int = DEFAULT_MAX_QUOTA_PER_AGENT
    max_total_cpu: float = float(DEFAULT_MAX_CONCURRENT_AGENTS)
    max_total_memory_mb: int = DEFAULT_MAX_CONCURRENT_AGENTS * DEFAULT_AGENT_MEMORY_QUOTA_MB


# ═══════════════════════════════════════════════════════════════════════════
# Resource Quota Manager
# ═══════════════════════════════════════════════════════════════════════════


class ResourceQuotaManager:
    """Manages resource quotas for multi-agent coordination.

    The ResourceQuotaManager provides:
    - Per-agent quota allocation with tracking
    - Global resource limit enforcement
    - Concurrent agent limit management (supports 100+ agents)
    - Quota usage statistics

    Thread safety:
        Uses asyncio.Lock for thread-safe operations.

    Example:
        >>> manager = ResourceQuotaManager(max_concurrent_agents=100)
        >>> manager.allocate("agent-1", {"cpu": 1.0, "memory": "256MB"})
        True
        >>> manager.check("agent-2", {"cpu": 2.0, "memory": "512MB"})
        True
        >>> manager.release("agent-1")
        >>> manager.get_stats()
        QuotaStats(current_agents=1, ...)
    """

    def __init__(
        self,
        max_concurrent_agents: int = DEFAULT_MAX_CONCURRENT_AGENTS,
        default_quota: dict[str, Any] | None = None,
        *,
        max_quota_per_agent: int = DEFAULT_MAX_QUOTA_PER_AGENT,
        max_total_cpu: float | None = None,
        max_total_memory_mb: int | None = None,
    ) -> None:
        """Initialize the ResourceQuotaManager.

        Args:
            max_concurrent_agents: Maximum number of concurrent agents (default: 100)
            default_quota: Default quota for agents if not specified at allocation
            max_quota_per_agent: Maximum quota units per agent (default: 4)
            max_total_cpu: Maximum total CPU cores (default: max_concurrent_agents)
            max_total_memory_mb: Maximum total memory in MB (default: 100 * 256MB)
        """
        self._limits = QuotaLimits(
            max_concurrent_agents=max_concurrent_agents,
            max_quota_per_agent=max_quota_per_agent,
            max_total_cpu=max_total_cpu or float(max_concurrent_agents),
            max_total_memory_mb=max_total_memory_mb or max_concurrent_agents * DEFAULT_AGENT_MEMORY_QUOTA_MB,
        )

        self._default_quota = default_quota or {
            "cpu": DEFAULT_AGENT_CPU_QUOTA,
            "memory_mb": DEFAULT_AGENT_MEMORY_QUOTA_MB,
        }

        # Per-agent quota allocations
        self._agent_quotas: dict[str, AgentQuota] = {}

        # Global resource usage tracking
        self._total_cpu_used: float = 0.0
        self._total_memory_used_mb: int = 0

        # Statistics
        self._total_allocated: int = 0
        self._total_released: int = 0
        self._peak_agents: int = 0
        self._denied_count: int = 0

        # Lock for thread-safe operations
        self._lock = asyncio.Lock()

        logger.info(
            "ResourceQuotaManager initialized (max_concurrent_agents=%d, default_quota=%s)",
            max_concurrent_agents,
            self._default_quota,
        )

    @property
    def default_quota(self) -> dict[str, Any]:
        """Get the default quota configuration."""
        return dict(self._default_quota)

    @property
    def limits(self) -> QuotaLimits:
        """Get the quota limits configuration."""
        return self._limits

    async def allocate(
        self,
        agent_id: str,
        quota: dict[str, Any] | None = None,
        *,
        correlation_id: str | None = None,
    ) -> bool:
        """Allocate quota for an agent.

        Args:
            agent_id: Unique agent identifier
            quota: Resource quota (cpu, memory_mb, etc.). Uses default if None.
            correlation_id: Optional correlation ID for tracking

        Returns:
            True if allocation succeeded

        Raises:
            QuotaExceededError: If quota limits are exceeded
            ValueError: If agent_id is empty or quota is invalid
        """
        if not agent_id or not agent_id.strip():
            raise ValueError("agent_id cannot be empty")

        quota = quota or self._default_quota
        cpu = float(quota.get("cpu", DEFAULT_AGENT_CPU_QUOTA))
        memory_mb = int(quota.get("memory_mb", DEFAULT_AGENT_MEMORY_QUOTA_MB))

        async with self._lock:
            # Check if agent already has allocation
            if agent_id in self._agent_quotas:
                logger.warning(
                    "ResourceQuotaManager: agent %s already has allocation, skipping",
                    agent_id,
                )
                return True

            # Check concurrent agent limit
            if len(self._agent_quotas) >= self._limits.max_concurrent_agents:
                self._denied_count += 1
                raise QuotaExceededError(
                    f"Concurrent agent limit reached: {self._limits.max_concurrent_agents}",
                    agent_id=agent_id,
                    requested=quota,
                    available={"current_agents": len(self._agent_quotas)},
                    limit_type=QuotaLimitType.GLOBAL,
                )

            # Check per-agent quota limit
            if cpu > self._limits.max_quota_per_agent:
                self._denied_count += 1
                raise QuotaExceededError(
                    f"Per-agent CPU quota exceeded: {cpu} > {self._limits.max_quota_per_agent}",
                    agent_id=agent_id,
                    requested=quota,
                    available={"max_quota_per_agent": self._limits.max_quota_per_agent},
                    limit_type=QuotaLimitType.PER_AGENT,
                )

            # Check total CPU limit
            if self._total_cpu_used + cpu > self._limits.max_total_cpu:
                self._denied_count += 1
                raise QuotaExceededError(
                    f"Total CPU limit exceeded: {self._total_cpu_used + cpu} > {self._limits.max_total_cpu}",
                    agent_id=agent_id,
                    requested=quota,
                    available={
                        "total_cpu_used": self._total_cpu_used,
                        "max_total_cpu": self._limits.max_total_cpu,
                    },
                    limit_type=QuotaLimitType.TOTAL,
                )

            # Check total memory limit
            if self._total_memory_used_mb + memory_mb > self._limits.max_total_memory_mb:
                self._denied_count += 1
                raise QuotaExceededError(
                    f"Total memory limit exceeded: {self._total_memory_used_mb + memory_mb} > {self._limits.max_total_memory_mb}",
                    agent_id=agent_id,
                    requested=quota,
                    available={
                        "total_memory_used_mb": self._total_memory_used_mb,
                        "max_total_memory_mb": self._limits.max_total_memory_mb,
                    },
                    limit_type=QuotaLimitType.TOTAL,
                )

            # Allocate the quota
            agent_quota = AgentQuota(
                agent_id=agent_id,
                quota=quota,
                correlation_id=correlation_id,
            )
            self._agent_quotas[agent_id] = agent_quota
            self._total_cpu_used += cpu
            self._total_memory_used_mb += memory_mb
            self._total_allocated += 1

            # Update peak agents
            if len(self._agent_quotas) > self._peak_agents:
                self._peak_agents = len(self._agent_quotas)

            logger.debug(
                "ResourceQuotaManager: allocated quota for agent=%s (cpu=%s, memory_mb=%s)",
                agent_id,
                cpu,
                memory_mb,
            )

            return True

    async def release(self, agent_id: str) -> bool:
        """Release quota for an agent.

        Args:
            agent_id: Agent identifier to release

        Returns:
            True if release succeeded

        Raises:
            QuotaNotFoundError: If agent_id has no allocated quota
        """
        if not agent_id:
            raise ValueError("agent_id cannot be empty")

        async with self._lock:
            if agent_id not in self._agent_quotas:
                raise QuotaNotFoundError(agent_id)

            agent_quota = self._agent_quotas.pop(agent_id)
            quota = agent_quota.quota
            cpu = float(quota.get("cpu", DEFAULT_AGENT_CPU_QUOTA))
            memory_mb = int(quota.get("memory_mb", DEFAULT_AGENT_MEMORY_QUOTA_MB))

            self._total_cpu_used = max(0.0, self._total_cpu_used - cpu)
            self._total_memory_used_mb = max(0, self._total_memory_used_mb - memory_mb)
            self._total_released += 1

            logger.debug(
                "ResourceQuotaManager: released quota for agent=%s",
                agent_id,
            )

            return True

    async def check(
        self,
        agent_id: str | None = None,
        quota: dict[str, Any] | None = None,
    ) -> bool:
        """Check if a quota allocation would succeed without actually allocating.

        Args:
            agent_id: Agent identifier (optional, for logging)
            quota: Resource quota to check. Uses default if None.

        Returns:
            True if the allocation would succeed, False otherwise
        """
        quota = quota or self._default_quota
        cpu = float(quota.get("cpu", DEFAULT_AGENT_CPU_QUOTA))
        memory_mb = int(quota.get("memory_mb", DEFAULT_AGENT_MEMORY_QUOTA_MB))

        async with self._lock:
            # Check concurrent agent limit
            if len(self._agent_quotas) >= self._limits.max_concurrent_agents:
                return False

            # Check per-agent quota limit
            if cpu > self._limits.max_quota_per_agent:
                return False

            # Check total CPU limit
            if self._total_cpu_used + cpu > self._limits.max_total_cpu:
                return False

            return self._total_memory_used_mb + memory_mb <= self._limits.max_total_memory_mb

    async def get_allocation(self, agent_id: str) -> dict[str, Any] | None:
        """Get the current allocation for an agent.

        Args:
            agent_id: Agent identifier

        Returns:
            Quota dict if allocated, None otherwise
        """
        async with self._lock:
            agent_quota = self._agent_quotas.get(agent_id)
            if agent_quota is None:
                return None
            return dict(agent_quota.quota)

    async def get_stats(self) -> QuotaStats:
        """Get quota manager statistics.

        Returns:
            QuotaStats with current statistics
        """
        async with self._lock:
            return QuotaStats(
                total_allocated=self._total_allocated,
                total_released=self._total_released,
                current_agents=len(self._agent_quotas),
                peak_agents=self._peak_agents,
                denied_count=self._denied_count,
                limit_type=QuotaLimitType.GLOBAL if self._denied_count > 0 else "global",
            )

    async def get_utilization(self) -> dict[str, Any]:
        """Get current resource utilization.

        Returns:
            Dict with utilization metrics
        """
        async with self._lock:
            return {
                "cpu": {
                    "used": self._total_cpu_used,
                    "limit": self._limits.max_total_cpu,
                    "utilization_pct": (
                        (self._total_cpu_used / self._limits.max_total_cpu * 100)
                        if self._limits.max_total_cpu > 0
                        else 0.0
                    ),
                },
                "memory_mb": {
                    "used": self._total_memory_used_mb,
                    "limit": self._limits.max_total_memory_mb,
                    "utilization_pct": (
                        (self._total_memory_used_mb / self._limits.max_total_memory_mb * 100)
                        if self._limits.max_total_memory_mb > 0
                        else 0.0
                    ),
                },
                "agents": {
                    "current": len(self._agent_quotas),
                    "limit": self._limits.max_concurrent_agents,
                    "utilization_pct": (
                        (len(self._agent_quotas) / self._limits.max_concurrent_agents * 100)
                        if self._limits.max_concurrent_agents > 0
                        else 0.0
                    ),
                },
            }

    async def reset(self) -> None:
        """Reset all allocations and statistics.

        This is primarily for testing purposes.
        """
        async with self._lock:
            self._agent_quotas.clear()
            self._total_cpu_used = 0.0
            self._total_memory_used_mb = 0
            self._total_allocated = 0
            self._total_released = 0
            self._peak_agents = 0
            self._denied_count = 0
            logger.info("ResourceQuotaManager: reset complete")


# ═══════════════════════════════════════════════════════════════════════════
# Factory
# ═══════════════════════════════════════════════════════════════════════════


def create_resource_quota_manager(
    max_concurrent_agents: int = DEFAULT_MAX_CONCURRENT_AGENTS,
    default_quota: dict[str, Any] | None = None,
) -> ResourceQuotaManager:
    """Create a configured ResourceQuotaManager.

    Args:
        max_concurrent_agents: Maximum concurrent agents (default: 100)
        default_quota: Default quota resources

    Returns:
        Configured ResourceQuotaManager instance
    """
    return ResourceQuotaManager(
        max_concurrent_agents=max_concurrent_agents,
        default_quota=default_quota,
    )


__all__ = [
    "DEFAULT_AGENT_CPU_QUOTA",
    "DEFAULT_AGENT_MEMORY_QUOTA_MB",
    "DEFAULT_MAX_CONCURRENT_AGENTS",
    "DEFAULT_MAX_QUOTA_PER_AGENT",
    "AgentQuota",
    "QuotaExceededError",
    "QuotaLimitType",
    "QuotaLimits",
    "QuotaNotFoundError",
    "QuotaStats",
    "ResourceQuotaManager",
    "create_resource_quota_manager",
]
