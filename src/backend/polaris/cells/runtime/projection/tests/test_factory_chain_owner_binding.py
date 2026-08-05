"""Focused GR1B/GR1C tests for bootstrap-bound Factory owner observation."""

from __future__ import annotations

import hashlib
import json
from dataclasses import fields
from pathlib import Path

import polaris.cells.runtime.projection.public as projection_public
import pytest
import yaml
from polaris.cells.runtime.projection.internal import project_outcome_factory_owner
from polaris.cells.runtime.projection.public import (
    ChainAxisV1,
    DeliveryAxisV1,
    FactoryChainOwnerObservationPortV1,
    FactoryChainOwnerObservationV1,
    ProjectOutcomeFactoryOwnerBindingV1,
    ProjectOutcomeFactoryOwnerQueryV1,
    ProjectOutcomeNonFactoryClaimsV1,
    ProjectOutcomeNonFactoryEvidenceRefsV1,
    ProjectOutcomeOwnerObservationV1Error,
    QaAxisV1,
    RunLedgerAxisV1,
    TaskBoundaryAxisV1,
    TaskRuntimeAxisV1,
    query_project_outcome_with_factory_owner,
)
from polaris.cells.runtime.projection.public.bootstrap import (
    bind_factory_chain_owner_observation_port,
)


class _ObservationPort:
    def __init__(
        self,
        observation: FactoryChainOwnerObservationV1 | None = None,
        error: Exception | None = None,
    ) -> None:
        self.observation = observation
        self.error = error
        self.calls: list[tuple[str, str]] = []

    async def observe_factory_chain(
        self,
        *,
        workspace: str,
        run_id: str,
    ) -> FactoryChainOwnerObservationV1:
        self.calls.append((workspace, run_id))
        if self.error is not None:
            raise self.error
        assert self.observation is not None
        return self.observation


@pytest.fixture(autouse=True)
def _reset_observation_port(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        project_outcome_factory_owner,
        "_factory_chain_owner_observation_port",
        None,
        raising=False,
    )


def _claims() -> ProjectOutcomeNonFactoryClaimsV1:
    return ProjectOutcomeNonFactoryClaimsV1(
        delivery=DeliveryAxisV1.VERIFIED,
        qa=QaAxisV1.PASSED,
        task_boundary=TaskBoundaryAxisV1.PASSED,
        task_runtime=TaskRuntimeAxisV1.CONVERGED,
        run_ledger=RunLedgerAxisV1.CLOSED,
        evidence_refs=ProjectOutcomeNonFactoryEvidenceRefsV1(
            delivery=("delivery:verified",),
            qa=("qa:passed",),
            task_boundary=("task-boundary:passed",),
            task_runtime=("task-runtime:converged",),
            run_ledger=("run-ledger:closed",),
        ),
        task_count=1,
        completed_task_count=1,
    )


def _observation(
    workspace: Path,
    *,
    status: str,
    chain_completed: bool,
    available: bool = True,
    run_id: str = "run-gr1b",
) -> FactoryChainOwnerObservationV1:
    event_refs = ("event:complete",) if chain_completed else (() if not available else ("event:active",))
    completion_ref = "event:complete" if chain_completed else None
    return FactoryChainOwnerObservationV1(
        workspace=str(workspace.resolve()),
        run_id=run_id,
        available=available,
        status=status if available else "",
        chain_completed=chain_completed,
        event_refs=event_refs,
        completion_event_ref=completion_ref,
        projection_hash=hashlib.sha256(f"{workspace}:{run_id}:{status}:{chain_completed}".encode()).hexdigest(),
    )


def _query(workspace: Path) -> ProjectOutcomeFactoryOwnerQueryV1:
    return ProjectOutcomeFactoryOwnerQueryV1(
        workspace=str(workspace),
        run_id="run-gr1b",
        claims=_claims(),
    )


@pytest.mark.asyncio
async def test_bound_observation_controls_chain_and_query_accepts_no_authority_injection(tmp_path: Path) -> None:
    observation = _observation(tmp_path, status="running", chain_completed=False)
    port = _ObservationPort(observation)
    bind_factory_chain_owner_observation_port(port)

    assert isinstance(port, FactoryChainOwnerObservationPortV1)
    assert [field.name for field in fields(ProjectOutcomeFactoryOwnerQueryV1)] == [
        "workspace",
        "run_id",
        "claims",
    ]

    result = await query_project_outcome_with_factory_owner(_query(tmp_path))

    assert port.calls == [(str(tmp_path.resolve()), "run-gr1b")]
    assert result.factory_chain_owner_observed is True
    assert result.factory_chain_projection_hash == observation.projection_hash
    assert result.outcome.delivery is DeliveryAxisV1.VERIFIED
    assert result.outcome.chain is ChainAxisV1.ACTIVE
    assert result.outcome.authority_bound is False
    assert result.outcome.completed_verified is False
    assert result.remaining_unbound_owner_axes == (
        "delivery",
        "qa",
        "task_boundary",
        "task_runtime",
        "run_ledger",
    )


@pytest.mark.asyncio
async def test_unbound_observation_port_fails_closed(tmp_path: Path) -> None:
    with pytest.raises(ProjectOutcomeOwnerObservationV1Error) as exc_info:
        await query_project_outcome_with_factory_owner(_query(tmp_path))

    assert exc_info.value.error_code == "factory_chain_owner_port_unbound"


def test_same_port_rebind_is_idempotent_and_conflicting_rebind_fails(tmp_path: Path) -> None:
    first = _ObservationPort(_observation(tmp_path, status="running", chain_completed=False))
    second = _ObservationPort(_observation(tmp_path, status="running", chain_completed=False))

    bind_factory_chain_owner_observation_port(first)
    bind_factory_chain_owner_observation_port(first)
    with pytest.raises(ProjectOutcomeOwnerObservationV1Error) as exc_info:
        bind_factory_chain_owner_observation_port(second)

    assert exc_info.value.error_code == "factory_chain_owner_port_conflicting_rebind"


@pytest.mark.asyncio
async def test_workspace_or_run_identity_mismatch_fails_closed(tmp_path: Path) -> None:
    port = _ObservationPort(_observation(tmp_path / "other", status="running", chain_completed=False))
    bind_factory_chain_owner_observation_port(port)

    with pytest.raises(ProjectOutcomeOwnerObservationV1Error) as exc_info:
        await query_project_outcome_with_factory_owner(_query(tmp_path))

    assert exc_info.value.error_code == "factory_chain_owner_identity_mismatch"


@pytest.mark.asyncio
async def test_wrong_exact_query_type_fails_closed() -> None:
    with pytest.raises(ProjectOutcomeOwnerObservationV1Error) as exc_info:
        await query_project_outcome_with_factory_owner(object())  # type: ignore[arg-type]

    assert exc_info.value.error_code == "invalid_factory_owner_query_type"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("available", "status", "chain_completed", "expected"),
    [
        (False, "", False, ChainAxisV1.NOT_STARTED),
        (True, "pending", False, ChainAxisV1.ACTIVE),
        (True, "running", False, ChainAxisV1.ACTIVE),
        (True, "paused", False, ChainAxisV1.ACTIVE),
        (True, "recovering", False, ChainAxisV1.ACTIVE),
        (True, "completed", True, ChainAxisV1.COMPLETED),
        (True, "completed", False, ChainAxisV1.INCOMPLETE),
        (True, "failed", False, ChainAxisV1.INCOMPLETE),
    ],
)
async def test_status_mapping_is_total_and_never_guesses_control_plane_failure(
    tmp_path: Path,
    available: bool,
    status: str,
    chain_completed: bool,
    expected: ChainAxisV1,
) -> None:
    bind_factory_chain_owner_observation_port(
        _ObservationPort(
            _observation(
                tmp_path,
                available=available,
                status=status,
                chain_completed=chain_completed,
            )
        )
    )

    result = await query_project_outcome_with_factory_owner(_query(tmp_path))

    assert result.outcome.chain is expected
    assert result.outcome.chain is not ChainAxisV1.CONTROL_PLANE_FAILED


@pytest.mark.asyncio
async def test_observation_query_error_is_typed_and_fail_closed(tmp_path: Path) -> None:
    bind_factory_chain_owner_observation_port(_ObservationPort(error=RuntimeError("owner unavailable")))

    with pytest.raises(ProjectOutcomeOwnerObservationV1Error) as exc_info:
        await query_project_outcome_with_factory_owner(_query(tmp_path))

    assert exc_info.value.error_code == "factory_chain_owner_query_failed"


def test_public_package_exports_projection_owned_observation_contracts() -> None:
    assert "FactoryChainOwnerObservationV1" in projection_public.__all__
    assert "FactoryChainOwnerObservationPortV1" in projection_public.__all__
    assert "ProjectOutcomeFactoryOwnerQueryV1" in projection_public.__all__
    assert "ProjectOutcomeFactoryOwnerBindingV1" in projection_public.__all__
    assert "query_project_outcome_with_factory_owner" in projection_public.__all__


@pytest.mark.asyncio
async def test_direct_binding_construction_rejects_forged_chain_evidence(tmp_path: Path) -> None:
    observation = _observation(tmp_path, status="completed", chain_completed=True)
    bind_factory_chain_owner_observation_port(_ObservationPort(observation))
    observed = await query_project_outcome_with_factory_owner(_query(tmp_path))

    with pytest.raises(ProjectOutcomeOwnerObservationV1Error) as exc_info:
        ProjectOutcomeFactoryOwnerBindingV1(
            outcome=observed.outcome,
            factory_chain_owner_observed=True,
            factory_chain_projection_hash=observed.factory_chain_projection_hash,
            factory_chain_evidence_refs=("factory-chain:forged",),
        )

    assert exc_info.value.error_code == "factory_chain_binding_evidence_mismatch"


def test_runtime_projection_has_no_static_factory_cell_dependency() -> None:
    cell_root = Path(__file__).resolve().parents[1]
    backend_root = cell_root.parents[3]
    manifest = yaml.safe_load((cell_root / "cell.yaml").read_text(encoding="utf-8"))
    catalog = yaml.safe_load((backend_root / "docs" / "graph" / "catalog" / "cells.yaml").read_text(encoding="utf-8"))
    catalog_cell = next(cell for cell in catalog["cells"] if cell["id"] == "runtime.projection")
    context_pack = json.loads((cell_root / "generated" / "context.pack.json").read_text(encoding="utf-8"))

    for source_root in (cell_root / "internal", cell_root / "public"):
        for path in source_root.rglob("*.py"):
            source = path.read_text(encoding="utf-8")
            assert "polaris.cells.factory" not in source, path
    assert "factory.pipeline" not in manifest["depends_on"]
    assert "factory.pipeline" not in catalog_cell["depends_on"]
    assert "factory.pipeline" not in context_pack["neighbors"]


def test_gr1c_public_contract_metadata_is_synced() -> None:
    cell_root = Path(__file__).resolve().parents[1]
    backend_root = cell_root.parents[3]
    manifest = yaml.safe_load((cell_root / "cell.yaml").read_text(encoding="utf-8"))
    catalog = yaml.safe_load((backend_root / "docs" / "graph" / "catalog" / "cells.yaml").read_text(encoding="utf-8"))
    catalog_cell = next(cell for cell in catalog["cells"] if cell["id"] == "runtime.projection")
    descriptor = json.loads((cell_root / "generated" / "descriptor.pack.json").read_text(encoding="utf-8"))
    context_pack = json.loads((cell_root / "generated" / "context.pack.json").read_text(encoding="utf-8"))
    descriptor_names = {
        item["name"]
        for item in descriptor["capabilities"]
        if item["defined_in"].startswith("polaris/cells/runtime/projection/public/")
    }

    for contracts in (manifest["public_contracts"], catalog_cell["public_contracts"]):
        assert "ProjectOutcomeFactoryOwnerQueryV1" in contracts["queries"]
        assert "FactoryChainOwnerObservationV1" in contracts["results"]
        assert "ProjectOutcomeFactoryOwnerBindingV1" in contracts["results"]
        assert "ProjectOutcomeOwnerObservationV1Error" in contracts["errors"]
    assert "ProjectOutcomeFactoryOwnerQueryV1" in context_pack["public_contracts"]["queries"]
    assert "FactoryChainOwnerObservationV1" in context_pack["public_contracts"]["results"]
    assert "ProjectOutcomeFactoryOwnerBindingV1" in context_pack["public_contracts"]["results"]
    assert "ProjectOutcomeOwnerObservationV1Error" in context_pack["public_contracts"]["errors"]
    assert {
        "FactoryChainOwnerObservationV1",
        "FactoryChainOwnerObservationPortV1",
        "ProjectOutcomeFactoryOwnerQueryV1",
        "ProjectOutcomeFactoryOwnerBindingV1",
        "ProjectOutcomeOwnerObservationV1Error",
        "bind_factory_chain_owner_observation_port",
        "query_project_outcome_with_factory_owner",
    } <= descriptor_names
