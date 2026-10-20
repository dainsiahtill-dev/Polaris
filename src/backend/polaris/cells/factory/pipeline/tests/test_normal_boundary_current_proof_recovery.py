"""Completed execution must consume current proofs after a normal verifier failure.

Private generic module fixtures use real CE persistence, TaskRuntime terminal
settlement, Broker verification receipts and Ledger cold reads. The existing
test authority port is synthetic; no provider or native qualification is claimed.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from polaris.cells.control_plane.run_ledger.public import (
    AppendRunLedgerEventCommandV1,
    append_run_ledger_event,
    evaluate_task_boundary_verdict,
)
from polaris.cells.events.fact_stream.public import QueryFactEventsV1, query_fact_events
from polaris.cells.factory.pipeline.internal import factory_materialization_impl as materialization
from polaris.cells.factory.pipeline.internal.run_ledger import load_run_ledger_projection
from polaris.cells.factory.pipeline.tests.test_materialization_deferred_boundary_revalidation import (
    CHILD,
    TASK,
    _setup,
)
from polaris.cells.runtime.execution_broker.public import project_verification as broker
from polaris.cells.runtime.task_runtime.public import query_observable_task_rows


def _facts(workspace: Path) -> tuple[dict[str, Any], ...]:
    return query_fact_events(
        QueryFactEventsV1(workspace=str(workspace), stream="execution.control_plane", strict_integrity=True, limit=1000)
    ).events


def _completed_case(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Any, Any, Any, Any]:
    setenv = monkeypatch.setenv

    def private_setenv(name: str, value: str, prepend: str | None = None) -> None:
        setenv(name, str(tmp_path / "authority-home") if name == "KERNELONE_HOME" else value, prepend)

    monkeypatch.setattr(monkeypatch, "setenv", private_setenv)
    executor, run, port, _deferred = _setup(tmp_path, monkeypatch, full_scope=True)
    workspace = Path(executor.workspace)
    before = _facts(workspace)
    original = next(row["payload"]["event"] for row in reversed(before) if row["event_type"] == "task_boundary_verdict")
    targets = [item.path for item in port.completion_contract.obligations.artifacts if item.applicability == "required"]
    commands = [
        item.command for item in port.completion_contract.obligations.verification if item.applicability == "required"
    ]
    old = evaluate_task_boundary_verdict(
        workspace=workspace,
        task_id=TASK,
        run_id=CHILD,
        target_files=targets,
        completed_artifacts=targets,
        required_verifiers=commands,
        completed_verifiers=[],
    )
    assert old.status == "required_verifier_missing" and not old.ok
    append_run_ledger_event(
        AppendRunLedgerEventCommandV1(
            workspace=str(workspace),
            run_id=CHILD,
            event={
                "event_type": "task_boundary_verdict",
                "stage": "task_boundary",
                "task_id": TASK,
                "run_id": CHILD,
                "task_boundary_verdict": old.to_dict(),
                "job_token": original["job_token"],
            },
        )
    )

    def current_projection(_run: Any, _context: Any) -> dict[str, Any]:
        value = load_run_ledger_projection(workspace, run_id=CHILD)
        observed = query_observable_task_rows(str(workspace))
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

    monkeypatch.setattr(executor, "_canonical_factory_projection", current_projection)
    projection = current_projection(run, {})
    assert all(row["execution_state"] == "completed" for row in projection["task_runtime_projection"]["rows"])
    prior = materialization.helpers.evaluate_canonical_factory_authority(projection)
    assert prior.source_valid and prior.task_runtime_projection_authoritative and prior.contract_task_scope_valid
    assert not prior.director_stage_authorized
    return executor, run, port, prior


def test_completed_normal_verifier_failure_uses_current_per_id_proof_and_cold_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    executor, run, port, prior = _completed_case(tmp_path, monkeypatch)
    workspace = Path(executor.workspace)
    before = _facts(workspace)
    recovered = materialization._recover_director_stage_authority_after_delivery_settle(
        executor,
        run=run,
        context={},
        prior_authority=prior,
    )
    assert recovered is not None and recovered.director_stage_authorized
    cold = load_run_ledger_projection(workspace, run_id=CHILD)
    latest = cold["task_boundary"]["latest_by_task"][TASK]
    assert latest["ok"] is True and latest["status"] == "completed_verified"
    assert len(latest["target_files"]) == 13
    basis = latest["reprojection"]
    assert len(basis["current_receipt_bindings"]) == len({row[0] for row in basis["current_receipt_bindings"]}) == 5
    assert len(latest["required_verifiers"]) == 3  # Three lint IDs share one literal command.
    assert set(latest["required_verifiers"]) == set(latest["completed_verifiers"])
    assert basis["completion_contract_hash"] == port.completion_contract.contract_hash
    after = _facts(workspace)
    assert after[: len(before)] == before and len(after) == len(before) + 1
    assert cold["task_boundary"]["historical_failed_count"] >= 2
    replayed = materialization._recover_director_stage_authority_after_delivery_settle(
        executor,
        run=run,
        context={},
        prior_authority=prior,
    )
    assert replayed is not None and replayed.director_stage_authorized
    assert _facts(workspace) == after


@pytest.mark.parametrize("defect", ["stale", "owner_denied", "missing_obligation"])
def test_normal_verifier_failure_does_not_fall_back_to_old_fields_without_current_proof(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, defect: str
) -> None:
    executor, run, port, prior = _completed_case(tmp_path, monkeypatch)
    workspace = Path(executor.workspace)
    if defect == "stale":
        (workspace / "src/module.py").write_text("value = 8\n", encoding="utf-8")
    elif defect == "owner_denied":
        port.denied = True
    else:
        query = broker.query_project_verification_receipt
        missing_id = port.completion_contract.obligations.verification[-1].obligation_id

        def missing(command: Any) -> Any:
            return None if command.obligation_id == missing_id else query(command)

        monkeypatch.setattr(broker, "query_project_verification_receipt", missing)
    before = _facts(workspace)
    recovered = materialization._recover_director_stage_authority_after_delivery_settle(
        executor,
        run=run,
        context={},
        prior_authority=prior,
    )
    assert recovered is None
    assert _facts(workspace) == before
    latest = load_run_ledger_projection(workspace, run_id=CHILD)["task_boundary"]["latest_by_task"][TASK]
    assert latest["status"] == "required_verifier_missing" and latest["ok"] is False
