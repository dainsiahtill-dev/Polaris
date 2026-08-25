"""Focused tests for Factory chain owner projection (GR1A)."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest
from polaris.cells.factory.pipeline.internal.factory_event_chain import FactoryEventChainError
from polaris.cells.factory.pipeline.internal.factory_run_models import (
    FactoryConfig,
    FactoryRun,
    FactoryRunStatus,
)
from polaris.cells.factory.pipeline.public import (
    FACTORY_CHAIN_PROJECTION_SCHEMA_VERSION,
    FACTORY_CHAIN_PROJECTION_SOURCE,
    FactoryChainProjectionV1,
    FactoryPipelineError,
    FactoryRunService,
    GetFactoryChainProjectionQueryV1,
    get_factory_chain_projection,
)
from polaris.cells.factory.pipeline.public.contracts import (
    compute_factory_chain_completed,
    compute_factory_chain_projection_hash,
    stable_factory_event_ref,
)


class _FakeFactoryRunService:
    """Minimal owner read surface for focused public query tests."""

    def __init__(
        self,
        workspace: Path,
        *,
        run: FactoryRun | None = None,
        events: list[dict[str, Any]] | None = None,
    ) -> None:
        self.workspace = workspace
        self._run = run
        self._events = list(events or [])

    async def get_run(self, run_id: str) -> FactoryRun | None:
        if self._run is None or self._run.id != run_id:
            return None
        return self._run

    async def get_authoritative_run_events(self, run_id: str) -> list[dict[str, Any]]:
        if self._run is None or self._run.id != run_id:
            return []
        return list(self._events)


def _completed_run(
    *,
    run_id: str = "factory_run_abc",
    stages: list[str] | None = None,
    completed: list[str] | None = None,
    failed: list[str] | None = None,
    status: FactoryRunStatus = FactoryRunStatus.COMPLETED,
) -> FactoryRun:
    stage_list = stages if stages is not None else ["pm_planning", "director_dispatch"]
    return FactoryRun(
        id=run_id,
        config=FactoryConfig(name="chain-projection", stages=list(stage_list)),
        status=status,
        created_at="2026-08-02T00:00:00Z",
        stages_completed=list(completed if completed is not None else stage_list),
        stages_failed=list(failed or []),
    )


def _query(workspace: Path, run_id: str = "factory_run_abc") -> GetFactoryChainProjectionQueryV1:
    return GetFactoryChainProjectionQueryV1(workspace=str(workspace.resolve()), run_id=run_id)


async def _project_with_private_test_reader(
    query: GetFactoryChainProjectionQueryV1,
    service: _FakeFactoryRunService,
) -> FactoryChainProjectionV1:
    with patch(
        "polaris.cells.factory.pipeline.public.service._create_factory_chain_projection_reader",
        return_value=service,
    ):
        return await get_factory_chain_projection(query)


@pytest.mark.asyncio
async def test_real_owner_service_reads_strict_authoritative_event_chain(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    service = FactoryRunService(workspace, cache_root=tmp_path / "runtime")
    run = await service.create_run(FactoryConfig(name="gr1a-owner-read", stages=["pm_planning"]))

    with patch(
        "polaris.cells.factory.pipeline.public.service.resolve_existing_runtime_root_read_only",
        return_value=SimpleNamespace(runtime_root=str(tmp_path / "runtime")),
    ):
        result = await get_factory_chain_projection(_query(workspace, run.id))

    assert result.available is True
    assert result.status == "pending"
    assert result.configured_stages == ("pm_planning",)
    assert result.event_count == 1
    assert len(result.event_refs) == 1
    assert len(result.event_refs[0]) == 64
    assert result.chain_completed is False


@pytest.mark.asyncio
async def test_real_owner_completion_requires_and_binds_terminal_event(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    runtime_root = tmp_path / "runtime"
    service = FactoryRunService(workspace, cache_root=runtime_root)
    run = await service.create_run(FactoryConfig(name="gr1a-complete", stages=["pm_planning"]))
    run.stages_completed = ["pm_planning"]
    await service.store.save_run(run)
    completed = await service.complete_run(run.id, success=True)

    with patch(
        "polaris.cells.factory.pipeline.public.service.resolve_existing_runtime_root_read_only",
        return_value=SimpleNamespace(runtime_root=str(runtime_root)),
    ):
        result = await get_factory_chain_projection(_query(workspace, completed.id))

    assert result.status == "completed"
    assert result.chain_completed is True
    assert result.completion_event_ref is not None
    assert result.completion_event_ref in result.event_refs


@pytest.mark.asyncio
async def test_real_owner_service_does_not_skip_corrupt_event_bytes(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    service = FactoryRunService(workspace, cache_root=tmp_path / "runtime")
    run = await service.create_run(FactoryConfig(name="gr1a-corrupt-chain", stages=["pm_planning"]))
    event_path = service.store.get_run_dir(run.id) / "events" / "events.jsonl"
    event_path.write_text('{"broken":true}', encoding="utf-8")

    with (
        patch(
            "polaris.cells.factory.pipeline.public.service.resolve_existing_runtime_root_read_only",
            return_value=SimpleNamespace(runtime_root=str(tmp_path / "runtime")),
        ),
        pytest.raises(FactoryEventChainError, match="lacks a final newline"),
    ):
        await get_factory_chain_projection(_query(workspace, run.id))


@pytest.mark.asyncio
async def test_completed_chain_projection_success(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    run = _completed_run()
    events: list[dict[str, Any]] = [
        {"event_id": "evt-1", "type": "run_admitted"},
        {"content_id": "content-2", "type": "stage_completed"},
        {"type": "no-stable-id", "payload": {"n": 1}},
        {"append_id": "append-3", "type": "completed", "success": True},
    ]
    service = _FakeFactoryRunService(workspace, run=run, events=events)

    result = await _project_with_private_test_reader(_query(workspace), service)

    assert isinstance(result, FactoryChainProjectionV1)
    assert result.source == FACTORY_CHAIN_PROJECTION_SOURCE
    assert result.schema_version == FACTORY_CHAIN_PROJECTION_SCHEMA_VERSION
    assert result.available is True
    assert result.status == "completed"
    assert result.configured_stages == ("pm_planning", "director_dispatch")
    assert result.completed_stages == ("pm_planning", "director_dispatch")
    assert result.failed_stages == ()
    assert result.missing_stages == ()
    assert result.chain_completed is True
    assert result.event_count == 4
    assert result.event_refs[0] == "evt-1"
    assert result.event_refs[1] == "content-2"
    assert len(result.event_refs[2]) == 64
    assert result.event_refs[3] == "append-3"
    assert result.completion_event_ref == "append-3"
    expected_hash = compute_factory_chain_projection_hash(
        workspace=str(workspace.resolve()),
        run_id=run.id,
        available=True,
        status="completed",
        configured_stages=result.configured_stages,
        completed_stages=result.completed_stages,
        failed_stages=result.failed_stages,
        missing_stages=result.missing_stages,
        chain_completed=True,
        event_count=result.event_count,
        event_refs=result.event_refs,
        completion_event_ref="append-3",
    )
    assert result.projection_hash == expected_hash


@pytest.mark.asyncio
async def test_missing_run_returns_unavailable_never_completes(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    runtime_root = tmp_path / "runtime"
    with patch(
        "polaris.cells.factory.pipeline.public.service.resolve_existing_runtime_root_read_only",
        return_value=None,
    ):
        result = await get_factory_chain_projection(_query(workspace, run_id="missing-run"))

    assert result.available is False
    assert result.status == ""
    assert result.configured_stages == ()
    assert result.completed_stages == ()
    assert result.failed_stages == ()
    assert result.missing_stages == ()
    assert result.event_count == 0
    assert result.event_refs == ()
    assert result.completion_event_ref is None
    assert result.chain_completed is False
    assert not (runtime_root / "factory").exists()
    assert result.projection_hash == compute_factory_chain_projection_hash(
        workspace=str(workspace.resolve()),
        run_id="missing-run",
        available=False,
        status="",
        configured_stages=(),
        completed_stages=(),
        failed_stages=(),
        missing_stages=(),
        chain_completed=False,
        event_count=0,
        event_refs=(),
        completion_event_ref=None,
    )


@pytest.mark.asyncio
async def test_cold_missing_run_query_never_initializes_storage(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A cold projection query must not turn a missing run into a write probe."""
    from polaris.kernelone.storage.layout import clear_storage_roots_cache

    workspace = tmp_path / "workspace"
    runtime_base = tmp_path / "runtime-base"
    workspace.mkdir()
    runtime_base.mkdir()
    monkeypatch.setenv("KERNELONE_RUNTIME_ROOT", str(runtime_base))
    clear_storage_roots_cache()
    before = sorted(path.relative_to(tmp_path) for path in tmp_path.rglob("*"))

    with patch(
        "polaris.kernelone.storage.layout._is_runtime_base_writable",
        side_effect=AssertionError("read-only query must not probe writability"),
    ):
        result = await get_factory_chain_projection(_query(workspace, run_id="missing-run"))

    after = sorted(path.relative_to(tmp_path) for path in tmp_path.rglob("*"))
    assert result.available is False
    assert after == before


@pytest.mark.asyncio
async def test_incomplete_stage_blocks_chain_completed(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    run = _completed_run(
        status=FactoryRunStatus.RUNNING,
        completed=["pm_planning"],
        failed=[],
    )
    service = _FakeFactoryRunService(
        workspace,
        run=run,
        events=[{"event_id": "evt-running"}],
    )

    result = await _project_with_private_test_reader(_query(workspace), service)

    assert result.available is True
    assert result.status == "running"
    assert result.missing_stages == ("director_dispatch",)
    assert result.chain_completed is False


@pytest.mark.asyncio
async def test_failed_stage_blocks_chain_completed(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    run = _completed_run(
        completed=["pm_planning"],
        failed=["director_dispatch"],
        status=FactoryRunStatus.FAILED,
    )
    service = _FakeFactoryRunService(
        workspace,
        run=run,
        events=[{"event_id": "evt-failed"}],
    )

    result = await _project_with_private_test_reader(_query(workspace), service)

    assert result.available is True
    assert result.status == "failed"
    assert result.failed_stages == ("director_dispatch",)
    assert result.missing_stages == ("director_dispatch",)
    assert result.chain_completed is False


@pytest.mark.asyncio
async def test_empty_configured_stages_never_complete(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    run = _completed_run(stages=[], completed=[], failed=[], status=FactoryRunStatus.COMPLETED)
    service = _FakeFactoryRunService(
        workspace,
        run=run,
        events=[{"event_id": "evt-empty-config", "type": "completed", "success": True}],
    )

    result = await _project_with_private_test_reader(_query(workspace), service)

    assert result.available is True
    assert result.configured_stages == ()
    assert result.chain_completed is False


@pytest.mark.asyncio
async def test_missing_events_blocks_chain_completed(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    run = _completed_run()
    service = _FakeFactoryRunService(workspace, run=run, events=[])

    result = await _project_with_private_test_reader(_query(workspace), service)

    assert result.available is True
    assert result.status == "completed"
    assert result.missing_stages == ()
    assert result.event_count == 0
    assert result.completion_event_ref is None
    assert result.chain_completed is False


@pytest.mark.asyncio
async def test_earlier_owner_event_cannot_substitute_for_terminal_completion(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    run = _completed_run()
    service = _FakeFactoryRunService(
        workspace,
        run=run,
        events=[{"event_id": "evt-admitted", "type": "factory_run_admitted"}],
    )

    result = await _project_with_private_test_reader(_query(workspace), service)

    assert result.status == "completed"
    assert result.event_refs == ("evt-admitted",)
    assert result.completion_event_ref is None
    assert result.chain_completed is False


@pytest.mark.asyncio
async def test_invalid_terminal_completion_event_fails_closed(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    run = _completed_run()
    service = _FakeFactoryRunService(
        workspace,
        run=run,
        events=[{"event_id": "evt-complete", "type": "completed", "success": False}],
    )

    with pytest.raises(FactoryPipelineError) as error:
        await _project_with_private_test_reader(_query(workspace), service)

    assert error.value.code == "factory_chain_projection_terminal_event_invalid"


@pytest.mark.asyncio
async def test_retry_separated_completion_events_select_latest_epoch(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    service = _FakeFactoryRunService(
        workspace,
        run=_completed_run(),
        events=[
            {"event_id": "evt-complete-1", "type": "completed", "success": True},
            {"event_id": "evt-retry", "type": "retry_requested"},
            {"event_id": "evt-complete-2", "type": "completed", "success": True},
        ],
    )

    result = await _project_with_private_test_reader(_query(workspace), service)

    assert result.completion_event_ref == "evt-complete-2"
    assert result.chain_completed is True


@pytest.mark.asyncio
async def test_duplicate_completion_in_same_epoch_fails_closed(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    service = _FakeFactoryRunService(
        workspace,
        run=_completed_run(),
        events=[
            {"event_id": "evt-complete-1", "type": "completed", "success": True},
            {"event_id": "evt-complete-2", "type": "completed", "success": True},
        ],
    )

    with pytest.raises(FactoryPipelineError) as error:
        await _project_with_private_test_reader(_query(workspace), service)

    assert error.value.code == "factory_chain_projection_terminal_event_invalid"


@pytest.mark.asyncio
async def test_query_type_must_be_exact(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    with pytest.raises(TypeError, match="exact GetFactoryChainProjectionQueryV1"):
        await get_factory_chain_projection(  # type: ignore[arg-type]
            object(),  # type: ignore[arg-type]
        )


@pytest.mark.asyncio
async def test_public_query_rejects_owner_factory_injection(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    service = _FakeFactoryRunService(workspace, run=_completed_run(), events=[])

    with pytest.raises(TypeError, match="unexpected keyword argument"):
        await get_factory_chain_projection(  # type: ignore[call-arg]
            _query(workspace),
            service_factory=lambda _ws: service,
        )


def test_query_rejects_non_exact_string_types() -> None:
    with pytest.raises(ValueError, match="workspace must be an exact string"):
        GetFactoryChainProjectionQueryV1(workspace=Path("/tmp/ws"), run_id="run-1")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="run_id must be an exact string"):
        GetFactoryChainProjectionQueryV1(workspace="/tmp/ws", run_id=123)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="workspace must be a non-empty string"):
        GetFactoryChainProjectionQueryV1(workspace="  ", run_id="run-1")
    with pytest.raises(ValueError, match="run_id must be a non-empty string"):
        GetFactoryChainProjectionQueryV1(workspace="/tmp/ws", run_id="")


@pytest.mark.asyncio
async def test_stage_and_event_normalization_is_deterministic(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    run = _completed_run(
        stages=[" pm_planning ", " director_dispatch "],
        completed=["director_dispatch", "pm_planning"],
        failed=[],
        status=FactoryRunStatus.FAILED,
    )
    events = [
        {"event_id": " evt-a ", "type": "a"},
        {"content_id": "content-b"},
    ]
    service = _FakeFactoryRunService(workspace, run=run, events=events)

    first = await _project_with_private_test_reader(_query(workspace), service)
    second = await _project_with_private_test_reader(_query(workspace), service)

    assert first.configured_stages == ("pm_planning", "director_dispatch")
    assert first.completed_stages == ("director_dispatch", "pm_planning")
    assert first.failed_stages == ()
    assert first.event_refs == ("evt-a", "content-b")
    assert first.event_count == 2
    assert first.projection_hash == second.projection_hash
    assert first.to_dict() == second.to_dict()


@pytest.mark.asyncio
async def test_duplicate_stage_owner_fact_fails_closed(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    run = _completed_run(stages=["pm_planning", " pm_planning "])
    service = _FakeFactoryRunService(workspace, run=run, events=[{"event_id": "evt-a"}])

    with pytest.raises(FactoryPipelineError) as error:
        await _project_with_private_test_reader(_query(workspace), service)

    assert error.value.code == "factory_chain_projection_owner_facts_invalid"


@pytest.mark.asyncio
async def test_duplicate_event_identity_fails_closed(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    run = _completed_run()
    service = _FakeFactoryRunService(
        workspace,
        run=run,
        events=[{"event_id": "evt-a"}, {"event_id": "evt-a"}],
    )

    with pytest.raises(FactoryPipelineError) as error:
        await _project_with_private_test_reader(_query(workspace), service)

    assert error.value.code == "factory_chain_projection_owner_events_invalid"


@pytest.mark.asyncio
async def test_non_object_event_fails_closed(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    run = _completed_run()
    service = _FakeFactoryRunService(workspace, run=run, events=[])
    service._events = ["not-an-event"]  # type: ignore[list-item]

    with pytest.raises(FactoryPipelineError) as error:
        await _project_with_private_test_reader(_query(workspace), service)

    assert error.value.code == "factory_chain_projection_owner_events_invalid"


def test_stable_event_ref_prefers_identity_fields_then_json_hash() -> None:
    chain_hash = "a" * 64
    assert stable_factory_event_ref({"chain_event_hash": chain_hash, "event_id": "e1"}) == chain_hash
    assert stable_factory_event_ref({"event_id": "e1", "content_id": "c1"}) == "e1"
    assert stable_factory_event_ref({"content_id": "c1", "append_id": "a1"}) == "c1"
    assert stable_factory_event_ref({"append_id": "a1"}) == "a1"
    hashed = stable_factory_event_ref({"type": "x", "n": 2})
    assert len(hashed) == 64
    assert hashed == stable_factory_event_ref({"n": 2, "type": "x"})

    with pytest.raises(ValueError, match="event_id must be"):
        stable_factory_event_ref({"event_id": 7})
    with pytest.raises(ValueError, match="chain_event_hash must be"):
        stable_factory_event_ref({"chain_event_hash": "not-a-hash"})
    with pytest.raises(ValueError, match="cannot be serialized"):
        stable_factory_event_ref({"payload": object()})


def test_direct_contradictory_projection_fails_closed() -> None:
    workspace = "/tmp/factory-chain-ws"
    run_id = "factory_run_xyz"
    configured = ("pm_planning",)
    completed = ("pm_planning",)
    failed: tuple[str, ...] = ()
    missing: tuple[str, ...] = ()
    event_refs = ("evt-1",)
    valid_hash = compute_factory_chain_projection_hash(
        workspace=workspace,
        run_id=run_id,
        available=True,
        status="completed",
        configured_stages=configured,
        completed_stages=completed,
        failed_stages=failed,
        missing_stages=missing,
        chain_completed=True,
        event_count=1,
        event_refs=event_refs,
        completion_event_ref="evt-1",
    )
    observed = FactoryChainProjectionV1(
        workspace=workspace,
        run_id=run_id,
        available=True,
        status="completed",
        configured_stages=configured,
        completed_stages=completed,
        failed_stages=failed,
        missing_stages=missing,
        chain_completed=True,
        event_count=1,
        event_refs=event_refs,
        completion_event_ref="evt-1",
        projection_hash=valid_hash,
    )
    assert observed.chain_completed is True
    assert "authority_bound" not in observed.to_dict()

    with pytest.raises(ValueError, match="chain_completed must be"):
        FactoryChainProjectionV1(
            workspace=workspace,
            run_id=run_id,
            available=True,
            status="completed",
            configured_stages=configured,
            completed_stages=completed,
            failed_stages=failed,
            missing_stages=missing,
            chain_completed=False,
            event_count=1,
            event_refs=event_refs,
            completion_event_ref="evt-1",
            projection_hash=valid_hash,
        )

    with pytest.raises(ValueError, match="projection_hash must bind"):
        FactoryChainProjectionV1(
            workspace=workspace,
            run_id=run_id,
            available=True,
            status="completed",
            configured_stages=configured,
            completed_stages=completed,
            failed_stages=failed,
            missing_stages=missing,
            chain_completed=True,
            event_count=1,
            event_refs=event_refs,
            completion_event_ref="evt-1",
            projection_hash="0" * 64,
        )

    with pytest.raises(ValueError, match="unavailable projection cannot be chain_completed"):
        FactoryChainProjectionV1(
            workspace=workspace,
            run_id=run_id,
            available=False,
            status="",
            configured_stages=(),
            completed_stages=(),
            failed_stages=(),
            missing_stages=(),
            chain_completed=True,
            event_count=0,
            event_refs=(),
            completion_event_ref=None,
            projection_hash=compute_factory_chain_projection_hash(
                workspace=workspace,
                run_id=run_id,
                available=False,
                status="",
                configured_stages=(),
                completed_stages=(),
                failed_stages=(),
                missing_stages=(),
                chain_completed=False,
                event_count=0,
                event_refs=(),
                completion_event_ref=None,
            ),
        )

    with pytest.raises(ValueError, match="missing_stages must equal"):
        FactoryChainProjectionV1(
            workspace=workspace,
            run_id=run_id,
            available=True,
            status="running",
            configured_stages=("pm_planning", "director_dispatch"),
            completed_stages=("pm_planning",),
            failed_stages=(),
            missing_stages=("pm_planning",),
            chain_completed=False,
            event_count=1,
            event_refs=event_refs,
            completion_event_ref=None,
            projection_hash="deadbeef",
        )

    with pytest.raises(ValueError, match="event_count must equal"):
        FactoryChainProjectionV1(
            workspace=workspace,
            run_id=run_id,
            available=True,
            status="completed",
            configured_stages=configured,
            completed_stages=completed,
            failed_stages=failed,
            missing_stages=missing,
            chain_completed=False,
            event_count=2,
            event_refs=event_refs,
            completion_event_ref="evt-1",
            projection_hash=valid_hash,
        )


def test_compute_chain_completed_invariant_matrix() -> None:
    assert (
        compute_factory_chain_completed(
            available=True,
            status="completed",
            configured_stages=("a",),
            completed_stages=("a",),
            failed_stages=(),
            event_refs=("e",),
            completion_event_ref="e",
        )
        is True
    )
    assert (
        compute_factory_chain_completed(
            available=False,
            status="completed",
            configured_stages=("a",),
            completed_stages=("a",),
            failed_stages=(),
            event_refs=("e",),
            completion_event_ref="e",
        )
        is False
    )
    assert (
        compute_factory_chain_completed(
            available=True,
            status="completed",
            configured_stages=(),
            completed_stages=(),
            failed_stages=(),
            event_refs=("e",),
            completion_event_ref="e",
        )
        is False
    )
    assert (
        compute_factory_chain_completed(
            available=True,
            status="failed",
            configured_stages=("a",),
            completed_stages=("a",),
            failed_stages=(),
            event_refs=("e",),
            completion_event_ref="e",
        )
        is False
    )
    assert (
        compute_factory_chain_completed(
            available=True,
            status="completed",
            configured_stages=("a",),
            completed_stages=("a",),
            failed_stages=("b",),
            event_refs=("e",),
            completion_event_ref="e",
        )
        is False
    )
    assert (
        compute_factory_chain_completed(
            available=True,
            status="completed",
            configured_stages=("a", "b"),
            completed_stages=("a",),
            failed_stages=(),
            event_refs=("e",),
            completion_event_ref="e",
        )
        is False
    )
    assert (
        compute_factory_chain_completed(
            available=True,
            status="completed",
            configured_stages=("a",),
            completed_stages=("a",),
            failed_stages=(),
            event_refs=(),
            completion_event_ref=None,
        )
        is False
    )


@pytest.mark.asyncio
async def test_workspace_binding_mismatch_fails_closed(tmp_path: Path) -> None:
    workspace = tmp_path / "ws-a"
    workspace.mkdir()
    other = tmp_path / "ws-b"
    other.mkdir()
    service = _FakeFactoryRunService(other, run=_completed_run(), events=[{"event_id": "e"}])

    with pytest.raises(FactoryPipelineError) as error:
        await _project_with_private_test_reader(_query(workspace), service)

    assert error.value.code == "factory_workspace_binding_mismatch"


def test_canonical_public_package_import() -> None:
    from polaris.cells.factory.pipeline import public as public_pkg

    assert public_pkg.GetFactoryChainProjectionQueryV1 is GetFactoryChainProjectionQueryV1
    assert public_pkg.FactoryChainProjectionV1 is FactoryChainProjectionV1
    assert public_pkg.get_factory_chain_projection is get_factory_chain_projection
