"""Tests for ``internal/lease_manager.py``."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from polaris.cells.runtime.task_market.internal.errors import (
    StaleLeaseTokenError,
    TaskNotClaimableError,
)
from polaris.cells.runtime.task_market.internal.lease_manager import LeaseManager
from polaris.cells.runtime.task_market.internal.models import TaskWorkItemRecord


class TestLeaseManager:
    """Unit tests for LeaseManager."""

    @pytest.fixture
    def mock_store(self) -> MagicMock:
        return MagicMock()

    @pytest.fixture
    def item(self) -> TaskWorkItemRecord:
        return TaskWorkItemRecord(
            task_id="task-1",
            trace_id="trace-1",
            run_id="run-1",
            workspace="/tmp/ws",
            stage="pending_exec",
            status="pending_exec",
            priority="medium",
            payload={},
            metadata={},
            version=1,
            attempts=0,
            max_attempts=3,
            lease_token="",
            lease_expires_at=0.0,
            claimed_by="",
            claimed_role="",
            last_error={},
        )

    @pytest.fixture
    def lm(self, mock_store: MagicMock) -> LeaseManager:
        return LeaseManager(mock_store)

    # ---- grant_lease --------------------------------------------------------

    def test_grant_lease_generates_token(self, lm: LeaseManager, item: TaskWorkItemRecord) -> None:
        token, expires_at = lm.grant_lease(
            item=item,
            worker_id="dir-1",
            worker_role="director",
            visibility_timeout_seconds=900,
        )
        assert isinstance(token, str)
        assert len(token) == 32
        assert expires_at > 0

    def test_grant_lease_updates_item(self, lm: LeaseManager, item: TaskWorkItemRecord) -> None:
        lm.grant_lease(
            item=item,
            worker_id="dir-1",
            worker_role="director",
            visibility_timeout_seconds=900,
        )
        assert item.lease_token != ""
        assert item.claimed_by == "dir-1"
        assert item.claimed_role == "director"
        assert item.status == "in_execution"
        assert item.attempts == 1

    def test_grant_lease_increments_attempts(self, lm: LeaseManager, item: TaskWorkItemRecord) -> None:
        item.attempts = 2
        lm.grant_lease(
            item=item,
            worker_id="dir-1",
            worker_role="director",
            visibility_timeout_seconds=60,
        )
        assert item.attempts == 3

    def test_grant_lease_raises_if_not_claimable(self, lm: LeaseManager, item: TaskWorkItemRecord) -> None:
        # Set a non-expired lease so is_claimable returns False.
        from polaris.cells.runtime.task_market.internal.models import now_epoch

        item.lease_token = "valid-lease"
        item.lease_expires_at = now_epoch() + 3600  # expires in 1 hour
        with pytest.raises(TaskNotClaimableError):
            lm.grant_lease(
                item=item,
                worker_id="dir-1",
                worker_role="director",
            )

    # ---- renew_lease --------------------------------------------------------

    def test_renew_lease_success(self, lm: LeaseManager, item: TaskWorkItemRecord) -> None:
        item.lease_token = "valid-token"
        item.lease_expires_at = 1000.0
        ok, expires_at = lm.renew_lease(
            item=item,
            lease_token="valid-token",
            visibility_timeout_seconds=300,
        )
        assert ok is True
        assert expires_at > 1000.0

    def test_renew_lease_fails_on_mismatch(self, lm: LeaseManager, item: TaskWorkItemRecord) -> None:
        item.lease_token = "valid-token"
        with pytest.raises(StaleLeaseTokenError):
            lm.renew_lease(
                item=item,
                lease_token="wrong-token",
            )

    def test_renew_lease_fails_on_empty_token(self, lm: LeaseManager, item: TaskWorkItemRecord) -> None:
        item.lease_token = ""
        with pytest.raises(StaleLeaseTokenError):
            lm.renew_lease(
                item=item,
                lease_token="some-token",
            )

    # ---- validate_token -----------------------------------------------------

    def test_validate_token_raises_on_mismatch(self, lm: LeaseManager, item: TaskWorkItemRecord) -> None:
        item.lease_token = "correct-token"
        with pytest.raises(StaleLeaseTokenError):
            lm.validate_token(item, "wrong-token")

    def test_validate_token_raises_on_empty(self, lm: LeaseManager, item: TaskWorkItemRecord) -> None:
        item.lease_token = ""
        with pytest.raises(StaleLeaseTokenError):
            lm.validate_token(item, "some-token")

    # ---- is_lease_expired --------------------------------------------------

    def test_is_lease_expired_true_when_no_token(self, lm: LeaseManager, item: TaskWorkItemRecord) -> None:
        item.lease_token = ""
        assert lm.is_lease_expired(item) is True

    def test_is_lease_expired_false_when_in_future(self, lm: LeaseManager, item: TaskWorkItemRecord) -> None:
        import time

        item.lease_token = "token"
        item.lease_expires_at = time.time() + 3600
        assert lm.is_lease_expired(item) is False

    def test_is_lease_expired_true_when_in_past(self, lm: LeaseManager, item: TaskWorkItemRecord) -> None:
        item.lease_token = "token"
        item.lease_expires_at = 0.0
        assert lm.is_lease_expired(item) is True

    # ---- clear_lease --------------------------------------------------------

    def test_clear_lease_resets_all_fields(self, lm: LeaseManager, item: TaskWorkItemRecord) -> None:
        item.lease_token = "token"
        item.lease_expires_at = 999.0
        item.claimed_by = "worker-1"
        item.claimed_role = "director"

        lm.clear_lease(item)

        assert item.lease_token == ""
        assert item.lease_expires_at == 0.0
        assert item.claimed_by == ""
        assert item.claimed_role == ""

    # ---- is_claimable (delegation) ----------------------------------------

    def test_is_claimable_delegates_to_item(self, lm: LeaseManager, item: TaskWorkItemRecord) -> None:
        import time

        item.lease_token = ""
        item.status = "pending_exec"
        assert lm.is_claimable(item, at_epoch=time.time()) is True
