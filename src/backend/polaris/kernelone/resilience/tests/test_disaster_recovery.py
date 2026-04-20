from __future__ import annotations

import asyncio
import dataclasses
import time

import pytest
from polaris.kernelone.resilience.disaster_recovery import (
    DisasterRecoveryManager,
    FailoverResult,
    ReplicaInfo,
    ReplicaStatus,
)


@pytest.fixture
def dr_manager() -> DisasterRecoveryManager:
    """Create a disaster recovery manager for testing."""
    return DisasterRecoveryManager(
        replica_id="test-replica-1",
        heartbeat_interval_ms=100.0,
        heartbeat_timeout_ms=500.0,
        election_timeout_ms=300.0,
    )


@pytest.fixture
def dr_manager_two_replicas() -> tuple[DisasterRecoveryManager, DisasterRecoveryManager]:
    """Create two disaster recovery managers for testing."""
    manager1 = DisasterRecoveryManager(
        replica_id="replica-1",
        heartbeat_interval_ms=100.0,
        heartbeat_timeout_ms=500.0,
        election_timeout_ms=300.0,
    )
    manager2 = DisasterRecoveryManager(
        replica_id="replica-2",
        heartbeat_interval_ms=100.0,
        heartbeat_timeout_ms=500.0,
        election_timeout_ms=300.0,
    )
    return manager1, manager2


class TestReplicaRegistration:
    """Test replica registration functionality."""

    @pytest.mark.asyncio
    async def test_register_single_replica(self, dr_manager: DisasterRecoveryManager) -> None:
        """Test registering a single replica."""
        replica_info = ReplicaInfo(
            replica_id="replica-1",
            status=ReplicaStatus.PRIMARY,
            last_heartbeat=str(time.time()),
            health_score=1.0,
            metadata={},
        )

        await dr_manager.register_replica(replica_info)

        result = dr_manager.get_replica_status("replica-1")
        assert result is not None
        assert result.replica_id == "replica-1"
        assert result.status == ReplicaStatus.PRIMARY

    @pytest.mark.asyncio
    async def test_register_multiple_replicas(self, dr_manager: DisasterRecoveryManager) -> None:
        """Test registering multiple replicas."""
        replicas = [
            ReplicaInfo(
                replica_id=f"replica-{i}",
                status=ReplicaStatus.SECONDARY,
                last_heartbeat=str(time.time()),
                health_score=1.0,
                metadata={},
            )
            for i in range(3)
        ]

        for replica in replicas:
            await dr_manager.register_replica(replica)

        all_replicas = dr_manager.get_all_replicas()
        assert len(all_replicas) == 3

    @pytest.mark.asyncio
    async def test_unregister_replica(self, dr_manager: DisasterRecoveryManager) -> None:
        """Test unregistering a replica."""
        replica_info = ReplicaInfo(
            replica_id="replica-1",
            status=ReplicaStatus.PRIMARY,
            last_heartbeat=str(time.time()),
            health_score=1.0,
            metadata={},
        )

        await dr_manager.register_replica(replica_info)
        await dr_manager.unregister_replica("replica-1")

        result = dr_manager.get_replica_status("replica-1")
        assert result is None

    @pytest.mark.asyncio
    async def test_unregister_nonexistent_replica(self, dr_manager: DisasterRecoveryManager) -> None:
        """Test unregistering a replica that doesn't exist."""
        # Should not raise an error
        await dr_manager.unregister_replica("nonexistent")
        assert dr_manager.get_all_replicas() == {}


class TestHeartbeatMechanism:
    """Test heartbeat mechanism functionality."""

    @pytest.mark.asyncio
    async def test_send_heartbeat_creates_self_entry(self, dr_manager: DisasterRecoveryManager) -> None:
        """Test that sending heartbeat creates entry for self."""
        await dr_manager.send_heartbeat()

        status = dr_manager.get_replica_status("test-replica-1")
        assert status is not None
        assert status.replica_id == "test-replica-1"

    @pytest.mark.asyncio
    async def test_heartbeat_updates_timestamp(self, dr_manager: DisasterRecoveryManager) -> None:
        """Test that heartbeat updates the timestamp."""
        await dr_manager.send_heartbeat()
        first_status = dr_manager.get_replica_status("test-replica-1")
        assert first_status is not None
        first_heartbeat = first_status.last_heartbeat

        # Wait a bit and send another heartbeat
        await asyncio.sleep(0.01)
        await dr_manager.send_heartbeat()
        second_status = dr_manager.get_replica_status("test-replica-1")
        assert second_status is not None

        assert second_status.last_heartbeat >= first_heartbeat

    @pytest.mark.asyncio
    async def test_start_stop_manager(self, dr_manager: DisasterRecoveryManager) -> None:
        """Test starting and stopping the manager."""
        await dr_manager.start()
        assert dr_manager._running is True

        await dr_manager.stop()
        assert dr_manager._running is False

    @pytest.mark.asyncio
    async def test_heartbeat_loop_runs(self, dr_manager: DisasterRecoveryManager) -> None:
        """Test that heartbeat loop actually runs."""
        await dr_manager.start()
        await asyncio.sleep(0.25)  # Wait for a couple heartbeats

        status = dr_manager.get_replica_status("test-replica-1")
        assert status is not None

        await dr_manager.stop()


class TestFailoverTriggering:
    """Test failover triggering functionality."""

    @pytest.mark.asyncio
    async def test_trigger_failover_no_replicas(self, dr_manager: DisasterRecoveryManager) -> None:
        """Test triggering failover with no registered replicas."""
        result = await dr_manager.trigger_failover()

        assert result.success is False
        assert result.error == "No eligible replicas for failover"

    @pytest.mark.asyncio
    async def test_trigger_failover_with_candidates(self, dr_manager: DisasterRecoveryManager) -> None:
        """Test triggering failover with eligible candidates."""
        # Register a secondary replica
        replica_info = ReplicaInfo(
            replica_id="replica-2",
            status=ReplicaStatus.SECONDARY,
            last_heartbeat=str(time.time()),
            health_score=1.0,
            metadata={},
        )
        await dr_manager.register_replica(replica_info)

        # Set this manager's primary
        dr_manager._primary = dr_manager._replica_id
        dr_manager._status = ReplicaStatus.PRIMARY

        # Trigger failover
        result = await dr_manager.trigger_failover()

        assert result.success is True
        assert result.old_primary == dr_manager._replica_id
        assert result.new_primary == "replica-2"

    @pytest.mark.asyncio
    async def test_failover_selects_highest_health_score(self, dr_manager: DisasterRecoveryManager) -> None:
        """Test that failover selects replica with highest health score."""
        # Register replicas with different health scores
        replicas = [
            ReplicaInfo(
                replica_id="low-health",
                status=ReplicaStatus.SECONDARY,
                last_heartbeat=str(time.time()),
                health_score=0.3,
                metadata={},
            ),
            ReplicaInfo(
                replica_id="high-health",
                status=ReplicaStatus.SECONDARY,
                last_heartbeat=str(time.time()),
                health_score=0.9,
                metadata={},
            ),
        ]

        for replica in replicas:
            await dr_manager.register_replica(replica)

        dr_manager._primary = "test-replica-1"
        dr_manager._status = ReplicaStatus.PRIMARY

        result = await dr_manager.trigger_failover()

        assert result.success is True
        assert result.new_primary == "high-health"


class TestReplicaStatusTracking:
    """Test replica status tracking functionality."""

    def test_get_replica_status_existing(self, dr_manager: DisasterRecoveryManager) -> None:
        """Test getting status of an existing replica."""
        dr_manager._replicas["test-replica-1"] = ReplicaInfo(
            replica_id="test-replica-1",
            status=ReplicaStatus.STANDBY,
            last_heartbeat=str(time.time()),
            health_score=1.0,
            metadata={},
        )

        result = dr_manager.get_replica_status("test-replica-1")
        assert result is not None
        assert result.status == ReplicaStatus.STANDBY

    def test_get_replica_status_nonexistent(self, dr_manager: DisasterRecoveryManager) -> None:
        """Test getting status of a nonexistent replica."""
        result = dr_manager.get_replica_status("nonexistent")
        assert result is None

    def test_get_all_replicas(self, dr_manager: DisasterRecoveryManager) -> None:
        """Test getting all replicas."""
        dr_manager._replicas["replica-1"] = ReplicaInfo(
            replica_id="replica-1",
            status=ReplicaStatus.PRIMARY,
            last_heartbeat=str(time.time()),
            health_score=1.0,
            metadata={},
        )
        dr_manager._replicas["replica-2"] = ReplicaInfo(
            replica_id="replica-2",
            status=ReplicaStatus.SECONDARY,
            last_heartbeat=str(time.time()),
            health_score=0.8,
            metadata={},
        )

        all_replicas = dr_manager.get_all_replicas()
        assert len(all_replicas) == 2
        assert "replica-1" in all_replicas
        assert "replica-2" in all_replicas

    def test_replica_info_immutable(self) -> None:
        """Test that ReplicaInfo is immutable."""
        replica_info = ReplicaInfo(
            replica_id="test",
            status=ReplicaStatus.PRIMARY,
            last_heartbeat=str(time.time()),
            health_score=1.0,
            metadata={},
        )

        with pytest.raises(dataclasses.FrozenInstanceError):
            replica_info.replica_id = "changed"  # type: ignore

    def test_failover_result_immutable(self) -> None:
        """Test that FailoverResult is immutable."""
        result = FailoverResult(
            success=True,
            old_primary="old",
            new_primary="new",
            switchover_time_ms=100.0,
            data_loss_ms=50.0,
        )

        with pytest.raises(dataclasses.FrozenInstanceError):
            result.success = False  # type: ignore
