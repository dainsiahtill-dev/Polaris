"""Lease manager for ``runtime.task_market`` — handles grant / renew / validate."""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from .errors import StaleLeaseTokenError, TaskNotClaimableError
from .models import TaskWorkItemRecord, now_epoch, now_iso

if TYPE_CHECKING:
    from .store import TaskMarketStore


class LeaseManager:
    """Manages lease lifecycle for task market work items.

    A lease gives a worker exclusive access to a task for a configurable
    ``visibility_timeout``.  The worker must periodically call ``renew`` to
    keep the lease alive; if the lease expires before renewal the task
    becomes visible again to other workers.
    """

    __slots__ = ("_store",)

    def __init__(self, store: TaskMarketStore) -> None:
        self._store = store

    def grant_lease(
        self,
        item: TaskWorkItemRecord,
        worker_id: str,
        worker_role: str,
        visibility_timeout_seconds: int = 900,
    ) -> tuple[str, float]:
        """Atomically grant a lease on ``item``.

        Returns:
            A tuple of (lease_token, lease_expires_at_epoch).

        Raises:
            TaskNotClaimableError: if the item is not currently claimable.
        """
        if not item.is_claimable(item.stage, now_epoch()):
            raise TaskNotClaimableError(
                f"Task {item.task_id} is not claimable",
                task_id=item.task_id,
                reason=f"status={item.status} stage={item.stage}",
            )

        lease_token = uuid.uuid4().hex
        lease_expires_at = now_epoch() + float(visibility_timeout_seconds)

        item.lease_token = lease_token
        item.lease_expires_at = lease_expires_at
        item.claimed_by = worker_id
        item.claimed_role = worker_role
        item.attempts += 1
        item.status = item.active_status()
        item.version += 1
        item.updated_at = now_iso()

        return lease_token, lease_expires_at

    def renew_lease(
        self,
        item: TaskWorkItemRecord,
        lease_token: str,
        visibility_timeout_seconds: int = 900,
    ) -> tuple[bool, float]:
        """Renew an existing lease.

        Returns:
            (True, new_expires_at) on success.
            (False, 0.0) if the token does not match.

        Raises:
            StaleLeaseTokenError: if the presented token does not match.
        """
        if item.lease_token != lease_token:
            raise StaleLeaseTokenError(
                f"Lease token mismatch for task {item.task_id}",
                task_id=item.task_id,
                presented_token=lease_token[:8] + "...",
                current_token=item.lease_token[:8] + "..." if item.lease_token else "(empty)",
            )

        new_expires_at = now_epoch() + float(visibility_timeout_seconds)
        item.lease_expires_at = new_expires_at
        item.version += 1
        item.updated_at = now_iso()

        return True, new_expires_at

    def validate_token(self, item: TaskWorkItemRecord, lease_token: str) -> None:
        """Validate that ``lease_token`` matches the current holder and is not expired.

        Raises:
            StaleLeaseTokenError: if the token is stale, missing, or expired.
        """
        if not item.lease_token or item.lease_token != lease_token:
            raise StaleLeaseTokenError(
                f"Stale lease token for task {item.task_id}",
                task_id=item.task_id,
                presented_token=lease_token[:8] + "..." if lease_token else "(empty)",
                current_token=item.lease_token[:8] + "..." if item.lease_token else "(empty)",
            )
        if self.is_lease_expired(item):
            raise StaleLeaseTokenError(
                f"Lease expired for task {item.task_id}",
                task_id=item.task_id,
                presented_token=lease_token[:8] + "..." if lease_token else "(empty)",
                current_token=item.lease_token[:8] + "..." if item.lease_token else "(empty)",
            )

    def is_lease_expired(self, item: TaskWorkItemRecord, *, at_epoch: float | None = None) -> bool:
        """Return True if the lease has expired (or was never held)."""
        if not item.lease_token:
            return True
        return item.lease_expires_at <= (at_epoch if at_epoch is not None else now_epoch())

    def clear_lease(self, item: TaskWorkItemRecord) -> None:
        """Clear all lease fields on an item."""
        item.lease_token = ""
        item.lease_expires_at = 0.0
        item.claimed_by = ""
        item.claimed_role = ""

    def is_claimable(self, item: TaskWorkItemRecord, *, at_epoch: float | None = None) -> bool:
        """Return True if the item can be claimed at the given epoch."""
        return item.is_claimable(item.stage, at_epoch if at_epoch is not None else now_epoch())


__all__ = ["LeaseManager"]
