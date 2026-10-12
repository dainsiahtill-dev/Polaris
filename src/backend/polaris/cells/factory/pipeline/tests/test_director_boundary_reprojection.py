"""Post-settle recovery refreshes existing owned verdicts without role replay."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from polaris.cells.control_plane.run_ledger.public import (
    AppendRunLedgerEventCommandV1,
    append_run_ledger_event,
    evaluate_task_boundary_verdict,
)
from polaris.cells.events.fact_stream.public import (
    BootstrapFactStreamWorkspaceCommandV1,
    bootstrap_fact_stream_workspace,
    fact_stream_bootstrap_streams,
)
from polaris.cells.factory.pipeline.internal.factory_materialization_impl import (
    _recover_director_stage_authority_after_delivery_settle,
)
from polaris.cells.factory.pipeline.internal.factory_stage_helpers import evaluate_canonical_factory_authority
from polaris.cells.factory.pipeline.internal.run_ledger import load_run_ledger_projection


@pytest.mark.parametrize(
    "runtime_state,failed_verifiers,authorized",
    [("completed", [], True), ("in_progress", [], False), ("completed", ["npm test"], False)],
)
def test_post_settle_refresh_reuses_real_owned_boundary_and_preserves_failure_obligations(
    tmp_path: Path, runtime_state: str, failed_verifiers: list[str], authorized: bool
) -> None:
    bootstrap_fact_stream_workspace(
        BootstrapFactStreamWorkspaceCommandV1(
            workspace=str(tmp_path),
            streams=fact_stream_bootstrap_streams(),
            maintenance_reason="post_settle_boundary_test",
        )
    )
    child_run = "director-boundary-owner"
    (tmp_path / "index.html").write_text("<html></html>", encoding="utf-8")
    manifest = tmp_path / "package.json"
    manifest.write_text(json.dumps({"scripts": {"serve": "node scripts/serve.js"}}), encoding="utf-8")
    for task_id in ["TASK-OWNED", "TASK-FOREIGN"]:
        verdict = evaluate_task_boundary_verdict(
            workspace=tmp_path,
            task_id=task_id,
            run_id=child_run,
            target_files=["package.json", "index.html"],
            completed_artifacts=["package.json", "index.html"],
            failed_required_verifiers=failed_verifiers if task_id == "TASK-OWNED" else [],
        ).to_dict()
        assert verdict["status"] == "missing_entrypoint_target"
        append_run_ledger_event(
            AppendRunLedgerEventCommandV1(
                workspace=str(tmp_path),
                run_id=child_run,
                event={
                    "event_type": "task_boundary_verdict",
                    "task_id": task_id,
                    "run_id": child_run,
                    "task_boundary_verdict": verdict,
                    "job_token": {"run_id": child_run, "task_id": task_id, "project_id": "owned-project"},
                },
            )
        )
    manifest.write_text('{"name":"repaired"}', encoding="utf-8")

    def projection(_run: object, _context: dict) -> dict:
        value = load_run_ledger_projection(tmp_path, run_id=child_run)
        # Controlled TaskRuntime query port: its lifecycle is tested separately.
        # TaskBoundary and all appends/reads here use the real public ledger.
        value["task_runtime_projection"] = {
            "schema_version": "task_runtime.observable_task_rows_authority.v1",
            "source": "task_runtime.execution_fact",
            "authoritative": True,
            "degraded": False,
            "readiness": {"ready": True},
            "owner_scope": "pm_contract_tasks",
            "owned_task_ids": ["TASK-OWNED"],
            "rows": [
                {
                    "task_id": "101",
                    "external_task_id": "TASK-OWNED",
                    "source": "task_runtime.execution_fact",
                    "status_source": "task_runtime.execution_fact",
                    "fact_event_seq": 1,
                    "execution_state": runtime_state,
                    "status": runtime_state,
                }
            ],
        }
        return value

    executor = SimpleNamespace(workspace=tmp_path, _canonical_factory_projection=projection)
    run = SimpleNamespace(id="factory-boundary-parent")
    prior = evaluate_canonical_factory_authority(projection(run, {}))
    assert prior.director_stage_authorized is False

    recovered = _recover_director_stage_authority_after_delivery_settle(
        executor, run=run, context={}, prior_authority=prior
    )
    final = projection(run, {})["task_boundary"]

    assert bool(recovered and recovered.director_stage_authorized) is authorized
    assert final["latest_by_task"]["TASK-FOREIGN"]["status"] == "missing_entrypoint_target"
    if runtime_state == "in_progress":
        assert final["verdict_count"] == 2
    else:
        assert final["verdict_count"] == 3
        assert final["historical_failed_count"] >= 2
        assert final["latest_by_task"]["TASK-OWNED"]["ok"] is authorized
        assert final["latest_by_task"]["TASK-OWNED"]["failed_required_verifiers"] == failed_verifiers
