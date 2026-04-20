from __future__ import annotations

import asyncio
import time
from contextlib import suppress
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class ReplicaStatus(str, Enum):
    PRIMARY = "primary"
    SECONDARY = "secondary"
    STANDBY = "standby"
    FAILED = "failed"


class HeartbeatStatus(str, Enum):
    ALIVE = "alive"
    SUSPECTED = "suspected"
    DEAD = "dead"


@dataclass(frozen=True)
class ReplicaInfo:
    """Information about a replica."""

    replica_id: str
    status: ReplicaStatus
    last_heartbeat: str
    health_score: float = 1.0
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class FailoverResult:
    """Result of a failover operation."""

    success: bool
    old_primary: str | None
    new_primary: str | None
    switchover_time_ms: float
    data_loss_ms: float
    error: str | None = None


class DisasterRecoveryManager:
    """Manages multi-replica disaster recovery."""

    def __init__(
        self,
        replica_id: str,
        heartbeat_interval_ms: float = 1000.0,
        heartbeat_timeout_ms: float = 5000.0,
        election_timeout_ms: float = 3000.0,
    ) -> None:
        self._replica_id = replica_id
        self._heartbeat_interval = heartbeat_interval_ms / 1000.0
        self._heartbeat_timeout = heartbeat_timeout_ms / 1000.0
        self._election_timeout = election_timeout_ms / 1000.0
        self._replicas: dict[str, ReplicaInfo] = {}
        self._primary: str | None = None
        self._status: ReplicaStatus = ReplicaStatus.STANDBY
        self._running = False
        self._heartbeat_task: asyncio.Task[None] | None = None
        self._lock = asyncio.Lock()

    async def start(self) -> None:
        """Start the disaster recovery manager."""
        async with self._lock:
            self._running = True
            self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())

    async def stop(self) -> None:
        """Stop the disaster recovery manager."""
        async with self._lock:
            self._running = False
            if self._heartbeat_task:
                self._heartbeat_task.cancel()
                with suppress(asyncio.CancelledError):
                    await self._heartbeat_task
                self._heartbeat_task = None

    async def _heartbeat_loop(self) -> None:
        """Internal heartbeat loop."""
        while self._running:
            try:
                await asyncio.sleep(self._heartbeat_interval)
                await self.send_heartbeat()
            except asyncio.CancelledError:
                break
            except (RuntimeError, ValueError):
                # Heartbeat loop should not crash the manager
                pass

    async def register_replica(self, replica: ReplicaInfo) -> None:
        """Register a new replica."""
        async with self._lock:
            self._replicas[replica.replica_id] = replica
            # If this is the first replica and we have no primary, make it primary
            if self._primary is None and replica.status == ReplicaStatus.PRIMARY:
                self._primary = replica.replica_id

    async def unregister_replica(self, replica_id: str) -> None:
        """Unregister a replica."""
        async with self._lock:
            if replica_id in self._replicas:
                del self._replicas[replica_id]
            if self._primary == replica_id:
                self._primary = None

    async def send_heartbeat(self) -> None:
        """Send heartbeat to other replicas."""
        current_time = time.time()
        async with self._lock:
            # Update own heartbeat entry
            own_info = self._replicas.get(self._replica_id)
            if own_info:
                updated = ReplicaInfo(
                    replica_id=self._replica_id,
                    status=own_info.status,
                    last_heartbeat=str(current_time),
                    health_score=own_info.health_score,
                    metadata=own_info.metadata,
                )
                self._replicas[self._replica_id] = updated
            else:
                # Register self if not registered
                new_replica = ReplicaInfo(
                    replica_id=self._replica_id,
                    status=self._status,
                    last_heartbeat=str(current_time),
                    health_score=1.0,
                    metadata={},
                )
                self._replicas[self._replica_id] = new_replica

            # Check other replicas for timeout
            for replica_id, replica_info in list(self._replicas.items()):
                if replica_id == self._replica_id:
                    continue
                last_hb = float(replica_info.last_heartbeat)
                if current_time - last_hb > self._heartbeat_timeout:
                    # Mark replica as dead
                    self._replicas[replica_id] = ReplicaInfo(
                        replica_id=replica_id,
                        status=ReplicaStatus.FAILED,
                        last_heartbeat=replica_info.last_heartbeat,
                        health_score=0.0,
                        metadata=replica_info.metadata,
                    )

    async def trigger_failover(self) -> FailoverResult:
        """Trigger failover to a new primary."""
        start_time = time.time()
        async with self._lock:
            old_primary = self._primary

            # Find the best candidate for new primary
            candidates = [
                (rid, info)
                for rid, info in self._replicas.items()
                if info.status != ReplicaStatus.FAILED and rid != old_primary
            ]

            if not candidates:
                return FailoverResult(
                    success=False,
                    old_primary=old_primary,
                    new_primary=None,
                    switchover_time_ms=(time.time() - start_time) * 1000,
                    data_loss_ms=0.0,
                    error="No eligible replicas for failover",
                )

            # Select candidate with highest health score
            candidates.sort(key=lambda x: x[1].health_score, reverse=True)
            new_primary_id = candidates[0][0]

            # Update replica statuses
            if old_primary and old_primary in self._replicas:
                old_info = self._replicas[old_primary]
                self._replicas[old_primary] = ReplicaInfo(
                    replica_id=old_primary,
                    status=ReplicaStatus.SECONDARY,
                    last_heartbeat=old_info.last_heartbeat,
                    health_score=old_info.health_score,
                    metadata=old_info.metadata,
                )

            new_info = self._replicas[new_primary_id]
            self._replicas[new_primary_id] = ReplicaInfo(
                replica_id=new_primary_id,
                status=ReplicaStatus.PRIMARY,
                last_heartbeat=new_info.last_heartbeat,
                health_score=new_info.health_score,
                metadata=new_info.metadata,
            )

            self._primary = new_primary_id
            self._status = ReplicaStatus.PRIMARY

            switchover_time_ms = (time.time() - start_time) * 1000
            # Estimate data loss based on last heartbeat
            data_loss_ms = 0.0
            if new_info.last_heartbeat:
                last_hb = float(new_info.last_heartbeat)
                data_loss_ms = max(0.0, (time.time() - last_hb) * 1000)

            return FailoverResult(
                success=True,
                old_primary=old_primary,
                new_primary=new_primary_id,
                switchover_time_ms=switchover_time_ms,
                data_loss_ms=data_loss_ms,
            )

    def get_replica_status(self, replica_id: str) -> ReplicaInfo | None:
        """Get status of a specific replica."""
        return self._replicas.get(replica_id)

    def get_all_replicas(self) -> dict[str, ReplicaInfo]:
        """Get status of all replicas."""
        return dict(self._replicas)
