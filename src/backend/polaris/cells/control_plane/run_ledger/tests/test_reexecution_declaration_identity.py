"""Real row reset/import producers and strict, persistent Ledger consumers.

Private module fixtures only: no model, target command or native evidence write.
Adversarial cases append explicitly synthetic facts in the fixture workspace.
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any, Literal

import pytest
from polaris.cells.control_plane.run_ledger.tests.test_canonical_task_identity import (
    ALIAS,
    CHILD,
    FACTORY,
    PROJECT,
    _bootstrap,
    _cp,
    _query,
)
from polaris.cells.events.fact_stream.public import (
    AppendFactEventCommandV1,
    QueryFactEventsV1,
    append_fact_event,
    query_fact_events,
)
from polaris.cells.runtime.task_runtime.public import (
    SettleTaskRuntimeExecutionAttemptCommandV1,
    TaskRuntimeExecutionAttemptIdentityV1,
    TaskRuntimeService,
)

NEXT_CHILD = "director-private-reexecution"


def _facts(workspace: Path) -> list[dict[str, Any]]:
    return list(
        query_fact_events(
            QueryFactEventsV1(workspace=str(workspace), stream="task_runtime.execution", limit=1000)
        ).events
    )


def _settle(service: TaskRuntimeService, claim: dict[str, Any], *, outcome: Literal["completed", "failed"]) -> None:
    result = service.settle_execution_attempt(
        SettleTaskRuntimeExecutionAttemptCommandV1(
            workspace=service.workspace,
            identity=TaskRuntimeExecutionAttemptIdentityV1.from_record(claim["execution_attempt"]),
            outcome=outcome,
            summary="Private producer fixture; no real command or delivery verdict",
        )
    )
    assert result["success"] is True, result


def _prepared(workspace: Path, operation: str = "reset") -> tuple[dict[str, Any], list[dict[str, Any]]]:
    _bootstrap(workspace)
    service = TaskRuntimeService(str(workspace))
    task = service.create_task_row(
        subject="Private reset lifecycle",
        metadata={
            "external_task_id": ALIAS,
            "factory_run_id": FACTORY,
            "factory_bench_project_id": PROJECT,
        },
    )
    claim = service.claim_execution(
        task["id"], worker_id="director", role_id="director", run_id=CHILD, external_task_id=ALIAS
    )
    assert claim["success"] is True, claim
    _settle(service, claim, outcome="failed")
    _cp(workspace)
    before = copy.deepcopy(_facts(workspace))
    if operation == "reset":
        result = service.reset_task_rows_for_reexecution(
            source="private_owner_rework", eligible_external_task_ids=[ALIAS]
        )
        assert result["reset_files"] == ["task_1.json"]
        kind = "reexecution_reset"
    else:
        assert operation == "import"
        saved = service.get_task(task["id"])
        assert saved is not None
        result = service.import_task_rows_for_reexecution(
            [saved], source="private_persisted_recovery", source_task_dir="private-snapshot"
        )
        assert result["imported_files"] == ["task_1.json"]
        kind = "reexecution_imported"
    after = _facts(workspace)
    assert after[: len(before)] == before
    prepared = next(row for row in reversed(after) if row["event_type"] == kind)
    assert prepared["payload"]["run_id"] == prepared["payload"]["session_id"] == ""
    assert prepared["payload"]["attempt"] == 0
    assert prepared["payload"]["task_row_snapshot"]["id"] == task["id"]
    assert not (workspace / ".polaris/runtime/tasks/task_1.session.json").exists()
    return prepared, before


@pytest.mark.parametrize("operation", ["reset", "import"])
def test_real_preexecution_declaration_survives_cold_query_and_new_claim_without_erasing_failure(
    tmp_path: Path, operation: str
) -> None:
    prepared, history = _prepared(tmp_path, operation)
    # The declaration alone proves no child: it cannot erase the old failed claim.
    before_new_claim = _query(tmp_path)
    assert before_new_claim["consumed_run_ids"] == [FACTORY, CHILD]
    assert before_new_claim["ok"] is False
    cold = TaskRuntimeService(str(tmp_path))
    row = cold.get_task(1)
    assert row is not None
    assert row["id"] == 1 and row["status"] == "pending"
    new = cold.claim_execution(1, worker_id="director", role_id="director", run_id=NEXT_CHILD, external_task_id=ALIAS)
    assert new["success"] is True, new
    old_claim = next(row for row in history if row["event_type"] == "claimed")
    assert new["execution_attempt"]["session_id"] != old_claim["payload"]["session_id"]
    _settle(cold, new, outcome="completed")
    _cp(tmp_path, child=NEXT_CHILD)
    # A fresh read uses only strict immutable streams, independently of live objects.
    result = _query(tmp_path)
    assert result["canonical_evidence"]["strict_integrity"] is True
    assert result["consumed_run_ids"] == [FACTORY, CHILD, NEXT_CHILD]
    assert result["ok"] is False
    after = _facts(tmp_path)
    assert after[: len(history)] == history
    assert prepared in after
    assert any(row["event_type"] == "failed" and row["payload"]["run_id"] == CHILD for row in after)
    assert any(row["event_type"] == "claimed" and row["payload"]["run_id"] == NEXT_CHILD for row in after)


@pytest.mark.parametrize(
    "defect",
    [
        "workspace",
        "factory",
        "project",
        "task",
        "run",
        "session",
        "snapshot_session",
        "runtime_session",
        "runtime_alias",
        "attempt",
        "status",
        "missing_snapshot",
    ],
)
def test_synthetic_reset_contradiction_cannot_become_execution_authority(tmp_path: Path, defect: str) -> None:
    prepared, _history = _prepared(tmp_path)
    payload = copy.deepcopy(prepared["payload"])
    snapshot = payload["task_row_snapshot"]
    metadata = snapshot["metadata"]
    if defect == "workspace":
        snapshot["workspace"] = str(tmp_path / "foreign")
    elif defect == "factory":
        metadata["factory_run_id"] = "foreign-factory"
    elif defect == "project":
        metadata["factory_bench_project_id"] = "foreign-project"
    elif defect == "task":
        snapshot["id"] = 99
    elif defect == "run":
        snapshot["workflow_run_id"] = "foreign-child"
    elif defect == "session":
        payload["session_id"] = "synthetic-session"
    elif defect == "snapshot_session":
        snapshot["session_id"] = "synthetic-session"
    elif defect == "runtime_session":
        metadata["runtime_execution"]["session_id"] = "synthetic-session"
    elif defect == "runtime_alias":
        metadata["runtime_execution"]["external_task_id"] = "synthetic-alias"
    elif defect == "attempt":
        payload["attempt"] = 1
    elif defect == "status":
        payload["status"] = payload["execution_state"] = snapshot["status"] = "completed"
    elif defect == "missing_snapshot":
        payload.pop("task_row_snapshot")
    else:
        raise AssertionError(defect)
    append_fact_event(
        AppendFactEventCommandV1(
            workspace=str(tmp_path),
            stream="task_runtime.execution",
            event_type="reexecution_reset",
            source="runtime.task_runtime",
            task_id=payload["task_id"],
            payload=payload,
            strict_integrity=True,
        )
    )
    with pytest.raises(ValueError, match="canonical_lineage_"):
        _query(tmp_path)


def test_real_dependency_row_declarations_cannot_lend_unclaimed_task_alias(tmp_path: Path) -> None:
    _bootstrap(tmp_path)
    service = TaskRuntimeService(str(tmp_path))
    metadata = {"factory_run_id": FACTORY, "factory_bench_project_id": PROJECT}
    parent = service.create_task_row(subject="First prerequisite", metadata={**metadata, "external_task_id": ALIAS})
    other = service.create_task_row(subject="Second prerequisite", metadata={**metadata, "external_task_id": "other"})
    dependent = service.create_task_row(
        subject="Never executed dependent",
        blocked_by=[parent["id"], other["id"]],
        metadata={**metadata, "external_task_id": "unclaimed-alias"},
    )
    for task, alias in ((parent, ALIAS), (other, "other")):
        claim = service.claim_execution(
            task["id"], worker_id="director", role_id="director", run_id=CHILD, external_task_id=alias
        )
        assert claim["success"] is True, claim
        _settle(service, claim, outcome="completed")
    assert service.reopen_task_row(parent["id"], reason="private dependency rework") is not None
    kinds = {row["event_type"] for row in _facts(tmp_path)}
    assert {
        "reverse_dependency_linked",
        "dependency_blockers_refreshed",
        "dependencies_unblocked",
        "downstream_dependency_reblocked",
    } <= kinds
    _cp(tmp_path)
    result = _query(tmp_path)
    assert result["canonical_evidence"]["strict_integrity"] is True
    assert result["ok"] is False
    # Synthetic CP task spelling cannot borrow the never-claimed dependent declaration.
    _cp(tmp_path, task="unclaimed-alias")
    with pytest.raises(ValueError, match="canonical_control_plane_task_mismatch"):
        _query(tmp_path)
    dependent_row = service.get_task(dependent["id"])
    assert dependent_row is not None and dependent_row["status"] == "blocked"


@pytest.mark.parametrize("state", ["active", "completed"])
def test_real_declarations_keep_historical_session_snapshot_without_claiming_it(tmp_path: Path, state: str) -> None:
    _bootstrap(tmp_path)
    service = TaskRuntimeService(str(tmp_path))
    metadata = {"factory_run_id": FACTORY, "factory_bench_project_id": PROJECT, "external_task_id": ALIAS}
    task = service.create_task_row(subject="Private historical row", metadata=metadata)
    claim = service.claim_execution(
        task["id"], worker_id="director", role_id="director", run_id=CHILD, external_task_id=ALIAS
    )
    assert claim["success"] is True, claim
    if state == "completed":
        _settle(service, claim, outcome="completed")
    assert service.update_task_row(task["id"], metadata={"private_note": "preserve historical identity"}) is not None
    service.create_task_row(
        subject="Unclaimed follower",
        blocked_by=[task["id"]],
        metadata={**metadata, "external_task_id": "unclaimed-follower"},
    )
    declarations = [row for row in _facts(tmp_path) if row["event_type"] in {"updated", "reverse_dependency_linked"}]
    assert {row["event_type"] for row in declarations} == {"updated", "reverse_dependency_linked"}
    for row in declarations:
        payload = row["payload"]
        assert payload["session_id"] == "" and not row.get("correlation_id")
        assert (
            payload["task_row_snapshot"]["metadata"]["runtime_execution"]["session_id"]
            == claim["session"]["session_id"]
        )
    _cp(tmp_path)
    result = _query(tmp_path)
    assert result["consumed_run_ids"] == [FACTORY, CHILD]
    assert result["ok"] is False


def _coordination_then_repair(workspace: Path, *, ce_factory: str = FACTORY, ce_project: str = "") -> None:
    """Real CE coordination and Director repair share the native Factory run."""
    _bootstrap(workspace)
    service = TaskRuntimeService(str(workspace))
    ce_metadata = {"external_task_id": "ce-coordination"}
    if ce_factory:
        ce_metadata["factory_run_id"] = ce_factory
    if ce_project:
        ce_metadata["factory_bench_project_id"] = ce_project
    ce = service.create_task_row(subject="Private CE coordination", metadata=ce_metadata)
    claim = service.claim_execution(
        ce["id"],
        worker_id="chief_engineer",
        role_id="chief_engineer",
        run_id=FACTORY,
        external_task_id="ce-coordination",
    )
    assert claim["success"] is True, claim
    _settle(service, claim, outcome="completed")
    director = service.create_task_row(
        subject="Private current Director repair",
        metadata={"external_task_id": ALIAS, "factory_run_id": FACTORY, "factory_bench_project_id": PROJECT},
    )
    repair = service.claim_execution(
        director["id"], worker_id="director", role_id="director", run_id=FACTORY, external_task_id=ALIAS
    )
    assert repair["success"] is True, repair
    _settle(service, repair, outcome="failed")
    _cp(workspace, child=FACTORY)


def test_real_projectless_ce_constraint_does_not_poison_scoped_repair_or_lend_its_alias(tmp_path: Path) -> None:
    _coordination_then_repair(tmp_path)
    result = _query(tmp_path)
    assert result["canonical_evidence"]["strict_integrity"] is True
    assert result["consumed_run_ids"] == [FACTORY]
    assert result["ok"] is False
    # The unspecified CE project contributes no selected-project execution authority.
    _cp(tmp_path, child=FACTORY, task="ce-coordination")
    with pytest.raises(ValueError, match="canonical_control_plane_task_mismatch"):
        _query(tmp_path)


@pytest.mark.parametrize("defect", ["foreign_factory", "missing_factory", "foreign_project"])
def test_real_incompatible_owner_constraints_still_fail_closed(tmp_path: Path, defect: str) -> None:
    _coordination_then_repair(
        tmp_path,
        ce_factory="foreign-factory" if defect == "foreign_factory" else "" if defect == "missing_factory" else FACTORY,
        ce_project="foreign-project" if defect == "foreign_project" else "",
    )
    with pytest.raises(ValueError, match="canonical_factory_child_lineage_conflict"):
        _query(tmp_path)
