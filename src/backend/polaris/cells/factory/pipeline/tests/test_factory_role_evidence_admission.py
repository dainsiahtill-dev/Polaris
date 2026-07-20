"""A009B1 stable stage-claim authority hold tests."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from polaris.cells.factory.pipeline.internal.factory_run_admission import FactoryWorkspaceRunAdmission
from polaris.cells.factory.pipeline.public.contracts import (
    FactoryStageExecutionClaimV1,
    FactoryWorkspaceReleaseEvidenceV1,
    FactoryWorkspaceRunLeaseConflictError,
    FactoryWorkspaceRunLeaseV1,
)


def _claimed(
    tmp_path: Path,
) -> tuple[FactoryWorkspaceRunAdmission, FactoryWorkspaceRunLeaseV1, FactoryStageExecutionClaimV1]:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True)
    admission = FactoryWorkspaceRunAdmission(workspace, state_root=tmp_path / "admission")
    lease = admission.acquire("factory-run-1")
    claimed = admission.claim_stage(
        "factory-run-1",
        fencing_token=lease.fencing_token,
        stage="director_dispatch",
        nonce="stage-nonce-1",
    )
    assert claimed.stage_execution_claim is not None
    return admission, claimed, claimed.stage_execution_claim


def test_hold_requires_exact_active_stage_claim(tmp_path: Path) -> None:
    admission, lease, claim = _claimed(tmp_path)

    with admission.hold_active_stage_claim(
        "factory-run-1",
        fencing_token=lease.fencing_token,
        stage=claim.stage,
        attempt=claim.attempt,
        nonce=claim.nonce,
    ) as revalidate:
        assert revalidate() == lease


def test_lifecycle_hold_requires_exact_active_claim(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True)
    admission = FactoryWorkspaceRunAdmission(workspace, state_root=tmp_path / "admission")
    claimed = admission.claim_lifecycle_operation(
        "factory-run-1",
        operation="recover_run",
        nonce="replay-nonce-1",
        acquire_if_available=True,
        expected_fencing_token=None,
    )
    assert claimed.lifecycle_operation_claim is not None
    claim = claimed.lifecycle_operation_claim

    with admission.hold_active_lifecycle_operation_claim(
        "factory-run-1",
        fencing_token=claimed.fencing_token,
        operation=claim.operation,
        sequence=claim.sequence,
        nonce=claim.nonce,
    ) as revalidate:
        assert revalidate() == claimed

    for overrides in (
        {"fencing_token": claimed.fencing_token + 1},
        {"operation": "retry_run_from_stage"},
        {"sequence": claim.sequence + 1},
        {"nonce": "wrong"},
    ):
        values = {
            "fencing_token": claimed.fencing_token,
            "operation": claim.operation,
            "sequence": claim.sequence,
            "nonce": claim.nonce,
        }
        values.update(overrides)
        with (
            pytest.raises(FactoryWorkspaceRunLeaseConflictError),
            admission.hold_active_lifecycle_operation_claim("factory-run-1", **values),
        ):
            raise AssertionError("unreachable")


def test_lifecycle_hold_revalidation_detects_expiry_without_renewal(tmp_path: Path) -> None:
    now = datetime(2026, 7, 20, tzinfo=timezone.utc)

    def clock() -> datetime:
        return now

    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True)
    admission = FactoryWorkspaceRunAdmission(
        workspace,
        state_root=tmp_path / "admission",
        lease_ttl_seconds=1,
        clock=clock,
    )
    claimed = admission.claim_lifecycle_operation(
        "factory-run-1",
        operation="recover_run",
        nonce="replay-nonce-1",
        acquire_if_available=True,
        expected_fencing_token=None,
    )
    assert claimed.lifecycle_operation_claim is not None
    claim = claimed.lifecycle_operation_claim

    with admission.hold_active_lifecycle_operation_claim(
        "factory-run-1",
        fencing_token=claimed.fencing_token,
        operation=claim.operation,
        sequence=claim.sequence,
        nonce=claim.nonce,
    ) as revalidate:
        assert revalidate().expires_at == claimed.expires_at
        now += timedelta(seconds=2)
        with pytest.raises(FactoryWorkspaceRunLeaseConflictError) as expired:
            revalidate()

    assert expired.value.code == "factory_workspace_run_lease_expired"
    durable = admission.current()
    assert durable is not None
    assert durable.expires_at == claimed.expires_at


def test_expired_owner_recovery_claim_and_hold_are_exact_and_read_only(tmp_path: Path) -> None:
    now = datetime(2026, 7, 20, tzinfo=timezone.utc)

    def clock() -> datetime:
        return now

    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True)
    admission = FactoryWorkspaceRunAdmission(
        workspace,
        state_root=tmp_path / "admission",
        lease_ttl_seconds=1,
        clock=clock,
    )
    owner = admission.acquire("factory-run-1")
    now += timedelta(seconds=2)

    claimed = admission.claim_lifecycle_operation(
        owner.run_id,
        operation="recover_stale_workspace_owner",
        nonce="stale-recovery-1",
        acquire_if_available=False,
        expected_fencing_token=owner.fencing_token,
        allow_expired_owner=True,
    )
    assert claimed.expires_at == owner.expires_at
    assert claimed.lifecycle_operation_claim is not None
    claim = claimed.lifecycle_operation_claim

    with admission.hold_active_lifecycle_operation_claim(
        owner.run_id,
        fencing_token=owner.fencing_token,
        operation=claim.operation,
        sequence=claim.sequence,
        nonce=claim.nonce,
        allow_expired_owner=True,
    ) as revalidate:
        assert revalidate() == claimed

    with (
        pytest.raises(FactoryWorkspaceRunLeaseConflictError) as wrong_nonce,
        admission.hold_active_lifecycle_operation_claim(
            owner.run_id,
            fencing_token=owner.fencing_token,
            operation=claim.operation,
            sequence=claim.sequence,
            nonce="wrong",
            allow_expired_owner=True,
        ),
    ):
        raise AssertionError("unreachable")
    assert wrong_nonce.value.code == "factory_lifecycle_operation_fenced"


def test_expired_owner_override_is_reserved_and_requires_actual_expiry(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True)
    admission = FactoryWorkspaceRunAdmission(workspace, state_root=tmp_path / "admission")
    owner = admission.acquire("factory-run-1")

    with pytest.raises(ValueError, match="reserved for stale-owner recovery"):
        admission.claim_lifecycle_operation(
            owner.run_id,
            operation="complete_run",
            nonce="not-recovery",
            acquire_if_available=False,
            expected_fencing_token=owner.fencing_token,
            allow_expired_owner=True,
        )
    with pytest.raises(FactoryWorkspaceRunLeaseConflictError) as active:
        admission.claim_lifecycle_operation(
            owner.run_id,
            operation="recover_stale_workspace_owner",
            nonce="premature-recovery",
            acquire_if_available=False,
            expected_fencing_token=owner.fencing_token,
            allow_expired_owner=True,
        )
    assert active.value.code == "factory_workspace_run_owner_not_stale"


def test_hold_revalidation_detects_expiry_without_renewing_lease(tmp_path: Path) -> None:
    now = datetime(2026, 7, 18, tzinfo=timezone.utc)

    def clock() -> datetime:
        return now

    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True)
    admission = FactoryWorkspaceRunAdmission(
        workspace,
        state_root=tmp_path / "admission",
        lease_ttl_seconds=1,
        clock=clock,
    )
    lease = admission.acquire("factory-run-1")
    claimed = admission.claim_stage(
        "factory-run-1",
        fencing_token=lease.fencing_token,
        stage="director_dispatch",
        nonce="stage-nonce-1",
    )
    assert claimed.stage_execution_claim is not None
    claim = claimed.stage_execution_claim

    with admission.hold_active_stage_claim(
        "factory-run-1",
        fencing_token=claimed.fencing_token,
        stage=claim.stage,
        attempt=claim.attempt,
        nonce=claim.nonce,
    ) as revalidate:
        assert revalidate().expires_at == claimed.expires_at
        now += timedelta(seconds=2)
        with pytest.raises(FactoryWorkspaceRunLeaseConflictError) as expired:
            revalidate()

    assert expired.value.code == "factory_workspace_run_lease_expired"
    assert admission.current() is not None
    assert admission.current().expires_at == claimed.expires_at  # type: ignore[union-attr]

    for overrides in (
        {"fencing_token": lease.fencing_token + 1},
        {"stage": "quality_gate"},
        {"attempt": claim.attempt + 1},
        {"nonce": "wrong"},
    ):
        values = {
            "fencing_token": lease.fencing_token,
            "stage": claim.stage,
            "attempt": claim.attempt,
            "nonce": claim.nonce,
        }
        values.update(overrides)
        with (
            pytest.raises(FactoryWorkspaceRunLeaseConflictError),
            admission.hold_active_stage_claim("factory-run-1", **values),
        ):
            raise AssertionError("unreachable")


def test_hold_rejects_draining_and_lifecycle_operation(tmp_path: Path) -> None:
    admission, lease, claim = _claimed(tmp_path / "draining")
    admission.begin_draining(
        "factory-run-1",
        fencing_token=lease.fencing_token,
        reason="test",
    )
    with (
        pytest.raises(FactoryWorkspaceRunLeaseConflictError, match="ACTIVE"),
        admission.hold_active_stage_claim(
            "factory-run-1",
            fencing_token=lease.fencing_token,
            stage=claim.stage,
            attempt=claim.attempt,
            nonce=claim.nonce,
        ),
    ):
        raise AssertionError("unreachable")

    other, other_lease, other_claim = _claimed(tmp_path / "lifecycle")
    other.claim_lifecycle_operation(
        "factory-run-1",
        operation="cancel",
        nonce="lifecycle-nonce",
        acquire_if_available=False,
        expected_fencing_token=other_lease.fencing_token,
    )
    with (
        pytest.raises(FactoryWorkspaceRunLeaseConflictError) as conflict,
        other.hold_active_stage_claim(
            "factory-run-1",
            fencing_token=other_lease.fencing_token,
            stage=other_claim.stage,
            attempt=other_claim.attempt,
            nonce=other_claim.nonce,
        ),
    ):
        raise AssertionError("unreachable")
    assert conflict.value.code == "factory_lifecycle_operation_inflight"


def test_hold_rejects_expired_and_released_lease(tmp_path: Path) -> None:
    now = datetime(2026, 7, 18, tzinfo=timezone.utc)

    def clock() -> datetime:
        return now

    workspace = tmp_path / "expired" / "workspace"
    workspace.mkdir(parents=True)
    expired = FactoryWorkspaceRunAdmission(
        workspace,
        state_root=tmp_path / "expired" / "admission",
        lease_ttl_seconds=1,
        clock=clock,
    )
    lease = expired.acquire("factory-run-1")
    claimed = expired.claim_stage(
        "factory-run-1",
        fencing_token=lease.fencing_token,
        stage="director_dispatch",
        nonce="stage-nonce-1",
    )
    assert claimed.stage_execution_claim is not None
    now += timedelta(seconds=2)
    with (
        pytest.raises(FactoryWorkspaceRunLeaseConflictError) as stale,
        expired.hold_active_stage_claim(
            "factory-run-1",
            fencing_token=claimed.fencing_token,
            stage=claimed.stage_execution_claim.stage,
            attempt=claimed.stage_execution_claim.attempt,
            nonce=claimed.stage_execution_claim.nonce,
        ),
    ):
        raise AssertionError("unreachable")
    assert stale.value.code == "factory_workspace_run_lease_expired"

    released, active, old_claim = _claimed(tmp_path / "released")
    released.release_stage(
        "factory-run-1",
        fencing_token=active.fencing_token,
        stage=old_claim.stage,
        nonce=old_claim.nonce,
    )
    released.begin_draining(
        "factory-run-1",
        fencing_token=active.fencing_token,
        reason="test",
    )
    released.release(
        "factory-run-1",
        fencing_token=active.fencing_token,
        settlement_evidence=FactoryWorkspaceReleaseEvidenceV1(
            factory_run_id="factory-run-1",
            source="test",
            observed_at="2026-07-18T00:00:00+00:00",
        ),
    )
    with (
        pytest.raises(FactoryWorkspaceRunLeaseConflictError) as inactive,
        released.hold_active_stage_claim(
            "factory-run-1",
            fencing_token=active.fencing_token,
            stage=old_claim.stage,
            attempt=old_claim.attempt,
            nonce=old_claim.nonce,
        ),
    ):
        raise AssertionError("unreachable")
    assert inactive.value.code == "factory_workspace_run_not_active"
