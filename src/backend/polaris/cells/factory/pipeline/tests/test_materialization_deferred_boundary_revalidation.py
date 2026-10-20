"""Normal deferred completion comes from current CE and real durable owner receipts."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest
from polaris.cells.control_plane.run_ledger.public import ReadRunLedgerProjectionQueryV1, read_run_ledger_projection
from polaris.cells.control_plane.run_ledger.public.task_boundary import reproject_task_boundary_verdict
from polaris.cells.control_plane.run_ledger.tests.test_canonical_task_identity import _bootstrap
from polaris.cells.events.fact_stream.public import QueryFactEventsV1, query_fact_events
from polaris.cells.factory.pipeline.internal import factory_materialization_impl as materialization
from polaris.cells.factory.pipeline.internal.run_ledger import load_run_ledger_projection
from polaris.cells.factory.pipeline.tests.test_materialization_contract_verifiers import _fixture as _verifier_fixture
from polaris.cells.factory.pipeline.tests.test_native_validation_inputs import _projection as _ce_projection
from polaris.cells.roles.kernel.internal.kernel.task_boundary import append_deferred_followup_task_boundary_verdict
from polaris.cells.runtime.execution_broker.public import project_verification as broker
from polaris.cells.runtime.task_runtime.public import (
    SettleTaskRuntimeExecutionAttemptCommandV1,
    TaskRuntimeExecutionAttemptIdentityV1,
    TaskRuntimeService,
    query_observable_task_rows,
)

TASK = "DEO-LIFECYCLE"
CHILD = "director-private-deferred-verification"


def _facts(workspace: Path) -> tuple[dict[str, Any], ...]:
    return query_fact_events(
        QueryFactEventsV1(workspace=str(workspace), stream="execution.control_plane", limit=1000, strict_integrity=True)
    ).events


def _setup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, failing: bool = False, full_scope: bool = False
) -> tuple[Any, Any, Any, Any]:
    executor, run, port = _verifier_fixture(
        tmp_path,
        monkeypatch,
        task_id=TASK,
        stdlib_verifier=True,
        failing=failing,
        additional_authored_files={
            f"docs/guide-{index}.md": f"# Private generic fixture {index}\n" for index in range(10)
        }
        if full_scope
        else None,
        additional_semantic_role="docs",
        required_lint=full_scope,
        lint_cwds=(".", ".", ".") if full_scope else (".",),
    )
    workspace = Path(executor.workspace)
    contract = port.completion_contract
    projection = _ce_projection(port.blueprint_portfolio, contract)
    _bootstrap(workspace)
    service = TaskRuntimeService(str(workspace))
    task = service.create_task_row(
        subject="Private normal completion fixture",
        metadata={
            "external_task_id": TASK,
            "factory_run_id": run.id,
            "project_id": contract.project_id,
            "factory_bench_project_id": contract.project_id,
            "task_completion_projection": projection,
            "job_token": {
                "run_id": run.id,
                "project_id": contract.project_id,
                "gate_policy": {"project_completion_contract_hash": contract.contract_hash},
            },
        },
    )
    claim = service.claim_execution(
        task["id"], worker_id="director", role_id="director", run_id=CHILD, external_task_id=TASK
    )
    assert claim["success"] is True
    identity = TaskRuntimeExecutionAttemptIdentityV1.from_record(claim["execution_attempt"])
    for artifact in contract.obligations.artifacts:
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
    observation = asyncio.run(materialization._collect_director_stage_materialization_diagnostics(executor, run=run))
    assert not observation.blockers, observation.to_dict()
    assert len(observation._owner_receipts) == (5 if full_scope else 2)
    assert (
        service.settle_execution_attempt(
            SettleTaskRuntimeExecutionAttemptCommandV1(
                workspace=str(workspace),
                identity=identity,
                outcome="completed",
                summary="Execution settled; verification remains separate",
            )
        )["success"]
        is True
    )
    append_deferred_followup_task_boundary_verdict(
        workspace=str(workspace), task_id=TASK, run_id=CHILD, reason="needs_followup_workflow"
    )
    before = read_run_ledger_projection(
        ReadRunLedgerProjectionQueryV1(workspace=str(workspace), run_id=CHILD, max_runs=1)
    ).projection["task_boundary"]["latest_by_task"][TASK]
    return executor, run, port, before


def _command(executor: Any, run: Any, before: Any, **changes: Any) -> Any:
    values = {"run": run, "task_id": TASK, "child_run": CHILD, "predecessor": before["content_id"]}
    values.update(changes)
    return materialization._revalidated_deferred_boundary_command(executor, **values)


def test_real_current_owner_proof_rebuilds_normal_boundary_and_cold_retry_is_idempotent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    executor, run, port, before = _setup(tmp_path, monkeypatch, full_scope=True)
    original = _facts(Path(executor.workspace))
    command = _command(executor, run, before)
    assert len(command.revalidated_verdict.target_files) == len(port.completion_contract.obligations.artifacts)
    assert len(command.current_receipt_bindings) == 5
    assert len({row[0] for row in command.current_receipt_bindings}) == 5
    assert len(command.revalidated_verdict.target_files) == 13

    def canonical_projection(_run: Any, _context: Any) -> dict[str, Any]:
        value = load_run_ledger_projection(Path(executor.workspace), run_id=CHILD)
        observed = query_observable_task_rows(str(executor.workspace))
        runtime = observed.to_authority_dict(factory_run_id=run.id)
        runtime.update(
            {
                "schema_version": "task_runtime.observable_task_rows_authority.v1",
                "owner_scope": "pm_contract_tasks",
                "owned_task_ids": list(port.completion_contract.covered_task_ids),
            }
        )
        value["task_runtime_projection"] = runtime
        return value

    monkeypatch.setattr(executor, "_canonical_factory_projection", canonical_projection)
    prior = materialization.helpers.evaluate_canonical_factory_authority(canonical_projection(run, {}))
    assert not prior.director_stage_authorized
    recovered = materialization._recover_director_stage_authority_after_delivery_settle(
        executor, run=run, context={}, prior_authority=prior
    )
    assert recovered is not None and recovered.director_stage_authorized
    cold = read_run_ledger_projection(
        ReadRunLedgerProjectionQueryV1(workspace=str(executor.workspace), run_id=CHILD, max_runs=1)
    ).projection["task_boundary"]
    assert cold["latest_by_task"][TASK]["ok"] is True
    assert cold["historical_failed_count"] == 1
    after = _facts(Path(executor.workspace))
    assert after[: len(original)] == original and len(after) == len(original) + 1
    retry = reproject_task_boundary_verdict(_command(executor, run, before))
    assert retry.status == "idempotent"
    assert _facts(Path(executor.workspace)) == after


@pytest.mark.parametrize(
    "defect",
    ["stale_source", "failed_verifier", "foreign_child", "foreign_task", "missing_receipt", "lookalike_receipt"],
)
def test_incomplete_current_proof_never_appends_completion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, defect: str
) -> None:
    executor, run, port, before = _setup(tmp_path, monkeypatch, failing=defect == "failed_verifier")
    changes = {}
    if defect == "stale_source":
        (Path(executor.workspace) / "src/module.py").write_text("value = 8\n", encoding="utf-8")
    elif defect == "foreign_child":
        changes["child_run"] = "foreign-director"
    elif defect == "foreign_task":
        changes["task_id"] = "FOREIGN"
    elif defect == "missing_receipt":
        port.denied = True
    elif defect == "lookalike_receipt":
        monkeypatch.setattr(
            broker, "query_project_verification_receipt", lambda _query: {"succeeded": True, "exit_code": 0}
        )
    original = _facts(Path(executor.workspace))
    with pytest.raises((ValueError, RuntimeError), match=r"revalidation|policy_rejected"):
        _command(executor, run, before, **changes)
    assert _facts(Path(executor.workspace)) == original
