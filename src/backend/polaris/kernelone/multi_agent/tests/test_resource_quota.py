"""Unit tests for ResourceQuotaManager module."""

from __future__ import annotations

import contextlib

import pytest
from polaris.kernelone.multi_agent.resource_quota import (
    AgentQuota,
    QuotaExceededError,
    QuotaLimits,
    QuotaLimitType,
    QuotaNotFoundError,
    ResourceQuotaManager,
    create_resource_quota_manager,
)


class TestQuotaLimits:
    """Tests for QuotaLimits dataclass."""

    def test_default_limits(self) -> None:
        """Default limits should be sensible values."""
        limits = QuotaLimits()
        assert limits.max_concurrent_agents == 100
        assert limits.max_quota_per_agent == 4
        assert limits.max_total_cpu == 100.0
        assert limits.max_total_memory_mb == 25600

    def test_custom_limits(self) -> None:
        """Custom limits should override defaults."""
        limits = QuotaLimits(
            max_concurrent_agents=50,
            max_quota_per_agent=2,
            max_total_cpu=50.0,
            max_total_memory_mb=12800,
        )
        assert limits.max_concurrent_agents == 50
        assert limits.max_quota_per_agent == 2
        assert limits.max_total_cpu == 50.0
        assert limits.max_total_memory_mb == 12800


class TestAgentQuota:
    """Tests for AgentQuota dataclass."""

    def test_create_agent_quota(self) -> None:
        """AgentQuota should be created with correct values."""
        quota = AgentQuota(
            agent_id="agent-1",
            quota={"cpu": 1.0, "memory_mb": 256},
        )
        assert quota.agent_id == "agent-1"
        assert quota.quota["cpu"] == 1.0
        assert quota.quota["memory_mb"] == 256

    def test_agent_quota_with_correlation_id(self) -> None:
        """AgentQuota should support correlation_id."""
        quota = AgentQuota(
            agent_id="agent-1",
            quota={"cpu": 1.0},
            correlation_id="task-123",
        )
        assert quota.correlation_id == "task-123"


class TestResourceQuotaManager:
    """Tests for ResourceQuotaManager."""

    @pytest.fixture
    def manager(self) -> ResourceQuotaManager:
        """Create a ResourceQuotaManager for testing."""
        return ResourceQuotaManager(
            max_concurrent_agents=10,
            default_quota={"cpu": 1.0, "memory_mb": 256},
        )

    @pytest.fixture
    def manager_with_small_limits(self) -> ResourceQuotaManager:
        """Create a ResourceQuotaManager with small limits for testing."""
        return ResourceQuotaManager(
            max_concurrent_agents=3,
            max_quota_per_agent=2,
            max_total_cpu=3.0,
            max_total_memory_mb=1024,  # 3 agents * 256MB = 768MB < 1024MB
        )

    @pytest.mark.asyncio
    async def test_allocate_success(self, manager: ResourceQuotaManager) -> None:
        """allocate should succeed for valid agent."""
        result = await manager.allocate("agent-1", {"cpu": 1.0, "memory_mb": 256})
        assert result is True

        allocation = await manager.get_allocation("agent-1")
        assert allocation is not None
        assert allocation["cpu"] == 1.0

    @pytest.mark.asyncio
    async def test_allocate_with_default_quota(self, manager: ResourceQuotaManager) -> None:
        """allocate should use default quota when not specified."""
        await manager.allocate("agent-1")
        allocation = await manager.get_allocation("agent-1")
        assert allocation is not None
        assert allocation["cpu"] == 1.0
        assert allocation["memory_mb"] == 256

    @pytest.mark.asyncio
    async def test_allocate_empty_agent_id_fails(self, manager: ResourceQuotaManager) -> None:
        """allocate should raise ValueError for empty agent_id."""
        with pytest.raises(ValueError, match="agent_id cannot be empty"):
            await manager.allocate("")

    @pytest.mark.asyncio
    async def test_allocate_duplicate_fails(self, manager: ResourceQuotaManager) -> None:
        """allocate should return True (not raise) for duplicate agent."""
        await manager.allocate("agent-1")
        result = await manager.allocate("agent-1")
        assert result is True  # Silently succeeds

    @pytest.mark.asyncio
    async def test_allocate_exceeds_concurrent_limit(self, manager_with_small_limits: ResourceQuotaManager) -> None:
        """allocate should raise QuotaExceededError when concurrent limit reached."""
        # Fill up the concurrent agent limit
        await manager_with_small_limits.allocate("agent-1")
        await manager_with_small_limits.allocate("agent-2")
        await manager_with_small_limits.allocate("agent-3")

        # Next allocation should fail
        with pytest.raises(QuotaExceededError) as exc_info:
            await manager_with_small_limits.allocate("agent-4")
        assert exc_info.value.limit_type == QuotaLimitType.GLOBAL

    @pytest.mark.asyncio
    async def test_allocate_exceeds_per_agent_limit(self, manager_with_small_limits: ResourceQuotaManager) -> None:
        """allocate should raise QuotaExceededError when per-agent limit exceeded."""
        with pytest.raises(QuotaExceededError) as exc_info:
            await manager_with_small_limits.allocate("agent-1", {"cpu": 3.0, "memory_mb": 256})
        assert exc_info.value.limit_type == QuotaLimitType.PER_AGENT

    @pytest.mark.asyncio
    async def test_allocate_exceeds_total_cpu(self, manager_with_small_limits: ResourceQuotaManager) -> None:
        """allocate should raise QuotaExceededError when total CPU limit exceeded."""
        # Allocate 2 agents at 1.0 CPU each = 2.0 CPU used
        await manager_with_small_limits.allocate("agent-1")
        await manager_with_small_limits.allocate("agent-2")

        # Next allocation should fail (would exceed 3.0 total CPU)
        with pytest.raises(QuotaExceededError) as exc_info:
            await manager_with_small_limits.allocate("agent-3", {"cpu": 2.0, "memory_mb": 256})
        assert exc_info.value.limit_type == QuotaLimitType.TOTAL

    @pytest.mark.asyncio
    async def test_release_success(self, manager: ResourceQuotaManager) -> None:
        """release should succeed for allocated agent."""
        await manager.allocate("agent-1")
        result = await manager.release("agent-1")
        assert result is True

        allocation = await manager.get_allocation("agent-1")
        assert allocation is None

    @pytest.mark.asyncio
    async def test_release_not_found(self, manager: ResourceQuotaManager) -> None:
        """release should raise QuotaNotFoundError for unknown agent."""
        with pytest.raises(QuotaNotFoundError) as exc_info:
            await manager.release("unknown-agent")
        assert exc_info.value.agent_id == "unknown-agent"

    @pytest.mark.asyncio
    async def test_release_empty_agent_id_fails(self, manager: ResourceQuotaManager) -> None:
        """release should raise ValueError for empty agent_id."""
        with pytest.raises(ValueError, match="agent_id cannot be empty"):
            await manager.release("")

    @pytest.mark.asyncio
    async def test_check_success(self, manager: ResourceQuotaManager) -> None:
        """check should return True when allocation would succeed."""
        can_allocate = await manager.check("agent-1", {"cpu": 1.0, "memory_mb": 256})
        assert can_allocate is True

    @pytest.mark.asyncio
    async def test_check_returns_false_when_limit_reached(
        self, manager_with_small_limits: ResourceQuotaManager
    ) -> None:
        """check should return False when limit would be exceeded."""
        # Fill up the limit
        await manager_with_small_limits.allocate("agent-1")
        await manager_with_small_limits.allocate("agent-2")
        await manager_with_small_limits.allocate("agent-3")

        can_allocate = await manager_with_small_limits.check("agent-4")
        assert can_allocate is False

    @pytest.mark.asyncio
    async def test_check_does_not_modify_state(self, manager: ResourceQuotaManager) -> None:
        """check should not modify allocation state."""
        await manager.allocate("agent-1", {"cpu": 1.0, "memory_mb": 256})
        await manager.check("agent-2", {"cpu": 1.0, "memory_mb": 256})

        # agent-2 should not be allocated
        allocation = await manager.get_allocation("agent-2")
        assert allocation is None

    @pytest.mark.asyncio
    async def test_get_stats(self, manager: ResourceQuotaManager) -> None:
        """get_stats should return correct statistics."""
        await manager.allocate("agent-1")
        await manager.allocate("agent-2")
        await manager.release("agent-1")

        # Attempt allocation that will be denied
        with contextlib.suppress(QuotaExceededError):
            await manager.allocate("agent-3", {"cpu": 10.0, "memory_mb": 256})

        stats = await manager.get_stats()
        assert stats.total_allocated == 2
        assert stats.total_released == 1
        assert stats.current_agents == 1
        assert stats.denied_count == 1

    @pytest.mark.asyncio
    async def test_get_utilization(self, manager: ResourceQuotaManager) -> None:
        """get_utilization should return correct utilization metrics."""
        await manager.allocate("agent-1", {"cpu": 1.0, "memory_mb": 256})

        utilization = await manager.get_utilization()
        assert utilization["cpu"]["used"] == 1.0
        assert utilization["cpu"]["limit"] == 10.0
        assert utilization["memory_mb"]["used"] == 256
        assert utilization["agents"]["current"] == 1

    @pytest.mark.asyncio
    async def test_reset(self, manager: ResourceQuotaManager) -> None:
        """reset should clear all allocations and statistics."""
        await manager.allocate("agent-1")
        await manager.allocate("agent-2")

        await manager.reset()

        stats = await manager.get_stats()
        assert stats.total_allocated == 0
        assert stats.total_released == 0
        assert stats.current_agents == 0


class TestConcurrentAgentLimits:
    """Tests for concurrent agent limit enforcement."""

    @pytest.mark.asyncio
    async def test_supports_100_concurrent_agents(self) -> None:
        """Manager should support 100+ concurrent agents."""
        manager = ResourceQuotaManager(max_concurrent_agents=100)

        # Allocate 100 agents
        for i in range(100):
            await manager.allocate(f"agent-{i}")

        stats = await manager.get_stats()
        assert stats.current_agents == 100

        # 101st agent should fail
        with pytest.raises(QuotaExceededError):
            await manager.allocate("agent-101")

    @pytest.mark.asyncio
    async def test_allocation_release_cycle(self) -> None:
        """Manager should handle rapid allocation/release cycles."""
        manager = ResourceQuotaManager(max_concurrent_agents=10)

        # Rapid cycle
        for i in range(20):
            agent_id = f"agent-{i % 10}"
            with contextlib.suppress(QuotaNotFoundError):
                await manager.release(agent_id)
            await manager.allocate(agent_id)

        stats = await manager.get_stats()
        assert stats.total_allocated == 20
        assert stats.total_released == 10  # 10 agents were released


class TestFactory:
    """Tests for create_resource_quota_manager factory function."""

    def test_create_with_defaults(self) -> None:
        """Factory should create manager with default values."""
        manager = create_resource_quota_manager()
        assert manager.limits.max_concurrent_agents == 100

    def test_create_with_custom_values(self) -> None:
        """Factory should create manager with custom values."""
        manager = create_resource_quota_manager(
            max_concurrent_agents=50,
            default_quota={"cpu": 2.0, "memory_mb": 512},
        )
        assert manager.limits.max_concurrent_agents == 50
        assert manager.default_quota["cpu"] == 2.0


class TestQuotaExceededError:
    """Tests for QuotaExceededError exception."""

    def test_error_attributes(self) -> None:
        """Error should have correct attributes."""
        error = QuotaExceededError(
            message="CPU limit exceeded",
            agent_id="agent-1",
            requested={"cpu": 5.0},
            available={"cpu": 2.0},
            limit_type=QuotaLimitType.TOTAL,
        )
        assert error.agent_id == "agent-1"
        assert error.requested == {"cpu": 5.0}
        assert error.available == {"cpu": 2.0}
        assert error.limit_type == QuotaLimitType.TOTAL

    def test_error_string(self) -> None:
        """Error should produce meaningful string."""
        error = QuotaExceededError("Test error")
        assert str(error) == "Test error"
