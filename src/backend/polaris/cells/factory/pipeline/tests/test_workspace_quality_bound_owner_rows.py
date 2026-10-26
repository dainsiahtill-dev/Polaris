"""Factory consumes bound owner rows without treating snapshots as execution grants."""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from typing import Any, Literal

import pytest
from polaris.cells.events.fact_stream.public import (
    BootstrapFactStreamWorkspaceCommandV1,
    QueryFactEventsV1,
    bootstrap_fact_stream_workspace,
    fact_stream_bootstrap_streams,
    query_fact_events,
)
from polaris.cells.factory.pipeline.internal import factory_workspace_quality_impl as quality
from polaris.cells.factory.pipeline.internal.factory_run_models import FactoryConfig, FactoryRun, FactoryRunStatus
from polaris.cells.factory.pipeline.internal.factory_stage_executor._executor import OrchestrationStageExecutor
from polaris.cells.factory.pipeline.public.contracts import (
    FACTORY_TERMINAL_TASK_RUNTIME_PROJECTION_METADATA_KEY,
    FactoryTerminalTaskRuntimeProjectionV1,
)
from polaris.cells.factory.pipeline.tests.test_factory_recovery_task_identity import _failed_ce_owner
from polaris.cells.runtime.task_runtime.public import (
    BindRuntimeTaskToFactoryRunCommandV1,
    RuntimeTaskFactoryRunBindingResultV1,
    SettleTaskRuntimeExecutionAttemptCommandV1,
    TaskRuntimeExecutionAttemptIdentityV1,
    TaskRuntimeService,
    ValidateTaskRuntimeExecutionAttemptQueryV1,
    bind_runtime_task_to_factory_run,
    query_observable_task_rows,
    validate_task_runtime_execution_attempt,
)


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("KERNELONE_HOME", str(tmp_path / "kernelone"))
    root = tmp_path / "workspace"
    root.mkdir()
    bootstrap_fact_stream_workspace(
        BootstrapFactStreamWorkspaceCommandV1(
            workspace=str(root), streams=fact_stream_bootstrap_streams(), maintenance_reason="private-bound-owner-test"
        )
    )
    return root


def test_materializer_preserves_bound_physical_row_and_recorded_fact(workspace: Path) -> None:
    executor = OrchestrationStageExecutor(workspace)
    result = executor._materialize_pm_plan_taskboard(
        [{"id": "TASK-OWNER", "goal": "Own generic module", "target_files": ["src/module.py"]}],
        run_id="factory-bound-owner",
        source_stage="quality_gate",
    )
    assert result["binding_failures"] == []
    rows = result.get("bound_task_rows")
    assert isinstance(rows, list) and len(rows) == 1, "materializer discarded the public owner's exact bound row"
    carried = rows[0]
    assert carried["external_task_id"] == "TASK-OWNER"
    assert carried["event_recorded"] is True
    assert carried["workspace"] == str(workspace)
    assert carried["factory_run_id"] == "factory-bound-owner"
    row = carried["row"]
    assert carried["task_id"] == str(row["id"])
    assert row["metadata"]["external_task_id"] == "TASK-OWNER"
    assert row["metadata"]["factory_run_id"] == "factory-bound-owner"
    assert json.loads(json.dumps(carried, ensure_ascii=False)) == carried


def test_stale_bound_row_cannot_suspend_a_successor_execution_attempt(workspace: Path) -> None:
    service = TaskRuntimeService(str(workspace))
    row = service.create_task_row(
        subject="Private terminal owner",
        metadata={"external_task_id": "TASK-OWNER", "factory_run_id": "factory-bound-owner"},
    )
    first = service.claim_execution(
        row["id"], worker_id="first", role_id="director", run_id="factory-bound-owner", external_task_id="TASK-OWNER"
    )
    assert first["success"]
    first_identity = TaskRuntimeExecutionAttemptIdentityV1.from_record(first["execution_attempt"])
    assert service.settle_execution_attempt(
        SettleTaskRuntimeExecutionAttemptCommandV1(str(workspace), first_identity, "completed", "private first attempt")
    )["success"]
    carried = bind_runtime_task_to_factory_run(
        BindRuntimeTaskToFactoryRunCommandV1(str(workspace), "TASK-OWNER", "factory-bound-owner")
    )
    assert carried.ok and carried.task_row["status"] == "completed"
    # This is the fresh Factory reader after materialization. Another owner can
    # advance the task before the caller applies its carried completed snapshot.
    factory_reader = TaskRuntimeService(str(workspace))
    successor = TaskRuntimeService(str(workspace))
    assert successor.reopen_task_row(row["id"], reason="private successor") is not None
    claimed = successor.claim_execution(
        row["id"],
        worker_id="successor",
        role_id="director",
        run_id="factory-bound-owner",
        external_task_id="TASK-OWNER",
    )
    assert claimed["success"]
    identity = TaskRuntimeExecutionAttemptIdentityV1.from_record(claimed["execution_attempt"])
    before = validate_task_runtime_execution_attempt(
        ValidateTaskRuntimeExecutionAttemptQueryV1(str(workspace), identity)
    )
    assert before.valid
    with pytest.raises(RuntimeError, match="compare_and_set_failed"):
        factory_reader.reopen_task_row(
            int(carried.task_id), reason="workspace_quality_gate_failed", expected_binding=carried
        )
    after = validate_task_runtime_execution_attempt(
        ValidateTaskRuntimeExecutionAttemptQueryV1(str(workspace), identity)
    )
    assert after.valid, "a stale completed row altered the current successor attempt"


@pytest.mark.parametrize("outcome", ["completed", "failed"])
def test_conditional_reopen_preserves_unchanged_terminal_owner_and_allows_next_claim(
    workspace: Path, outcome: Literal["completed", "failed"]
) -> None:
    service = TaskRuntimeService(str(workspace))
    row = service.create_task_row(
        subject="Private conditional owner",
        metadata={"external_task_id": "TASK-OWNER", "factory_run_id": "factory-bound-owner"},
    )
    claim = service.claim_execution(
        row["id"], worker_id="first", role_id="director", run_id="factory-bound-owner", external_task_id="TASK-OWNER"
    )
    identity = TaskRuntimeExecutionAttemptIdentityV1.from_record(claim["execution_attempt"])
    assert service.settle_execution_attempt(
        SettleTaskRuntimeExecutionAttemptCommandV1(str(workspace), identity, outcome, "private terminal owner")
    )["success"]
    binding = bind_runtime_task_to_factory_run(
        BindRuntimeTaskToFactoryRunCommandV1(str(workspace), "TASK-OWNER", "factory-bound-owner")
    )
    reopened = service.reopen_task_row(row["id"], reason="workspace_quality_gate_failed", expected_binding=binding)
    assert reopened is not None and reopened["status"] in {"pending", "ready"}
    successor = TaskRuntimeService(str(workspace)).claim_execution(
        row["id"], worker_id="next", role_id="director", run_id="factory-bound-owner", external_task_id="TASK-OWNER"
    )
    assert successor["success"]


def test_conditional_reopen_preserves_blocked_owner_without_claiming_its_dependencies(workspace: Path) -> None:
    service = TaskRuntimeService(str(workspace))
    dependency = service.create_task_row(subject="Unresolved dependency")
    row = service.create_task_row(
        subject="Private blocked owner",
        blocked_by=[dependency["id"]],
        metadata={"external_task_id": "TASK-OWNER", "factory_run_id": "factory-bound-owner"},
    )
    binding = bind_runtime_task_to_factory_run(
        BindRuntimeTaskToFactoryRunCommandV1(str(workspace), "TASK-OWNER", "factory-bound-owner")
    )
    assert binding.task_row["status"] == "blocked"
    reopened = service.reopen_task_row(row["id"], reason="workspace_quality_gate_failed", expected_binding=binding)
    assert reopened is not None and reopened["status"] == "blocked"
    assert reopened["blocked_by"] == [dependency["id"]]


def _completed_binding(workspace: Path) -> tuple[TaskRuntimeService, RuntimeTaskFactoryRunBindingResultV1]:
    service = TaskRuntimeService(str(workspace))
    row = service.create_task_row(
        subject="Private bound comparison",
        metadata={
            "external_task_id": "TASK-OWNER",
            "factory_run_id": "factory-bound-owner",
            "task_completion_projection": {"project_contract_hash": "a" * 64, "projection_hash": "b" * 64},
            "control_plane_job_token": {"token_id": "private-token", "allowed_write_paths": ["src/module.py"]},
        },
    )
    claim = service.claim_execution(
        row["id"], worker_id="first", role_id="director", run_id="factory-bound-owner", external_task_id="TASK-OWNER"
    )
    assert claim["success"]
    identity = TaskRuntimeExecutionAttemptIdentityV1.from_record(claim["execution_attempt"])
    assert service.settle_execution_attempt(
        SettleTaskRuntimeExecutionAttemptCommandV1(str(workspace), identity, "completed", "private owner")
    )["success"]
    binding = bind_runtime_task_to_factory_run(
        BindRuntimeTaskToFactoryRunCommandV1(str(workspace), "TASK-OWNER", "factory-bound-owner")
    )
    assert binding.ok
    return service, binding


def test_conditional_reopen_rejects_new_namespace_sibling(workspace: Path) -> None:
    service, binding = _completed_binding(workspace)
    service.create_task_row(subject="Competing logical owner", metadata=dict(binding.task_row["metadata"]))
    before = service.list_observable_task_rows()
    with pytest.raises(RuntimeError, match="compare_and_set_failed"):
        service.reopen_task_row(binding.task_id, reason="workspace_quality_gate_failed", expected_binding=binding)
    assert TaskRuntimeService(str(workspace)).list_observable_task_rows() == before


@pytest.mark.parametrize("field", ["task_completion_projection", "control_plane_job_token"])
def test_conditional_reopen_rejects_authority_drift_then_advances_with_fresh_binding(
    workspace: Path, field: str
) -> None:
    service, binding = _completed_binding(workspace)
    service.update_task_row(int(binding.task_id), metadata={field: {"changed": "owner metadata"}})
    before = service.list_observable_task_rows()
    with pytest.raises(RuntimeError, match="compare_and_set_failed"):
        service.reopen_task_row(binding.task_id, reason="workspace_quality_gate_failed", expected_binding=binding)
    assert service.list_observable_task_rows() == before
    fresh = bind_runtime_task_to_factory_run(
        BindRuntimeTaskToFactoryRunCommandV1(str(workspace), binding.task_id, "factory-bound-owner")
    )
    assert fresh.ok
    reopened = service.reopen_task_row(fresh.task_id, reason="workspace_quality_gate_failed", expected_binding=fresh)
    assert reopened is not None and reopened["status"] in {"pending", "ready"}


def test_conditional_reopen_rejects_foreign_binding_and_missing_carried_row(workspace: Path) -> None:
    service, binding = _completed_binding(workspace)
    foreign = bind_runtime_task_to_factory_run(
        BindRuntimeTaskToFactoryRunCommandV1(str(workspace), binding.task_id, "factory-foreign")
    )
    assert not foreign.ok and foreign.code == "factory_run_binding_conflict"
    before = service.list_observable_task_rows()
    for invalid in (foreign, replace(binding, task_row={})):
        with pytest.raises(RuntimeError, match="compare_and_set_failed"):
            service.reopen_task_row(binding.task_id, reason="workspace_quality_gate_failed", expected_binding=invalid)
    assert service.list_observable_task_rows() == before


def test_partial_binding_fact_failure_is_not_a_reopen_grant(workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    service = TaskRuntimeService(str(workspace))
    row = service.create_task_row(subject="Private unbound owner", metadata={"external_task_id": "TASK-OWNER"})
    native_append = TaskRuntimeService._append_execution_event

    def failed_fact(self: TaskRuntimeService, event_type: str, **kwargs: Any) -> dict[str, Any]:
        if event_type == "factory_run_bound":
            return {"ok": False, "error": "private fault after durable binding before fact append"}
        return native_append(self, event_type, **kwargs)

    # Fault only the append boundary; binding's actual row write and typed
    # public failure remain real. No positive owner result is substituted.
    monkeypatch.setattr(TaskRuntimeService, "_append_execution_event", failed_fact)
    partial = bind_runtime_task_to_factory_run(
        BindRuntimeTaskToFactoryRunCommandV1(str(workspace), str(row["id"]), "factory-bound-owner")
    )
    assert not partial.ok and partial.row_updated and not partial.event_recorded
    assert partial.task_row["metadata"]["factory_run_id"] == "factory-bound-owner"
    with pytest.raises(RuntimeError, match="compare_and_set_failed"):
        service.reopen_task_row(partial.task_id, reason="workspace_quality_gate_failed", expected_binding=partial)


def test_two_conditional_reopen_callers_commit_only_one_transition(workspace: Path) -> None:
    _service, binding = _completed_binding(workspace)
    peers = [TaskRuntimeService(str(workspace)), TaskRuntimeService(str(workspace))]

    def reopen(peer: TaskRuntimeService) -> bool:
        try:
            return (
                peer.reopen_task_row(binding.task_id, reason="workspace_quality_gate_failed", expected_binding=binding)
                is not None
            )
        except RuntimeError:
            return False

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(reopen, peers))
    assert results.count(True) == results.count(False) == 1
    assert len(TaskRuntimeService(str(workspace)).list_observable_task_rows()) == 1


def test_conditional_reopen_does_not_treat_session_read_failure_as_absence(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, binding = _completed_binding(workspace)
    native_read = TaskRuntimeService._read_session_locked

    def failed_read(self: TaskRuntimeService, task_id: int, *, raise_infrastructure_errors: bool = False) -> Any:
        if self.workspace == str(workspace) and task_id == int(binding.task_id):
            if raise_infrastructure_errors:
                raise ValueError("private unreadable session")
            return None
        return native_read(self, task_id, raise_infrastructure_errors=raise_infrastructure_errors)

    # Exercise the real transition with the reader's existing explicit error
    # contract. An unavailable session must not become a successful absence.
    monkeypatch.setattr(TaskRuntimeService, "_read_session_locked", failed_read)
    with pytest.raises(ValueError, match="private unreadable session"):
        service.reopen_task_row(binding.task_id, reason="workspace_quality_gate_failed", expected_binding=binding)


def _facts(workspace: Path) -> tuple[dict[str, Any], ...]:
    return query_fact_events(
        QueryFactEventsV1(workspace=str(workspace), stream="task_runtime.execution", strict_integrity=True, limit=1000)
    ).events


def _run() -> FactoryRun:
    return FactoryRun(
        id="factory-one",
        config=FactoryConfig(name="private owner"),
        status=FactoryRunStatus.FAILED,
        created_at="2026-10-26T00:00:00+00:00",
    )


def test_normal_owner_extra_exact_binding_is_idempotent_before_scoring(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _service, owner, _plan = _failed_ce_owner(workspace)
    native_bind = quality.bind_runtime_task_to_factory_run
    exact_bindings: list[RuntimeTaskFactoryRunBindingResultV1] = []

    def observe_bind(command: BindRuntimeTaskToFactoryRunCommandV1) -> RuntimeTaskFactoryRunBindingResultV1:
        before = _facts(workspace)
        result = native_bind(command)
        if command.task_id == str(owner["id"]):
            assert _facts(workspace) == before
            exact_bindings.append(result)
        return result

    monkeypatch.setattr(quality, "bind_runtime_task_to_factory_run", observe_bind)
    external, task_id, identity, _task = quality._claim_workspace_quality_repair_attempt(
        OrchestrationStageExecutor(workspace), run=_run(), repair_attempt=1, target_files=["src/a.py"]
    )
    assert external == "WORK-A" and task_id == owner["id"] and identity.task_id == owner["id"]
    assert len(exact_bindings) == 1 and exact_bindings[0].idempotent
    assert exact_bindings[0].task_id == str(owner["id"])


def test_normal_owner_stale_binding_refuses_concurrent_successor_before_reopen(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _service, owner, _plan = _failed_ce_owner(workspace)
    native_bind = quality.bind_runtime_task_to_factory_run
    successor: list[TaskRuntimeExecutionAttemptIdentityV1] = []
    after_successor: list[tuple[dict[str, Any], ...]] = []

    def claim_successor() -> None:
        peer = TaskRuntimeService(str(workspace))
        assert peer.reopen_task_row(owner["id"], reason="separately admitted continuation") is not None
        claim = peer.claim_execution(
            owner["id"],
            worker_id="concurrent",
            role_id="director",
            run_id="director-concurrent",
            external_task_id="WORK-A",
        )
        assert claim["success"]
        successor.append(TaskRuntimeExecutionAttemptIdentityV1.from_record(claim["execution_attempt"]))
        after_successor.append(_facts(workspace))

    def interleave_bind(command: BindRuntimeTaskToFactoryRunCommandV1) -> RuntimeTaskFactoryRunBindingResultV1:
        bound = native_bind(command)
        if command.task_id == str(owner["id"]) and not successor:
            with ThreadPoolExecutor(max_workers=1) as pool:
                pool.submit(claim_successor).result(timeout=5)
        return bound

    monkeypatch.setattr(quality, "bind_runtime_task_to_factory_run", interleave_bind)
    with pytest.raises(RuntimeError, match="compare_and_set_failed"):
        quality._claim_workspace_quality_repair_attempt(
            OrchestrationStageExecutor(workspace), run=_run(), repair_attempt=1, target_files=["src/a.py"]
        )
    assert len(successor) == len(after_successor) == 1
    assert validate_task_runtime_execution_attempt(
        ValidateTaskRuntimeExecutionAttemptQueryV1(str(workspace), successor[0])
    ).valid
    assert _facts(workspace) == after_successor[0]


def test_normal_exact_numeric_binding_never_mutates_an_external_numeric_alias(workspace: Path) -> None:
    service, owner, _plan = _failed_ce_owner(workspace)
    alias = service.create_task_row(
        subject="Different unbound logical task", metadata={"external_task_id": str(owner["id"])}
    )
    before = _facts(workspace)
    with pytest.raises(RuntimeError):
        quality._claim_workspace_quality_repair_attempt(
            OrchestrationStageExecutor(workspace), run=_run(), repair_attempt=1, target_files=["src/a.py"]
        )
    current = next(row for row in service.list_observable_task_rows() if row["id"] == alias["id"])
    assert not current["metadata"].get("factory_run_id"), "numeric owner lookup bound a different logical row"
    assert _facts(workspace) == before


@pytest.mark.parametrize("fault", ["none", "missing", "old_row", "incomplete"])
def test_frozen_owner_uses_real_materializer_binding_and_rejects_incomplete_projection(
    workspace: Path, monkeypatch: pytest.MonkeyPatch, fault: str
) -> None:
    service, original, plan = _failed_ce_owner(workspace)
    projection = query_observable_task_rows(str(workspace)).to_authority_dict(factory_run_id="factory-one")
    run = _run()
    run.metadata[FACTORY_TERMINAL_TASK_RUNTIME_PROJECTION_METADATA_KEY] = FactoryTerminalTaskRuntimeProjectionV1(
        workspace=str(workspace),
        factory_run_id=run.id,
        captured_at="2026-10-26T00:00:00+00:00",
        projection=projection,
    ).to_dict()
    assert service.reset_records(keep_plan=True, factory_run_id=run.id)["ok"]
    executor = OrchestrationStageExecutor(workspace)
    executor._write_json_artifact("tasks/plan.json", {"tasks": [plan]})
    blueprint_id = original["metadata"]["blueprint_id"]
    executor._write_json_artifact(
        f"runtime/state/blueprints/{run.id}.review.json",
        {
            "factory_run_id": run.id,
            "blueprints": [
                {
                    "task_id": "WORK-A",
                    "blueprint_id": blueprint_id,
                    "blueprint_path": f"runtime/blueprints/{blueprint_id}.json",
                    "status": "generated",
                    "handoff_ready": True,
                }
            ],
        },
    )
    materialize = executor._materialize_pm_plan_taskboard

    def transport(*args: Any, **kwargs: Any) -> dict[str, Any]:
        result = materialize(*args, **kwargs)
        if fault == "missing":
            result["bound_task_rows"] = []
        elif fault == "old_row":
            result["bound_task_rows"][0]["row"] = original
        elif fault == "incomplete":
            result["bound_task_rows"][0]["event_recorded"] = False
        return result

    # Corrupt only the Factory result transport after actual owner writes.
    # Frozen authority and reset facts above come from their real public APIs.
    monkeypatch.setattr(executor, "_materialize_pm_plan_taskboard", transport)
    before = _facts(workspace)
    if fault == "none":
        external, physical_id, identity, task = quality._claim_workspace_quality_repair_attempt(
            executor, run=run, repair_attempt=1, target_files=["src/a.py"]
        )
        assert external == "WORK-A" and identity.task_id == physical_id
        assert task["metadata"]["task_completion_projection"] == original["metadata"]["task_completion_projection"]
        assert task["metadata"]["control_plane_job_token"]["token_id"]
    else:
        with pytest.raises(RuntimeError, match="frozen_owner_restore_failed"):
            quality._claim_workspace_quality_repair_attempt(
                executor, run=run, repair_attempt=1, target_files=["src/a.py"]
            )
        assert not any(event["event_type"] == "claimed" for event in _facts(workspace)[len(before) :])
