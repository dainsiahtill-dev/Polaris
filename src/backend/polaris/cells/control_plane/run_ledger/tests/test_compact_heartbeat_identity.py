"""Real owner heartbeats consumed by the strict cold Ledger reader.

All tasks and CP failures are private fixtures. Adversarial copies are explicit
synthetic facts; no native experiment, provider, command or receipt is changed.
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest
from polaris.cells.control_plane.run_ledger.public import (
    AppendRunLedgerEventCommandV1,
    ReadRunLedgerProjectionQueryV1,
    append_run_ledger_event,
    read_run_ledger_projection,
)
from polaris.cells.control_plane.run_ledger.public.service import (
    _captured_native_task_identity,
)
from polaris.cells.events.fact_stream.public import (
    BootstrapFactStreamWorkspaceCommandV1,
    QueryFactEventsV1,
    bootstrap_fact_stream_workspace,
    query_fact_events,
)
from polaris.cells.runtime.task_runtime.public import (
    SettleTaskRuntimeExecutionAttemptCommandV1,
    TaskRuntimeExecutionAttemptIdentityV1,
    TaskRuntimeService,
)

FACTORY = "factory-private-heartbeat"
CHILD = "child-private-heartbeat"
PROJECT = "opaque-project.heartbeat"
ALIAS = "opaque-task.heartbeat"


def _heartbeat(workspace: Path, role: str = "chief_engineer") -> dict[str, Any]:
    bootstrap_fact_stream_workspace(
        BootstrapFactStreamWorkspaceCommandV1(
            workspace=str(workspace),
            streams=("execution.control_plane", "task_runtime.execution", "taskboard.terminal.events"),
            maintenance_reason="private compact heartbeat identity experiment",
        )
    )
    service = TaskRuntimeService(str(workspace))
    task = service.create_task_row(
        subject="Private heartbeat fixture",
        metadata={
            "factory_run_id": FACTORY,
            "factory_bench_project_id": PROJECT,
            "external_task_id": ALIAS,
        },
    )
    claim = service.claim_execution(task["id"], worker_id=role, role_id=role, run_id=CHILD, external_task_id=ALIAS)
    assert claim["success"] is True, claim
    renewed = service.heartbeat_execution(task["id"], session_id=claim["session"]["session_id"])
    assert renewed["success"] is True, renewed
    current_attempt = {**claim["execution_attempt"], "lease_expires_at": renewed["session"]["lease_expires_at"]}
    identity = TaskRuntimeExecutionAttemptIdentityV1.from_record(current_attempt)
    settled = service.settle_execution_attempt(
        SettleTaskRuntimeExecutionAttemptCommandV1(
            workspace=str(workspace), identity=identity, outcome="completed", summary="Private owner ended"
        )
    )
    assert settled["success"] is True, settled
    rows = query_fact_events(
        QueryFactEventsV1(workspace=str(workspace), stream="task_runtime.execution", limit=1000)
    ).events
    heartbeat = next(row for row in rows if row["event_type"] == "heartbeat_renewed")
    assert "workflow_run_id" not in heartbeat["payload"]["task_row_snapshot"]
    return dict(heartbeat)


@pytest.mark.parametrize("role", ["chief_engineer", "director"])
def test_real_compact_heartbeat_allows_strict_cold_projection_without_passing_failure(
    tmp_path: Path, role: str
) -> None:
    heartbeat = _heartbeat(tmp_path, role)
    identity = _captured_native_task_identity(heartbeat, heartbeat["payload"], tmp_path.resolve())
    assert identity is not None and identity.external_task_id == ALIAS and identity.role_id == role
    for run_id in (FACTORY, CHILD):
        append_run_ledger_event(
            AppendRunLedgerEventCommandV1(
                workspace=str(tmp_path),
                run_id=run_id,
                event={
                    "event_type": "gate_evaluated",
                    "task_id": ALIAS if run_id == CHILD else "",
                    "gate": {"name": "private_missing_verifier", "ok": False},
                    "job_token": {
                        "run_id": run_id,
                        "project_id": PROJECT,
                        "task_id": ALIAS if run_id == CHILD else "",
                    },
                },
            )
        )
    projection = read_run_ledger_projection(
        ReadRunLedgerProjectionQueryV1(
            workspace=str(tmp_path),
            run_id=FACTORY,
            factory_run_id=FACTORY,
            project_id=PROJECT,
            canonical_only=True,
            include_migration_ledgers=False,
        )
    ).projection
    assert projection["canonical_evidence"]["strict_integrity"] is True
    assert projection["consumed_run_ids"] == [FACTORY, CHILD]
    assert projection["ok"] is False


@pytest.mark.parametrize(
    "defect",
    [
        "explicit_foreign",
        "explicit_null",
        "explicit_empty",
        "wrong_event",
        "payload_run",
        "runtime_run",
        "session",
        "attempt",
        "task",
        "workspace",
        "missing_runtime",
    ],
)
def test_compact_heartbeat_cannot_hide_explicit_or_typed_identity_contradiction(tmp_path: Path, defect: str) -> None:
    row = copy.deepcopy(_heartbeat(tmp_path))
    payload = row["payload"]
    snapshot = payload["task_row_snapshot"]
    runtime = snapshot["metadata"]["runtime_execution"]
    if defect.startswith("explicit_"):
        snapshot["workflow_run_id"] = {"explicit_foreign": "foreign", "explicit_null": None, "explicit_empty": ""}[
            defect
        ]
    elif defect == "wrong_event":
        row["event_type"] = "claimed"
    elif defect == "payload_run":
        payload["run_id"] = "foreign"
    elif defect == "runtime_run":
        runtime["run_id"] = "foreign"
    elif defect == "session":
        payload["session_id"] = "foreign"
    elif defect == "attempt":
        payload["attempt"] = runtime["attempt"] + 1
    elif defect == "task":
        snapshot["id"] += 1
    elif defect == "workspace":
        runtime["workspace"] = str(tmp_path / "foreign")
    elif defect == "missing_runtime":
        snapshot["metadata"].pop("runtime_execution")
    else:
        raise AssertionError(defect)
    with pytest.raises(ValueError, match="canonical_lineage_"):
        _captured_native_task_identity(row, payload, tmp_path.resolve())
