"""Adversarial tests for the VerificationGuard-owned contract observation."""

from __future__ import annotations

from dataclasses import replace

import pytest
from polaris.cells.factory.verification_guard.public.bootstrap import (
    build_project_completion_contract_observation,
)
from polaris.cells.factory.verification_guard.public.contracts import (
    ProjectArtifactObligationObservationV1,
    ProjectCompletionContractObservationV1,
    ProjectCompletionObligationsObservationV1,
    ProjectCompletionOwnerObservationV1,
    ProjectEntrypointObligationObservationV1,
    ProjectKindAuthorityObservationV1,
    ProjectVerificationCommandAuthorityObservationV1,
    ProjectVerificationObligationObservationV1,
)

_HASH_A = "a" * 64
_HASH_B = "b" * 64


def _kind_authority() -> ProjectKindAuthorityObservationV1:
    payload = {
        "domain": "polaris.project_completion_project_kind_authority.v1",
        "project_kind": "application",
        "source_ref": "factory://project-kind/project-1",
        "source_hash": "8" * 64,
        "justification": "Factory project definition declares an application.",
    }
    return ProjectKindAuthorityObservationV1(
        project_kind="application",
        source_ref=payload["source_ref"],
        source_hash=payload["source_hash"],
        justification=payload["justification"],
        authority_hash=_canonical_hash(payload),
    )


def _contract() -> ProjectCompletionContractObservationV1:
    kind_authority = _kind_authority()
    authority = ProjectVerificationCommandAuthorityObservationV1(
        task_id="task-1",
        modality="test",
        argv=("python", "-m", "pytest", "-q"),
        cwd=".",
        command="python -m pytest -q",
        authority_hash="c" * 64,
    )
    obligations = ProjectCompletionObligationsObservationV1(
        artifacts=(
            ProjectArtifactObligationObservationV1(
                obligation_id="artifact.main",
                path="src/main.py",
                semantic_role="source",
                applicability="required",
                owner_task_id="task-1",
            ),
        ),
        entrypoints=(
            ProjectEntrypointObligationObservationV1(
                obligation_id="entrypoint.cli",
                kind="cli",
                applicability="required",
                owner_task_id="task-1",
                source_path="src/main.py",
                runtime_path=None,
                command="python src/main.py",
            ),
        ),
        verification=(
            ProjectVerificationObligationObservationV1(
                obligation_id="verify.test",
                modality="test",
                command="python -m pytest -q",
                applicability="required",
                covers_obligation_ids=("artifact.main",),
                owner_task_id="task-1",
                command_authority_hash=authority.authority_hash,
            ),
        ),
    )
    seed = {
        "schema_version": "polaris.project_completion_contract.v1",
        "project_id": "project-1",
        "run_id": "run-1",
        "project_kind": "application",
        "project_kind_authority": kind_authority.to_dict(),
        "pm_contract_hash": _HASH_A,
        "covered_task_ids": ["task-1"],
        "obligations": obligations.to_dict(),
        "completion_predicate_version": "predicate-v1",
        "verifier_policy_hash": _HASH_B,
        "verifier_policy_snapshot_hash": "d" * 64,
        "verification_command_authority": [authority.to_dict()],
    }
    return build_project_completion_contract_observation(
        contract_id="project-completion-" + _canonical_hash(seed)[:24],
        contract_hash=_canonical_hash(seed),
        project_id="project-1",
        run_id="run-1",
        project_kind="application",
        project_kind_authority=kind_authority,
        pm_contract_hash=_HASH_A,
        covered_task_ids=("task-1",),
        obligations=obligations,
        completion_predicate_version="predicate-v1",
        verifier_policy_hash=_HASH_B,
        verifier_policy_snapshot_hash="d" * 64,
        verification_command_authority=(authority,),
    )


def _canonical_hash(payload: object) -> str:
    import hashlib
    import json

    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
    ).hexdigest()


def test_contract_observation_is_complete_and_hash_bound() -> None:
    contract = _contract()

    assert contract.contract_hash == _canonical_hash(contract.to_seed_dict())
    assert contract.obligations.artifacts[0].owner_task_id == "task-1"
    assert contract.verification_command_authority[0].argv == ("python", "-m", "pytest", "-q")


def test_contract_observation_cannot_be_retagged_with_replace() -> None:
    with pytest.raises(ValueError, match="sealed"):
        replace(_contract(), project_id="project-other")


def test_owner_observation_rejects_contract_lookalike() -> None:
    contract = _contract()

    with pytest.raises(TypeError, match="exact ProjectCompletionContractObservationV1"):
        ProjectCompletionOwnerObservationV1(
            workspace="/tmp/workspace",
            project_id=contract.project_id,
            run_id=contract.run_id,
            completion_contract_hash=contract.contract_hash,
            contract=object(),  # type: ignore[arg-type]
            evidence=(),
            repair_coverage=(),
        )


@pytest.mark.parametrize(("field", "value"), (("project_id", "project-other"), ("run_id", "run-other")))
def test_owner_observation_rejects_cross_identity_contract(field: str, value: str) -> None:
    contract = _contract()
    kwargs = {
        "workspace": "/tmp/workspace",
        "project_id": contract.project_id,
        "run_id": contract.run_id,
        "completion_contract_hash": contract.contract_hash,
        "contract": contract,
        "evidence": (),
        "repair_coverage": (),
    }
    kwargs[field] = value

    with pytest.raises(ValueError, match="identity"):
        ProjectCompletionOwnerObservationV1(**kwargs)
