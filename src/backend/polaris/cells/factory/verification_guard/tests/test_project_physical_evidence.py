"""Attack tests for VerificationGuard physical-evidence dispatch."""

from __future__ import annotations

import hashlib
import json
from dataclasses import fields, replace
from pathlib import Path

import pytest
from polaris.cells.factory.verification_guard.internal import (
    project_completion_authority,
    project_physical_evidence,
)
from polaris.cells.factory.verification_guard.public import (
    ProjectCompletionOwnerObservationV1,
    ProjectCompletionOwnerObservationV1Error,
    RunProjectCompletionEvidenceBatchCommandV1,
    RunProjectCompletionEvidenceCommandV1,
    bind_project_completion_owner_observation_port,
    bind_project_completion_physical_evidence_port,
    run_project_completion_evidence,
    run_project_completion_evidence_batch,
)
from polaris.cells.factory.verification_guard.public.bootstrap import (
    build_project_completion_contract_observation,
    build_project_completion_physical_evidence_intent,
)
from polaris.cells.factory.verification_guard.public.contracts import (
    ProjectArtifactObligationObservationV1,
    ProjectCompletionContractObservationV1,
    ProjectCompletionObligationsObservationV1,
    ProjectCompletionPhysicalEvidenceEffectV1,
    ProjectCompletionPhysicalEvidenceIntentV1,
    ProjectEntrypointObligationObservationV1,
    ProjectKindAuthorityObservationV1,
    ProjectVerificationCommandAuthorityObservationV1,
    ProjectVerificationObligationObservationV1,
)


def _authority_hash(*, task_id: str, modality: str, argv: tuple[str, ...], cwd: str = ".") -> str:
    return hashlib.sha256(
        json.dumps(
            {
                "domain": "polaris.project_completion_verification_command_authority.v1",
                "task_id": task_id,
                "modality": modality,
                "argv": list(argv),
                "cwd": cwd,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


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
        authority_hash=hashlib.sha256(
            json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
        ).hexdigest(),
    )


def _contract(
    *,
    include_entrypoint_verifier: bool = True,
    include_cross_task_test: bool = False,
    include_auxiliary_inputs: bool = False,
) -> ProjectCompletionContractObservationV1:
    kind_authority = _kind_authority()
    test_argv = ("python", "-m", "pytest", "-q")
    entry_argv = ("python", "src/main.py")
    test_owner_task_id = "task-2" if include_cross_task_test else "task-1"
    test_coverage = ("artifact.test",) if include_cross_task_test else ("artifact.main",)
    authorities = [
        ProjectVerificationCommandAuthorityObservationV1(
            task_id=test_owner_task_id,
            modality="test",
            argv=test_argv,
            cwd=".",
            command="python -m pytest -q",
            authority_hash=_authority_hash(task_id=test_owner_task_id, modality="test", argv=test_argv),
        ),
    ]
    verification = [
        ProjectVerificationObligationObservationV1(
            obligation_id="verify.test",
            modality="test",
            command="python -m pytest -q",
            applicability="required",
            covers_obligation_ids=test_coverage,
            owner_task_id=test_owner_task_id,
            command_authority_hash=authorities[0].authority_hash,
        )
    ]
    if include_entrypoint_verifier:
        entry_authority = ProjectVerificationCommandAuthorityObservationV1(
            task_id="task-1",
            modality="entrypoint",
            argv=entry_argv,
            cwd=".",
            command="python src/main.py",
            authority_hash=_authority_hash(task_id="task-1", modality="entrypoint", argv=entry_argv),
        )
        authorities.append(entry_authority)
        verification.append(
            ProjectVerificationObligationObservationV1(
                obligation_id="verify.entrypoint",
                modality="entrypoint",
                command="python src/main.py",
                applicability="required",
                covers_obligation_ids=("entrypoint.cli",),
                owner_task_id="task-1",
                command_authority_hash=entry_authority.authority_hash,
            )
        )
    obligations = ProjectCompletionObligationsObservationV1(
        artifacts=tuple(
            [
                ProjectArtifactObligationObservationV1(
                obligation_id="artifact.main",
                path="src/main.py",
                semantic_role="source",
                applicability="required",
                owner_task_id="task-1",
                )
            ]
            + (
                [
                    ProjectArtifactObligationObservationV1(
                        obligation_id="artifact.test",
                        path="tests/test_main.py",
                        semantic_role="test",
                        applicability="required",
                        owner_task_id="task-2",
                    )
                ]
                if include_cross_task_test
                else []
            )
            + (
                [
                    ProjectArtifactObligationObservationV1(
                        obligation_id="artifact.entrypoint",
                        path="bin/run-app",
                        semantic_role="entrypoint",
                        applicability="required",
                        owner_task_id="task-1",
                    ),
                    ProjectArtifactObligationObservationV1(
                        obligation_id="artifact.assets",
                        path="fixtures/scenario.json",
                        semantic_role="assets",
                        applicability="required",
                        owner_task_id="task-1",
                    ),
                ]
                if include_auxiliary_inputs
                else []
            )
        ),
        entrypoints=(
            ProjectEntrypointObligationObservationV1(
                obligation_id="entrypoint.cli",
                kind="cli",
                applicability="required",
                owner_task_id="task-1",
                source_path="src/main.py",
                command="python src/main.py",
            ),
        ),
        verification=tuple(verification),
    )
    authorities.sort(key=lambda item: item.authority_hash)
    seed = {
        "schema_version": "polaris.project_completion_contract.v1",
        "project_id": "project-1",
        "run_id": "run-1",
        "project_kind": "application",
        "project_kind_authority": kind_authority.to_dict(),
        "pm_contract_hash": "a" * 64,
        "covered_task_ids": ["task-1", "task-2"] if include_cross_task_test else ["task-1"],
        "obligations": obligations.to_dict(),
        "completion_predicate_version": "predicate-v1",
        "verifier_policy_hash": "b" * 64,
        "verifier_policy_snapshot_hash": "c" * 64,
        "verification_command_authority": [item.to_dict() for item in authorities],
    }
    contract_hash = hashlib.sha256(
        json.dumps(seed, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return build_project_completion_contract_observation(
        contract_id=f"project-completion-{contract_hash[:24]}",
        contract_hash=contract_hash,
        project_id="project-1",
        run_id="run-1",
        project_kind="application",
        project_kind_authority=kind_authority,
        pm_contract_hash="a" * 64,
        covered_task_ids=("task-1", "task-2") if include_cross_task_test else ("task-1",),
        obligations=obligations,
        completion_predicate_version="predicate-v1",
        verifier_policy_hash="b" * 64,
        verifier_policy_snapshot_hash="c" * 64,
        verification_command_authority=tuple(authorities),
    )


class _OwnerPort:
    def __init__(self, workspace: Path, contract: ProjectCompletionContractObservationV1) -> None:
        self.workspace = workspace
        self.contract = contract

    def observe_project_completion(
        self,
        *,
        workspace: str,
        project_id: str,
        run_id: str,
        completion_contract_hash: str,
    ) -> ProjectCompletionOwnerObservationV1:
        del workspace, project_id, run_id, completion_contract_hash
        return ProjectCompletionOwnerObservationV1(
            workspace=str(self.workspace.resolve()),
            project_id=self.contract.project_id,
            run_id=self.contract.run_id,
            completion_contract_hash=self.contract.contract_hash,
            contract=self.contract,
            evidence=(),
            repair_coverage=(),
        )


class _PhysicalPort:
    def __init__(self) -> None:
        self.intents: list[ProjectCompletionPhysicalEvidenceIntentV1] = []

    def materialize_project_completion_evidence(
        self,
        intent: ProjectCompletionPhysicalEvidenceIntentV1,
        /,
    ) -> ProjectCompletionPhysicalEvidenceEffectV1:
        self.intents.append(intent)
        return ProjectCompletionPhysicalEvidenceEffectV1(
            code="physical_effect_recorded",
            spawned=intent.kind == "command",
            receipt_ref="execution-broker://receipt/one",
        )


@pytest.fixture(autouse=True)
def _reset_ports(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(project_completion_authority, "_project_completion_owner_observation_port", None)
    monkeypatch.setattr(project_physical_evidence, "_project_completion_physical_evidence_port", None)


def _bind(tmp_path: Path, contract: ProjectCompletionContractObservationV1) -> _PhysicalPort:
    physical = _PhysicalPort()
    bind_project_completion_owner_observation_port(_OwnerPort(tmp_path, contract))
    bind_project_completion_physical_evidence_port(physical)
    return physical


def _command(tmp_path: Path, contract: ProjectCompletionContractObservationV1, obligation_id: str):
    return RunProjectCompletionEvidenceCommandV1(
        workspace=str(tmp_path.resolve()),
        project_id=contract.project_id,
        run_id=contract.run_id,
        completion_contract_hash=contract.contract_hash,
        obligation_id=obligation_id,
    )


def test_public_command_accepts_identity_only_not_evidence_or_verdict() -> None:
    assert [item.name for item in fields(RunProjectCompletionEvidenceCommandV1)] == [
        "workspace",
        "project_id",
        "run_id",
        "completion_contract_hash",
        "obligation_id",
    ]


def test_artifact_dispatch_is_derived_from_exact_contract(tmp_path: Path) -> None:
    contract = _contract()
    physical = _bind(tmp_path, contract)

    result = run_project_completion_evidence(_command(tmp_path, contract, "artifact.main"))

    assert result.code == "physical_effect_recorded"
    assert not hasattr(result, "completed_verified")
    intent = physical.intents[0]
    assert intent.kind == "artifact"
    assert intent.obligation_id == "artifact.main"
    assert intent.owner_task_id == "task-1"
    assert intent.artifact_path == "src/main.py"
    assert intent.argv == ()


def test_batch_reads_owner_once_and_preserves_obligation_order(tmp_path: Path) -> None:
    contract = _contract()
    physical = _PhysicalPort()

    class _CountingOwner(_OwnerPort):
        calls = 0

        def observe_project_completion(self, **kwargs):  # type: ignore[no-untyped-def]
            self.calls += 1
            return super().observe_project_completion(**kwargs)

    owner = _CountingOwner(tmp_path, contract)
    bind_project_completion_owner_observation_port(owner)
    bind_project_completion_physical_evidence_port(physical)

    result = run_project_completion_evidence_batch(
        RunProjectCompletionEvidenceBatchCommandV1(
            workspace=str(tmp_path.resolve()),
            project_id=contract.project_id,
            run_id=contract.run_id,
            completion_contract_hash=contract.contract_hash,
            obligation_ids=("artifact.main", "verify.test", "entrypoint.cli"),
        )
    )

    assert owner.calls == 1
    assert [item.obligation_id for item in result.effects] == [
        "artifact.main",
        "verify.test",
        "entrypoint.cli",
    ]
    assert [item.obligation_id for item in physical.intents] == [
        "artifact.main",
        "verify.test",
        "entrypoint.cli",
    ]


def test_verifier_dispatch_pins_canonical_argv_cwd_and_inputs(tmp_path: Path) -> None:
    contract = _contract()
    physical = _bind(tmp_path, contract)

    run_project_completion_evidence(_command(tmp_path, contract, "verify.test"))

    intent = physical.intents[0]
    assert intent.kind == "command"
    assert intent.modality == "test"
    assert intent.argv == ("python", "-m", "pytest", "-q")
    assert intent.cwd == "."
    assert [(item.obligation_id, item.path) for item in intent.input_artifacts] == [("artifact.main", "src/main.py")]


def test_verifier_inputs_bind_cross_task_contract_sources(tmp_path: Path) -> None:
    """A verifier receipt must stale when any contract-authoritative source drifts."""

    contract = _contract(include_cross_task_test=True)
    physical = _bind(tmp_path, contract)

    run_project_completion_evidence(_command(tmp_path, contract, "verify.test"))

    intent = physical.intents[0]
    assert [(item.obligation_id, item.path) for item in intent.input_artifacts] == [
        ("artifact.main", "src/main.py"),
        ("artifact.test", "tests/test_main.py"),
    ]


def test_verifier_inputs_bind_entrypoint_assets_and_fixtures(tmp_path: Path) -> None:
    """Executable and fixture inputs are part of the immutable proof closure."""

    contract = _contract(include_auxiliary_inputs=True)
    physical = _bind(tmp_path, contract)

    run_project_completion_evidence(_command(tmp_path, contract, "verify.test"))

    intent = physical.intents[0]
    assert [(item.obligation_id, item.path) for item in intent.input_artifacts] == [
        ("artifact.assets", "fixtures/scenario.json"),
        ("artifact.entrypoint", "bin/run-app"),
        ("artifact.main", "src/main.py"),
    ]


def test_entrypoint_requires_real_typed_probe_authority(tmp_path: Path) -> None:
    contract = _contract(include_entrypoint_verifier=False)
    physical = _bind(tmp_path, contract)

    with pytest.raises(ProjectCompletionOwnerObservationV1Error) as exc_info:
        run_project_completion_evidence(_command(tmp_path, contract, "entrypoint.cli"))

    assert exc_info.value.error_code == "project_entrypoint_probe_authority_missing"
    assert physical.intents == []


def test_physical_intent_direct_construction_and_retag_are_rejected() -> None:
    contract = _contract()
    intent = build_project_completion_physical_evidence_intent(contract, "verify.test", workspace="/tmp")
    kwargs = {
        field: getattr(intent, field)
        for field in (
            "workspace",
            "project_id",
            "run_id",
            "completion_contract_hash",
            "obligation_id",
            "owner_task_id",
            "kind",
            "artifact_path",
            "modality",
            "argv",
            "cwd",
            "command_authority_hash",
            "input_artifacts",
            "timeout_seconds",
        )
    }
    with pytest.raises(ProjectCompletionOwnerObservationV1Error, match="sealed"):
        ProjectCompletionPhysicalEvidenceIntentV1(**kwargs)
    with pytest.raises(ProjectCompletionOwnerObservationV1Error, match="sealed"):
        replace(intent, obligation_id="verify.other")
