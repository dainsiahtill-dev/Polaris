"""A physical Factory binding has one durable fact per current owner generation."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from polaris.cells.events.fact_stream.public import (
    AppendFactEventCommandV1,
    BootstrapFactStreamWorkspaceCommandV1,
    QueryFactEventsV1,
    append_fact_event,
    bootstrap_fact_stream_workspace,
    fact_stream_bootstrap_streams,
    query_fact_events,
)
from polaris.cells.runtime.task_runtime.internal import execution_effect_closure as strict_reader
from polaris.cells.runtime.task_runtime.internal.task_board import TaskBoard
from polaris.cells.runtime.task_runtime.public import (
    BindRuntimeTaskToFactoryRunCommandV1,
    SettleTaskRuntimeExecutionAttemptCommandV1,
    TaskRuntimeExecutionAttemptIdentityV1,
    TaskRuntimeService,
    bind_runtime_task_to_factory_run,
)


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("KERNELONE_HOME", str(tmp_path / "kernelone"))
    root = tmp_path / "workspace"
    root.mkdir()
    bootstrap_fact_stream_workspace(
        BootstrapFactStreamWorkspaceCommandV1(
            str(root), streams=fact_stream_bootstrap_streams(), maintenance_reason="private binding evidence tests"
        )
    )
    return root


def _facts(root: Path) -> tuple[dict[str, Any], ...]:
    return query_fact_events(
        QueryFactEventsV1(str(root), stream="task_runtime.execution", strict_integrity=True, limit=1000)
    ).events


def _owner(root: Path) -> tuple[TaskRuntimeService, dict[str, Any]]:
    service = TaskRuntimeService(str(root))
    row = service.create_task_row(subject="Private unbound owner", metadata={"external_task_id": "OWNER"})
    return service, row


def _bind(root: Path, row_id: int, factory: str = "factory-one") -> Any:
    return bind_runtime_task_to_factory_run(BindRuntimeTaskToFactoryRunCommandV1(str(root), str(row_id), factory))


def test_cold_repeated_binding_adds_no_duplicate_fact(workspace: Path) -> None:
    _service, row = _owner(workspace)
    first = _bind(workspace, row["id"])
    assert first.ok and first.row_updated
    before = _facts(workspace)
    repeated = _bind(workspace, row["id"])
    assert repeated.ok and repeated.idempotent and repeated.event_recorded
    assert _facts(workspace) == before
    assert len([event for event in before if event["event_type"] == "factory_run_bound"]) == 1


def test_hidden_terminal_owner_keeps_committed_binding_without_observer_lookup(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, row = _owner(workspace)
    assert _bind(workspace, row["id"]).ok
    claim = service.claim_execution(
        row["id"], worker_id="director", role_id="director", run_id="director-one", external_task_id="OWNER"
    )
    identity = TaskRuntimeExecutionAttemptIdentityV1.from_record(claim["execution_attempt"])
    assert service.settle_execution_attempt(
        SettleTaskRuntimeExecutionAttemptCommandV1(str(workspace), identity, "completed", "private completion")
    )["success"]
    before = _facts(workspace)
    monkeypatch.setattr(TaskRuntimeService, "list_observable_task_rows", lambda self: [])
    result = _bind(workspace, row["id"])
    assert result.ok and result.idempotent and result.task_row["id"] == row["id"]
    assert _facts(workspace) == before


def test_heartbeat_and_other_tasks_do_not_discard_committed_binding(workspace: Path) -> None:
    service, row = _owner(workspace)
    assert _bind(workspace, row["id"]).ok
    claim = service.claim_execution(
        row["id"], worker_id="director", role_id="director", run_id="director-one", external_task_id="OWNER"
    )
    assert service.heartbeat_execution(row["id"], session_id=claim["session"]["session_id"], lease_ttl_seconds=120)[
        "success"
    ]
    service.create_task_row(subject="Other physical task")
    before = _facts(workspace)
    assert _bind(workspace, row["id"]).idempotent
    assert _facts(workspace) == before


def test_conflicting_factory_cannot_change_owner_or_append_fact(workspace: Path) -> None:
    _service, row = _owner(workspace)
    assert _bind(workspace, row["id"]).ok
    before = _facts(workspace)
    conflict = _bind(workspace, row["id"], "factory-other")
    assert not conflict.ok and conflict.code == "factory_run_binding_conflict"
    current = TaskRuntimeService(str(workspace)).get_task(row["id"])
    assert current is not None and current["metadata"]["factory_run_id"] == "factory-one"
    assert _facts(workspace) == before


def test_strict_pagination_failure_happens_before_physical_binding(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _service, row = _owner(workspace)
    row_path = workspace / ".polaris/runtime/tasks" / f"task_{row['id']}.json"
    before = row_path.read_bytes()
    native_query = strict_reader.query_fact_events

    def failed_page(query: QueryFactEventsV1) -> Any:
        if query.offset:
            raise RuntimeError("private second-page read unavailable")
        page = native_query(replace(query, limit=1))
        # At least two actual events force a real second page.
        assert page.next_offset > 0
        return page

    TaskRuntimeService(str(workspace)).create_task_row(subject="Second actual event")
    monkeypatch.setattr(strict_reader, "query_fact_events", failed_page)
    with pytest.raises(RuntimeError, match="private second-page read unavailable"):
        _bind(workspace, row["id"])
    assert row_path.read_bytes() == before


def test_malformed_current_owner_fact_is_unknown_before_binding(workspace: Path) -> None:
    _service, row = _owner(workspace)
    append_fact_event(
        AppendFactEventCommandV1(
            workspace=str(workspace),
            stream="task_runtime.execution",
            event_type="updated",
            source="runtime.task_runtime",
            task_id=str(row["id"]),
            payload={
                "schema_version": "private-invalid-owner-schema",
                "workspace": str(workspace),
                "task_id": str(row["id"]),
                "task_row_snapshot": row,
            },
            idempotency_key="private-invalid-binding-fact",
            strict_integrity=True,
        )
    )
    row_path = workspace / ".polaris/runtime/tasks" / f"task_{row['id']}.json"
    before = row_path.read_bytes()
    with pytest.raises(RuntimeError, match="binding_fact_identity_invalid"):
        _bind(workspace, row["id"])
    assert row_path.read_bytes() == before


def test_lost_append_ack_cold_retry_finds_the_committed_fact(workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _service, row = _owner(workspace)
    native_append = TaskRuntimeService._append_execution_event

    def lost_ack(self: TaskRuntimeService, event_type: str, **kwargs: Any) -> dict[str, Any]:
        result = native_append(self, event_type, **kwargs)
        if event_type == "factory_run_bound":
            assert result["ok"]
            raise RuntimeError("private ACK lost after durable binding fact")
        return result

    monkeypatch.setattr(TaskRuntimeService, "_append_execution_event", lost_ack)
    with pytest.raises(RuntimeError, match="private ACK lost"):
        _bind(workspace, row["id"])
    committed = _facts(workspace)
    monkeypatch.setattr(TaskRuntimeService, "_append_execution_event", native_append)
    retry = _bind(workspace, row["id"])
    assert retry.ok and retry.idempotent
    assert _facts(workspace) == committed


def test_two_processes_record_one_binding_fact(workspace: Path) -> None:
    _service, row = _owner(workspace)
    code = """
import json,sys
from polaris.cells.runtime.task_runtime.public import BindRuntimeTaskToFactoryRunCommandV1,bind_runtime_task_to_factory_run
print('READY',flush=True)
sys.stdin.readline()
result=bind_runtime_task_to_factory_run(BindRuntimeTaskToFactoryRunCommandV1(sys.argv[1],sys.argv[2],'factory-one'))
print(json.dumps({'ok':result.ok,'code':result.code,'idempotent':result.idempotent}),flush=True)
"""
    children = [
        subprocess.Popen(
            [sys.executable, "-c", code, str(workspace), str(row["id"])],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
        )
        for _ in range(2)
    ]
    try:
        for child in children:
            assert child.stdout is not None and child.stdout.readline().strip() == "READY"
        for child in children:
            assert child.stdin is not None
            child.stdin.write("GO\n")
            child.stdin.flush()
        results = []
        for child in children:
            output, errors = child.communicate(timeout=20)
            assert child.returncode == 0, errors
            results.append(json.loads(output))
        assert all(result["ok"] for result in results)
        assert sum(result["idempotent"] for result in results) == 1
        assert len([event for event in _facts(workspace) if event["event_type"] == "factory_run_bound"]) == 1
    finally:
        for child in children:
            if child.poll() is None:
                child.kill()
            child.communicate(timeout=10)


def test_reset_generation_does_not_borrow_previous_logical_owner_binding(workspace: Path) -> None:
    service, row = _owner(workspace)
    assert _bind(workspace, row["id"]).ok
    claim = service.claim_execution(
        row["id"], worker_id="director", role_id="director", run_id="director-old", external_task_id="OWNER"
    )
    identity = TaskRuntimeExecutionAttemptIdentityV1.from_record(claim["execution_attempt"])
    assert service.settle_execution_attempt(
        SettleTaskRuntimeExecutionAttemptCommandV1(str(workspace), identity, "completed", "private prior generation")
    )["success"]
    assert service.reset_records(keep_plan=True, factory_run_id="factory-one")["ok"]
    previous = _facts(workspace)
    current_service = TaskRuntimeService(str(workspace))
    current = current_service.create_task_row(subject="New current generation", metadata={"external_task_id": "OWNER"})
    bound = _bind(workspace, current["id"], "factory-new")
    assert bound.ok and bound.row_updated and bound.factory_run_id == "factory-new"
    assert _facts(workspace)[: len(previous)] == previous
    committed = _facts(workspace)
    assert _bind(workspace, current["id"], "factory-new").idempotent
    assert _facts(workspace) == committed


def test_binding_releases_namespace_before_session_augmentation(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _service, row = _owner(workspace)
    native_guard = TaskBoard._mutation_guard
    native_augment = TaskRuntimeService._augment_task_row
    native_session = TaskRuntimeService._read_session
    held = [False]

    @contextmanager
    def observed_guard(self: TaskBoard) -> Any:
        with native_guard(self) as guard:
            held[0] = True
            try:
                yield guard
            finally:
                held[0] = False

    def observed_augment(self: TaskRuntimeService, value: dict[str, Any]) -> dict[str, Any]:
        assert not held[0], "binding inverted namespace/session acquisition"
        return native_augment(self, value)

    def observed_session(self: TaskRuntimeService, task_id: int) -> Any:
        assert not held[0], "binding read a session while holding namespace"
        return native_session(self, task_id)

    monkeypatch.setattr(TaskBoard, "_mutation_guard", observed_guard)
    monkeypatch.setattr(TaskRuntimeService, "_augment_task_row", observed_augment)
    monkeypatch.setattr(TaskRuntimeService, "_read_session", observed_session)
    assert _bind(workspace, row["id"]).ok


def test_held_binding_ticket_rejects_replaced_namespace(workspace: Path) -> None:
    _service, row = _owner(workspace)
    board = TaskBoard(str(workspace))
    if os.name != "posix":
        with (
            pytest.raises(RuntimeError, match="taskboard_guarded_recovery_platform_unsupported"),
            board.transaction(),
            board._mutation_guard(),
        ):
            pytest.fail("unsupported namespace guard unexpectedly opened")
        return
    with board.transaction(), board._mutation_guard() as guard:
        board.tasks_dir.rename(workspace / ".polaris/runtime/old-private-tasks")
        board.tasks_dir.mkdir()
        with pytest.raises(RuntimeError):
            board.bind_factory_run_id(row["id"], "factory-one", _mutation_guard=guard)
