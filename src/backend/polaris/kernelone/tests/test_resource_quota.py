"""Tests for the resource quota management system.

Tests cover:
- ResourceQuota and ResourceUsage data classes
- QuotaStatus enumeration
- ResourceQuotaManager allocation, release, and quota checking
- Concurrent tool tracking (acquire/release)
- Global manager singleton
- Context manager integration
- QuotaMetrics
"""

from __future__ import annotations

import threading
import time

import pytest
from polaris.kernelone.resource import (
    AgentResources,
    QuotaMetrics,
    QuotaStatus,
    ResourceQuota,
    ResourceQuotaManager,
    ResourceUsage,
    agent_quota_context,
    get_global_quota_manager,
    get_quota_metrics,
    reset_global_quota_manager,
)


class TestResourceQuota:
    """Tests for ResourceQuota dataclass."""

    def test_default_values(self) -> None:
        """Test default quota values."""
        quota = ResourceQuota()
        assert quota.cpu_quota_ns == 60_000_000_000
        assert quota.memory_bytes == 2 * 1024 * 1024 * 1024
        assert quota.max_concurrent_tools == 10
        assert quota.max_turns == 50
        assert quota.max_wall_time_seconds == 300

    def test_custom_values(self) -> None:
        """Test custom quota values."""
        quota = ResourceQuota(
            cpu_quota_ns=120_000_000_000,
            memory_bytes=4 * 1024 * 1024 * 1024,
            max_concurrent_tools=5,
            max_turns=100,
            max_wall_time_seconds=600,
        )
        assert quota.cpu_quota_ns == 120_000_000_000
        assert quota.memory_bytes == 4 * 1024 * 1024 * 1024
        assert quota.max_concurrent_tools == 5
        assert quota.max_turns == 100
        assert quota.max_wall_time_seconds == 600

    def test_system_constants(self) -> None:
        """Test system-wide quota constants."""
        assert ResourceQuota.SYSTEM_CPU_QUOTA_NS == 600_000_000_000
        assert ResourceQuota.SYSTEM_MEMORY_BYTES == 16 * 1024 * 1024 * 1024


class TestResourceUsage:
    """Tests for ResourceUsage dataclass."""

    def test_default_values(self) -> None:
        """Test default usage values."""
        usage = ResourceUsage()
        assert usage.cpu_used_ns == 0
        assert usage.memory_used_bytes == 0
        assert usage.concurrent_tools == 0
        assert usage.turns == 0
        assert usage.wall_time_seconds == 0

    def test_is_within_quota_allowed(self) -> None:
        """Test usage within quota limits."""
        quota = ResourceQuota()
        usage = ResourceUsage(
            cpu_used_ns=30_000_000_000,  # 30s out of 60s
            memory_used_bytes=1024 * 1024 * 1024,  # 1GB out of 2GB
            concurrent_tools=5,  # 5 out of 10
            turns=25,  # 25 out of 50
            wall_time_seconds=150,  # 150s out of 300s
        )
        assert usage.is_within_quota(quota) is True

    def test_is_within_quota_denied_cpu(self) -> None:
        """Test usage exceeds CPU quota."""
        quota = ResourceQuota()
        usage = ResourceUsage(cpu_used_ns=70_000_000_000)  # 70s out of 60s limit
        assert usage.is_within_quota(quota) is False

    def test_is_within_quota_denied_memory(self) -> None:
        """Test usage exceeds memory quota."""
        quota = ResourceQuota()
        usage = ResourceUsage(memory_used_bytes=3 * 1024 * 1024 * 1024)  # 3GB out of 2GB limit
        assert usage.is_within_quota(quota) is False

    def test_is_within_quota_denied_concurrent_tools(self) -> None:
        """Test usage exceeds concurrent tools quota."""
        quota = ResourceQuota()
        usage = ResourceUsage(concurrent_tools=15)  # 15 out of 10 limit
        assert usage.is_within_quota(quota) is False

    def test_is_within_quota_denied_turns(self) -> None:
        """Test usage exceeds turns quota."""
        quota = ResourceQuota()
        usage = ResourceUsage(turns=60)  # 60 out of 50 limit
        assert usage.is_within_quota(quota) is False

    def test_is_within_quota_denied_wall_time(self) -> None:
        """Test usage exceeds wall time quota."""
        quota = ResourceQuota()
        usage = ResourceUsage(wall_time_seconds=350)  # 350s out of 300s limit
        assert usage.is_within_quota(quota) is False


class TestQuotaStatus:
    """Tests for QuotaStatus enumeration."""

    def test_all_statuses_exist(self) -> None:
        """Test all expected quota statuses exist."""
        assert QuotaStatus.ALLOWED.value == "allowed"
        assert QuotaStatus.DENIED_SINGLE.value == "denied_single"
        assert QuotaStatus.DENIED_MULTIPLE.value == "denied_multiple"
        assert QuotaStatus.SYSTEM_OVERLOADED.value == "system_overloaded"


class TestResourceQuotaManager:
    """Tests for ResourceQuotaManager."""

    def setup_method(self) -> None:
        """Reset global manager before each test."""
        reset_global_quota_manager()

    def teardown_method(self) -> None:
        """Clean up after each test."""
        reset_global_quota_manager()

    def test_allocate(self) -> None:
        """Test allocating resources to an agent."""
        manager = ResourceQuotaManager()
        resources = manager.allocate("agent-1")

        assert isinstance(resources, AgentResources)
        assert resources.agent_id == "agent-1"
        assert isinstance(resources.quota, ResourceQuota)
        assert isinstance(resources.usage, ResourceUsage)
        assert resources.usage.concurrent_tools == 0
        assert resources.usage.turns == 0

    def test_allocate_with_custom_quota(self) -> None:
        """Test allocating with custom quota."""
        manager = ResourceQuotaManager()
        custom_quota = ResourceQuota(max_turns=10, max_concurrent_tools=5)
        resources = manager.allocate("agent-1", custom_quota)

        assert resources.quota.max_turns == 10
        assert resources.quota.max_concurrent_tools == 5

    def test_allocate_duplicate_raises(self) -> None:
        """Test allocating to same agent twice raises error."""
        manager = ResourceQuotaManager()
        manager.allocate("agent-1")

        with pytest.raises(ValueError, match="already allocated"):
            manager.allocate("agent-1")

    def test_release(self) -> None:
        """Test releasing agent resources."""
        manager = ResourceQuotaManager()
        manager.allocate("agent-1")
        manager.release("agent-1")

        with pytest.raises(KeyError):
            manager.get_usage("agent-1")

    def test_release_not_found(self) -> None:
        """Test releasing non-existent agent raises error."""
        manager = ResourceQuotaManager()

        with pytest.raises(KeyError, match="not found"):
            manager.release("agent-1")

    def test_check_quota_allowed(self) -> None:
        """Test quota check returns ALLOWED when within limits."""
        manager = ResourceQuotaManager()
        manager.allocate("agent-1")

        status = manager.check_quota("agent-1")
        assert status == QuotaStatus.ALLOWED

    def test_check_quota_denied_single(self) -> None:
        """Test quota check returns DENIED_SINGLE for single violation."""
        manager = ResourceQuotaManager()
        resources = manager.allocate("agent-1")
        resources.usage.turns = 60  # Exceeds default 50

        status = manager.check_quota("agent-1")
        assert status == QuotaStatus.DENIED_SINGLE

    def test_check_quota_denied_multiple(self) -> None:
        """Test quota check returns DENIED_MULTIPLE for multiple violations."""
        manager = ResourceQuotaManager()
        resources = manager.allocate("agent-1")
        resources.usage.turns = 60
        resources.usage.concurrent_tools = 15

        status = manager.check_quota("agent-1")
        assert status == QuotaStatus.DENIED_MULTIPLE

    def test_check_quota_not_found(self) -> None:
        """Test checking quota for non-existent agent raises error."""
        manager = ResourceQuotaManager()

        with pytest.raises(KeyError, match="not found"):
            manager.check_quota("agent-1")

    def test_get_usage(self) -> None:
        """Test getting agent usage."""
        manager = ResourceQuotaManager()
        allocated = manager.allocate("agent-1")
        allocated.usage.turns = 5
        allocated.usage.concurrent_tools = 2

        usage = manager.get_usage("agent-1")
        assert usage.turns == 5
        assert usage.concurrent_tools == 2

    def test_update_usage(self) -> None:
        """Test updating agent usage."""
        manager = ResourceQuotaManager()
        manager.allocate("agent-1")

        new_usage = ResourceUsage(turns=10, concurrent_tools=3)
        manager.update_usage("agent-1", new_usage)

        usage = manager.get_usage("agent-1")
        assert usage.turns == 10
        assert usage.concurrent_tools == 3

    def test_increment_turn(self) -> None:
        """Test incrementing turn count."""
        manager = ResourceQuotaManager()
        manager.allocate("agent-1")

        manager.increment_turn("agent-1")
        manager.increment_turn("agent-1")
        manager.increment_turn("agent-1")

        usage = manager.get_usage("agent-1")
        assert usage.turns == 3

    def test_add_wall_time(self) -> None:
        """Test adding wall time."""
        manager = ResourceQuotaManager()
        manager.allocate("agent-1")

        manager.add_wall_time("agent-1", 10.5)

        usage = manager.get_usage("agent-1")
        assert usage.wall_time_seconds == 10

    def test_acquire_concurrent_tool(self) -> None:
        """Test acquiring concurrent tool slot."""
        manager = ResourceQuotaManager()
        manager.allocate("agent-1")

        assert manager.acquire_concurrent_tool("agent-1") is True
        assert manager.acquire_concurrent_tool("agent-1") is True
        assert manager.acquire_concurrent_tool("agent-1") is True

        usage = manager.get_usage("agent-1")
        assert usage.concurrent_tools == 3

    def test_acquire_concurrent_tool_at_limit(self) -> None:
        """Test acquiring tool slot when at quota limit."""
        manager = ResourceQuotaManager()
        custom_quota = ResourceQuota(max_concurrent_tools=2)
        manager.allocate("agent-1", quota=custom_quota)

        assert manager.acquire_concurrent_tool("agent-1") is True
        assert manager.acquire_concurrent_tool("agent-1") is True
        assert manager.acquire_concurrent_tool("agent-1") is False  # At limit

    def test_release_concurrent_tool(self) -> None:
        """Test releasing concurrent tool slot."""
        manager = ResourceQuotaManager()
        manager.allocate("agent-1")
        manager.acquire_concurrent_tool("agent-1")
        manager.acquire_concurrent_tool("agent-1")
        manager.release_concurrent_tool("agent-1")

        usage = manager.get_usage("agent-1")
        assert usage.concurrent_tools == 1

    def test_release_concurrent_tool_at_zero(self) -> None:
        """Test releasing when at zero raises error."""
        manager = ResourceQuotaManager()
        manager.allocate("agent-1")

        with pytest.raises(ValueError, match="already 0"):
            manager.release_concurrent_tool("agent-1")

    def test_check_tool_quota_allowed(self) -> None:
        """Test tool quota check allows when under limit."""
        manager = ResourceQuotaManager()
        manager.allocate("agent-1")

        allowed, reason = manager.check_tool_quota("agent-1")
        assert allowed is True
        assert reason == ""

    def test_check_tool_quota_denied(self) -> None:
        """Test tool quota check denies when at limit."""
        manager = ResourceQuotaManager()
        custom_quota = ResourceQuota(max_concurrent_tools=1)
        manager.allocate("agent-1", quota=custom_quota)
        manager.acquire_concurrent_tool("agent-1")

        allowed, reason = manager.check_tool_quota("agent-1")
        assert allowed is False
        assert "limit reached" in reason.lower()

    def test_get_all_agents(self) -> None:
        """Test getting all registered agent IDs."""
        manager = ResourceQuotaManager()
        manager.allocate("agent-1")
        manager.allocate("agent-2")
        manager.allocate("agent-3")

        agents = manager.get_all_agents()
        assert len(agents) == 3
        assert "agent-1" in agents
        assert "agent-2" in agents
        assert "agent-3" in agents

    def test_quota_status_summary(self) -> None:
        """Test getting quota status summary."""
        manager = ResourceQuotaManager()
        manager.allocate("agent-1")
        resources2 = manager.allocate("agent-2")
        resources2.usage.turns = 60  # Over quota

        summary = manager.get_quota_status_summary()

        assert summary["total_agents"] == 2
        assert summary["by_status"]["allowed"] == 1
        assert summary["by_status"]["denied_single"] == 1
        assert "agent-1" in summary["agents"]
        assert "agent-2" in summary["agents"]


class TestGlobalManager:
    """Tests for global manager singleton."""

    def setup_method(self) -> None:
        """Reset global manager before each test."""
        reset_global_quota_manager()

    def teardown_method(self) -> None:
        """Clean up after each test."""
        reset_global_quota_manager()

    def test_get_global_manager(self) -> None:
        """Test getting global manager singleton."""
        manager1 = get_global_quota_manager()
        manager2 = get_global_quota_manager()

        assert manager1 is manager2  # Same instance

    def test_global_manager_independent(self) -> None:
        """Test that global manager is independent of local managers."""
        local_manager = ResourceQuotaManager()
        global_manager = get_global_quota_manager()

        assert local_manager is not global_manager

        local_manager.allocate("agent-1")
        assert "agent-1" not in global_manager.get_all_agents()


class TestAgentQuotaContext:
    """Tests for agent_quota_context context manager."""

    def setup_method(self) -> None:
        """Reset global manager before each test."""
        reset_global_quota_manager()

    def teardown_method(self) -> None:
        """Clean up after each test."""
        reset_global_quota_manager()

    def test_context_allocates_and_releases(self) -> None:
        """Test context manager allocates on entry and releases on exit."""
        with agent_quota_context("agent-1") as resources:
            assert isinstance(resources, AgentResources)
            assert resources.agent_id == "agent-1"

        # After context exit, agent should be released
        manager = get_global_quota_manager()
        with pytest.raises(KeyError):
            manager.get_usage("agent-1")

    def test_context_with_custom_quota(self) -> None:
        """Test context manager with custom quota."""
        custom_quota = ResourceQuota(max_turns=5)

        with agent_quota_context("agent-1", quota=custom_quota) as resources:
            assert resources.quota.max_turns == 5

    def test_context_releases_on_exception(self) -> None:
        """Test context manager releases resources even on exception."""

        class CustomError(Exception):
            pass

        with pytest.raises(CustomError), agent_quota_context("agent-1"):
            raise CustomError("test error")

        # Should still be released
        manager = get_global_quota_manager()
        with pytest.raises(KeyError):
            manager.get_usage("agent-1")


class TestQuotaMetrics:
    """Tests for QuotaMetrics."""

    def setup_method(self) -> None:
        """Reset global metrics before each test."""
        self.metrics = QuotaMetrics()

    def test_record_usage(self) -> None:
        """Test recording usage metrics."""
        usage = ResourceUsage(turns=5, concurrent_tools=2)
        self.metrics.enable()
        self.metrics.record_usage("agent-1", usage)

        metrics = self.metrics.get_metrics()
        assert "agent-1" in metrics
        assert metrics["agent-1"]["turns"] == 5.0
        assert metrics["agent-1"]["concurrent_tools"] == 2.0

    def test_record_usage_disabled(self) -> None:
        """Test that recording is no-op when disabled."""
        usage = ResourceUsage(turns=5)
        self.metrics.disable()
        self.metrics.record_usage("agent-1", usage)

        assert "agent-1" not in self.metrics.get_metrics()

    def test_clear_metrics(self) -> None:
        """Test clearing metrics."""
        usage = ResourceUsage(turns=5)
        self.metrics.enable()
        self.metrics.record_usage("agent-1", usage)
        self.metrics.clear()

        assert self.metrics.get_metrics() == {}

    def test_get_quota_metrics_singleton(self) -> None:
        """Test getting global metrics singleton."""
        metrics1 = get_quota_metrics()
        metrics2 = get_quota_metrics()

        assert metrics1 is metrics2


class TestThreadSafety:
    """Tests for thread safety of ResourceQuotaManager."""

    def setup_method(self) -> None:
        """Reset global manager before each test."""
        reset_global_quota_manager()

    def teardown_method(self) -> None:
        """Clean up after each test."""
        reset_global_quota_manager()

    def test_concurrent_allocation(self) -> None:
        """Test concurrent allocation from multiple threads."""
        manager = ResourceQuotaManager()
        errors: list[Exception] = []
        success_count = [0]  # Use list for mutability in threads
        lock = threading.Lock()

        def allocate_agent(agent_id: str) -> None:
            try:
                manager.allocate(agent_id)
                with lock:
                    success_count[0] += 1
            except (RuntimeError, ValueError) as e:
                with lock:
                    errors.append(e)

        threads = [threading.Thread(target=allocate_agent, args=(f"agent-{i}",)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0
        assert success_count[0] == 10
        assert len(manager.get_all_agents()) == 10

    def test_concurrent_tool_tracking(self) -> None:
        """Test concurrent tool acquire/release."""
        manager = ResourceQuotaManager()
        manager.allocate("agent-1")

        acquired_count = 0
        released_count = 0
        lock = threading.Lock()

        def acquire_and_release() -> None:
            nonlocal acquired_count, released_count
            if manager.acquire_concurrent_tool("agent-1"):
                with lock:
                    acquired_count += 1
                time.sleep(0.001)  # Small delay
                manager.release_concurrent_tool("agent-1")
                with lock:
                    released_count += 1

        threads = [threading.Thread(target=acquire_and_release) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert acquired_count == 5
        assert released_count == 5
        # Final count should be 0
        assert manager.get_usage("agent-1").concurrent_tools == 0
