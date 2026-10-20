"""Captured native identity relations; private fixtures, no provider or target command.

TaskRuntime creation/claim/settlement and CP append use real public producers.
Adversarial cases append explicit synthetic canonical facts, never mutate r01.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest
from polaris.cells.control_plane.run_ledger.public import (
    AppendRunLedgerEventCommandV1,
    AppendToolCallLifecycleEventCommandV1,
    ReadRunLedgerProjectionQueryV1,
    append_run_ledger_event,
    append_tool_call_lifecycle_event,
    build_tool_call_lifecycle_receipt,
    evaluate_task_boundary_verdict,
    read_run_ledger_projection,
)
from polaris.cells.events.fact_stream.public import (
    AppendFactEventCommandV1,
    BootstrapFactStreamWorkspaceCommandV1,
    QueryFactEventsV1,
    append_fact_event,
    bootstrap_fact_stream_workspace,
    query_fact_events,
)
from polaris.cells.runtime.task_runtime.public import (
    SettleTaskRuntimeExecutionAttemptCommandV1,
    TaskRuntimeExecutionAttemptIdentityV1,
    TaskRuntimeService,
)

FACTORY = "factory-private-identity"
CHILD = "director-private-identity"
PROJECT = "opaque-project.q7"
ALIAS = "opaque-unit.4f"


def _bootstrap(workspace: Path) -> None:
    bootstrap_fact_stream_workspace(
        BootstrapFactStreamWorkspaceCommandV1(
            workspace=str(workspace),
            streams=("execution.control_plane", "task_runtime.execution", "taskboard.terminal.events"),
            maintenance_reason="private canonical identity test",
        )
    )


def _native(workspace: Path, *, alias: str = ALIAS, child: str = CHILD) -> dict[str, Any]:
    _bootstrap(workspace)
    service = TaskRuntimeService(str(workspace))
    task = service.create_task_row(
        subject="Private identity fixture",
        metadata={
            "external_task_id": alias,
            "source_task_id": alias,
            "factory_run_id": FACTORY,
            "factory_bench_project_id": PROJECT,
        },
    )
    claim = service.claim_execution(
        task["id"],
        worker_id="director",
        role_id="director",
        run_id=child,
        external_task_id=alias,
        selection_source="private_identity_test",
    )
    assert claim["success"] is True, claim
    identity = TaskRuntimeExecutionAttemptIdentityV1.from_record(claim["execution_attempt"])
    settled = service.settle_execution_attempt(
        SettleTaskRuntimeExecutionAttemptCommandV1(
            workspace=str(workspace),
            identity=identity,
            outcome="completed",
            summary="Synthetic execution ended",
        )
    )
    assert settled["success"] is True, settled
    events = query_fact_events(
        QueryFactEventsV1(workspace=str(workspace), stream="task_runtime.execution", limit=1000)
    ).events
    native = next(
        row for row in events if row["event_type"] == "claimed" and row["payload"]["task_id"] == str(task["id"])
    )
    return dict(native)


def _cp(workspace: Path, *, task: str = ALIAS, child: str = CHILD, defect: str = "") -> None:
    append_run_ledger_event(
        AppendRunLedgerEventCommandV1(
            workspace=str(workspace),
            run_id=FACTORY,
            event={
                "event_type": "gate_evaluated",
                "gate": {"ok": False},
                "job_token": {"run_id": FACTORY, "project_id": PROJECT},
            },
        )
    )
    lifecycle = build_tool_call_lifecycle_receipt(
        run_id=child,
        task_id=task,
        turn_id="private-turn",
        role="director",
        dispatch_status="not_requested",
    )
    append_tool_call_lifecycle_event(
        AppendToolCallLifecycleEventCommandV1(
            workspace=str(workspace),
            run_id=child,
            task_id=task,
            turn_id="private-turn",
            role="director",
            lifecycle_receipt=lifecycle.to_dict(),
            project_id=PROJECT,
        )
    )
    verdict = evaluate_task_boundary_verdict(
        workspace=workspace,
        task_id=task,
        run_id=child,
        required_verifiers=["private-required-check"],
        completed_verifiers=[],
    ).to_dict()
    assert verdict["status"] == "required_verifier_missing"
    token = {"run_id": child, "task_id": task, "project_id": PROJECT}
    if defect == "token_task":
        token["task_id"] = "foreign-unit"
    if defect == "token_run":
        token["run_id"] = "foreign-child"
    if defect == "nested_task":
        verdict["task_id"] = "foreign-unit"
    if defect == "nested_run":
        verdict["run_id"] = "foreign-child"
    event = {
        "event_type": "task_boundary_verdict",
        "run_id": child,
        "task_id": task,
        "job_token": token,
        "task_boundary_verdict": verdict,
    }
    append_run_ledger_event(AppendRunLedgerEventCommandV1(workspace=str(workspace), run_id=child, event=event))


def _query(workspace: Path) -> dict[str, Any]:
    return read_run_ledger_projection(
        ReadRunLedgerProjectionQueryV1(
            workspace=str(workspace),
            run_id=FACTORY,
            factory_run_id=FACTORY,
            project_id=PROJECT,
            canonical_only=True,
            include_migration_ledgers=False,
        )
    ).projection


@pytest.mark.parametrize("alias", ["TASK-1", ALIAS])
def test_native_numeric_and_opaque_external_identity_cold_query_preserves_failed_boundary(
    tmp_path: Path, alias: str
) -> None:
    native = _native(tmp_path, alias=alias)
    assert native["payload"]["task_id"] == "1"
    _cp(tmp_path, task=alias)
    # New read-only service projection consumes persisted strict streams, not the live task object.
    result = _query(tmp_path)
    assert result["consumed_run_ids"] == [FACTORY, CHILD]
    assert result["canonical_evidence"]["strict_integrity"] is True
    assert result["ok"] is False
    boundary = result["task_boundary"]["latest_by_task"][alias]
    assert boundary["status"] == "required_verifier_missing"
    assert boundary["ok"] is False


@pytest.mark.parametrize("defect", ["token_task", "token_run", "nested_task", "nested_run"])
def test_cp_identities_remain_exact_after_native_alias_resolution(tmp_path: Path, defect: str) -> None:
    _native(tmp_path)
    _cp(tmp_path, defect=defect)
    with pytest.raises(ValueError, match=r"canonical_.*identity_mismatch"):
        _query(tmp_path)


def _append_variant(workspace: Path, native: dict[str, Any], defect: str) -> None:
    payload = copy.deepcopy(native["payload"])
    row = payload["task_row_snapshot"]
    metadata = row["metadata"]
    runtime = metadata["runtime_execution"]
    if defect == "alias_change":
        runtime["external_task_id"] = "changed-unit"
        metadata["external_task_id"] = "changed-unit"
        metadata["source_task_id"] = "changed-unit"
    elif defect == "ambiguous_alias":
        payload["task_id"] = "99"
        row["id"] = 99
        runtime["task_id"] = 99
        runtime["session_id"] = "private-second-session"
        payload["session_id"] = runtime["session_id"]
    elif defect == "foreign_session":
        runtime["session_id"] = "foreign-session"
    elif defect == "foreign_child":
        runtime["run_id"] = "foreign-child"
    elif defect == "foreign_workspace":
        metadata["workspace"] = str(workspace / "foreign")
    elif defect == "foreign_factory":
        metadata["factory_run_id"] = "foreign-factory"
    elif defect == "missing_session":
        payload["session_id"] = ""
    elif defect == "row_mismatch":
        row["id"] = 99
    elif defect == "missing_runtime":
        metadata.pop("runtime_execution")
    elif defect == "alias_disagreement":
        metadata["external_task_id"] = "foreign-unit"
    else:
        raise AssertionError(defect)
    append_fact_event(
        AppendFactEventCommandV1(
            workspace=str(workspace),
            stream="task_runtime.execution",
            event_type="claimed",
            source="runtime.task_runtime",
            run_id=CHILD,
            task_id=payload["task_id"],
            correlation_id=payload["session_id"],
            payload=payload,
            strict_integrity=True,
        )
    )


@pytest.mark.parametrize(
    "defect",
    [
        "alias_change",
        "ambiguous_alias",
        "foreign_session",
        "foreign_child",
        "foreign_workspace",
        "foreign_factory",
        "missing_session",
        "row_mismatch",
        "missing_runtime",
        "alias_disagreement",
    ],
)
def test_captured_alias_requires_one_consistent_native_owner(tmp_path: Path, defect: str) -> None:
    native = _native(tmp_path)
    _append_variant(tmp_path, native, defect)
    _cp(tmp_path)
    with pytest.raises(ValueError, match=r"canonical_lineage_.*(identity|session|alias|scope|workspace)"):
        _query(tmp_path)


def test_foreign_child_cannot_lend_its_alias_to_selected_child(tmp_path: Path) -> None:
    _native(tmp_path, alias="selected-unit")
    _native(tmp_path, alias=ALIAS, child="foreign-child")
    _cp(tmp_path, task=ALIAS)
    with pytest.raises(ValueError, match="canonical_control_plane_task_mismatch"):
        _query(tmp_path)


def test_cold_query_ignores_mutable_task_rows_and_sessions(tmp_path: Path) -> None:
    _native(tmp_path)
    _cp(tmp_path)
    # Deliberately contradictory private mirrors: only the immutable cut can qualify.
    for path in (tmp_path / ".polaris" / "runtime" / "tasks").glob("task_1*.json"):
        path.write_text(json.dumps({"id": 1, "external_task_id": "foreign-mutable"}), encoding="utf-8")
    result = _query(tmp_path)
    assert result["task_boundary"]["latest_by_task"][ALIAS]["ok"] is False


def test_repeated_identical_native_identity_is_not_ambiguous(tmp_path: Path) -> None:
    native = _native(tmp_path)
    payload = copy.deepcopy(native["payload"])
    append_fact_event(
        AppendFactEventCommandV1(
            workspace=str(tmp_path),
            stream="task_runtime.execution",
            event_type="claimed",
            source="runtime.task_runtime",
            run_id=CHILD,
            task_id=payload["task_id"],
            correlation_id=payload["session_id"],
            payload=payload,
            strict_integrity=True,
        )
    )
    _cp(tmp_path)
    assert _query(tmp_path)["task_boundary"]["latest_by_task"][ALIAS]["status"] == "required_verifier_missing"
