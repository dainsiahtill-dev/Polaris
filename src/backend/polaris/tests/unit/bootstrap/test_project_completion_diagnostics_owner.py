"""Bootstrap owner adapter tests for project completion diagnostics."""

from __future__ import annotations

import hashlib
import sys
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

import pytest
from polaris.bootstrap import project_completion_diagnostics_owner as adapter_module
from polaris.cells.chief_engineer.blueprint.public import (
    ArtifactObligationV1,
    EntrypointObligationV1,
    ProjectCompletionContractV1,
    ProjectCompletionObligationsV1,
    ProjectKindAuthorityV1,
    VerificationCommandAuthorityV1,
    VerificationObligationV1,
)
from polaris.cells.control_plane.run_ledger.public import RunLedgerProjectionResultV1
from polaris.cells.control_plane.verifier_policy.public import VerifierCommandPolicyDecisionV1
from polaris.cells.factory.verification_guard.public import ProjectCompletionOwnerObservationV1Error
from polaris.cells.factory.verification_guard.public.contracts import ProjectRepairCoverageV1
from polaris.cells.runtime.execution_broker.internal import project_verification_authority as authority_module
from polaris.cells.runtime.execution_broker.public.project_verification import (
    ConsumeProjectVerificationCapabilityCommandV1,
    ProjectVerificationProcessResultV1,
    RecordProjectArtifactCommandV1,
    ResolveProjectVerificationAuthorityQueryV1,
    authorize_project_verification_command,
    record_project_artifact,
    run_project_verification,
)
from polaris.cells.runtime.task_runtime.public import ObservableTaskRowsProjectionV1


@pytest.fixture(autouse=True)
def _platform_verifier_policy(monkeypatch: pytest.MonkeyPatch) -> None:
    """Unit owner tests isolate Run Ledger/capability behavior from policy catalog fixtures."""

    def _authorize(query):
        executable_path = Path(sys.executable).absolute()
        executable_realpath = executable_path.resolve(strict=True)
        return VerifierCommandPolicyDecisionV1(
            authorized=True,
            error_code="",
            detail="authorized test profile",
            profile_id=f"test.{query.modality}",
            normalized_argv=query.argv,
            normalized_cwd=query.cwd,
            input_obligation_ids=query.input_obligation_ids,
            executable_path=str(executable_path),
            executable_realpath=str(executable_realpath),
            executable_hash=hashlib.sha256(executable_realpath.read_bytes()).hexdigest(),
            policy_decision_hash="f" * 64,
        )

    monkeypatch.setattr(adapter_module, "evaluate_verifier_command_policy", _authorize)


def _contract() -> ProjectCompletionContractV1:
    command_authority = (
        VerificationCommandAuthorityV1(
            task_id="task-1", modality="environment_prep", argv=("python", "-m", "pip", "check")
        ),
        VerificationCommandAuthorityV1(task_id="task-1", modality="lint", argv=("ruff", "check", "src")),
        VerificationCommandAuthorityV1(task_id="task-2", modality="test", argv=("pytest", "-q")),
        VerificationCommandAuthorityV1(task_id="task-1", modality="entrypoint", argv=("python", "src/main.py")),
    )
    authority_by_modality = {item.modality: item.authority_hash for item in command_authority}
    return ProjectCompletionContractV1(
        project_id="project-1",
        run_id="run-1",
        project_kind="application",
        project_kind_authority=ProjectKindAuthorityV1(
            project_kind="application",
            source_ref="factory://project-kind/project-1",
            source_hash="8" * 64,
            justification="Factory project definition declares an application.",
        ),
        pm_contract_hash="a" * 64,
        covered_task_ids=("task-1", "task-2"),
        obligations=ProjectCompletionObligationsV1(
            artifacts=(
                ArtifactObligationV1("artifact.main", "src/main.py", "entrypoint", "required", "task-1"),
                ArtifactObligationV1("artifact.test", "tests/test_main.py", "test", "required", "task-2"),
            ),
            entrypoints=(
                EntrypointObligationV1(
                    "entrypoint.cli", "cli", "required", "task-1", "src/main.py", None, "python src/main.py"
                ),
            ),
            verification=(
                VerificationObligationV1(
                    "verify.environment",
                    "environment_prep",
                    "python -m pip check",
                    "required",
                    ("artifact.main",),
                    "task-1",
                    authority_by_modality["environment_prep"],
                ),
                VerificationObligationV1(
                    "verify.lint",
                    "lint",
                    "ruff check src",
                    "required",
                    ("artifact.main",),
                    "task-1",
                    authority_by_modality["lint"],
                ),
                VerificationObligationV1(
                    "verify.test",
                    "test",
                    "pytest -q",
                    "required",
                    ("artifact.test",),
                    "task-2",
                    authority_by_modality["test"],
                ),
                VerificationObligationV1(
                    "verify.entrypoint",
                    "entrypoint",
                    "python src/main.py",
                    "required",
                    ("entrypoint.cli",),
                    "task-1",
                    authority_by_modality["entrypoint"],
                ),
            ),
        ),
        completion_predicate_version="predicate-v1",
        verifier_policy_hash="b" * 64,
        verifier_policy_snapshot_hash="d" * 64,
        verification_command_authority=command_authority,
    )


def _task_projection(workspace: Path, *, authoritative: bool = True) -> ObservableTaskRowsProjectionV1:
    return ObservableTaskRowsProjectionV1(
        workspace=str(workspace.resolve()),
        source=("task_runtime.execution_fact" if authoritative else "legacy"),
        authoritative=authoritative,
        degraded=not authoritative,
        rows=tuple(
            {
                "task_id": task_id,
                "workflow_run_id": f"workflow-{task_id}",
                "factory_run_id": "run-1",
                "status": "completed",
                "execution_state": "completed",
                "fact_event_seq": index,
            }
            for index, task_id in enumerate(("task-1", "task-2"), start=1)
        ),
    )


def _ledger(contract: ProjectCompletionContractV1) -> RunLedgerProjectionResultV1:
    latest = {
        task_id: {
            "task_id": task_id,
            "run_id": f"workflow-{task_id}",
            "ok": True,
            "status": "completed_verified",
            "evidence_refs": [f"boundary://{task_id}"],
        }
        for task_id in ("task-1", "task-2")
    }
    gates = [
        {
            "content_id": f"gate-{modality}",
            "job_token_id": "job-token-1",
            "evidence_modalities": {modality: {"present": True, "ok": True, "metadata": {"exit_code": 0}}},
        }
        for modality in ("lint", "test", "entrypoint")
    ]
    return RunLedgerProjectionResultV1(
        projection={
            "query_scope": {"run_id": "run-1", "factory_run_id": "run-1", "project_id": "project-1"},
            "run_projection": {
                "capability": {
                    "ok": True,
                    "issues": [],
                    "latest_contract_hash": contract.contract_hash,
                    "job_token_ids": ["job-token-1"],
                    "latest_token_id": "job-token-1",
                },
                "evidence_policy": {
                    "enabled_modalities": ["environment_prep", "lint", "test", "entrypoint"],
                    "required_modalities": ["environment_prep", "test", "entrypoint"],
                },
                "gates": gates,
            },
            "task_boundary": {"latest_by_task": latest},
        }
    )


def _patch_owners(
    monkeypatch: pytest.MonkeyPatch, workspace: Path, *, authoritative: bool = True
) -> ProjectCompletionContractV1:
    contract = _contract()
    monkeypatch.setattr(
        authority_module,
        "_EXECUTION_AUTHORITY_PORT",
        adapter_module.PROJECT_COMPLETION_OWNER_OBSERVATION_ADAPTER,
    )
    monkeypatch.setattr(adapter_module, "query_project_completion_contract", lambda query: contract)
    monkeypatch.setattr(
        adapter_module,
        "query_observable_task_rows",
        lambda requested_workspace: _task_projection(workspace, authoritative=authoritative),
    )
    monkeypatch.setattr(adapter_module, "read_run_ledger_projection", lambda query: _ledger(contract))
    return contract


class _PhysicalRunner:
    def __init__(self, *, exit_code: int) -> None:
        self.exit_code = exit_code

    def run(
        self,
        *,
        name: str,
        argv: tuple[str, ...],
        cwd: str,
        timeout_seconds: float,
        log_path: str,
        metadata: dict[str, str],
        on_launched: Callable[[str, int | None, str | None], None],
    ) -> ProjectVerificationProcessResultV1:
        del name, argv, cwd, timeout_seconds, log_path, metadata
        on_launched("bootstrap-test-execution", None, None)
        return ProjectVerificationProcessResultV1(
            exit_code=self.exit_code,
            timed_out=False,
            output_bytes=f"exit={self.exit_code}\n".encode(),
        )


def test_capability_consume_is_one_use_and_fenced_to_attempt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    contract = _patch_owners(monkeypatch, tmp_path)
    authority = adapter_module.PROJECT_COMPLETION_OWNER_OBSERVATION_ADAPTER.resolve_project_verification_authority(
        ResolveProjectVerificationAuthorityQueryV1(
            workspace=str(tmp_path),
            project_id=contract.project_id,
            run_id=contract.run_id,
            completion_contract_hash=contract.contract_hash,
            obligation_id="verify.test",
        )
    )
    command = ConsumeProjectVerificationCapabilityCommandV1(
        **{name: getattr(authority, name) for name in authority.__dataclass_fields__},
        effect_key="command:" + "1" * 64,
        attempt_id="2" * 64,
        _authority_token=authority_module._CAPABILITY_COMMAND_SEAL,
    )

    first = adapter_module.PROJECT_COMPLETION_OWNER_OBSERVATION_ADAPTER.consume_project_verification_execution_capability(
        command
    )
    with pytest.raises(ProjectCompletionOwnerObservationV1Error, match="one-use"):
        adapter_module.PROJECT_COMPLETION_OWNER_OBSERVATION_ADAPTER.consume_project_verification_execution_capability(
            command
        )

    assert first.attempt_id == "2" * 64


def test_exact_owner_artifact_receipt_maps_to_pass_and_drift_returns_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    contract = _patch_owners(monkeypatch, tmp_path)
    source = tmp_path / "src" / "main.py"
    source.parent.mkdir(parents=True)
    source.write_text("print('one')\n", encoding="utf-8")
    receipt = record_project_artifact(
        RecordProjectArtifactCommandV1(
            workspace=str(tmp_path),
            project_id=contract.project_id,
            run_id=contract.run_id,
            completion_contract_hash=contract.contract_hash,
            obligation_id="artifact.main",
            owner_task_id="task-1",
            path="src/main.py",
        )
    )

    first = adapter_module.PROJECT_COMPLETION_OWNER_OBSERVATION_ADAPTER.observe_project_completion(
        workspace=str(tmp_path),
        project_id=contract.project_id,
        run_id=contract.run_id,
        completion_contract_hash=contract.contract_hash,
    )
    evidence = next(item for item in first.evidence if item.obligation_id == "artifact.main")
    assert evidence.status == "passed"
    assert evidence.owner_module_id == "runtime.execution_broker"
    assert evidence.artifact_hash == receipt.artifact_hash

    source.write_text("print('two')\n", encoding="utf-8")
    second = adapter_module.PROJECT_COMPLETION_OWNER_OBSERVATION_ADAPTER.observe_project_completion(
        workspace=str(tmp_path),
        project_id=contract.project_id,
        run_id=contract.run_id,
        completion_contract_hash=contract.contract_hash,
    )
    assert all(item.obligation_id != "artifact.main" for item in second.evidence)


def test_nonzero_physical_command_receipt_is_failed_not_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    contract = _patch_owners(monkeypatch, tmp_path)
    source = tmp_path / "src" / "main.py"
    source.parent.mkdir(parents=True)
    source.write_text("print('ok')\n", encoding="utf-8")
    test_file = tmp_path / "tests" / "test_main.py"
    test_file.parent.mkdir(parents=True)
    test_file.write_text("def test_main():\n    assert True\n", encoding="utf-8")
    authority = next(item for item in contract.verification_command_authority if item.modality == "test")
    del authority
    monkeypatch.setattr(
        authority_module,
        "_EXECUTION_AUTHORITY_PORT",
        adapter_module.PROJECT_COMPLETION_OWNER_OBSERVATION_ADAPTER,
    )
    runner = _PhysicalRunner(exit_code=7)
    monkeypatch.setattr(authority_module._ExecutionBrokerProjectVerificationRunner, "run", runner.run)
    command = authorize_project_verification_command(
        ResolveProjectVerificationAuthorityQueryV1(
            workspace=str(tmp_path),
            project_id=contract.project_id,
            run_id=contract.run_id,
            completion_contract_hash=contract.contract_hash,
            obligation_id="verify.test",
        )
    )
    run_project_verification(command)

    def _coverage(**kwargs: object) -> ProjectRepairCoverageV1:
        return ProjectRepairCoverageV1(
            workspace=str(kwargs["workspace"]),
            project_id=str(kwargs["project_id"]),
            run_id=str(kwargs["run_id"]),
            completion_contract_hash=str(kwargs["contract_hash"]),
            obligation_id="verify.test",
            owner_task_id="task-2",
            status="uncovered",
            evidence_ref="director-runtime://repair-coverage/failed-test",
        )

    monkeypatch.setattr(adapter_module, "_repair_coverage", _coverage)
    observation = adapter_module.PROJECT_COMPLETION_OWNER_OBSERVATION_ADAPTER.observe_project_completion(
        workspace=str(tmp_path),
        project_id=contract.project_id,
        run_id=contract.run_id,
        completion_contract_hash=contract.contract_hash,
    )

    evidence = next(item for item in observation.evidence if item.obligation_id == "verify.test")
    assert evidence.status == "failed"
    assert evidence.verifier_exit_code == 7
    assert evidence.verifier_timed_out is False
    assert evidence.verifier_output_hash
    assert [item.obligation_id for item in observation.repair_coverage] == ["verify.test"]


def test_empty_workspace_and_arbitrary_owner_refs_cannot_satisfy_delivery(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    contract = _patch_owners(monkeypatch, tmp_path)

    assert tuple(tmp_path.iterdir()) == ()

    observation = adapter_module.PROJECT_COMPLETION_OWNER_OBSERVATION_ADAPTER.observe_project_completion(
        workspace=str(tmp_path),
        project_id=contract.project_id,
        run_id=contract.run_id,
        completion_contract_hash=contract.contract_hash,
    )

    assert observation.contract.project_id == contract.project_id
    assert observation.contract.run_id == contract.run_id
    assert observation.contract.contract_hash == contract.contract_hash
    assert observation.contract.to_seed_dict() == contract.to_seed_dict()
    assert observation.evidence == ()
    assert observation.repair_coverage == ()


def test_bootstrap_adapter_rejects_degraded_task_runtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    contract = _patch_owners(monkeypatch, tmp_path, authoritative=False)

    with pytest.raises(ProjectCompletionOwnerObservationV1Error) as exc_info:
        adapter_module.PROJECT_COMPLETION_OWNER_OBSERVATION_ADAPTER.observe_project_completion(
            workspace=str(tmp_path),
            project_id=contract.project_id,
            run_id=contract.run_id,
            completion_contract_hash=contract.contract_hash,
        )

    assert exc_info.value.error_code == "project_completion_task_runtime_not_authoritative"


def test_bootstrap_adapter_rejects_ce_contract_lookalike(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    contract = _contract()
    monkeypatch.setattr(adapter_module, "query_project_completion_contract", lambda query: object())

    with pytest.raises(ProjectCompletionOwnerObservationV1Error) as exc_info:
        adapter_module.PROJECT_COMPLETION_OWNER_OBSERVATION_ADAPTER.observe_project_completion(
            workspace=str(tmp_path),
            project_id=contract.project_id,
            run_id=contract.run_id,
            completion_contract_hash=contract.contract_hash,
        )

    assert exc_info.value.error_code == "project_completion_contract_identity_mismatch"


def test_bootstrap_adapter_rejects_cross_identity_ce_contract(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    contract = _contract()
    cross_project = replace(contract, project_id="project-other")
    monkeypatch.setattr(adapter_module, "query_project_completion_contract", lambda query: cross_project)

    with pytest.raises(ProjectCompletionOwnerObservationV1Error) as exc_info:
        adapter_module.PROJECT_COMPLETION_OWNER_OBSERVATION_ADAPTER.observe_project_completion(
            workspace=str(tmp_path),
            project_id=contract.project_id,
            run_id=contract.run_id,
            completion_contract_hash=contract.contract_hash,
        )

    assert exc_info.value.error_code == "project_completion_contract_identity_mismatch"


def _ledger_with_claimed_verifier_metadata(
    contract: ProjectCompletionContractV1,
    *,
    obligation_id: str = "verify.test",
    command: str = "pytest -q",
    owner_task_id: str = "task-2",
    run_id: str = "run-1",
    completion_contract_hash: str | None = None,
) -> RunLedgerProjectionResultV1:
    ledger = _ledger(contract).projection
    gate = ledger["run_projection"]["gates"][1]
    gate["content_id"] = "arbitrary-projection-content-id"
    gate["evidence_modalities"]["test"]["metadata"] = {
        "exit_code": 0,
        "workspace": "/attacker/claimed/workspace",
        "project_id": "project-1",
        "run_id": run_id,
        "completion_contract_hash": completion_contract_hash or contract.contract_hash,
        "obligation_id": obligation_id,
        "owner_task_id": owner_task_id,
        "canonical_command": command,
        "modality": "test",
        "effect_receipt_ref": "projection-hash://not-an-authoritative-effect-receipt",
    }
    return RunLedgerProjectionResultV1(projection=ledger)


def test_untyped_fully_claimed_gate_metadata_cannot_impersonate_owner_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    contract = _patch_owners(monkeypatch, tmp_path)
    monkeypatch.setattr(
        adapter_module,
        "read_run_ledger_projection",
        lambda query: _ledger_with_claimed_verifier_metadata(contract),
    )

    observation = adapter_module.PROJECT_COMPLETION_OWNER_OBSERVATION_ADAPTER.observe_project_completion(
        workspace=str(tmp_path),
        project_id=contract.project_id,
        run_id=contract.run_id,
        completion_contract_hash=contract.contract_hash,
    )

    assert observation.evidence == ()


@pytest.mark.parametrize(
    ("override", "value"),
    [
        ("obligation_id", "verify.lint"),
        ("command", "pytest tests/other_test.py -q"),
        ("owner_task_id", "task-1"),
        ("run_id", "run-old"),
        ("completion_contract_hash", "c" * 64),
    ],
    ids=(
        "cross-obligation",
        "cross-command",
        "cross-owner",
        "stale-run",
        "stale-contract",
    ),
)
def test_cross_bound_or_stale_verifier_claims_remain_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    override: str,
    value: str,
) -> None:
    contract = _patch_owners(monkeypatch, tmp_path)
    kwargs = {override: value}
    monkeypatch.setattr(
        adapter_module,
        "read_run_ledger_projection",
        lambda query: _ledger_with_claimed_verifier_metadata(contract, **kwargs),
    )

    observation = adapter_module.PROJECT_COMPLETION_OWNER_OBSERVATION_ADAPTER.observe_project_completion(
        workspace=str(tmp_path),
        project_id=contract.project_id,
        run_id=contract.run_id,
        completion_contract_hash=contract.contract_hash,
    )

    assert observation.evidence == ()
