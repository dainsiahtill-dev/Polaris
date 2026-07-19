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
