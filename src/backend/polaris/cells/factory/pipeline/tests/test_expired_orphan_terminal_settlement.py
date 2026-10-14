"""Expired terminal lifecycle claims retain exact, non-dispatching authority."""

from __future__ import annotations

import asyncio
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from polaris.cells.factory.pipeline.internal.factory_run_admission import FactoryWorkspaceRunAdmission
from polaris.cells.factory.pipeline.public import (
    FactoryConfig,
    FactoryPipelineError,
    FactoryRunService,
    FactoryRunStatus,
    RecoverStaleFactoryWorkspaceOwnerCommandV1,
    recover_stale_factory_workspace_owner,
)


class _Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 10, 14, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        return self.now


async def _orphan(
    tmp_path: Path, *, stage_claim: bool = False
) -> tuple[FactoryRunService, FactoryWorkspaceRunAdmission, str]:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    clock = _Clock()
    admission = FactoryWorkspaceRunAdmission(
        workspace, state_root=workspace / ".polaris/runtime/factory", clock=clock, lease_ttl_seconds=10
    )
    owner = FactoryRunService(workspace, admission=admission)
    run = await owner.create_run(FactoryConfig(name="expired-orphan", stages=["director_dispatch"]))
    run = await owner.start_run(run.id)
    if stage_claim:
        current = admission.current()
        assert current is not None
        admission.claim_stage(
            run.id, fencing_token=current.fencing_token, stage="director_dispatch", nonce="live-stage"
        )
    run.status = FactoryRunStatus.FAILED
    run.metadata["factory_stage_in_flight"] = False
    drain = owner._physical_attempt_coordinator(run.id).close()
    run.metadata["factory_physical_attempt_drain"] = asdict(drain)
    await owner.store.save_run(run)
    owner._claim_lifecycle_operation(
        run, operation="settle_terminal_run", nonce="exact-orphan-nonce", acquire_if_available=False
    )
    await owner._begin_terminal_drain(run, reason="factory_run_failed", operation_nonce="exact-orphan-nonce")
    clock.now += timedelta(seconds=11)
    restarted = FactoryRunService(workspace, admission=admission)
    return restarted, admission, run.id


@pytest.mark.asyncio
async def test_public_recovery_resumes_expired_exact_terminal_claim(tmp_path: Path) -> None:
    service, admission, run_id = await _orphan(tmp_path)
    before = admission.current()
    assert before is not None
    result = await recover_stale_factory_workspace_owner(
        RecoverStaleFactoryWorkspaceOwnerCommandV1(
            workspace=str(service.workspace),
            run_id=run_id,
            expected_fencing_token=before.fencing_token,
            reason="resume exact failed terminal drain",
        ),
        service_factory=lambda _: service,
    )
    assert result.lease.state.value == "released"
    assert result.lease.fencing_token == before.fencing_token
    assert result.lease.lifecycle_claim_sequence == before.lifecycle_claim_sequence
    assert result.lease.lifecycle_operation_claim is None
    run = await service.store.get_run(run_id)
    assert run is not None and run.status is FactoryRunStatus.FAILED


@pytest.mark.asyncio
async def test_retry_resumes_expired_exact_terminal_claim_without_new_task(tmp_path: Path) -> None:
    service, admission, run_id = await _orphan(tmp_path)
    before = admission.current()
    assert before is not None
    retried = await service.retry_run_from_stage(run_id, target_stage="director_dispatch", reason="same run retry")
    assert retried.id == run_id
    assert retried.status is FactoryRunStatus.RECOVERING
    current = admission.current()
    assert current is not None and current.lifecycle_operation_claim is None
    assert current.fencing_token > before.fencing_token


@pytest.mark.asyncio
async def test_terminal_claim_hold_is_read_only_and_exact(tmp_path: Path) -> None:
    _service, admission, run_id = await _orphan(tmp_path)
    before = admission.current()
    assert before is not None and before.lifecycle_operation_claim is not None
    claim = before.lifecycle_operation_claim
    with admission.hold_terminal_settlement_claim(
        run_id, fencing_token=before.fencing_token, sequence=claim.sequence, nonce=claim.nonce
    ) as revalidate:
        assert revalidate() == before
        assert revalidate() == before
    assert admission.current() == before
    with (
        pytest.raises(FactoryPipelineError),
        admission.hold_terminal_settlement_claim(
            run_id, fencing_token=before.fencing_token, sequence=claim.sequence, nonce="other-nonce"
        ),
    ):
        pytest.fail("wrong nonce must reject before entering hold")
    assert admission.current() == before


@pytest.mark.asyncio
async def test_concurrent_recovery_releases_once(tmp_path: Path) -> None:
    service, admission, run_id = await _orphan(tmp_path)
    before = admission.current()
    assert before is not None
    command = RecoverStaleFactoryWorkspaceOwnerCommandV1(
        workspace=str(service.workspace),
        run_id=run_id,
        expected_fencing_token=before.fencing_token,
        reason="concurrent exact terminal recovery",
    )
    outcomes = await asyncio.gather(
        recover_stale_factory_workspace_owner(command, service_factory=lambda _: service),
        recover_stale_factory_workspace_owner(command, service_factory=lambda _: service),
        return_exceptions=True,
    )
    assert sum(not isinstance(item, Exception) for item in outcomes) == 1
    assert sum(isinstance(item, FactoryPipelineError) for item in outcomes) == 1
    after = admission.current()
    assert after is not None and after.state.value == "released"
    assert after.fencing_token == before.fencing_token


@pytest.mark.asyncio
@pytest.mark.parametrize("identical_evidence", [False, True])
async def test_two_services_cannot_commit_duplicate_terminal_release(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, identical_evidence: bool
) -> None:
    first, admission, run_id = await _orphan(tmp_path)
    second_admission = FactoryWorkspaceRunAdmission(
        first.workspace,
        state_root=first.workspace / ".polaris/runtime/factory",
        clock=lambda: datetime(2026, 10, 14, tzinfo=timezone.utc) + timedelta(seconds=11),
        lease_ttl_seconds=10,
    )
    second = FactoryRunService(first.workspace, admission=second_admission)
    if identical_evidence:
        fixed_now = "2026-10-14T00:00:11+00:00"
        monkeypatch.setattr(first, "_now", lambda: fixed_now)
        monkeypatch.setattr(second, "_now", lambda: fixed_now)
    before = admission.current()
    assert before is not None
    outcomes = await asyncio.gather(
        first.recover_stale_workspace_owner(run_id, expected_fencing_token=before.fencing_token, reason="first"),
        second.recover_stale_workspace_owner(run_id, expected_fencing_token=before.fencing_token, reason="second"),
        return_exceptions=True,
    )
    assert sum(not isinstance(item, Exception) for item in outcomes) >= 1
    after = admission.current()
    assert after is not None and after.state.value == "released"
    assert after.version == before.version + 1
    events = first.store._read_authoritative_events_sync(run_id)
    assert sum(event.get("type") == "workspace_run_lease_released" for event in events) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["run_id", "fencing_token", "sequence", "nonce"])
async def test_terminal_hold_rejects_wrong_identity_without_mutation(tmp_path: Path, field: str) -> None:
    _, admission, run_id = await _orphan(tmp_path)
    before = admission.current()
    assert before is not None and before.lifecycle_operation_claim is not None
    args = {
        "run_id": run_id,
        "fencing_token": before.fencing_token,
        "sequence": before.lifecycle_operation_claim.sequence,
        "nonce": before.lifecycle_operation_claim.nonce,
    }
    args[field] = args[field] + 1 if isinstance(args[field], int) else "foreign"
    with pytest.raises(FactoryPipelineError), admission.hold_terminal_settlement_claim(**args):
        pytest.fail("foreign identity must reject")
    assert admission.current() == before


@pytest.mark.asyncio
async def test_existing_stage_claim_blocks_terminal_recovery(tmp_path: Path) -> None:
    service, admission, run_id = await _orphan(tmp_path, stage_claim=True)
    before = admission.current()
    assert before is not None and before.stage_execution_claim is not None
    with pytest.raises(FactoryPipelineError):
        await service.recover_stale_workspace_owner(
            run_id, expected_fencing_token=before.fencing_token, reason="deny stage"
        )
    assert admission.current() == before


@pytest.mark.asyncio
async def test_unknown_physical_history_preserves_original_orphan_claim(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, admission, run_id = await _orphan(tmp_path)
    before = admission.current()
    assert before is not None

    def unknown(*args: object, **kwargs: object) -> object:
        raise RuntimeError("canonical history unavailable")

    monkeypatch.setattr(service, "_capture_physical_attempt_replay_views", unknown)
    with pytest.raises(FactoryPipelineError):
        await service.recover_stale_workspace_owner(
            run_id, expected_fencing_token=before.fencing_token, reason="deny unknown"
        )
    assert admission.current() == before
    run = await service.store.get_run(run_id)
    assert run is not None and run.status is FactoryRunStatus.FAILED


@pytest.mark.asyncio
@pytest.mark.parametrize("no_missing_evidence", [False, True])
async def test_active_child_cannot_be_force_closed_by_orphan_recovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, no_missing_evidence: bool
) -> None:
    from types import SimpleNamespace

    from polaris.cells.factory.pipeline.tests.test_factory_workspace_admission import _create_active_factory_child
    from polaris.cells.runtime.task_runtime.public.service import query_factory_run_settlement

    service, admission, run_id = await _orphan(tmp_path)
    before = admission.current()
    assert before is not None
    _runtime, _, _ = _create_active_factory_child(service.workspace, factory_run_id=run_id)
    if no_missing_evidence:
        monkeypatch.setattr(
            service, "_settlement_barrier_query", lambda *_args: SimpleNamespace(missing_required_modalities=())
        )

        async def forbidden_finalize(*_args: object, **_kwargs: object) -> None:
            pytest.fail("active child must reject before the finalizer or its force-abort branch")

        monkeypatch.setattr(service, "_finalize_terminal_drain", forbidden_finalize)
    settlement_before = query_factory_run_settlement(str(service.workspace), factory_run_id=run_id)
    assert settlement_before["active_session_count"] == 1
    runtime_root = service.workspace / ".polaris/runtime"
    runtime_bytes_before = {
        str(path.relative_to(runtime_root)): path.read_bytes() for path in runtime_root.rglob("*") if path.is_file()
    }
    with pytest.raises(FactoryPipelineError):
        await service.recover_stale_workspace_owner(
            run_id, expected_fencing_token=before.fencing_token, reason="deny active"
        )
    assert admission.current() == before
    assert query_factory_run_settlement(str(service.workspace), factory_run_id=run_id) == settlement_before
    assert {
        str(path.relative_to(runtime_root)): path.read_bytes() for path in runtime_root.rglob("*") if path.is_file()
    } == runtime_bytes_before


@pytest.mark.asyncio
async def test_http_orphan_recovery_succeeds_and_wrong_token_remains_409(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from httpx import ASGITransport, AsyncClient
    from polaris.delivery.http.routers import factory as factory_router
    from polaris.tests.integration.delivery.routers.test_factory_stale_recovery_router import _build_app

    service, admission, run_id = await _orphan(tmp_path)
    before = admission.current()
    assert before is not None
    monkeypatch.setattr(factory_router, "_get_service", lambda _: service)
    payload = {
        "workspace": str(service.workspace),
        "run_id": run_id,
        "expected_fencing_token": before.fencing_token,
        "reason": "official exact terminal action",
    }
    async with AsyncClient(
        transport=ASGITransport(app=_build_app(service.workspace)), base_url="http://test"
    ) as client:
        wrong = await client.post(
            f"/v2/factory/runs/{run_id}/actions/recover-stale-workspace-owner",
            json={**payload, "expected_fencing_token": before.fencing_token + 1},
        )
        assert wrong.status_code == 409
        assert admission.current() == before
        good = await client.post(f"/v2/factory/runs/{run_id}/actions/recover-stale-workspace-owner", json=payload)
        assert good.status_code == 200
        assert good.json()["lease"]["state"] == "released"


@pytest.mark.asyncio
async def test_child_reopening_between_strict_reads_cannot_be_force_aborted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from types import SimpleNamespace

    from polaris.cells.factory.pipeline.tests.test_factory_workspace_admission import _create_active_factory_child
    from polaris.cells.runtime.task_runtime.public.service import TaskRuntimeService

    service, admission, run_id = await _orphan(tmp_path)
    before = admission.current()
    assert before is not None
    query = service._query_child_session_settlement
    session_bytes: dict[Path, bytes] = {}
    calls = 0

    def reopen_after_first_read(queried_run_id: str) -> dict[str, object]:
        nonlocal calls
        calls += 1
        result = query(queried_run_id)
        if calls == 1:
            assert result["settled"] is True
            _create_active_factory_child(service.workspace, factory_run_id=run_id)
            for path in (service.workspace / ".polaris/runtime/tasks").glob("*session.json"):
                session_bytes[path] = path.read_bytes()
            assert session_bytes
        return result

    def forbidden_abort(*_args: object, **_kwargs: object) -> None:
        pytest.fail("orphan closure cannot force-abort a child that reopened")

    monkeypatch.setattr(service, "_query_child_session_settlement", reopen_after_first_read)
    monkeypatch.setattr(
        service, "_settlement_barrier_query", lambda *_args: SimpleNamespace(missing_required_modalities=())
    )
    monkeypatch.setattr(TaskRuntimeService, "terminalize_open_tasks_for_factory_abort", forbidden_abort)
    with pytest.raises(FactoryPipelineError):
        await service.recover_stale_workspace_owner(
            run_id, expected_fencing_token=before.fencing_token, reason="reject reopened child"
        )
    assert calls == 2
    assert admission.current() == before
    assert all(path.read_bytes() == payload for path, payload in session_bytes.items())
    assert query(run_id)["active_session_count"] == 1
