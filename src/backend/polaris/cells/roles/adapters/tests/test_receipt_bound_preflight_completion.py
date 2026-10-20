"""Private generic owners must prove required verification before preflight completion.

The CE portfolio, artifact receipts, verifier execution, TaskRuntime claim and
settlement are real public owners.  The fixture authority port is the existing
execution-broker test composition; no success receipt or finalization is mocked.
"""

from __future__ import annotations

import asyncio
import hashlib
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from polaris.cells.control_plane.run_ledger.public import ReadRunLedgerProjectionQueryV1, read_run_ledger_projection
from polaris.cells.control_plane.run_ledger.tests.test_canonical_task_identity import _bootstrap
from polaris.cells.events.fact_stream.public import QueryFactEventsV1, query_fact_events
from polaris.cells.factory.pipeline.internal import factory_materialization_impl as materialization
from polaris.cells.factory.pipeline.internal.run_ledger import build_job_token_from_record
from polaris.cells.factory.pipeline.tests import test_materialization_contract_verifiers as fixture_owner
from polaris.cells.factory.pipeline.tests.test_materialization_contract_verifiers import _fixture as _owner_fixture
from polaris.cells.factory.pipeline.tests.test_native_validation_inputs import _projection
from polaris.cells.roles.adapters.internal.director.execute_method import _phases_materialization as phases
from polaris.cells.roles.adapters.internal.director.execute_method._helpers import MaterializationState
from polaris.cells.roles.adapters.public.service import create_role_adapter
from polaris.cells.runtime.execution_broker.public import project_verification as broker
from polaris.cells.runtime.task_runtime.public import (
    TaskRuntimeExecutionAttemptIdentityV1,
    TaskRuntimeService,
    create_task_runtime_execution_attempt_authority,
)

TASK = "DEO-LIFECYCLE"
CHILD = "director-private-preflight-completion"


def _facts(workspace: Path, stream: str = "task_runtime.execution") -> tuple[dict[str, Any], ...]:
    return query_fact_events(
        QueryFactEventsV1(workspace=str(workspace), stream=stream, strict_integrity=True, limit=1000)
    ).events


def _setup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    failing: bool = False,
    duplicate_commands: bool = False,
    optional: bool = False,
) -> dict[str, Any]:
    # The reused fixture requests its old shared test home. Keep every module
    # fixture owner under this test's explicitly private namespace instead.
    setenv = monkeypatch.setenv

    def private_setenv(name: str, value: str, prepend: str | None = None) -> None:
        setenv(name, str(tmp_path / "authority-home") if name == "KERNELONE_HOME" else value, prepend)

    monkeypatch.setattr(monkeypatch, "setenv", private_setenv)
    if optional:
        original_builder = fixture_owner.build_chief_engineer_blueprint_portfolio

        def build_optional(command: Any) -> Any:
            candidate = deepcopy(command.llm_blueprint)
            candidate["project_completion_contract"]["obligations"]["verification"][-1]["applicability"] = "optional"
            return original_builder(replace(command, llm_blueprint=candidate))

        monkeypatch.setattr(fixture_owner, "build_chief_engineer_blueprint_portfolio", build_optional)
    executor, run, port = _owner_fixture(
        tmp_path,
        monkeypatch,
        task_id=TASK,
        stdlib_verifier=True,
        failing=failing,
        required_lint=duplicate_commands,
        lint_cwds=(".", ".", "."),
    )
    workspace = Path(executor.workspace)
    monkeypatch.chdir(workspace)
    contract = port.completion_contract
    projection = _projection(port.blueprint_portfolio, contract)
    paths = [item.path for item in contract.obligations.artifacts if item.applicability == "required"]
    token = build_job_token_from_record(
        {
            "factory_run_id": run.id,
            "target_files": paths,
            "allowed_paths": paths,
            "required_artifacts": paths,
            "pm_task_contract": {"task_id": TASK, "description": "Build and test the private generic module"},
            "blueprint_id": port.blueprint_portfolio.portfolio_id,
            "chief_engineer_blueprint": projection,
            "required_evidence_modalities": ["code", "command", "qa"],
        },
        run_id=run.id,
        project_id=contract.project_id,
        stage="pending_exec",
    ).to_dict()
    assert token["capability_audit"]["ok"] is True
    _bootstrap(workspace)
    service = TaskRuntimeService(str(workspace))
    row = service.create_task_row(
        subject="Private preflight completion owner",
        metadata={
            "external_task_id": TASK,
            "factory_run_id": run.id,
            "project_id": contract.project_id,
            "factory_bench_project_id": contract.project_id,
            "target_files": paths,
            "task_completion_projection": projection,
            "job_token": token,
            "control_plane_job_token": token,
        },
    )
    claim = service.claim_execution(
        row["id"], worker_id="director", role_id="director", run_id=CHILD, external_task_id=TASK
    )
    assert claim["success"] is True
    identity = TaskRuntimeExecutionAttemptIdentityV1.from_record(claim["execution_attempt"])
    authority = create_task_runtime_execution_attempt_authority(identity)
    for artifact in contract.obligations.artifacts:
        if artifact.applicability != "required":
            continue
        broker.record_project_artifact(
            broker.RecordProjectArtifactCommandV1(
                workspace=str(workspace),
                project_id=contract.project_id,
                run_id=run.id,
                completion_contract_hash=contract.contract_hash,
                obligation_id=artifact.obligation_id,
                owner_task_id=TASK,
                path=artifact.path,
            )
        )
    adapter = create_role_adapter("director", str(workspace))
    task = adapter._get_task(str(row["id"]))
    assert task is not None
    return {
        "workspace": workspace,
        "executor": executor,
        "run": run,
        "port": port,
        "adapter": adapter,
        "task": task,
        "authority": authority,
        "identity": identity,
        "context": {
            "run_id": CHILD,
            "factory_run_id": run.id,
            "task_id": str(row["id"]),
            "task_completion_projection": projection,
            "job_token": token,
            "control_plane_job_token": token,
            "task_runtime_execution_attempt_authority": authority,
            "metadata": {"task_completion_projection": projection},
        },
        "state": MaterializationState.from_locals(
            {path: hashlib.sha256((workspace / path).read_bytes()).hexdigest() for path in paths}, [], [], [], []
        ),
    }


def _preflight(case: dict[str, Any]) -> dict[str, Any] | None:
    return phases._phase_existing_scope_preflight(
        case["adapter"],
        board_claim_applied=True,
        task_execution_attempt_authority=case["authority"],
        context=case["context"],
        decision_signals=[],
        requires_fresh_materialization=False,
        run_id=CHILD,
        target_task_id=str(case["identity"].task_id),
        task=case["task"],
        task_claim_session_id=case["identity"].session_id,
        workspace_name=case["workspace"].name,
        state=case["state"],
    )


def test_missing_required_command_receipts_leave_real_claim_for_existing_execution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    case = _setup(tmp_path, monkeypatch)
    original = _facts(case["workspace"])
    result = _preflight(case)
    after = _facts(case["workspace"])
    assert not any(row["event_type"] == "completed" for row in after[len(original) :])
    assert result is None
    assert case["authority"].snapshot().closed is False


def test_current_required_receipts_complete_real_owner_and_preserve_global_qa_requirement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    case = _setup(tmp_path, monkeypatch, duplicate_commands=True)
    observed = asyncio.run(
        materialization._collect_director_stage_materialization_diagnostics(case["executor"], run=case["run"])
    )
    assert not observed.blockers, observed.to_dict()
    assert len(observed._owner_receipts) == 5
    result = _preflight(case)
    assert result is not None and result["success"] is True
    assert case["authority"].snapshot().closed is True
    events = _facts(case["workspace"], "execution.control_plane")
    boundaries = [
        row["payload"]["event"]["task_boundary_verdict"]
        for row in events
        if row["event_type"] == "task_boundary_verdict"
    ]
    assert boundaries
    boundary = boundaries[-1]
    assert set(boundary["required_verifiers"]) == set(boundary["completed_verifiers"])
    assert len(boundary["required_verifiers"]) == 3
    assert set(boundary["present_evidence_modalities"]) == {"code", "command"}
    event = next(row["payload"]["event"] for row in reversed(events) if row["event_type"] == "task_boundary_verdict")
    gate_event = next(
        row["payload"]["event"]
        for row in reversed(events)
        if row["event_type"] == "gate_evaluated" and row["payload"]["event"].get("stage") == "workspace_validation"
    )
    assert gate_event["job_token"] == case["context"]["job_token"]
    assert "source_job_token" not in event
    assert "capability_audit" not in event["job_token"] and "token_id" not in event["job_token"]
    bindings = event["task_boundary_completion_basis"]["current_receipt_bindings"]
    assert len(bindings) == len({row[0] for row in bindings}) == 5
    assert all(row[1] in boundary["evidence_refs"] for row in bindings)
    projection = read_run_ledger_projection(
        ReadRunLedgerProjectionQueryV1(
            workspace=str(case["workspace"]),
            run_id=case["run"].id,
            factory_run_id=case["run"].id,
            project_id=case["port"].completion_contract.project_id,
            max_runs=10,
            canonical_only=True,
        )
    ).projection
    assert projection["evidence_modalities"]["code"]["present"] > 0
    assert projection["evidence_modalities"]["command"]["present"] > 0
    assert projection["evidence_policy"]["missing_required_modalities"] == ["qa"]


def test_optional_verifier_has_no_receipt_and_does_not_block_required_completion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    case = _setup(tmp_path, monkeypatch, duplicate_commands=True, optional=True)
    observed = asyncio.run(
        materialization._collect_director_stage_materialization_diagnostics(case["executor"], run=case["run"])
    )
    assert not observed.blockers, observed.to_dict()
    assert len(observed._owner_receipts) == 4
    command = materialization._revalidated_deferred_boundary_command(
        case["executor"], run=case["run"], task_id=TASK, child_run=CHILD, predecessor="b" * 64
    )
    assert len(command.current_receipt_bindings) == 4
    optional_id = next(
        item.obligation_id
        for item in case["port"].completion_contract.obligations.verification
        if item.applicability == "optional"
    )
    assert optional_id not in {row[0] for row in command.current_receipt_bindings}
    result = _preflight(case)
    assert result is not None and result["success"] is True
    event = next(
        row["payload"]["event"]
        for row in reversed(_facts(case["workspace"], "execution.control_plane"))
        if row["event_type"] == "task_boundary_verdict"
    )
    assert len(event["task_boundary_completion_basis"]["current_receipt_bindings"]) == 4


@pytest.mark.parametrize("failure", ["failed", "stale", "foreign_context", "stale_claim", "append"])
def test_incomplete_or_uncommitted_current_proof_never_settles_completed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    case = _setup(tmp_path, monkeypatch, failing=failure == "failed")
    observed = asyncio.run(
        materialization._collect_director_stage_materialization_diagnostics(case["executor"], run=case["run"])
    )
    assert any(not receipt.succeeded for receipt in observed._owner_receipts) is (failure == "failed")
    if failure == "stale":
        path = case["workspace"] / "src/module.py"
        path.write_text("value = 8\n", encoding="utf-8")
        artifact = next(
            item for item in case["port"].completion_contract.obligations.artifacts if item.path == "src/module.py"
        )
        broker.record_project_artifact(
            broker.RecordProjectArtifactCommandV1(
                workspace=str(case["workspace"]),
                project_id=case["port"].completion_contract.project_id,
                run_id=case["run"].id,
                completion_contract_hash=case["port"].completion_contract.contract_hash,
                obligation_id=artifact.obligation_id,
                owner_task_id=TASK,
                path=artifact.path,
            )
        )
    elif failure == "foreign_context":
        case["context"]["job_token"] = {**case["context"]["job_token"], "token_id": "foreign-token"}
    elif failure == "stale_claim":
        assert case["authority"].settle(outcome="failed", summary="Private old attempt ended").success
        case["authority"] = create_task_runtime_execution_attempt_authority(case["identity"])
    elif failure == "append":
        from polaris.cells.roles.adapters.internal.director.execute_method import _claim

        original = _claim.append_run_ledger_event

        def reject_boundary(command: Any) -> Any:
            if command.event.get("event_type") == "task_boundary_verdict":
                raise OSError("private fault before boundary durable commit")
            return original(command)

        monkeypatch.setattr(_claim, "append_run_ledger_event", reject_boundary)
    before = _facts(case["workspace"])
    assert _preflight(case) is None
    after = _facts(case["workspace"])
    assert not any(row["event_type"] == "completed" for row in after[len(before) :])
    assert case["authority"].snapshot().closed is False
    if failure == "failed":
        projection = read_run_ledger_projection(
            ReadRunLedgerProjectionQueryV1(workspace=str(case["workspace"]), run_id=case["run"].id, max_runs=10)
        ).projection
        assert "command" in projection["evidence_policy"]["failed_required_modalities"]
        assert "command" not in projection["evidence_policy"]["missing_required_modalities"]
        historical = _facts(case["workspace"], "execution.control_plane")
        # Explicit private fixture input change, not a Director repair effect.
        # The new proof is produced by the same real Broker and claim; no
        # historical command result or receipt is rewritten as successful.
        source = case["workspace"] / "tests/test_output.py"
        source.write_text(
            source.read_text(encoding="utf-8").replace("module.value == 8", "module.value == 7"), encoding="utf-8"
        )
        for artifact in case["port"].completion_contract.obligations.artifacts:
            broker.record_project_artifact(
                broker.RecordProjectArtifactCommandV1(
                    workspace=str(case["workspace"]),
                    project_id=case["port"].completion_contract.project_id,
                    run_id=case["run"].id,
                    completion_contract_hash=case["port"].completion_contract.contract_hash,
                    obligation_id=artifact.obligation_id,
                    owner_task_id=TASK,
                    path=artifact.path,
                )
            )
        repaired = asyncio.run(
            materialization._collect_director_stage_materialization_diagnostics(case["executor"], run=case["run"])
        )
        assert all(receipt.succeeded for receipt in repaired._owner_receipts)
        result = _preflight(case)
        assert result is not None and result["success"] is True
        assert _facts(case["workspace"], "execution.control_plane")[: len(historical)] == historical
        current = read_run_ledger_projection(
            ReadRunLedgerProjectionQueryV1(
                workspace=str(case["workspace"]),
                run_id=case["run"].id,
                factory_run_id=case["run"].id,
                project_id=case["port"].completion_contract.project_id,
                max_runs=10,
                canonical_only=True,
            )
        ).projection
        assert current["evidence_policy"]["failed_required_modalities"] == []
        assert current["evidence_policy"]["missing_required_modalities"] == ["qa"]
        sealed_facts = _facts(case["workspace"], "execution.control_plane")
        cold = read_run_ledger_projection(
            ReadRunLedgerProjectionQueryV1(
                workspace=str(case["workspace"]),
                run_id=case["run"].id,
                factory_run_id=case["run"].id,
                project_id=case["port"].completion_contract.project_id,
                max_runs=10,
                canonical_only=True,
            )
        ).projection
        assert cold["evidence_policy"] == current["evidence_policy"]
        assert _facts(case["workspace"], "execution.control_plane") == sealed_facts
