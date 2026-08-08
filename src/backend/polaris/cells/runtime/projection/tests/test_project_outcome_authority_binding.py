"""F1a tests for the all-owner ProjectOutcome authority seam."""

from __future__ import annotations

import hashlib
from dataclasses import fields, replace
from pathlib import Path

import pytest
from polaris.cells.runtime.projection.internal import (
    project_outcome_authority,
    project_outcome_factory_owner,
)
from polaris.cells.runtime.projection.public import (
    DeliveryAxisV1,
    FactoryChainOwnerObservationV1,
    ProjectOutcomeAuthorityBindingV1,
    ProjectOutcomeAuthorityQueryV1,
    ProjectOutcomeNonFactoryEvidenceRefsV1,
    ProjectOutcomeNonFactoryOwnerObservationPortV1,
    ProjectOutcomeNonFactoryOwnerObservationV1,
    ProjectOutcomeNonFactoryOwnerProjectionHashesV1,
    ProjectOutcomeOwnerObservationV1Error,
    ProjectOutcomeValidationV1Error,
    QaAxisV1,
    RunLedgerAxisV1,
    TaskBoundaryAxisV1,
    TaskRuntimeAxisV1,
    query_authoritative_project_outcome,
)
from polaris.cells.runtime.projection.public.bootstrap import (
    bind_factory_chain_owner_observation_port,
    bind_project_outcome_non_factory_owner_observation_port,
)


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class _FactoryPort:
    def __init__(self, observation: FactoryChainOwnerObservationV1) -> None:
        self.observation = observation
        self.calls: list[tuple[str, str]] = []

    async def observe_factory_chain(
        self,
        *,
        workspace: str,
        run_id: str,
    ) -> FactoryChainOwnerObservationV1:
        self.calls.append((workspace, run_id))
        return self.observation


class _NonFactoryPort:
    def __init__(self, observation: ProjectOutcomeNonFactoryOwnerObservationV1) -> None:
        self.observation = observation
        self.calls: list[tuple[str, str, str, str]] = []

    async def observe_project_outcome_non_factory(
        self,
        *,
        workspace: str,
        project_id: str,
        run_id: str,
        completion_contract_hash: str,
    ) -> ProjectOutcomeNonFactoryOwnerObservationV1:
        self.calls.append((workspace, project_id, run_id, completion_contract_hash))
        return self.observation


class _WrongResultNonFactoryPort:
    async def observe_project_outcome_non_factory(
        self,
        *,
        workspace: str,
        project_id: str,
        run_id: str,
        completion_contract_hash: str,
    ) -> ProjectOutcomeNonFactoryOwnerObservationV1:
        del workspace, project_id, run_id, completion_contract_hash
        return object()  # type: ignore[return-value]


@pytest.fixture(autouse=True)
def _reset_owner_ports(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        project_outcome_factory_owner,
        "_factory_chain_owner_observation_port",
        None,
        raising=False,
    )
    monkeypatch.setattr(
        project_outcome_authority,
        "_project_outcome_non_factory_owner_observation_port",
        None,
        raising=False,
    )


def _query(workspace: Path) -> ProjectOutcomeAuthorityQueryV1:
    return ProjectOutcomeAuthorityQueryV1(
        workspace=str(workspace),
        project_id="project-authority",
        run_id="run-authority",
        completion_contract_hash=_hash("completion-contract"),
    )


def _factory_observation(workspace: Path) -> FactoryChainOwnerObservationV1:
    projection_hash = _hash("factory-chain")
    return FactoryChainOwnerObservationV1(
        workspace=str(workspace.resolve()),
        run_id="run-authority",
        available=True,
        status="completed",
        chain_completed=True,
        event_refs=("factory:event:complete",),
        completion_event_ref="factory:event:complete",
        projection_hash=projection_hash,
    )


def _projection_hashes() -> ProjectOutcomeNonFactoryOwnerProjectionHashesV1:
    return ProjectOutcomeNonFactoryOwnerProjectionHashesV1(
        delivery=_hash("delivery"),
        qa=_hash("qa"),
        task_boundary=_hash("task-boundary"),
        task_runtime=_hash("task-runtime"),
        run_ledger=_hash("run-ledger"),
    )


def _non_factory_observation(
    workspace: Path,
    **overrides: object,
) -> ProjectOutcomeNonFactoryOwnerObservationV1:
    hashes = _projection_hashes()
    payload: dict[str, object] = {
        "workspace": str(workspace.resolve()),
        "project_id": "project-authority",
        "run_id": "run-authority",
        "completion_contract_hash": _hash("completion-contract"),
        "delivery": DeliveryAxisV1.VERIFIED,
        "qa": QaAxisV1.PASSED,
        "task_boundary": TaskBoundaryAxisV1.PASSED,
        "task_runtime": TaskRuntimeAxisV1.CONVERGED,
        "run_ledger": RunLedgerAxisV1.CLOSED,
        "evidence_refs": ProjectOutcomeNonFactoryEvidenceRefsV1(
            delivery=(hashes.delivery, "delivery:verified"),
            qa=(hashes.qa, "qa:passed"),
            task_boundary=(hashes.task_boundary, "task-boundary:passed"),
            task_runtime=(hashes.task_runtime, "task-runtime:converged"),
            run_ledger=(hashes.run_ledger, "run-ledger:closed"),
        ),
        "projection_hashes": hashes,
        "task_count": 2,
        "completed_task_count": 2,
    }
    payload.update(overrides)
    return ProjectOutcomeNonFactoryOwnerObservationV1(**payload)  # type: ignore[arg-type]


def _bind_green_ports(workspace: Path) -> tuple[_FactoryPort, _NonFactoryPort]:
    factory = _FactoryPort(_factory_observation(workspace))
    non_factory = _NonFactoryPort(_non_factory_observation(workspace))
    bind_factory_chain_owner_observation_port(factory)
    bind_project_outcome_non_factory_owner_observation_port(non_factory)
    return factory, non_factory


def _replace_identity(
    observation: ProjectOutcomeNonFactoryOwnerObservationV1,
    field_name: str,
    value: str,
) -> ProjectOutcomeNonFactoryOwnerObservationV1:
    if field_name == "workspace":
        return replace(observation, workspace=value)
    if field_name == "project_id":
        return replace(observation, project_id=value)
    if field_name == "run_id":
        return replace(observation, run_id=value)
    if field_name == "completion_contract_hash":
        return replace(observation, completion_contract_hash=value)
    raise AssertionError(f"unexpected identity field: {field_name}")


def test_authority_query_has_no_caller_axes_or_evidence_fields(tmp_path: Path) -> None:
    query = _query(tmp_path)

    assert [field.name for field in fields(ProjectOutcomeAuthorityQueryV1)] == [
        "workspace",
        "project_id",
        "run_id",
        "completion_contract_hash",
    ]
    assert not hasattr(query, "delivery")
    assert not hasattr(query, "evidence_refs")


@pytest.mark.asyncio
async def test_unbound_non_factory_owner_port_fails_closed(tmp_path: Path) -> None:
    bind_factory_chain_owner_observation_port(_FactoryPort(_factory_observation(tmp_path)))

    with pytest.raises(ProjectOutcomeOwnerObservationV1Error) as exc_info:
        await query_authoritative_project_outcome(_query(tmp_path))

    assert exc_info.value.error_code == "project_outcome_non_factory_owner_port_unbound"


@pytest.mark.asyncio
async def test_authority_service_rejects_wrong_exact_query_type() -> None:
    with pytest.raises(ProjectOutcomeOwnerObservationV1Error) as exc_info:
        await query_authoritative_project_outcome(object())  # type: ignore[arg-type]

    assert exc_info.value.error_code == "invalid_project_outcome_authority_query_type"


@pytest.mark.asyncio
async def test_non_factory_owner_result_requires_exact_contract_type(tmp_path: Path) -> None:
    bind_project_outcome_non_factory_owner_observation_port(_WrongResultNonFactoryPort())

    with pytest.raises(ProjectOutcomeOwnerObservationV1Error) as exc_info:
        await query_authoritative_project_outcome(_query(tmp_path))

    assert exc_info.value.error_code == "invalid_project_outcome_non_factory_owner_result_type"


def test_non_factory_owner_port_rebind_is_idempotent_and_conflict_fails(tmp_path: Path) -> None:
    first = _NonFactoryPort(_non_factory_observation(tmp_path))
    second = _NonFactoryPort(_non_factory_observation(tmp_path))

    assert isinstance(first, ProjectOutcomeNonFactoryOwnerObservationPortV1)
    bind_project_outcome_non_factory_owner_observation_port(first)
    bind_project_outcome_non_factory_owner_observation_port(first)
    with pytest.raises(ProjectOutcomeOwnerObservationV1Error) as exc_info:
        bind_project_outcome_non_factory_owner_observation_port(second)

    assert exc_info.value.error_code == "project_outcome_non_factory_owner_port_conflicting_rebind"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("workspace", "/tmp/other-project-outcome-workspace"),
        ("project_id", "other-project"),
        ("run_id", "other-run"),
        ("completion_contract_hash", _hash("other-contract")),
    ],
)
async def test_non_factory_owner_identity_or_contract_mismatch_fails_closed(
    tmp_path: Path,
    field_name: str,
    value: str,
) -> None:
    factory = _FactoryPort(_factory_observation(tmp_path))
    observation = _replace_identity(_non_factory_observation(tmp_path), field_name, value)
    bind_factory_chain_owner_observation_port(factory)
    bind_project_outcome_non_factory_owner_observation_port(_NonFactoryPort(observation))

    with pytest.raises(ProjectOutcomeOwnerObservationV1Error) as exc_info:
        await query_authoritative_project_outcome(_query(tmp_path))

    assert exc_info.value.error_code == "project_outcome_non_factory_owner_identity_mismatch"
    assert factory.calls == []


@pytest.mark.asyncio
async def test_fully_green_owner_facts_bind_authoritative_completion(tmp_path: Path) -> None:
    factory, non_factory = _bind_green_ports(tmp_path)

    result = await query_authoritative_project_outcome(_query(tmp_path))

    assert type(result) is ProjectOutcomeAuthorityBindingV1
    assert result.workspace == str(tmp_path.resolve())
    assert result.project_id == "project-authority"
    assert result.run_id == "run-authority"
    assert result.completion_contract_hash == _hash("completion-contract")
    assert result.outcome.completion_candidate is True
    assert result.outcome.authority_bound is True
    assert result.outcome.completed_verified is True
    assert "authority_bound" not in {field.name for field in fields(ProjectOutcomeAuthorityBindingV1)}
    assert "completed_verified" not in {field.name for field in fields(ProjectOutcomeAuthorityBindingV1)}
    assert result.factory_chain_projection_hash in result.factory_chain_evidence_refs
    for axis in ("delivery", "qa", "task_boundary", "task_runtime", "run_ledger"):
        projection_hash = getattr(result.non_factory_projection_hashes, axis)
        assert projection_hash
        assert projection_hash in getattr(result.non_factory_evidence_refs, axis)
    assert factory.calls == [(str(tmp_path.resolve()), "run-authority")]
    assert non_factory.calls == [
        (
            str(tmp_path.resolve()),
            "project-authority",
            "run-authority",
            _hash("completion-contract"),
        )
    ]


@pytest.mark.asyncio
async def test_non_green_owner_axis_is_bound_but_not_completed(tmp_path: Path) -> None:
    factory = _FactoryPort(_factory_observation(tmp_path))
    observation = _non_factory_observation(tmp_path, qa=QaAxisV1.FAILED)
    bind_factory_chain_owner_observation_port(factory)
    bind_project_outcome_non_factory_owner_observation_port(_NonFactoryPort(observation))

    result = await query_authoritative_project_outcome(_query(tmp_path))

    assert result.outcome.authority_bound is True
    assert result.outcome.completed_verified is False
    assert result.outcome.completion_candidate is False
    assert "qa" in result.outcome.blocking_axes


@pytest.mark.asyncio
@pytest.mark.parametrize("missing_kind", ["reference", "projection_hash"])
async def test_missing_owner_reference_or_hash_never_binds_authority(
    tmp_path: Path,
    missing_kind: str,
) -> None:
    hashes = _projection_hashes()
    if missing_kind == "reference":
        evidence_refs = ProjectOutcomeNonFactoryEvidenceRefsV1(
            delivery=(),
            qa=(hashes.qa,),
            task_boundary=(hashes.task_boundary,),
            task_runtime=(hashes.task_runtime,),
            run_ledger=(hashes.run_ledger,),
        )
        observation = _non_factory_observation(tmp_path, evidence_refs=evidence_refs)
    else:
        projection_hashes = replace(hashes, delivery="")
        evidence_refs = ProjectOutcomeNonFactoryEvidenceRefsV1(
            delivery=("delivery:verified",),
            qa=(hashes.qa,),
            task_boundary=(hashes.task_boundary,),
            task_runtime=(hashes.task_runtime,),
            run_ledger=(hashes.run_ledger,),
        )
        observation = _non_factory_observation(
            tmp_path,
            projection_hashes=projection_hashes,
            evidence_refs=evidence_refs,
        )
    bind_factory_chain_owner_observation_port(_FactoryPort(_factory_observation(tmp_path)))
    bind_project_outcome_non_factory_owner_observation_port(_NonFactoryPort(observation))

    with pytest.raises(ProjectOutcomeOwnerObservationV1Error) as exc_info:
        await query_authoritative_project_outcome(_query(tmp_path))

    assert exc_info.value.error_code == "project_outcome_owner_evidence_incomplete"


@pytest.mark.asyncio
async def test_nonempty_owner_hash_must_be_present_in_axis_evidence(tmp_path: Path) -> None:
    hashes = _projection_hashes()
    observation = _non_factory_observation(
        tmp_path,
        evidence_refs=ProjectOutcomeNonFactoryEvidenceRefsV1(
            delivery=("delivery:other",),
            qa=(hashes.qa,),
            task_boundary=(hashes.task_boundary,),
            task_runtime=(hashes.task_runtime,),
            run_ledger=(hashes.run_ledger,),
        ),
    )
    bind_factory_chain_owner_observation_port(_FactoryPort(_factory_observation(tmp_path)))
    bind_project_outcome_non_factory_owner_observation_port(_NonFactoryPort(observation))

    with pytest.raises(ProjectOutcomeOwnerObservationV1Error) as exc_info:
        await query_authoritative_project_outcome(_query(tmp_path))

    assert exc_info.value.error_code == "project_outcome_owner_projection_hash_not_bound"


@pytest.mark.asyncio
async def test_authoritative_outcome_cannot_be_reconstructed_without_private_seal(tmp_path: Path) -> None:
    _bind_green_ports(tmp_path)
    authoritative = await query_authoritative_project_outcome(_query(tmp_path))

    with pytest.raises(ProjectOutcomeValidationV1Error) as exc_info:
        replace(authoritative.outcome)

    assert exc_info.value.error_code == "unsupported_authority_binding_v1"


def _replace_binding_identity(
    binding: ProjectOutcomeAuthorityBindingV1,
    field_name: str,
    value: str,
) -> ProjectOutcomeAuthorityBindingV1:
    if field_name == "workspace":
        return replace(binding, workspace=value)
    if field_name == "project_id":
        return replace(binding, project_id=value)
    if field_name == "run_id":
        return replace(binding, run_id=value)
    if field_name == "completion_contract_hash":
        return replace(binding, completion_contract_hash=value)
    raise AssertionError(f"unexpected binding identity field: {field_name}")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("workspace", "/tmp/forged-project-outcome-workspace"),
        ("project_id", "forged-project"),
        ("run_id", "forged-run"),
        ("completion_contract_hash", _hash("forged-contract")),
    ],
)
async def test_authority_binding_cannot_be_retagged_without_private_seal(
    tmp_path: Path,
    field_name: str,
    value: str,
) -> None:
    _bind_green_ports(tmp_path)
    authoritative = await query_authoritative_project_outcome(_query(tmp_path))

    with pytest.raises(ProjectOutcomeOwnerObservationV1Error) as exc_info:
        _replace_binding_identity(authoritative, field_name, value)

    assert exc_info.value.error_code == "project_outcome_authority_binding_seal_required"


@pytest.mark.asyncio
async def test_authority_binding_direct_reconstruction_requires_private_seal(tmp_path: Path) -> None:
    _bind_green_ports(tmp_path)
    authoritative = await query_authoritative_project_outcome(_query(tmp_path))

    with pytest.raises(ProjectOutcomeOwnerObservationV1Error) as exc_info:
        ProjectOutcomeAuthorityBindingV1(
            outcome=authoritative.outcome,
            workspace=authoritative.workspace,
            project_id=authoritative.project_id,
            run_id=authoritative.run_id,
            completion_contract_hash=authoritative.completion_contract_hash,
            factory_chain_projection_hash=authoritative.factory_chain_projection_hash,
            factory_chain_evidence_refs=authoritative.factory_chain_evidence_refs,
            non_factory_projection_hashes=authoritative.non_factory_projection_hashes,
            non_factory_evidence_refs=authoritative.non_factory_evidence_refs,
        )

    assert exc_info.value.error_code == "project_outcome_authority_binding_seal_required"
