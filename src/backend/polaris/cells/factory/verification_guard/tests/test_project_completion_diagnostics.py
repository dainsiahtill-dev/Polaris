"""Authority and residual-DAG tests for project completion diagnostics."""

from __future__ import annotations

import hashlib
import json
from dataclasses import fields, replace
from pathlib import Path

import pytest
from polaris.cells.factory.verification_guard.internal import project_completion_authority
from polaris.cells.factory.verification_guard.public import (
    ProjectCompletionDiagnosticsV1,
    ProjectCompletionDiagnosticV1,
    ProjectCompletionEvidenceV1,
    ProjectCompletionOwnerObservationPortV1,
    ProjectCompletionOwnerObservationV1,
    ProjectCompletionOwnerObservationV1Error,
    ProjectRepairCoverageV1,
    QueryProjectCompletionDiagnosticsV1,
    bind_project_completion_owner_observation_port,
    query_project_completion_diagnostics,
)
from polaris.cells.factory.verification_guard.public.bootstrap import (
    build_project_completion_contract_observation,
)
from polaris.cells.factory.verification_guard.public.contracts import (
    _PROJECT_COMPLETION_DIAGNOSTICS_AUTHORITY_TOKEN,
    ProjectArtifactObligationObservationV1,
    ProjectCompletionContractObservationV1,
    ProjectCompletionObligationsObservationV1,
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
        authority_hash=hashlib.sha256(
            json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
        ).hexdigest(),
    )


def _contract() -> ProjectCompletionContractObservationV1:
    kind_authority = _kind_authority()
    command_authority = (
        ProjectVerificationCommandAuthorityObservationV1(
            task_id="task-1",
            modality="environment_prep",
            argv=("python", "-m", "pip", "--version"),
            cwd=".",
            command="python -m pip --version",
            authority_hash="c" * 64,
        ),
        ProjectVerificationCommandAuthorityObservationV1(
            task_id="task-1",
            modality="lint",
            argv=("ruff", "check", "src"),
            cwd=".",
            command="ruff check src",
            authority_hash="d" * 64,
        ),
        ProjectVerificationCommandAuthorityObservationV1(
            task_id="task-2",
            modality="test",
            argv=("pytest", "-q"),
            cwd=".",
            command="pytest -q",
            authority_hash="e" * 64,
        ),
        ProjectVerificationCommandAuthorityObservationV1(
            task_id="task-1",
            modality="entrypoint",
            argv=("python", "src/main.py"),
            cwd=".",
            command="python src/main.py",
            authority_hash="f" * 64,
        ),
    )
    authority_by_modality = {item.modality: item.authority_hash for item in command_authority}
    obligations = ProjectCompletionObligationsObservationV1(
        artifacts=(
            ProjectArtifactObligationObservationV1(
                obligation_id="artifact.main",
                path="src/main.py",
                semantic_role="entrypoint",
                applicability="required",
                owner_task_id="task-1",
            ),
            ProjectArtifactObligationObservationV1(
                obligation_id="artifact.test",
                path="tests/test_main.py",
                semantic_role="test",
                applicability="required",
                owner_task_id="task-2",
            ),
            ProjectArtifactObligationObservationV1(
                obligation_id="artifact.docs",
                path="README.md",
                semantic_role="docs",
                applicability="optional",
                owner_task_id="task-2",
            ),
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
        verification=(
            ProjectVerificationObligationObservationV1(
                obligation_id="verify.environment",
                modality="environment_prep",
                command="python -m pip --version",
                applicability="required",
                covers_obligation_ids=("artifact.main",),
                owner_task_id="task-1",
                command_authority_hash=authority_by_modality["environment_prep"],
            ),
            ProjectVerificationObligationObservationV1(
                obligation_id="verify.lint",
                modality="lint",
                command="ruff check src",
                applicability="required",
                covers_obligation_ids=("artifact.main",),
                owner_task_id="task-1",
                command_authority_hash=authority_by_modality["lint"],
            ),
            ProjectVerificationObligationObservationV1(
                obligation_id="verify.test",
                modality="test",
                command="pytest -q",
                applicability="required",
                covers_obligation_ids=("artifact.test",),
                owner_task_id="task-2",
                command_authority_hash=authority_by_modality["test"],
            ),
            ProjectVerificationObligationObservationV1(
                obligation_id="verify.entrypoint",
                modality="entrypoint",
                command="python src/main.py",
                applicability="required",
                covers_obligation_ids=("entrypoint.cli",),
                owner_task_id="task-1",
                command_authority_hash=authority_by_modality["entrypoint"],
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
        "covered_task_ids": ["task-1", "task-2"],
        "obligations": obligations.to_dict(),
        "completion_predicate_version": "predicate-v1",
        "verifier_policy_hash": _HASH_B,
        "verifier_policy_snapshot_hash": "9" * 64,
        "verification_command_authority": [item.to_dict() for item in command_authority],
    }
    contract_hash = hashlib.sha256(
        json.dumps(seed, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
    ).hexdigest()
    return build_project_completion_contract_observation(
        contract_id=f"project-completion-{contract_hash[:24]}",
        contract_hash=contract_hash,
        project_id="project-1",
        run_id="run-1",
        project_kind="application",
        project_kind_authority=kind_authority,
        pm_contract_hash=_HASH_A,
        covered_task_ids=("task-1", "task-2"),
        obligations=obligations,
        completion_predicate_version="predicate-v1",
        verifier_policy_hash=_HASH_B,
        verifier_policy_snapshot_hash="9" * 64,
        verification_command_authority=command_authority,
    )


def _query(workspace: Path, contract: ProjectCompletionContractObservationV1) -> QueryProjectCompletionDiagnosticsV1:
    return QueryProjectCompletionDiagnosticsV1(
        workspace=str(workspace.resolve()),
        project_id=contract.project_id,
        run_id=contract.run_id,
        completion_contract_hash=contract.contract_hash,
    )


def _evidence(
    workspace: Path,
    contract: ProjectCompletionContractObservationV1,
    obligation_id: str,
    owner_task_id: str,
    *,
    status: str = "passed",
    verification: bool = False,
) -> ProjectCompletionEvidenceV1:
    return ProjectCompletionEvidenceV1(
        workspace=str(workspace.resolve()),
        project_id=contract.project_id,
        run_id=contract.run_id,
        completion_contract_hash=contract.contract_hash,
        obligation_id=obligation_id,
        owner_task_id=owner_task_id,
        owner_module_id=("control_plane.run_ledger" if verification else "runtime.task_runtime"),
        status=status,  # type: ignore[arg-type]
        owner_evidence_refs=(f"owner://{owner_task_id}/{obligation_id}",),
        verifier_receipt_ref=(f"receipt://{obligation_id}" if verification else None),
        verifier_exit_code=(0 if verification else None),
    )


def _full_evidence(
    workspace: Path, contract: ProjectCompletionContractObservationV1
) -> tuple[ProjectCompletionEvidenceV1, ...]:
    return (
        _evidence(workspace, contract, "artifact.main", "task-1"),
        _evidence(workspace, contract, "artifact.test", "task-2"),
        _evidence(workspace, contract, "entrypoint.cli", "task-1"),
        _evidence(workspace, contract, "verify.environment", "task-1", verification=True),
        _evidence(workspace, contract, "verify.lint", "task-1", verification=True),
        _evidence(workspace, contract, "verify.test", "task-2", verification=True),
        _evidence(workspace, contract, "verify.entrypoint", "task-1", verification=True),
    )


class _Port:
    def __init__(self, observation: ProjectCompletionOwnerObservationV1) -> None:
        self.observation = observation

    def observe_project_completion(
        self,
        *,
        workspace: str,
        project_id: str,
        run_id: str,
        completion_contract_hash: str,
    ) -> ProjectCompletionOwnerObservationV1:
        del workspace, project_id, run_id, completion_contract_hash
        return self.observation


class _LookalikePort:
    def observe_project_completion(
        self,
        *,
        workspace: str,
        project_id: str,
        run_id: str,
        completion_contract_hash: str,
    ) -> ProjectCompletionOwnerObservationV1:
        del workspace, project_id, run_id, completion_contract_hash
        return object()  # type: ignore[return-value]


@pytest.fixture(autouse=True)
def _reset_owner_port(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(project_completion_authority, "_project_completion_owner_observation_port", None)


def test_production_query_has_exact_identity_only(tmp_path: Path) -> None:
    query = _query(tmp_path, _contract())

    assert [item.name for item in fields(query)] == [
        "workspace",
        "project_id",
        "run_id",
        "completion_contract_hash",
    ]
    assert not hasattr(query, "evidence")
    assert not hasattr(query, "repair_coverage")


def test_owner_evidence_hash_binds_all_authority_fields(tmp_path: Path) -> None:
    contract = _contract()
    evidence = _evidence(tmp_path, contract, "verify.test", "task-2", verification=True)

    variants = (
        replace(evidence, run_id="run-other"),
        replace(evidence, completion_contract_hash="c" * 64),
        replace(evidence, owner_task_id="task-1"),
        replace(evidence, owner_module_id="other.owner"),
        replace(evidence, status="failed"),
        replace(evidence, verifier_receipt_ref="receipt://other"),
        replace(evidence, verifier_exit_code=1),
    )
    assert len({evidence.owner_evidence_hash, *(item.owner_evidence_hash for item in variants)}) == 8


def test_unbound_owner_port_fails_closed(tmp_path: Path) -> None:
    with pytest.raises(ProjectCompletionOwnerObservationV1Error) as exc_info:
        query_project_completion_diagnostics(_query(tmp_path, _contract()))

    assert exc_info.value.error_code == "project_completion_owner_port_unbound"


def test_owner_port_requires_exact_result_not_lookalike(tmp_path: Path) -> None:
    bind_project_completion_owner_observation_port(_LookalikePort())

    with pytest.raises(ProjectCompletionOwnerObservationV1Error) as exc_info:
        query_project_completion_diagnostics(_query(tmp_path, _contract()))

    assert exc_info.value.error_code == "invalid_project_completion_owner_observation_type"


def test_authoritative_query_returns_owner_task_bound_diagnostics(tmp_path: Path) -> None:
    contract = _contract()
    rows = _full_evidence(tmp_path, contract)
    rows = tuple(row for row in rows if row.obligation_id != "artifact.test")
    observation = ProjectCompletionOwnerObservationV1(
        workspace=str(tmp_path.resolve()),
        project_id=contract.project_id,
        run_id=contract.run_id,
        completion_contract_hash=contract.contract_hash,
        contract=contract,
        evidence=rows,
        repair_coverage=(),
    )
    port = _Port(observation)
    assert isinstance(port, ProjectCompletionOwnerObservationPortV1)
    bind_project_completion_owner_observation_port(port)

    result = query_project_completion_diagnostics(_query(tmp_path, contract))

    diagnostic = next(item for item in result.diagnostics if item.obligation_id == "artifact.test")
    assert result.authority_bound is True
    assert diagnostic.owner_task_id == "task-2"
    assert diagnostic.evidence_state == "missing"
    assert "artifact.test" in result.missing_obligation_ids
    assert "artifact.test" not in result.failed_obligation_ids
    assert diagnostic.repair_coverage == "unknown"
    assert diagnostic.retry_class == "owner_rework"


def test_ready_entrypoint_controlled_termination_is_not_rejected_by_exit_code(tmp_path: Path) -> None:
    """A readiness-proven service is successful even after controlled SIGTERM.

    ExecutionBroker derives ``status=passed`` only after the entrypoint became
    ready and the platform terminated it deliberately.  VerificationGuard must
    not reinterpret that expected negative exit code as a verifier failure.
    """

    contract = _contract()
    rows = tuple(
        replace(row, verifier_exit_code=-15)
        if row.obligation_id == "verify.entrypoint"
        else row
        for row in _full_evidence(tmp_path, contract)
    )
    observation = ProjectCompletionOwnerObservationV1(
        workspace=str(tmp_path.resolve()),
        project_id=contract.project_id,
        run_id=contract.run_id,
        completion_contract_hash=contract.contract_hash,
        contract=contract,
        evidence=rows,
        repair_coverage=(),
    )
    bind_project_completion_owner_observation_port(_Port(observation))

    result = query_project_completion_diagnostics(_query(tmp_path, contract))

    assert result.diagnostics == ()
    assert result.failed_obligation_ids == ()
    assert "verify.entrypoint" in result.passed_obligation_ids


def test_owner_bundle_and_diagnostics_cannot_be_retagged_with_replace(tmp_path: Path) -> None:
    contract = _contract()
    observation = ProjectCompletionOwnerObservationV1(
        workspace=str(tmp_path.resolve()),
        project_id=contract.project_id,
        run_id=contract.run_id,
        completion_contract_hash=contract.contract_hash,
        contract=contract,
        evidence=_full_evidence(tmp_path, contract),
        repair_coverage=(),
    )
    bind_project_completion_owner_observation_port(_Port(observation))
    result = query_project_completion_diagnostics(_query(tmp_path, contract))
    bundle = project_completion_authority.observe_project_completion_owner(_query(tmp_path, contract))

    with pytest.raises(ProjectCompletionOwnerObservationV1Error, match="seal"):
        replace(bundle, project_id="other-project")
    with pytest.raises(ProjectCompletionOwnerObservationV1Error, match="seal"):
        replace(result, project_id="other-project")


def _diagnostic(diagnostic_id: str, dependency_ids: tuple[str, ...]) -> ProjectCompletionDiagnosticV1:
    return ProjectCompletionDiagnosticV1(
        diagnostic_id=diagnostic_id,
        archetype="missing_required_artifact",
        evidence_state="missing",
        primary_module_id="runtime.task_runtime",
        obligation_id=f"obligation.{diagnostic_id}",
        owner_task_id=f"task.{diagnostic_id}",
        affected_target=f"src/{diagnostic_id}.py",
        owner_evidence_refs=(),
        retry_class=("dependency_blocked" if dependency_ids else "owner_rework"),
        allowed_next_action=("wait_for_dependencies" if dependency_ids else "publish_owner_rework"),
        dependency_ids=dependency_ids,
        repair_coverage="unknown",
        repair_source_tool=None,
        repair_coverage_evidence_ref=None,
        repair_coverage_evidence_hash=None,
        required_verifier_ids=(),
    )


@pytest.mark.parametrize(
    "diagnostics",
    [
        (_diagnostic("one", ("two",)), _diagnostic("two", ("one",))),
        (
            _diagnostic("one", ("two",)),
            _diagnostic("two", ("three",)),
            _diagnostic("three", ("one",)),
        ),
    ],
)
def test_diagnostics_reject_full_dependency_cycles(
    diagnostics: tuple[ProjectCompletionDiagnosticV1, ...],
) -> None:
    with pytest.raises(ValueError, match="acyclic"):
        ProjectCompletionDiagnosticsV1(
            workspace="/tmp/workspace",
            project_id="project-1",
            run_id="run-1",
            completion_contract_hash="c" * 64,
            owner_bundle_hash="d" * 64,
            diagnostics=diagnostics,
            passed_obligation_ids=(),
            missing_obligation_ids=tuple(item.obligation_id for item in diagnostics),
            failed_obligation_ids=(),
            non_blocking_obligation_ids=(),
            _authority_token=_PROJECT_COMPLETION_DIAGNOSTICS_AUTHORITY_TOKEN,
        )


def test_caller_cannot_claim_executable_repair_coverage(tmp_path: Path) -> None:
    contract = _contract()
    query = _query(tmp_path, contract)

    with pytest.raises(TypeError):
        QueryProjectCompletionDiagnosticsV1(
            workspace=query.workspace,
            project_id=query.project_id,
            run_id=query.run_id,
            completion_contract_hash=query.completion_contract_hash,
            repair_coverage=(
                ProjectRepairCoverageV1(
                    workspace=query.workspace,
                    project_id=query.project_id,
                    run_id=query.run_id,
                    completion_contract_hash=query.completion_contract_hash,
                    obligation_id="artifact.test",
                    owner_task_id="task-2",
                    status="executable_runtime",
                    evidence_ref="director-runtime://coverage",
                    source_tool="fake_tool",
                ),
            ),
        )
