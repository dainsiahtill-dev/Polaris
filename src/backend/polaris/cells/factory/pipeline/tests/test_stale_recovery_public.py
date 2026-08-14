"""Public contract tests for explicit Factory stale-owner recovery."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from polaris.cells.factory.pipeline.internal.factory_run_admission import (
    FactoryWorkspaceRunAdmission,
)
from polaris.cells.factory.pipeline.public import (
    FactoryConfig,
    FactoryPipelineError,
    FactoryRunService,
    FactoryWorkspaceRunLeaseStateV1,
    RecoverStaleFactoryWorkspaceOwnerCommandV1,
    RecoverStaleFactoryWorkspaceOwnerResultV1,
    recover_stale_factory_workspace_owner,
)


class _MutableClock:
    def __init__(self) -> None:
        self.value = datetime(2026, 7, 13, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += timedelta(seconds=seconds)


async def _create_service(
    tmp_path: Path,
    *,
    stale: bool,
    name: str = "workspace",
) -> tuple[FactoryRunService, FactoryWorkspaceRunAdmission, _MutableClock, str, int]:
    workspace = tmp_path / name
    workspace.mkdir()
    runtime_root = tmp_path / f"runtime-{name}"
    clock = _MutableClock()
    admission = FactoryWorkspaceRunAdmission(
        workspace,
        state_root=runtime_root / "factory",
        lease_ttl_seconds=10,
        clock=clock,
    )
    service = FactoryRunService(
        workspace,
        cache_root=runtime_root,
        admission=admission,
    )
    run = await service.create_run(FactoryConfig(name=f"stale-owner-{name}"))
    await service.start_run(run.id)
    lease = admission.current()
    assert lease is not None
    if stale:
        clock.advance(11)
    return service, admission, clock, run.id, lease.fencing_token


def _command(workspace: Path, run_id: str, fencing_token: int) -> RecoverStaleFactoryWorkspaceOwnerCommandV1:
    return RecoverStaleFactoryWorkspaceOwnerCommandV1(
        workspace=str(workspace.resolve()),
        run_id=run_id,
        expected_fencing_token=fencing_token,
        reason="owner process disappeared",
    )


@pytest.mark.asyncio
async def test_public_recovery_returns_typed_released_lease(tmp_path: Path) -> None:
    service, admission, _, run_id, fencing_token = await _create_service(tmp_path, stale=True)
    command = _command(service.workspace, run_id, fencing_token)

    result = await recover_stale_factory_workspace_owner(
        command,
        service_factory=lambda _workspace: service,
    )

    assert isinstance(result, RecoverStaleFactoryWorkspaceOwnerResultV1)
    assert result.workspace == str(service.workspace.resolve())
    assert result.run_id == run_id
    assert result.expected_fencing_token == fencing_token
    assert result.lease.state is FactoryWorkspaceRunLeaseStateV1.RELEASED
    assert result.to_dict()["lease"]["release_evidence"]["source"] == "factory_stale_owner_recovery"
    assert admission.current() == result.lease


@pytest.mark.asyncio
async def test_public_recovery_accepts_single_restart_replay_fence_increment(tmp_path: Path) -> None:
    """A restarted service may fence the stale token before releasing it."""

    service, admission, _, run_id, fencing_token = await _create_service(tmp_path, stale=True)
    service._physical_attempt_coordinators.clear()

    result = await recover_stale_factory_workspace_owner(
        _command(service.workspace, run_id, fencing_token),
        service_factory=lambda _workspace: service,
    )

    assert result.expected_fencing_token == fencing_token
    assert result.lease.fencing_token == fencing_token + 1
    assert result.lease.state is FactoryWorkspaceRunLeaseStateV1.RELEASED
    assert admission.current() == result.lease


@pytest.mark.asyncio
async def test_public_recovery_preserves_wrong_fencing_token_error(tmp_path: Path) -> None:
    service, _, _, run_id, fencing_token = await _create_service(tmp_path, stale=True)

    with pytest.raises(FactoryPipelineError) as error:
        await recover_stale_factory_workspace_owner(
            _command(service.workspace, run_id, fencing_token + 1),
            service_factory=lambda _workspace: service,
        )

    assert error.value.code == "factory_workspace_run_fenced"


@pytest.mark.asyncio
async def test_public_recovery_rejects_non_stale_owner(tmp_path: Path) -> None:
    service, _, _, run_id, fencing_token = await _create_service(tmp_path, stale=False)

    with pytest.raises(FactoryPipelineError) as error:
        await recover_stale_factory_workspace_owner(
            _command(service.workspace, run_id, fencing_token),
            service_factory=lambda _workspace: service,
        )

    assert error.value.code == "factory_workspace_run_owner_not_stale"


@pytest.mark.asyncio
async def test_public_recovery_retry_is_fail_closed_and_does_not_mutate_again(tmp_path: Path) -> None:
    service, admission, _, run_id, fencing_token = await _create_service(tmp_path, stale=True)
    command = _command(service.workspace, run_id, fencing_token)
    first = await recover_stale_factory_workspace_owner(
        command,
        service_factory=lambda _workspace: service,
    )

    with pytest.raises(FactoryPipelineError) as error:
        await recover_stale_factory_workspace_owner(
            command,
            service_factory=lambda _workspace: service,
        )

    assert error.value.code == "factory_workspace_run_owner_not_stale"
    assert admission.current() == first.lease


@pytest.mark.asyncio
async def test_concurrent_public_recovery_has_one_authoritative_release(tmp_path: Path) -> None:
    service, admission, _, run_id, fencing_token = await _create_service(tmp_path, stale=True)
    command = _command(service.workspace, run_id, fencing_token)

    outcomes = await asyncio.gather(
        recover_stale_factory_workspace_owner(command, service_factory=lambda _workspace: service),
        recover_stale_factory_workspace_owner(command, service_factory=lambda _workspace: service),
        return_exceptions=True,
    )

    successes = [outcome for outcome in outcomes if isinstance(outcome, RecoverStaleFactoryWorkspaceOwnerResultV1)]
    failures = [outcome for outcome in outcomes if isinstance(outcome, FactoryPipelineError)]
    assert len(successes) == 1
    assert len(failures) == 1
    assert failures[0].code == "factory_workspace_run_owner_not_stale"
    current = admission.current()
    assert current is not None
    assert current == successes[0].lease
    assert current.state is FactoryWorkspaceRunLeaseStateV1.RELEASED


@pytest.mark.asyncio
async def test_public_recovery_rejects_service_workspace_mismatch(tmp_path: Path) -> None:
    requested, _, _, run_id, fencing_token = await _create_service(
        tmp_path,
        stale=True,
        name="requested",
    )
    wrong_service, wrong_admission, _, _, _ = await _create_service(
        tmp_path,
        stale=True,
        name="wrong",
    )

    with pytest.raises(FactoryPipelineError) as error:
        await recover_stale_factory_workspace_owner(
            _command(requested.workspace, run_id, fencing_token),
            service_factory=lambda _workspace: wrong_service,
        )

    assert error.value.code == "factory_workspace_binding_mismatch"
    wrong_lease = wrong_admission.current()
    assert wrong_lease is not None
    assert wrong_lease.state is FactoryWorkspaceRunLeaseStateV1.ACTIVE


@pytest.mark.parametrize(
    "overrides",
    [
        {"workspace": ""},
        {"run_id": ""},
        {"expected_fencing_token": 0},
        {"expected_fencing_token": True},
        {"reason": ""},
        {"reason": "x" * 513},
    ],
)
def test_recovery_command_rejects_invalid_authority(overrides: dict[str, object]) -> None:
    values: dict[str, object] = {
        "workspace": "/tmp/workspace",
        "run_id": "factory_run",
        "expected_fencing_token": 1,
        "reason": "operator request",
    }
    values.update(overrides)

    with pytest.raises((TypeError, ValueError)):
        RecoverStaleFactoryWorkspaceOwnerCommandV1(**values)  # type: ignore[arg-type]
