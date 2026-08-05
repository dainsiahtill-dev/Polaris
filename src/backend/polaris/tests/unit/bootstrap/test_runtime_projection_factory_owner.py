"""GR1C bootstrap adapter tests for Factory chain owner observation."""

from __future__ import annotations

from pathlib import Path

import pytest
from polaris.cells.factory.pipeline.public import (
    FactoryChainProjectionV1,
)
from polaris.cells.factory.pipeline.public.contracts import compute_factory_chain_projection_hash
from polaris.cells.runtime.projection.internal import project_outcome_factory_owner
from polaris.cells.runtime.projection.public import (
    FactoryChainOwnerObservationV1,
    ProjectOutcomeOwnerObservationV1Error,
)


def _factory_projection(workspace: Path) -> FactoryChainProjectionV1:
    stages = ("pm", "chief_engineer", "director")
    event_refs = ("event:completed",)
    payload = {
        "workspace": str(workspace.resolve()),
        "run_id": "run-gr1c",
        "available": True,
        "status": "completed",
        "configured_stages": stages,
        "completed_stages": stages,
        "failed_stages": (),
        "missing_stages": (),
        "chain_completed": True,
        "event_count": 1,
        "event_refs": event_refs,
        "completion_event_ref": event_refs[0],
    }
    return FactoryChainProjectionV1(
        **payload,
        projection_hash=compute_factory_chain_projection_hash(**payload),
    )


@pytest.fixture(autouse=True)
def _reset_observation_port(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        project_outcome_factory_owner,
        "_factory_chain_owner_observation_port",
        None,
        raising=False,
    )


@pytest.mark.asyncio
async def test_adapter_translates_exact_factory_dto_to_projection_observation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from polaris.bootstrap import runtime_projection_factory_owner as bootstrap_adapter

    projection = _factory_projection(tmp_path)

    async def owner_query(query: object) -> FactoryChainProjectionV1:
        return projection

    monkeypatch.setattr(bootstrap_adapter, "get_factory_chain_projection", owner_query)
    adapter = bootstrap_adapter.FactoryChainOwnerObservationAdapter()

    observation = await adapter.observe_factory_chain(
        workspace=str(tmp_path),
        run_id="run-gr1c",
    )

    assert type(observation) is FactoryChainOwnerObservationV1
    assert observation.workspace == str(tmp_path.resolve())
    assert observation.run_id == "run-gr1c"
    assert observation.chain_completed is True
    assert observation.event_refs == projection.event_refs
    assert observation.projection_hash == projection.projection_hash


@pytest.mark.asyncio
async def test_adapter_rejects_non_exact_factory_dto(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from polaris.bootstrap import runtime_projection_factory_owner as bootstrap_adapter

    async def owner_query(query: object) -> object:
        return object()

    monkeypatch.setattr(bootstrap_adapter, "get_factory_chain_projection", owner_query)

    with pytest.raises(ProjectOutcomeOwnerObservationV1Error) as exc_info:
        await bootstrap_adapter.FactoryChainOwnerObservationAdapter().observe_factory_chain(
            workspace=str(tmp_path),
            run_id="run-gr1c",
        )

    assert exc_info.value.error_code == "invalid_factory_chain_owner_result_type"


@pytest.mark.asyncio
async def test_adapter_rejects_identity_or_hash_evidence_drift(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from polaris.bootstrap import runtime_projection_factory_owner as bootstrap_adapter

    projection = _factory_projection(tmp_path)
    object.__setattr__(projection, "projection_hash", "0" * 64)

    async def owner_query(query: object) -> FactoryChainProjectionV1:
        return projection

    monkeypatch.setattr(bootstrap_adapter, "get_factory_chain_projection", owner_query)

    with pytest.raises(ProjectOutcomeOwnerObservationV1Error) as exc_info:
        await bootstrap_adapter.FactoryChainOwnerObservationAdapter().observe_factory_chain(
            workspace=str(tmp_path),
            run_id="run-gr1c",
        )

    assert exc_info.value.error_code == "factory_chain_owner_evidence_invalid"


@pytest.mark.asyncio
async def test_adapter_rejects_factory_workspace_or_run_identity_mismatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from polaris.bootstrap import runtime_projection_factory_owner as bootstrap_adapter

    projection = _factory_projection(tmp_path / "other")

    async def owner_query(query: object) -> FactoryChainProjectionV1:
        return projection

    monkeypatch.setattr(bootstrap_adapter, "get_factory_chain_projection", owner_query)

    with pytest.raises(ProjectOutcomeOwnerObservationV1Error) as exc_info:
        await bootstrap_adapter.FactoryChainOwnerObservationAdapter().observe_factory_chain(
            workspace=str(tmp_path),
            run_id="run-gr1c",
        )

    assert exc_info.value.error_code == "factory_chain_owner_identity_mismatch"


def test_bootstrap_configure_binds_singleton_idempotently(monkeypatch: pytest.MonkeyPatch) -> None:
    from polaris.bootstrap import runtime_projection_factory_owner as bootstrap_adapter

    bound: list[object] = []

    def bind(port: object) -> None:
        bound.append(port)

    monkeypatch.setattr(bootstrap_adapter, "bind_factory_chain_owner_observation_port", bind)

    bootstrap_adapter.configure_runtime_projection_factory_owner()
    bootstrap_adapter.configure_runtime_projection_factory_owner()

    assert bound == [bootstrap_adapter.FACTORY_CHAIN_OWNER_OBSERVATION_ADAPTER] * 2


def test_http_app_factory_invokes_bootstrap_wiring() -> None:
    app_factory_path = Path(__file__).resolve().parents[3] / "delivery" / "http" / "app_factory.py"
    source = app_factory_path.read_text(encoding="utf-8")

    assert "configure_runtime_projection_factory_owner" in source


def test_bootstrap_adapter_is_sole_cross_cell_factory_owner_query_import() -> None:
    backend_root = Path(__file__).resolve().parents[4]
    cross_cell_imports: list[Path] = []
    for path in (backend_root / "polaris").rglob("*.py"):
        if "cells/factory/pipeline" in path.as_posix() or "/tests/" in path.as_posix():
            continue
        source = path.read_text(encoding="utf-8")
        if "get_factory_chain_projection" in source:
            cross_cell_imports.append(path.relative_to(backend_root))

    assert cross_cell_imports == [Path("polaris/bootstrap/runtime_projection_factory_owner.py")]
