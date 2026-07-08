from __future__ import annotations

import hashlib
import json
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, NoReturn

import pytest
from polaris.cells.events.fact_stream.public.service import (
    AppendFactEventCommandV1,
    FactStreamError,
    QueryFactEventsV1,
    append_fact_event,
    query_fact_events,
)
from polaris.cells.runtime.task_runtime.internal import service as service_module
from polaris.cells.runtime.task_runtime.internal.execution_session import (
    TaskExecutionSession,
    terminal_session_timestamp,
)
from polaris.cells.runtime.task_runtime.internal.task_board import InvalidTaskStateTransitionError
from polaris.cells.runtime.task_runtime.public.service import TaskRuntimeService, reset_runtime_task_records
from polaris.kernelone.storage import resolve_runtime_path


def _task_file_path(workspace: Path, task_id: object) -> Path:
    return Path(resolve_runtime_path(str(workspace), f"runtime/tasks/task_{task_id}.json"))


def _sha256_utf8_file(path: Path) -> str:
    return hashlib.sha256(path.read_text(encoding="utf-8").encode("utf-8")).hexdigest()


_ROW_WRITE_RECEIPT_FIELDS = frozenset({"task_id", "task_path", "before_hash", "after_hash", "operation", "written_at"})


def _execution_event_payload_for_result(
    workspace: Path,
    execution_event: dict[str, Any],
    *,
    event_type: str,
) -> dict[str, Any]:
    event_id = str(execution_event.get("fact_event_id") or "").strip()
    assert event_id
    events = query_fact_events(
        QueryFactEventsV1(
            workspace=str(workspace),
            stream="task_runtime.execution",
            event_type=event_type,
        )
    ).events
    matches = [event for event in events if str(event.get("event_id") or "").strip() == event_id]
    assert len(matches) == 1
    payload = matches[0].get("payload")
    assert isinstance(payload, dict)
    return payload


def _assert_task_row_write_receipt(
    receipt: object,
    *,
    task_id: int,
    task_path: Path,
) -> dict[str, Any]:
    assert receipt is not None
    if isinstance(receipt, dict):
        values = {field: receipt[field] for field in _ROW_WRITE_RECEIPT_FIELDS}
    else:
        values = {field: getattr(receipt, field) for field in _ROW_WRITE_RECEIPT_FIELDS if hasattr(receipt, field)}
    missing = _ROW_WRITE_RECEIPT_FIELDS - set(values)
    assert not missing
    assert values["task_id"] == task_id
    assert str(values["task_path"]) in {str(task_path), f"runtime/tasks/task_{task_id}.json"}
    assert isinstance(values["operation"], str)
    assert values["operation"].strip()
    assert isinstance(values["written_at"], str)
    assert values["written_at"].strip()
    return values


def _assert_execution_event_row_write_receipt(
    payload: dict[str, Any],
    *,
    task_id: int,
    task_path: Path,
) -> dict[str, Any]:
    details = payload.get("details")
    assert isinstance(details, dict)
    return _assert_task_row_write_receipt(
        details.get("row_write_receipt"),
        task_id=task_id,
        task_path=task_path,
    )


def _raise_fact_stream_unavailable(
    *,
    event_type_str: str,
    payload: dict[str, Any],
) -> NoReturn:
    assert event_type_str
    assert payload
    raise RuntimeError("fact stream unavailable")


def _assert_execution_event_append_failure_with_row_write_receipt(
    execution_event: dict[str, Any],
    *,
    event_type: str,
    task_id: int,
    task_path: Path,
) -> dict[str, Any]:
    assert execution_event["ok"] is False
    assert execution_event["event_type"] == event_type
    assert execution_event["published"] is False
    assert execution_event["error"] == "fact stream unavailable"
    return _assert_execution_event_row_write_receipt(
        execution_event,
        task_id=task_id,
        task_path=task_path,
    )


def _session_file_path(workspace: Path, task_id: object) -> Path:
    return Path(resolve_runtime_path(str(workspace), f"runtime/tasks/task_{task_id}.session.json"))


ExecutionTransitionInvoker = Callable[[TaskRuntimeService, object, str], dict[str, Any]]
OwnerTerminalTransitionInvoker = Callable[[TaskRuntimeService, object], dict[str, Any] | None]


def _complete_execution_transition(
    service: TaskRuntimeService,
    task_id: object,
    session_id: str,
) -> dict[str, Any]:
    return service.complete_execution(
        task_id,
        session_id=session_id,
        result_summary="done",
    )


def _fail_execution_transition(
    service: TaskRuntimeService,
    task_id: object,
    session_id: str,
) -> dict[str, Any]:
    return service.fail_execution(
        task_id,
        session_id=session_id,
        error="director execution failed",
    )


def _suspend_execution_transition(
    service: TaskRuntimeService,
    task_id: object,
    session_id: str,
) -> dict[str, Any]:
    return service.suspend_execution(
        task_id,
        session_id=session_id,
        reason="factory_stage_timeout",
    )


_EXECUTION_TRANSITION_HELPER_CASES: tuple[tuple[str, str, str, ExecutionTransitionInvoker], ...] = (
    ("complete_execution", "completed", "completed", _complete_execution_transition),
    ("fail_execution", "failed", "failed", _fail_execution_transition),
    ("suspend_execution", "suspended", "pending", _suspend_execution_transition),
)


def _cancel_owner_terminal_transition(
    service: TaskRuntimeService,
    task_id: object,
) -> dict[str, Any] | None:
    return service.cancel_task_row_for_deduplication(
        task_id,
        primary_task_id=101,
        reason="unit_owner_cancel",
        metadata={"owner_transition": "cancel"},
        source="unit_owner_transition",
    )


def _fail_owner_terminal_transition(
    service: TaskRuntimeService,
    task_id: object,
) -> dict[str, Any] | None:
    return service.fail_task_row_from_role_adapter(
        task_id,
        reason="unit_owner_failure",
        metadata={"owner_transition": "fail"},
        role_id="pm",
        source="unit_owner_transition",
        failure_class="unit_failure",
    )


_OWNER_TERMINAL_TRANSITION_CASES: tuple[tuple[str, OwnerTerminalTransitionInvoker], ...] = (
    ("cancel_task_row_for_deduplication", _cancel_owner_terminal_transition),
    ("fail_task_row_from_role_adapter", _fail_owner_terminal_transition),
)


def _claimed_execution_for_transition(
    service: TaskRuntimeService,
    *,
    subject: str,
    run_id: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    created = service.create_task_row(subject=subject)
    claimed = service.claim_execution(
        created["id"],
        worker_id="director",
        role_id="director",
        run_id=run_id,
        selection_source="unit",
    )
    assert claimed["success"] is True
    return created, claimed


def _session_for_terminal_reconcile(task_id: int, *, status: str) -> TaskExecutionSession:
    session = TaskExecutionSession.create(
        task_id=task_id,
        role_id="director",
        worker_id="director-worker",
        run_id=f"run-terminal-reconcile-{status}",
        lease_ttl_seconds=120,
        attempt=1,
        resume_count=0,
        origin="unit",
        selection_source="task_id_lookup",
    )
    if status == "completed":
        session.mark_completed(result_summary="terminal reconcile completed")
    elif status == "failed":
        session.mark_failed(error="terminal reconcile failed")
    elif status != "active":
        raise AssertionError(f"unsupported reconcile session status: {status}")
    return session


def _assert_terminal_reconcile_result_shape(
    result: tuple[dict[str, Any] | None, str, dict[str, Any] | None],
) -> tuple[dict[str, Any] | None, str, dict[str, Any] | None]:
    assert isinstance(result, tuple)
    assert len(result) == 3
    row, error, event = result
    assert row is None or isinstance(row, dict)
    assert isinstance(error, str)
    assert event is None or isinstance(event, dict)
    return row, error, event


def test_task_runtime_service_normalizes_task_ids() -> None:
    assert TaskRuntimeService.normalize_task_id("task-12") == 12
    assert TaskRuntimeService.normalize_task_id("12") == 12
    assert TaskRuntimeService.normalize_task_id("task-12-extra") == 12
    assert TaskRuntimeService.normalize_task_id("bad-id") is None


def test_task_runtime_service_manages_task_rows(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = TaskRuntimeService(str(workspace))

    created = service.create_task_row(
        subject="wire runtime.v2 taskboard",
        description="use snapshot.tasks as primary source",
        metadata={"phase": "projection"},
    )
    assert created["id"] > 0

    updated = service.update_task_row(
        f"task-{created['id']}",
        status="ready",
        metadata={"owner_role": "director"},
    )
    assert updated is not None
    assert str(updated["status"]) == "ready"

    row = service.get_task(f"task-{created['id']}")
    assert isinstance(row, dict)
    assert row["subject"] == "wire runtime.v2 taskboard"
    assert row["status"] == "ready"
    assert row["metadata"]["owner_role"] == "director"

    rows = service.list_task_rows()
    assert len(rows) == 1
    assert rows[0]["id"] == created["id"]


def test_task_runtime_service_records_taskboard_row_write_receipt(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = TaskRuntimeService(str(workspace))

    created = service.create_task_row(
        subject="receipt anchored task",
        description="create should record a file-write receipt",
        metadata={"phase": "row-write-receipt"},
    )
    task_id = int(created["id"])
    task_path = _task_file_path(workspace, task_id)
    create_after_hash = _sha256_utf8_file(task_path)

    create_receipt = _assert_task_row_write_receipt(
        service._board.last_row_write_receipt(),
        task_id=task_id,
        task_path=task_path,
    )
    assert create_receipt["before_hash"] in {
        "",
        "file_absent",
        hashlib.sha256(b"").hexdigest(),
    }
    assert create_receipt["after_hash"] == create_after_hash

    updated = service.update_task_row(
        f"task-{task_id}",
        metadata={"owner_role": "director", "receipt_probe": "update"},
    )
    assert updated is not None
    update_after_hash = _sha256_utf8_file(task_path)

    update_receipt = _assert_task_row_write_receipt(
        service._board.last_row_write_receipt(),
        task_id=task_id,
        task_path=task_path,
    )
    assert update_receipt["before_hash"] == create_after_hash
    assert update_receipt["after_hash"] == update_after_hash
    assert update_receipt["after_hash"] != update_receipt["before_hash"]


def test_taskboard_save_task_row_write_is_guarded_by_per_row_file_lock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = TaskRuntimeService(str(workspace))

    first = service.create_task_row(
        subject="row lock probe one",
        description="baseline row for per-task lock ordering",
        metadata={"phase": "row-lock-baseline", "probe": "one"},
    )
    second = service.create_task_row(
        subject="row lock probe two",
        description="baseline row for per-task lock path separation",
        metadata={"phase": "row-lock-baseline", "probe": "two"},
    )
    first_id = int(first["id"])
    second_id = int(second["id"])

    events: list[str] = []
    active_lock_paths: list[Path] = []
    replace_lock_paths_by_task: dict[int, Path] = {}
    original_replace_task_file = service._board._replace_task_file

    @contextmanager
    def fake_file_lock(lock_file_path: Path) -> Iterator[object]:
        lock_path = Path(lock_file_path)
        events.append(f"lock_enter:{lock_path}")
        active_lock_paths.append(lock_path)
        try:
            yield object()
        finally:
            receipt = service._board._last_row_write_receipt
            if receipt is not None:
                events.append(f"receipt_seen_at_lock_exit:{receipt.task_id}:{lock_path}")
            released_lock_path = active_lock_paths.pop()
            assert released_lock_path == lock_path
            events.append(f"lock_exit:{lock_path}")

    def replace_task_file_with_trace(tmp_path_arg: Path, task_path_arg: Path) -> None:
        assert active_lock_paths, "_replace_task_file must run inside a per-row _file_lock"
        task_id = int(task_path_arg.stem.removeprefix("task_"))
        lock_path = active_lock_paths[-1]
        replace_lock_paths_by_task[task_id] = lock_path
        events.append(f"replace:{task_id}:{task_path_arg}")
        original_replace_task_file(tmp_path_arg, task_path_arg)

    monkeypatch.setattr(service._board, "_file_lock", fake_file_lock)
    monkeypatch.setattr(service._board, "_replace_task_file", replace_task_file_with_trace)

    def index_event_starting_with(prefix: str) -> int:
        return next(index for index, event in enumerate(events) if event.startswith(prefix))

    for task_id, marker in ((first_id, "first-update"), (second_id, "second-update")):
        updated = service.update_task_row(
            f"task-{task_id}",
            metadata={"phase": "row-lock-update", "probe": marker},
        )
        assert updated is not None

        task_path = _task_file_path(workspace, task_id)
        receipt = _assert_task_row_write_receipt(
            service._board.last_row_write_receipt(),
            task_id=task_id,
            task_path=task_path,
        )
        assert receipt["after_hash"] == _sha256_utf8_file(task_path)
        events.append(f"receipt_after_update:{task_id}")

    assert set(replace_lock_paths_by_task) == {first_id, second_id}
    first_lock_path = replace_lock_paths_by_task[first_id]
    second_lock_path = replace_lock_paths_by_task[second_id]
    assert first_lock_path != second_lock_path

    for task_id, lock_path in replace_lock_paths_by_task.items():
        assert f"task_{task_id}" in lock_path.name

        enter_index = events.index(f"lock_enter:{lock_path}")
        replace_index = index_event_starting_with(f"replace:{task_id}:")
        exit_index = events.index(f"lock_exit:{lock_path}")
        receipt_after_update_index = events.index(f"receipt_after_update:{task_id}")
        assert enter_index < replace_index < exit_index < receipt_after_update_index

        receipt_at_exit = f"receipt_seen_at_lock_exit:{task_id}:{lock_path}"
        if receipt_at_exit in events:
            assert replace_index < events.index(receipt_at_exit) < exit_index


def test_taskboard_save_task_fails_closed_when_row_hash_changes_before_replace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = TaskRuntimeService(str(workspace))

    created = service.create_task_row(
        subject="cas guarded task row",
        description="create establishes the successful row-write baseline",
        metadata={"phase": "row-cas"},
    )
    task_id = int(created["id"])
    task_path = _task_file_path(workspace, task_id)
    create_after_hash = _sha256_utf8_file(task_path)
    create_receipt = _assert_task_row_write_receipt(
        service._board.last_row_write_receipt(),
        task_id=task_id,
        task_path=task_path,
    )
    assert create_receipt["after_hash"] == create_after_hash

    attempted_task = service._board.get(task_id)
    assert attempted_task is not None
    attempted_task.metadata["cas_probe"] = "attempted_update"

    external_payload = json.loads(task_path.read_text(encoding="utf-8"))
    external_metadata = external_payload.setdefault("metadata", {})
    assert isinstance(external_metadata, dict)
    external_metadata["cas_probe"] = "external_update"
    external_content = json.dumps(external_payload, indent=2, ensure_ascii=False) + "\n"
    external_hash = hashlib.sha256(external_content.encode("utf-8")).hexdigest()
    assert external_hash != create_after_hash

    original_write_text = service._board._kernel_fs.write_text
    injected_external_write = False
    attempted_payload_hash: str | None = None

    def write_text_with_external_row_change(
        logical_path: str,
        content: str,
        *,
        encoding: str = "utf-8",
    ) -> object:
        nonlocal attempted_payload_hash, injected_external_write
        receipt = original_write_text(logical_path, content, encoding=encoding)
        logical_name = Path(str(logical_path)).name
        if (
            not injected_external_write
            and logical_name.startswith(f".task_{task_id}.")
            and logical_name.endswith(".tmp")
        ):
            attempted_payload_hash = hashlib.sha256(str(content).encode("utf-8")).hexdigest()
            task_path.write_text(external_content, encoding="utf-8")
            injected_external_write = True
        return receipt

    monkeypatch.setattr(service._board._kernel_fs, "write_text", write_text_with_external_row_change)

    with pytest.raises(RuntimeError) as exc_info:
        service._board._save_task(attempted_task)

    error_message = str(exc_info.value).lower()
    assert any(term in error_message for term in ("cas", "hash", "concurrent", "changed", "stale", "drift", "conflict"))
    assert injected_external_write is True
    assert attempted_payload_hash is not None
    assert attempted_payload_hash != external_hash

    assert json.loads(task_path.read_text(encoding="utf-8")) == external_payload
    assert _sha256_utf8_file(task_path) == external_hash

    receipt_after_failure = _assert_task_row_write_receipt(
        service._board.last_row_write_receipt(),
        task_id=task_id,
        task_path=task_path,
    )
    assert receipt_after_failure == create_receipt
    assert receipt_after_failure["after_hash"] == create_after_hash
    assert receipt_after_failure["after_hash"] != attempted_payload_hash


def test_create_task_row_execution_event_details_include_row_write_receipt(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = TaskRuntimeService(str(workspace))

    created = service.create_task_row(
        subject="execution event receipt anchored task",
        description="created fact payload must carry the row-write receipt",
        metadata={"phase": "row-write-receipt-execution-event"},
    )
    task_id = int(created["id"])
    task_path = _task_file_path(workspace, task_id)
    after_hash = _sha256_utf8_file(task_path)

    execution_event = created["execution_event"]
    assert execution_event["ok"] is True
    assert execution_event["event_type"] == "created"
    _assert_execution_event_row_write_receipt(
        execution_event,
        task_id=task_id,
        task_path=task_path,
    )
    payload = _execution_event_payload_for_result(
        workspace,
        execution_event,
        event_type="created",
    )

    assert payload["task_id"] == str(task_id)
    assert payload["task_row_snapshot"]["id"] == task_id
    assert payload["details"]["source"] == "runtime.task_runtime.create"
    receipt = _assert_execution_event_row_write_receipt(
        payload,
        task_id=task_id,
        task_path=task_path,
    )
    assert receipt["before_hash"] in {
        "",
        "file_absent",
        hashlib.sha256(b"").hexdigest(),
    }
    assert receipt["after_hash"] == after_hash
    assert receipt["operation"] == "replace"


def test_append_execution_event_omits_stale_row_write_receipt_for_different_task(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = TaskRuntimeService(str(workspace))

    current = service.create_task_row(subject="current task for stale receipt guard")
    current_id = int(current["id"])
    stale = service.create_task_row(subject="different task with latest receipt")
    stale_id = int(stale["id"])
    assert stale_id != current_id
    stale_receipt = _assert_task_row_write_receipt(
        service._board.last_row_write_receipt(),
        task_id=stale_id,
        task_path=_task_file_path(workspace, stale_id),
    )
    assert stale_receipt["task_id"] != current_id

    current_row = service.get_task(current_id)
    assert isinstance(current_row, dict)
    execution_event = service._append_execution_event(
        "unit_stale_receipt_probe",
        task_row=current_row,
        session=None,
        details={"source": "unit.stale_receipt_probe"},
    )

    assert execution_event["ok"] is True
    payload = _execution_event_payload_for_result(
        workspace,
        execution_event,
        event_type="unit_stale_receipt_probe",
    )
    details = payload["details"]
    assert details["source"] == "unit.stale_receipt_probe"
    assert "row_write_receipt" not in details


def test_task_runtime_service_projects_rows_from_execution_facts(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = TaskRuntimeService(str(workspace))

    append_fact_event(
        AppendFactEventCommandV1(
            workspace=str(workspace),
            stream="task_runtime.execution",
            event_type="claimed",
            source="runtime.task_runtime",
            task_id="TASK-FACT",
            run_id="run-fact",
            payload={
                "task_id": "TASK-FACT",
                "run_id": "run-fact",
                "event_type": "claimed",
                "status": "in_progress",
                "execution_state": "in_progress",
                "session_id": "session-fact",
                "task_row_snapshot": {
                    "id": "TASK-FACT",
                    "task_id": "TASK-FACT",
                    "subject": "Fact backed task",
                    "description": "Projected by task runtime owner",
                    "priority": "HIGH",
                    "metadata": {"source": "task_runtime.row_snapshot"},
                },
            },
        )
    )

    rows = service.list_task_rows_from_execution_facts()

    assert len(rows) == 1
    assert rows[0]["task_id"] == "TASK-FACT"
    assert rows[0]["subject"] == "Fact backed task"
    assert rows[0]["description"] == "Projected by task runtime owner"
    assert rows[0]["running"] is True
    assert rows[0]["metadata"]["source"] == "task_runtime.execution_fact"


def test_task_runtime_service_observable_rows_overlay_execution_facts(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = TaskRuntimeService(str(workspace))
    created = service.create_task_row(
        subject="Observable task",
        description="File row should receive fact overlay",
        priority=2,
    )
    task_id = str(created["id"])

    append_fact_event(
        AppendFactEventCommandV1(
            workspace=str(workspace),
            stream="task_runtime.execution",
            event_type="claimed",
            source="runtime.task_runtime",
            task_id=task_id,
            run_id="run-observable",
            payload={
                "task_id": task_id,
                "run_id": "run-observable",
                "event_type": "claimed",
                "status": "in_progress",
                "execution_state": "in_progress",
                "session_id": "session-observable",
                "task_row_snapshot": created,
            },
        )
    )

    rows = service.list_observable_task_rows()

    assert len(rows) == 1
    assert rows[0]["id"] == created["id"]
    assert rows[0]["subject"] == "Observable task"
    assert rows[0]["status"] == "in_progress"
    assert rows[0]["running"] is True
    assert rows[0]["metadata"]["previous_status"] == "pending"
    assert rows[0]["metadata"]["source"] == "task_runtime.execution_fact"


def test_list_observable_task_rows_does_not_refresh_dependency_unblocks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = TaskRuntimeService(str(workspace))
    file_rows: list[dict[str, Any]] = [
        {
            "id": 1,
            "task_id": "1",
            "subject": "read-only observable projection",
            "status": "pending",
            "metadata": {"source": "file_row"},
        }
    ]
    refresh_calls: list[str] = []

    def reject_refresh_dependency_unblocks() -> NoReturn:
        refresh_calls.append("refresh_dependency_unblocks")
        raise AssertionError("list_observable_task_rows must be a pure read projection")

    def list_file_task_rows(*, include_terminal: bool = True) -> list[dict[str, Any]]:
        assert include_terminal is True
        return [dict(row) for row in file_rows]

    def list_fact_rows() -> list[dict[str, Any]]:
        return []

    monkeypatch.setattr(service, "refresh_dependency_unblocks", reject_refresh_dependency_unblocks)
    monkeypatch.setattr(service, "_list_file_task_rows", list_file_task_rows)
    monkeypatch.setattr(service, "list_task_rows_from_execution_facts", list_fact_rows)

    rows = service.list_observable_task_rows()

    assert rows == file_rows
    assert refresh_calls == []


def test_list_task_rows_continues_to_refresh_dependency_unblocks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = TaskRuntimeService(str(workspace))
    file_rows: list[dict[str, Any]] = [
        {
            "id": 2,
            "task_id": "2",
            "subject": "mutable file-backed projection",
            "status": "pending",
            "metadata": {"source": "file_row"},
        }
    ]
    refresh_calls: list[str] = []

    def record_refresh_dependency_unblocks() -> dict[str, Any]:
        refresh_calls.append("refresh_dependency_unblocks")
        return {"unblocked_task_ids": [], "execution_events": []}

    def list_file_task_rows(*, include_terminal: bool = True) -> list[dict[str, Any]]:
        assert include_terminal is False
        return [dict(row) for row in file_rows]

    monkeypatch.setattr(service, "refresh_dependency_unblocks", record_refresh_dependency_unblocks)
    monkeypatch.setattr(service, "_list_file_task_rows", list_file_task_rows)

    rows = service.list_task_rows(include_terminal=False)

    assert rows == file_rows
    assert refresh_calls == ["refresh_dependency_unblocks"]


def test_list_ready_task_rows_refreshes_before_observable_projection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = TaskRuntimeService(str(workspace))
    events: list[str] = []

    def record_refresh_dependency_unblocks() -> dict[str, Any]:
        events.append("refresh_dependency_unblocks")
        return {"unblocked_task_ids": [], "execution_events": []}

    def list_observable_task_rows() -> list[dict[str, Any]]:
        events.append("list_observable_task_rows")
        return [
            {
                "id": 3,
                "task_id": "3",
                "subject": "ready row",
                "status": "pending",
                "blocked_by": [],
            },
            {
                "id": 4,
                "task_id": "4",
                "subject": "blocked row",
                "status": "pending",
                "blocked_by": [3],
            },
        ]

    monkeypatch.setattr(service, "refresh_dependency_unblocks", record_refresh_dependency_unblocks)
    monkeypatch.setattr(service, "list_observable_task_rows", list_observable_task_rows)

    rows = service.list_ready_task_rows()

    assert [row["id"] for row in rows] == [3]
    assert events == ["refresh_dependency_unblocks", "list_observable_task_rows"]


def test_selection_entrypoints_refresh_before_observable_projection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = TaskRuntimeService(str(workspace))
    events: list[str] = []

    def record_refresh_dependency_unblocks() -> dict[str, Any]:
        events.append("refresh_dependency_unblocks")
        return {"unblocked_task_ids": [], "execution_events": []}

    def list_observable_task_rows() -> list[dict[str, Any]]:
        events.append("list_observable_task_rows")
        return []

    monkeypatch.setattr(service, "refresh_dependency_unblocks", record_refresh_dependency_unblocks)
    monkeypatch.setattr(service, "list_observable_task_rows", list_observable_task_rows)

    selected = service.select_next_task()
    claim_next = service.claim_next_execution(
        worker_id="director",
        role_id="director",
        selection_source="test-selection-refresh",
    )

    assert selected is None
    assert claim_next["success"] is False
    assert claim_next["reason"] == "no_claimable_tasks"
    assert events == [
        "refresh_dependency_unblocks",
        "list_observable_task_rows",
        "refresh_dependency_unblocks",
        "list_observable_task_rows",
    ]


def test_list_observable_task_rows_consumes_fact_overlay_without_refresh(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = TaskRuntimeService(str(workspace))
    created = service.create_task_row(
        subject="Observable fact overlay without refresh",
        description="File row should stay stale while fact row is projected",
        priority=2,
    )
    task_id = str(created["id"])
    refresh_calls: list[str] = []

    append_fact_event(
        AppendFactEventCommandV1(
            workspace=str(workspace),
            stream="task_runtime.execution",
            event_type="claimed",
            source="runtime.task_runtime",
            task_id=task_id,
            run_id="run-observable-no-refresh",
            payload={
                "task_id": task_id,
                "run_id": "run-observable-no-refresh",
                "event_type": "claimed",
                "status": "in_progress",
                "execution_state": "in_progress",
                "session_id": "session-observable-no-refresh",
                "task_row_snapshot": created,
            },
        )
    )

    def reject_refresh_dependency_unblocks() -> NoReturn:
        refresh_calls.append("refresh_dependency_unblocks")
        raise AssertionError("observable fact overlay must not refresh dependencies")

    monkeypatch.setattr(service, "refresh_dependency_unblocks", reject_refresh_dependency_unblocks)

    rows = service.list_observable_task_rows()

    assert len(rows) == 1
    assert rows[0]["id"] == created["id"]
    assert rows[0]["status"] == "in_progress"
    assert rows[0]["running"] is True
    assert rows[0]["metadata"]["previous_status"] == "pending"
    assert rows[0]["metadata"]["source"] == "task_runtime.execution_fact"
    assert refresh_calls == []


def test_task_runtime_service_stats_use_observable_execution_fact_rows(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = TaskRuntimeService(str(workspace))
    created = service.create_task_row(
        subject="Observable stats task",
        description="File row should not dominate execution facts",
    )
    task_id = str(created["id"])

    append_fact_event(
        AppendFactEventCommandV1(
            workspace=str(workspace),
            stream="task_runtime.execution",
            event_type="claimed",
            source="runtime.task_runtime",
            task_id=task_id,
            run_id="run-observable-stats",
            payload={
                "task_id": task_id,
                "run_id": "run-observable-stats",
                "event_type": "claimed",
                "status": "in_progress",
                "execution_state": "in_progress",
                "session_id": "session-observable-stats",
                "task_row_snapshot": created,
            },
        )
    )

    stats = service.get_task_row_stats()

    assert stats["total"] == 1
    assert stats["pending"] == 0
    assert stats["ready"] == 0
    assert stats["in_progress"] == 1


def test_task_runtime_service_raw_list_all_is_retired(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = TaskRuntimeService(str(workspace))
    service.create_task_row(subject="row projection only")

    with pytest.raises(RuntimeError, match="use list_task_rows"):
        service.list_all()


def test_task_runtime_service_entity_apis_are_retired(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = TaskRuntimeService(str(workspace))
    created = service.create_task_row(subject="row projection only")

    with pytest.raises(RuntimeError, match="use create_task_row"):
        service.create(subject="legacy entity create")
    with pytest.raises(RuntimeError, match="use get_task"):
        service.get(created["id"])
    with pytest.raises(RuntimeError, match="use update_task_row"):
        service.update(created["id"], status="in_progress")
    with pytest.raises(RuntimeError, match="use update_task_row"):
        service.update_task(created["id"], status="in_progress")
    with pytest.raises(RuntimeError, match="use reopen_task_row"):
        service.reopen(created["id"])


def test_task_runtime_service_ready_and_stats_entity_apis_are_retired(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = TaskRuntimeService(str(workspace))
    created = service.create_task_row(subject="ready row projection")

    ready_rows = service.list_ready_task_rows()
    stats = service.get_task_row_stats()

    assert [row["id"] for row in ready_rows] == [created["id"]]
    assert stats["total"] == 1
    assert stats["pending"] == 1
    assert stats["ready"] == 1
    with pytest.raises(RuntimeError, match="use list_ready_task_rows"):
        service.list_ready()
    with pytest.raises(RuntimeError, match="use list_ready_task_rows"):
        service.get_ready_tasks()
    with pytest.raises(RuntimeError, match="use get_task_row_stats"):
        service.get_stats()


def test_task_runtime_service_does_not_proxy_legacy_board_methods(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = TaskRuntimeService(str(workspace))

    for method_name in ("list_my_tasks", "get_dependency_graph", "get_critical_path"):
        with pytest.raises(AttributeError):
            getattr(service, method_name)


def test_task_runtime_service_create_links_reverse_dependency_with_execution_event(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = TaskRuntimeService(str(workspace))

    parent = service.create_task_row(subject="parent task")
    parent_id = int(parent["id"])
    child = service.create_task_row(subject="child task", blocked_by=[parent_id])
    child_id = int(child["id"])

    parent_after = service.get_task(parent_id)
    assert parent_after is not None
    assert parent_after["blocks"] == [child_id]

    reverse_events = [
        event for event in child.get("execution_events", []) if event.get("event_type") == "reverse_dependency_linked"
    ]
    assert len(reverse_events) == 1
    reverse_event = reverse_events[0]
    assert reverse_event["ok"] is True
    events = query_fact_events(QueryFactEventsV1(workspace=str(workspace), stream="task_runtime.execution")).events
    linked_payloads = [event["payload"] for event in events if event["event_type"] == "reverse_dependency_linked"]
    assert len(linked_payloads) == 1
    linked_payload = linked_payloads[0]
    assert linked_payload["details"]["dependent_task_id"] == child_id
    assert linked_payload["details"]["blocks"] == [child_id]
    assert linked_payload["task_row_snapshot"]["id"] == parent_id


def test_create_task_row_reports_event_append_failure_with_row_receipt_without_persisting_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = TaskRuntimeService(str(workspace))

    monkeypatch.setattr(service, "_append_execution_fact_with_cas", _raise_fact_stream_unavailable)

    row = service.create_task_row(
        subject="create with append evidence",
        description="return projection evidence without mutating task metadata",
        metadata={"phase": "projection"},
    )

    task_id = int(row["id"])
    assert row["status"] == "pending"
    _assert_execution_event_append_failure_with_row_write_receipt(
        row["execution_event"],
        event_type="created",
        task_id=task_id,
        task_path=_task_file_path(workspace, task_id),
    )
    persisted = service.get_task(row["id"])
    assert persisted is not None
    assert "execution_event" not in persisted
    assert "execution_event" not in persisted["metadata"]


def test_update_task_row_reports_event_append_failure_with_row_receipt_without_persisting_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = TaskRuntimeService(str(workspace))
    created = service.create_task_row(subject="update with append evidence")
    task_id = int(created["id"])

    monkeypatch.setattr(service, "_append_execution_fact_with_cas", _raise_fact_stream_unavailable)

    row = service.update_task_row(
        created["id"],
        status="ready",
        metadata={"owner_role": "director"},
    )

    assert row is not None
    assert row["status"] == "ready"
    _assert_execution_event_append_failure_with_row_write_receipt(
        row["execution_event"],
        event_type="updated",
        task_id=task_id,
        task_path=_task_file_path(workspace, task_id),
    )
    persisted = service.get_task(row["id"])
    assert persisted is not None
    assert "execution_event" not in persisted
    assert "execution_event" not in persisted["metadata"]


def test_update_task_row_rejects_terminal_status_owner_bypass(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = TaskRuntimeService(str(workspace))
    created = service.create_task_row(subject="terminal bypass guard")

    with pytest.raises(RuntimeError, match="terminal_task_status_requires_task_runtime_owner_transition:completed"):
        service.update_task_row(created["id"], status="completed")
    with pytest.raises(RuntimeError, match="execution_task_status_requires_task_runtime_owner_transition:in_progress"):
        service.update_task_row(created["id"], status="in_progress")
    with pytest.raises(RuntimeError, match="execution_task_status_requires_task_runtime_owner_transition:claimed"):
        service.update_task_row(created["id"], status="claimed")

    row = service.get_task(created["id"])
    assert row is not None
    assert row["status"] == "pending"


def test_claim_execution_fails_closed_on_execution_event_append_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = TaskRuntimeService(str(workspace))
    created = service.create_task_row(subject="claim with append evidence")
    task_id = int(created["id"])

    monkeypatch.setattr(service, "_append_execution_fact_with_cas", _raise_fact_stream_unavailable)

    claimed = service.claim_execution(
        created["id"],
        worker_id="director",
        role_id="director",
        run_id="run-append-failure",
        selection_source="unit",
    )

    assert claimed["success"] is False
    assert claimed["reason"] == "execution_event_append_failed"
    assert claimed["requested_reason"] == "claimed"
    assert claimed["failure_class"] == "ledger_append_failed"
    assert claimed["state_mutation_applied"] is True
    _assert_execution_event_append_failure_with_row_write_receipt(
        claimed["execution_event"],
        event_type="claimed",
        task_id=task_id,
        task_path=_task_file_path(workspace, task_id),
    )
    assert claimed["task"]["status"] == "in_progress"


def test_task_entity_for_transition_normalizes_and_reads_raw_board_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = TaskRuntimeService(str(workspace))
    created = service.create_task_row(subject="transition helper boundary")
    created_id = int(created["id"])

    original_get = service._board.get
    get_calls: list[object] = []

    def tracing_get(task_id: object) -> Any:
        get_calls.append(task_id)
        return original_get(task_id)

    monkeypatch.setattr(service._board, "get", tracing_get)

    normalized, task = service._task_entity_for_transition(f"task-{created_id}-extra")

    assert normalized == created_id
    assert task is not None
    assert task.id == created_id
    assert get_calls == [created_id]
    assert service._task_entity_for_transition("bad-id") == (None, None)
    assert get_calls == [created_id]


def test_task_entity_for_owner_terminal_transition_normalizes_and_reads_raw_board_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = TaskRuntimeService(str(workspace))
    created = service.create_task_row(subject="owner terminal transition helper boundary")
    created_id = int(created["id"])

    original_get = service._board.get
    get_calls: list[object] = []

    def tracing_get(task_id: object) -> Any:
        get_calls.append(task_id)
        return original_get(task_id)

    monkeypatch.setattr(service._board, "get", tracing_get)

    normalized, task = service._task_entity_for_owner_terminal_transition(f"task-{created_id}-extra")

    assert normalized == created_id
    assert task is not None
    assert task.id == created_id
    assert get_calls == [created_id]
    assert service._task_entity_for_owner_terminal_transition("bad-id") == (None, None)
    assert get_calls == [created_id]


def test_task_entity_for_dependency_side_effect_normalizes_and_reads_raw_board_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = TaskRuntimeService(str(workspace))
    created = service.create_task_row(subject="dependency side-effect helper boundary")
    created_id = int(created["id"])

    original_get = service._board.get
    get_calls: list[object] = []

    def tracing_get(task_id: object) -> Any:
        get_calls.append(task_id)
        return original_get(task_id)

    monkeypatch.setattr(service._board, "get", tracing_get)

    normalized, task = service._task_entity_for_dependency_side_effect(f"task-{created_id}-extra")

    assert normalized == created_id
    assert task is not None
    assert task.id == created_id
    assert get_calls == [created_id]
    assert service._task_entity_for_dependency_side_effect("bad-id") == (None, None)
    assert get_calls == [created_id]


def test_task_entity_for_claim_execution_normalizes_and_reads_raw_board_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = TaskRuntimeService(str(workspace))
    created = service.create_task_row(subject="claim helper boundary")
    created_id = int(created["id"])

    original_get = service._board.get
    get_calls: list[object] = []

    def tracing_get(task_id: object) -> Any:
        get_calls.append(task_id)
        return original_get(task_id)

    def reject_refresh_dependency_unblocks() -> None:
        raise AssertionError("_task_entity_for_claim_execution must not own claim refresh side effects")

    monkeypatch.setattr(service._board, "get", tracing_get)
    monkeypatch.setattr(service, "refresh_dependency_unblocks", reject_refresh_dependency_unblocks)

    helper = getattr(service, "_task_entity_for_claim_execution", None)
    assert callable(helper), "TaskRuntimeService must expose _task_entity_for_claim_execution"

    normalized, task = helper(f"task-{created_id}-extra")

    assert normalized == created_id
    assert task is not None
    assert task.id == created_id
    assert get_calls == [created_id]
    assert helper("bad-id") == (None, None)
    assert get_calls == [created_id]


def test_claim_execution_uses_task_entity_helper_and_preserves_success_shape(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = TaskRuntimeService(str(workspace))
    created = service.create_task_row(subject="claim helper success")
    created_id = int(created["id"])
    task = service._board.get(created_id)
    assert task is not None

    helper_calls: list[object] = []
    claim_steps: list[str] = []
    direct_board_get_calls: list[object] = []

    def task_entity_for_claim_execution(task_id: object) -> tuple[int | None, Any | None]:
        claim_steps.append("helper")
        helper_calls.append(task_id)
        return service.normalize_task_id(task_id), task

    def record_refresh_dependency_unblocks() -> None:
        claim_steps.append("refresh")

    def reject_direct_board_get(task_id: object) -> Any:
        direct_board_get_calls.append(task_id)
        raise AssertionError("claim_execution must read task entities through _task_entity_for_claim_execution")

    monkeypatch.setattr(
        service,
        "_task_entity_for_claim_execution",
        task_entity_for_claim_execution,
        raising=False,
    )
    monkeypatch.setattr(service, "refresh_dependency_unblocks", record_refresh_dependency_unblocks)
    monkeypatch.setattr(service._board, "get", reject_direct_board_get)

    claimed = service.claim_execution(
        f"task-{created_id}",
        worker_id="director",
        role_id="director",
        run_id="run-claim-helper-success",
        selection_source="unit",
    )

    assert helper_calls == [f"task-{created_id}"]
    assert claim_steps == ["refresh", "helper"]
    assert direct_board_get_calls == []
    assert claimed["success"] is True
    assert claimed["reason"] == "claimed"
    assert claimed["claim_applied"] is True
    assert claimed["resumed"] is False
    assert claimed["task"]["id"] == created_id
    assert claimed["task"]["status"] == "in_progress"
    assert claimed["session"]["task_id"] == created_id
    assert claimed["session"]["role_id"] == "director"
    assert claimed["session"]["worker_id"] == "director"
    assert claimed["session"]["run_id"] == "run-claim-helper-success"
    assert claimed["session"]["status"] == "active"
    execution_event = claimed["execution_event"]
    assert execution_event["ok"] is True
    assert execution_event["event_type"] == "claimed"
    assert "published" in execution_event
    assert execution_event["fact_stream"] == "task_runtime.execution"
    assert isinstance(execution_event.get("fact_event_id"), str)
    assert execution_event["fact_event_id"]
    assert isinstance(execution_event.get("fact_event_seq"), int)
    assert "requested_reason" not in claimed
    assert "failure_class" not in claimed
    assert "state_mutation_applied" not in claimed


@pytest.mark.parametrize(
    ("boundary_name", "task_id", "helper_result", "expected_reason", "expected_refresh_calls"),
    (
        ("invalid_task_id", "not-a-task", (None, None), "invalid_task_id", []),
        ("task_not_found", "task-7001", (7001, None), "task_not_found", ["refresh"]),
    ),
    ids=("invalid_task_id", "task_not_found"),
)
def test_claim_execution_short_circuits_from_task_entity_helper_boundary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    boundary_name: str,
    task_id: object,
    helper_result: tuple[int | None, Any | None],
    expected_reason: str,
    expected_refresh_calls: list[str],
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = TaskRuntimeService(str(workspace))
    sentinel = service.create_task_row(subject=f"claim helper {boundary_name} sentinel")
    sentinel_id = int(sentinel["id"])
    sentinel_before = json.loads(_task_file_path(workspace, sentinel_id).read_text(encoding="utf-8"))
    helper_calls: list[object] = []
    refresh_calls: list[str] = []
    direct_board_get_calls: list[object] = []
    session_lock_calls: list[object] = []
    session_read_calls: list[object] = []
    update_calls: list[object] = []
    event_calls: list[str] = []

    def task_entity_for_claim_execution(raw_task_id: object) -> tuple[int | None, Any | None]:
        helper_calls.append(raw_task_id)
        return helper_result

    def record_refresh_dependency_unblocks() -> None:
        refresh_calls.append("refresh")

    def reject_direct_board_get(raw_task_id: object) -> Any:
        direct_board_get_calls.append(raw_task_id)
        raise AssertionError(f"claim_execution must short-circuit raw board reads for {boundary_name}")

    def reject_session_lock(normalized_task_id: object) -> Any:
        session_lock_calls.append(normalized_task_id)
        raise AssertionError(f"claim_execution must not acquire session locks for {boundary_name}")

    def reject_session_read(normalized_task_id: object) -> Any:
        session_read_calls.append(normalized_task_id)
        raise AssertionError(f"claim_execution must not read sessions for {boundary_name}")

    def reject_board_update(*args: object, **_kwargs: object) -> Any:
        update_calls.append(args)
        raise AssertionError(f"claim_execution must not mutate rows for {boundary_name}")

    def reject_append_execution_event(event_type: str, **_kwargs: Any) -> dict[str, Any]:
        event_calls.append(event_type)
        raise AssertionError(f"claim_execution must not append events for {boundary_name}")

    monkeypatch.setattr(
        service,
        "_task_entity_for_claim_execution",
        task_entity_for_claim_execution,
        raising=False,
    )
    monkeypatch.setattr(service, "refresh_dependency_unblocks", record_refresh_dependency_unblocks)
    monkeypatch.setattr(service._board, "get", reject_direct_board_get)
    monkeypatch.setattr(service, "_get_session_lock", reject_session_lock)
    monkeypatch.setattr(service, "_read_session", reject_session_read)
    monkeypatch.setattr(service._board, "update", reject_board_update)
    monkeypatch.setattr(service, "_append_execution_event", reject_append_execution_event)

    claimed = service.claim_execution(
        task_id,
        worker_id="director",
        role_id="director",
        run_id="run-claim-helper-boundary",
        selection_source="unit",
    )

    assert helper_calls == ([] if expected_reason == "invalid_task_id" else [task_id])
    assert refresh_calls == expected_refresh_calls
    assert direct_board_get_calls == []
    assert session_lock_calls == []
    assert session_read_calls == []
    assert update_calls == []
    assert event_calls == []
    assert claimed == {"success": False, "reason": expected_reason}
    sentinel_after = json.loads(_task_file_path(workspace, sentinel_id).read_text(encoding="utf-8"))
    assert sentinel_after == sentinel_before
    assert not _session_file_path(workspace, sentinel_id).exists()


def test_apply_reverse_dependency_links_uses_dependency_side_effect_helper_and_preserves_event_shape(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = TaskRuntimeService(str(workspace))
    blocker = service.create_task_row(subject="reverse helper blocker")
    dependent = service.create_task_row(subject="reverse helper dependent")
    blocker_id = int(blocker["id"])
    dependent_id = int(dependent["id"])
    blocker_task = service._board.get(blocker_id)
    assert blocker_task is not None

    helper_calls: list[object] = []
    direct_board_get_calls: list[object] = []

    def task_entity_for_dependency_side_effect(task_id: object) -> tuple[int | None, Any | None]:
        helper_calls.append(task_id)
        return service.normalize_task_id(task_id), blocker_task

    def reject_direct_board_get(task_id: object) -> Any:
        direct_board_get_calls.append(task_id)
        raise AssertionError(
            "reverse dependency side effects must read task entities through _task_entity_for_dependency_side_effect"
        )

    monkeypatch.setattr(
        service,
        "_task_entity_for_dependency_side_effect",
        task_entity_for_dependency_side_effect,
        raising=False,
    )
    monkeypatch.setattr(service._board, "get", reject_direct_board_get)

    events = service._apply_reverse_dependency_links(
        created_task_id=dependent_id,
        blocker_ids=[blocker_id],
    )

    assert helper_calls == [blocker_id]
    assert direct_board_get_calls == []
    assert len(events) == 1
    assert events[0]["ok"] is True
    assert events[0]["event_type"] == "reverse_dependency_linked"

    blocker_after = service.get_task(blocker_id)
    assert blocker_after is not None
    assert blocker_after["blocks"] == [dependent_id]

    fact_events = query_fact_events(QueryFactEventsV1(workspace=str(workspace), stream="task_runtime.execution")).events
    linked_payloads = [event["payload"] for event in fact_events if event["event_type"] == "reverse_dependency_linked"]
    assert len(linked_payloads) == 1
    linked_payload = linked_payloads[0]
    assert linked_payload["task_row_snapshot"]["id"] == blocker_id
    assert linked_payload["task_row_snapshot"]["blocks"] == [dependent_id]
    assert (
        linked_payload["details"].items()
        >= {
            "dependent_task_id": dependent_id,
            "previous_blocks": [],
            "blocks": [dependent_id],
        }.items()
    )
    _assert_execution_event_row_write_receipt(
        linked_payload,
        task_id=blocker_id,
        task_path=_task_file_path(workspace, blocker_id),
    )


def test_apply_reopen_downstream_reblocks_uses_dependency_side_effect_helper_and_preserves_event_shape(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = TaskRuntimeService(str(workspace))
    reopened = service.create_task_row(subject="reopened prerequisite")
    dependent = service.create_task_row(subject="dependent to reblock")
    reopened_id = int(reopened["id"])
    dependent_id = int(dependent["id"])
    dependent_task = service._board.get(dependent_id)
    assert dependent_task is not None

    helper_calls: list[object] = []
    direct_board_get_calls: list[object] = []

    def task_entity_for_dependency_side_effect(task_id: object) -> tuple[int | None, Any | None]:
        helper_calls.append(task_id)
        return service.normalize_task_id(task_id), dependent_task

    def reject_direct_board_get(task_id: object) -> Any:
        direct_board_get_calls.append(task_id)
        raise AssertionError(
            "downstream dependency reblocks must read task entities through _task_entity_for_dependency_side_effect"
        )

    monkeypatch.setattr(
        service,
        "_task_entity_for_dependency_side_effect",
        task_entity_for_dependency_side_effect,
        raising=False,
    )
    monkeypatch.setattr(service._board, "get", reject_direct_board_get)

    events = service._apply_reopen_downstream_reblocks(
        reopened_task_id=reopened_id,
        dependent_ids=[dependent_id],
    )

    assert helper_calls == [dependent_id]
    assert direct_board_get_calls == []
    assert len(events) == 1
    assert events[0]["ok"] is True
    assert events[0]["event_type"] == "downstream_dependency_reblocked"

    dependent_after = service.get_task(dependent_id)
    assert dependent_after is not None
    assert dependent_after["status"] == "blocked"
    assert dependent_after["blocked_by"] == [reopened_id]

    fact_events = query_fact_events(QueryFactEventsV1(workspace=str(workspace), stream="task_runtime.execution")).events
    reblocked_payloads = [
        event["payload"] for event in fact_events if event["event_type"] == "downstream_dependency_reblocked"
    ]
    assert len(reblocked_payloads) == 1
    reblocked_payload = reblocked_payloads[0]
    assert reblocked_payload["task_row_snapshot"]["id"] == dependent_id
    assert reblocked_payload["task_row_snapshot"]["status"] == "blocked"
    assert reblocked_payload["task_row_snapshot"]["blocked_by"] == [reopened_id]
    assert (
        reblocked_payload["details"].items()
        >= {
            "reopened_task_id": reopened_id,
            "previous_status": "pending",
            "previous_blockers": [],
            "active_blockers": [reopened_id],
        }.items()
    )
    _assert_execution_event_row_write_receipt(
        reblocked_payload,
        task_id=dependent_id,
        task_path=_task_file_path(workspace, dependent_id),
    )


@pytest.mark.parametrize(
    ("side_effect_name", "invoke_side_effect", "mutator_name"),
    (
        (
            "_apply_reverse_dependency_links",
            lambda service, task_id: service._apply_reverse_dependency_links(
                created_task_id=7002,
                blocker_ids=[task_id],
            ),
            "update_blocks",
        ),
        (
            "_apply_reopen_downstream_reblocks",
            lambda service, task_id: service._apply_reopen_downstream_reblocks(
                reopened_task_id=7003,
                dependent_ids=[task_id],
            ),
            "update",
        ),
    ),
    ids=("reverse_dependency_links", "reopen_downstream_reblocks"),
)
def test_dependency_side_effects_skip_missing_helper_results_without_mutating_rows(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    side_effect_name: str,
    invoke_side_effect: Callable[[TaskRuntimeService, int], list[dict[str, Any]]],
    mutator_name: str,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = TaskRuntimeService(str(workspace))
    task_id = 7001
    helper_calls: list[object] = []
    direct_board_get_calls: list[object] = []
    mutator_calls: list[object] = []

    def task_entity_for_dependency_side_effect(raw_task_id: object) -> tuple[int | None, Any | None]:
        helper_calls.append(raw_task_id)
        return service.normalize_task_id(raw_task_id), None

    def reject_direct_board_get(raw_task_id: object) -> Any:
        direct_board_get_calls.append(raw_task_id)
        raise AssertionError(f"{side_effect_name} must short-circuit missing rows from the dependency helper")

    def reject_mutator(*args: object, **_kwargs: object) -> Any:
        mutator_calls.append(args)
        raise AssertionError(f"{side_effect_name} must not mutate rows when the dependency helper returns no task")

    def reject_append_execution_event(event_type: str, **_kwargs: Any) -> dict[str, Any]:
        raise AssertionError(f"{side_effect_name} must not append {event_type!r} events for missing helper results")

    monkeypatch.setattr(
        service,
        "_task_entity_for_dependency_side_effect",
        task_entity_for_dependency_side_effect,
        raising=False,
    )
    monkeypatch.setattr(service._board, "get", reject_direct_board_get)
    monkeypatch.setattr(service._board, mutator_name, reject_mutator)
    monkeypatch.setattr(service, "_append_execution_event", reject_append_execution_event)

    events = invoke_side_effect(service, task_id)

    assert helper_calls == [task_id]
    assert direct_board_get_calls == []
    assert mutator_calls == []
    assert events == []


@pytest.mark.parametrize(
    ("transition_name", "expected_reason", "expected_task_status", "invoke_transition"),
    _EXECUTION_TRANSITION_HELPER_CASES,
    ids=[case[0] for case in _EXECUTION_TRANSITION_HELPER_CASES],
)
def test_execution_transitions_use_task_entity_helper_and_preserve_success_shape(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    transition_name: str,
    expected_reason: str,
    expected_task_status: str,
    invoke_transition: ExecutionTransitionInvoker,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = TaskRuntimeService(str(workspace))
    created, claimed = _claimed_execution_for_transition(
        service,
        subject=f"{transition_name} helper success",
        run_id=f"run-{transition_name}-helper-success",
    )
    created_id = int(created["id"])
    task = service._board.get(created_id)
    assert task is not None

    helper_calls: list[object] = []
    direct_board_get_calls: list[object] = []

    def task_entity_for_transition(task_id: object) -> tuple[int | None, Any | None]:
        helper_calls.append(task_id)
        return service.normalize_task_id(task_id), task

    def reject_direct_board_get(task_id: object) -> Any:
        direct_board_get_calls.append(task_id)
        raise AssertionError("execution transitions must read task entities through _task_entity_for_transition")

    def append_execution_event(event_type: str, **_kwargs: Any) -> dict[str, Any]:
        return {"ok": True, "event_type": event_type, "published": True}

    monkeypatch.setattr(service, "_task_entity_for_transition", task_entity_for_transition)
    monkeypatch.setattr(service._board, "get", reject_direct_board_get)
    monkeypatch.setattr(service, "_append_execution_event", append_execution_event)

    result = invoke_transition(
        service,
        f"task-{created_id}",
        str(claimed["session"]["session_id"]),
    )

    assert helper_calls == [f"task-{created_id}"]
    assert direct_board_get_calls == []
    assert result["success"] is True
    assert result["reason"] == expected_reason
    assert result["task"]["id"] == created_id
    assert result["task"]["status"] == expected_task_status
    assert result["session"]["session_id"] == claimed["session"]["session_id"]
    assert result["execution_event"] == {
        "ok": True,
        "event_type": expected_reason,
        "published": True,
    }
    assert "requested_reason" not in result
    assert "failure_class" not in result
    assert "state_mutation_applied" not in result


@pytest.mark.parametrize(
    ("boundary_name", "task_id", "helper_normalized", "expected_reason"),
    (
        ("invalid_task_id", "not-a-task", None, "invalid_task_id"),
        ("task_not_found", "task-7001", 7001, "task_not_found"),
    ),
    ids=("invalid_task_id", "task_not_found"),
)
@pytest.mark.parametrize(
    ("transition_name", "_expected_reason", "_expected_task_status", "invoke_transition"),
    _EXECUTION_TRANSITION_HELPER_CASES,
    ids=[case[0] for case in _EXECUTION_TRANSITION_HELPER_CASES],
)
def test_execution_transitions_short_circuit_from_task_entity_helper_boundary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    boundary_name: str,
    task_id: object,
    helper_normalized: int | None,
    expected_reason: str,
    transition_name: str,
    _expected_reason: str,
    _expected_task_status: str,
    invoke_transition: ExecutionTransitionInvoker,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = TaskRuntimeService(str(workspace))
    helper_calls: list[object] = []
    session_lock_calls: list[object] = []

    def task_entity_for_transition(raw_task_id: object) -> tuple[int | None, Any | None]:
        helper_calls.append(raw_task_id)
        return helper_normalized, None

    def reject_session_lock(normalized_task_id: object) -> Any:
        session_lock_calls.append(normalized_task_id)
        raise AssertionError(f"{transition_name} must short-circuit before session reads for {boundary_name}")

    monkeypatch.setattr(service, "_task_entity_for_transition", task_entity_for_transition)
    monkeypatch.setattr(service, "_get_session_lock", reject_session_lock)

    result = invoke_transition(service, task_id, "unused-session-id")

    assert helper_calls == [task_id]
    assert session_lock_calls == []
    assert result == {"success": False, "reason": expected_reason}


@pytest.mark.parametrize(
    ("boundary_name", "task_id", "helper_result"),
    (
        ("invalid_task_id", "not-a-task", (None, None)),
        ("task_not_found", "task-7001", (7001, None)),
    ),
    ids=("invalid_task_id", "task_not_found"),
)
@pytest.mark.parametrize(
    ("transition_name", "invoke_transition"),
    _OWNER_TERMINAL_TRANSITION_CASES,
    ids=[case[0] for case in _OWNER_TERMINAL_TRANSITION_CASES],
)
def test_owner_terminal_transitions_short_circuit_before_session_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    boundary_name: str,
    task_id: object,
    helper_result: tuple[int | None, Any | None],
    transition_name: str,
    invoke_transition: OwnerTerminalTransitionInvoker,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = TaskRuntimeService(str(workspace))
    helper_calls: list[object] = []
    session_read_calls: list[object] = []
    update_calls: list[object] = []

    def task_entity_for_owner_terminal_transition(raw_task_id: object) -> tuple[int | None, Any | None]:
        helper_calls.append(raw_task_id)
        return helper_result

    def reject_session_read(normalized_task_id: object) -> Any:
        session_read_calls.append(normalized_task_id)
        raise AssertionError(f"{transition_name} must short-circuit before session reads for {boundary_name}")

    def reject_board_update(task_id_to_update: object, **_kwargs: Any) -> Any:
        update_calls.append(task_id_to_update)
        raise AssertionError(f"{transition_name} must not mutate rows for {boundary_name}")

    monkeypatch.setattr(
        service,
        "_task_entity_for_owner_terminal_transition",
        task_entity_for_owner_terminal_transition,
    )
    monkeypatch.setattr(service, "_read_session", reject_session_read)
    monkeypatch.setattr(service._board, "update", reject_board_update)

    result = invoke_transition(service, task_id)

    assert helper_calls == [task_id]
    assert session_read_calls == []
    assert update_calls == []
    assert result is None


def test_complete_execution_fails_closed_on_execution_event_append_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = TaskRuntimeService(str(workspace))
    created = service.create_task_row(subject="complete with append evidence")
    task_id = int(created["id"])
    claimed = service.claim_execution(
        created["id"],
        worker_id="director",
        role_id="director",
        run_id="run-complete-append-failure",
        selection_source="unit",
    )
    assert claimed["success"] is True

    monkeypatch.setattr(service, "_append_execution_fact_with_cas", _raise_fact_stream_unavailable)

    completed = service.complete_execution(
        created["id"],
        session_id=str(claimed["session"]["session_id"]),
        result_summary="done",
    )

    assert completed["success"] is False
    assert completed["reason"] == "execution_event_append_failed"
    assert completed["requested_reason"] == "completed"
    assert completed["failure_class"] == "ledger_append_failed"
    assert completed["state_mutation_applied"] is True
    _assert_execution_event_append_failure_with_row_write_receipt(
        completed["execution_event"],
        event_type="completed",
        task_id=task_id,
        task_path=_task_file_path(workspace, task_id),
    )
    assert completed["task"]["status"] == "completed"


def test_fail_execution_fails_closed_on_execution_event_append_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = TaskRuntimeService(str(workspace))
    created = service.create_task_row(subject="fail with append evidence")
    task_id = int(created["id"])
    claimed = service.claim_execution(
        created["id"],
        worker_id="director",
        role_id="director",
        run_id="run-fail-append-failure",
        selection_source="unit",
    )
    assert claimed["success"] is True

    monkeypatch.setattr(service, "_append_execution_fact_with_cas", _raise_fact_stream_unavailable)

    failed = service.fail_execution(
        created["id"],
        session_id=str(claimed["session"]["session_id"]),
        error="director execution failed",
    )

    assert failed["success"] is False
    assert failed["reason"] == "execution_event_append_failed"
    assert failed["requested_reason"] == "failed"
    assert failed["failure_class"] == "ledger_append_failed"
    assert failed["state_mutation_applied"] is True
    _assert_execution_event_append_failure_with_row_write_receipt(
        failed["execution_event"],
        event_type="failed",
        task_id=task_id,
        task_path=_task_file_path(workspace, task_id),
    )
    assert failed["task"]["status"] == "failed"


def test_suspend_execution_fails_closed_on_execution_event_append_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = TaskRuntimeService(str(workspace))
    created = service.create_task_row(subject="suspend with append evidence")
    task_id = int(created["id"])
    claimed = service.claim_execution(
        created["id"],
        worker_id="director",
        role_id="director",
        run_id="run-suspend-append-failure",
        selection_source="unit",
    )
    assert claimed["success"] is True

    monkeypatch.setattr(service, "_append_execution_fact_with_cas", _raise_fact_stream_unavailable)

    suspended = service.suspend_execution(
        created["id"],
        session_id=str(claimed["session"]["session_id"]),
        reason="factory_stage_timeout",
    )

    assert suspended["success"] is False
    assert suspended["reason"] == "execution_event_append_failed"
    assert suspended["requested_reason"] == "suspended"
    assert suspended["failure_class"] == "ledger_append_failed"
    assert suspended["state_mutation_applied"] is True
    _assert_execution_event_append_failure_with_row_write_receipt(
        suspended["execution_event"],
        event_type="suspended",
        task_id=task_id,
        task_path=_task_file_path(workspace, task_id),
    )
    assert suspended["task"]["status"] == "pending"


def test_task_runtime_service_wakes_ready_waiters_on_create(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = TaskRuntimeService(str(workspace))

    ready_events: list[str] = []
    listener_event = threading.Event()

    def on_ready() -> None:
        ready_events.append("ready")
        listener_event.set()

    unsubscribe = service.add_ready_listener(on_ready)
    waiter_started = threading.Event()
    wait_results: list[bool] = []

    def wait_for_ready() -> None:
        waiter_started.set()
        wait_results.append(service.wait_ready(timeout=1.0))

    waiter = threading.Thread(target=wait_for_ready)
    waiter.start()
    assert waiter_started.wait(timeout=0.5)

    service.create_task_row(subject="wake ready waiters")

    waiter.join(timeout=2.0)
    assert not waiter.is_alive()
    assert wait_results == [True]
    assert listener_event.wait(timeout=0.5)
    assert ready_events == ["ready"]

    unsubscribe()
    service.create_task_row(subject="listener already removed")
    assert ready_events == ["ready"]


def test_task_runtime_service_wakes_ready_waiters_when_dependency_unblocks(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = TaskRuntimeService(str(workspace))

    parent = service.create_task_row(subject="parent task")
    parent_id = int(parent["id"])
    child = service.create_task_row(subject="child task", blocked_by=[parent_id])
    claimed = service.claim_execution(
        parent_id,
        worker_id="director",
        role_id="director",
        run_id="run-unblock",
        selection_source="unit",
    )
    assert claimed["success"] is True
    assert service.wait_ready(timeout=0.0) is False

    ready_events: list[str] = []
    listener_event = threading.Event()

    def on_ready() -> None:
        ready_events.append("ready")
        listener_event.set()

    service.add_ready_listener(on_ready)
    waiter_started = threading.Event()
    wait_results: list[bool] = []

    def wait_for_ready() -> None:
        waiter_started.set()
        wait_results.append(service.wait_ready(timeout=1.0))

    waiter = threading.Thread(target=wait_for_ready)
    waiter.start()
    assert waiter_started.wait(timeout=0.5)

    completed = service.complete_execution(
        parent_id,
        session_id=str(claimed["session"]["session_id"]),
        result_summary="parent done",
    )

    waiter.join(timeout=2.0)
    assert completed["success"] is True
    assert not waiter.is_alive()
    assert wait_results == [True]
    assert listener_event.wait(timeout=0.5)
    assert ready_events == ["ready"]
    child_row = service.get_task(child["id"])
    assert child_row is not None
    assert child_row["status"] == "pending"


def test_task_runtime_service_reconciles_terminal_session_before_reclaim(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = TaskRuntimeService(str(workspace))

    created = service.create_task_row(subject="completed task with stale row")
    created_id = created["id"]
    claimed = service.claim_execution(
        created_id,
        worker_id="director",
        role_id="director",
        run_id="run-terminal-session",
        selection_source="task_id_lookup",
    )
    assert claimed["success"] is True

    completed = service.complete_execution(
        created_id,
        session_id=str(claimed["session"]["session_id"]),
        result_summary="done",
    )
    assert completed["success"] is True

    task_path = _task_file_path(workspace, created_id)
    stale_payload = json.loads(task_path.read_text(encoding="utf-8"))
    stale_payload["status"] = "pending"
    stale_payload["completed_at"] = None
    task_path.write_text(json.dumps(stale_payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    reloaded = TaskRuntimeService(str(workspace))
    reclaimed = reloaded.claim_execution(
        created_id,
        worker_id="director",
        role_id="director",
        run_id="run-should-not-reclaim",
        selection_source="task_id_lookup",
    )

    assert reclaimed["success"] is False
    assert reclaimed["reason"] == "task_terminal"
    assert reclaimed["reconciled_from_terminal_session"] is True
    assert reclaimed["execution_event"]["ok"] is True
    assert reclaimed["execution_event"]["event_type"] == "terminal_session_reconciled"
    assert reclaimed["task"]["status"] == "completed"
    persisted = json.loads(task_path.read_text(encoding="utf-8"))
    assert persisted["status"] == "completed"


def test_apply_terminal_session_reconcile_non_terminal_session_uses_helper_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = TaskRuntimeService(str(workspace))
    created = service.create_task_row(subject="non-terminal reconcile helper fallback")
    task_id = int(created["id"])
    task = service._board.get(task_id)
    assert task is not None
    session = _session_for_terminal_reconcile(task_id, status="active")
    helper_calls: list[int] = []
    direct_board_get_calls: list[object] = []

    def task_entity_for_terminal_session_reconcile(raw_task_id: int) -> Any:
        helper_calls.append(raw_task_id)
        return task

    def reject_direct_board_get(raw_task_id: object) -> Any:
        direct_board_get_calls.append(raw_task_id)
        raise AssertionError("_apply_terminal_session_reconcile must read fallback rows through its helper")

    monkeypatch.setattr(
        service,
        "_task_entity_for_terminal_session_reconcile",
        task_entity_for_terminal_session_reconcile,
    )
    monkeypatch.setattr(service._board, "get", reject_direct_board_get)

    row, error, event = _assert_terminal_reconcile_result_shape(
        service._apply_terminal_session_reconcile(task_id, session=session)
    )

    assert helper_calls == [task_id]
    assert direct_board_get_calls == []
    assert row is not None
    assert row["id"] == task_id
    assert row["status"] == "pending"
    assert row["raw_status"] == "pending"
    assert error == ""
    assert event is None


def test_apply_terminal_session_reconcile_missing_helper_result_after_invalid_transition(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = TaskRuntimeService(str(workspace))
    task_id = 7001
    session = _session_for_terminal_reconcile(task_id, status="failed")
    helper_calls: list[int] = []
    direct_board_get_calls: list[object] = []
    reconcile_calls: list[object] = []

    def raise_invalid_transition(*_args: object, **_kwargs: Any) -> Any:
        raise InvalidTaskStateTransitionError("unit invalid transition")

    def task_entity_for_terminal_session_reconcile(raw_task_id: int) -> None:
        helper_calls.append(raw_task_id)
        return None

    def reject_direct_board_get(raw_task_id: object) -> Any:
        direct_board_get_calls.append(raw_task_id)
        raise AssertionError("_apply_terminal_session_reconcile must not bypass the missing-row helper result")

    def reject_reconcile_terminal_status(*args: object, **_kwargs: Any) -> Any:
        reconcile_calls.append(args)
        raise AssertionError("_apply_terminal_session_reconcile must short-circuit when the helper returns None")

    monkeypatch.setattr(service._board, "update", raise_invalid_transition)
    monkeypatch.setattr(
        service,
        "_task_entity_for_terminal_session_reconcile",
        task_entity_for_terminal_session_reconcile,
    )
    monkeypatch.setattr(service._board, "get", reject_direct_board_get)
    monkeypatch.setattr(service._board, "reconcile_terminal_status", reject_reconcile_terminal_status)

    row, error, event = _assert_terminal_reconcile_result_shape(
        service._apply_terminal_session_reconcile(task_id, session=session)
    )

    assert helper_calls == [task_id]
    assert direct_board_get_calls == []
    assert reconcile_calls == []
    assert row is None
    assert error == "task_not_found"
    assert event is None


def test_apply_terminal_session_reconcile_terminal_task_conflict_uses_helper_row(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = TaskRuntimeService(str(workspace))
    created = service.create_task_row(subject="terminal reconcile conflict helper row")
    task_id = int(created["id"])
    terminal_task = service._board.reconcile_terminal_status(
        task_id,
        "completed",
        result_summary="already completed",
    )
    assert terminal_task is not None
    assert terminal_task.status.value == "completed"
    session = _session_for_terminal_reconcile(task_id, status="failed")
    helper_calls: list[int] = []
    direct_board_get_calls: list[object] = []
    reconcile_calls: list[object] = []

    def raise_invalid_transition(*_args: object, **_kwargs: Any) -> Any:
        raise InvalidTaskStateTransitionError("unit invalid transition")

    def task_entity_for_terminal_session_reconcile(raw_task_id: int) -> Any:
        helper_calls.append(raw_task_id)
        return terminal_task

    def reject_direct_board_get(raw_task_id: object) -> Any:
        direct_board_get_calls.append(raw_task_id)
        raise AssertionError("_apply_terminal_session_reconcile must use helper row for terminal conflicts")

    def reject_reconcile_terminal_status(*args: object, **_kwargs: Any) -> Any:
        reconcile_calls.append(args)
        raise AssertionError("_apply_terminal_session_reconcile must not rewrite a terminal row conflict")

    monkeypatch.setattr(service._board, "update", raise_invalid_transition)
    monkeypatch.setattr(
        service,
        "_task_entity_for_terminal_session_reconcile",
        task_entity_for_terminal_session_reconcile,
    )
    monkeypatch.setattr(service._board, "get", reject_direct_board_get)
    monkeypatch.setattr(service._board, "reconcile_terminal_status", reject_reconcile_terminal_status)

    row, error, event = _assert_terminal_reconcile_result_shape(
        service._apply_terminal_session_reconcile(task_id, session=session)
    )

    assert helper_calls == [task_id]
    assert direct_board_get_calls == []
    assert reconcile_calls == []
    assert row is not None
    assert row["id"] == task_id
    assert row["status"] == "completed"
    assert row["raw_status"] == "completed"
    assert error == "terminal_row_conflict"
    assert event is None
    persisted = json.loads(_task_file_path(workspace, task_id).read_text(encoding="utf-8"))
    assert persisted["status"] == "completed"


def test_apply_terminal_session_reconcile_update_none_uses_helper_row_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = TaskRuntimeService(str(workspace))
    created = service.create_task_row(subject="terminal reconcile update none helper fallback")
    task_id = int(created["id"])
    task = service._board.get(task_id)
    assert task is not None
    session = _session_for_terminal_reconcile(task_id, status="completed")
    helper_calls: list[int] = []
    direct_board_get_calls: list[object] = []
    event_calls: list[str] = []

    def update_return_none(*_args: object, **_kwargs: Any) -> None:
        return None

    def task_entity_for_terminal_session_reconcile(raw_task_id: int) -> Any:
        helper_calls.append(raw_task_id)
        return task

    def reject_direct_board_get(raw_task_id: object) -> Any:
        direct_board_get_calls.append(raw_task_id)
        raise AssertionError("_apply_terminal_session_reconcile must use helper row when update returns None")

    def reject_append_execution_event(event_type: str, **_kwargs: Any) -> dict[str, Any]:
        event_calls.append(event_type)
        raise AssertionError("_apply_terminal_session_reconcile must not append events for update None fallback")

    monkeypatch.setattr(service._board, "update", update_return_none)
    monkeypatch.setattr(
        service,
        "_task_entity_for_terminal_session_reconcile",
        task_entity_for_terminal_session_reconcile,
    )
    monkeypatch.setattr(service._board, "get", reject_direct_board_get)
    monkeypatch.setattr(service, "_append_execution_event", reject_append_execution_event)

    row, error, event = _assert_terminal_reconcile_result_shape(
        service._apply_terminal_session_reconcile(task_id, session=session)
    )

    assert helper_calls == [task_id]
    assert direct_board_get_calls == []
    assert event_calls == []
    assert row is not None
    assert row["id"] == task_id
    assert row["status"] == "pending"
    assert row["raw_status"] == "pending"
    assert error == ""
    assert event is None


def test_task_runtime_ready_reset_row_with_older_terminal_session_is_claimable(tmp_path: Path) -> None:
    """A deliberate FAILED->READY reset must win over the stale terminal session.

    Regression: claim_execution used to crash with
    InvalidTaskStateTransitionError (ready -> failed) when reconciling the
    stale terminal session against a deliberately reset row.
    """
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = TaskRuntimeService(str(workspace))

    created = service.create_task_row(subject="retry after failure via ready reset")
    created_id = created["id"]
    claimed = service.claim_execution(
        created_id,
        worker_id="director",
        role_id="director",
        run_id="run-retry-ready",
        selection_source="task_id_lookup",
    )
    assert claimed["success"] is True
    old_session_id = str(claimed["session"]["session_id"])

    failed = service.fail_execution(created_id, session_id=old_session_id, error="transient failure")
    assert failed["success"] is True

    time.sleep(0.02)
    reset = service.update_task_row(created_id, status="ready")
    assert reset is not None
    assert reset["status"] == "ready"

    reclaimed = service.claim_execution(
        created_id,
        worker_id="director",
        role_id="director",
        run_id="run-retry-ready-2",
        selection_source="task_id_lookup",
    )

    assert reclaimed["success"] is True
    assert reclaimed["reason"] == "claimed"
    assert reclaimed["resumed"] is False
    assert str(reclaimed["session"]["session_id"]) != old_session_id
    assert reclaimed["task"]["status"] == "in_progress"
    persisted = json.loads(_task_file_path(workspace, created_id).read_text(encoding="utf-8"))
    assert persisted["status"] == "in_progress"


def test_task_runtime_deliberate_pending_retry_is_claimable_and_not_flipped_to_failed(tmp_path: Path) -> None:
    """A deliberate FAILED->PENDING retry must not be reconciled back to failed."""
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = TaskRuntimeService(str(workspace))

    created = service.create_task_row(subject="retry after failure via pending reset")
    created_id = created["id"]
    claimed = service.claim_execution(
        created_id,
        worker_id="director",
        role_id="director",
        run_id="run-retry-pending",
        selection_source="task_id_lookup",
    )
    assert claimed["success"] is True
    failed = service.fail_execution(
        created_id,
        session_id=str(claimed["session"]["session_id"]),
        error="transient failure",
    )
    assert failed["success"] is True

    time.sleep(0.02)
    reset = service.update_task_row(created_id, status="pending")
    assert reset is not None
    assert reset["status"] == "pending"

    reclaimed = service.claim_execution(
        created_id,
        worker_id="director",
        role_id="director",
        run_id="run-retry-pending-2",
        selection_source="task_id_lookup",
    )

    assert reclaimed["success"] is True
    assert reclaimed["task"]["status"] == "in_progress"
    persisted = json.loads(_task_file_path(workspace, created_id).read_text(encoding="utf-8"))
    assert persisted["status"] == "in_progress"

    completed = service.complete_execution(
        created_id,
        session_id=str(reclaimed["session"]["session_id"]),
        result_summary="second attempt worked",
    )
    assert completed["success"] is True
    assert completed["task"]["status"] == "completed"


def test_task_runtime_claim_next_honors_deliberate_retry_projection(tmp_path: Path) -> None:
    """Queue projection must not hide a deliberate retry behind stale session evidence."""
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = TaskRuntimeService(str(workspace))

    created = service.create_task_row(subject="retry should be visible to queue")
    created_id = created["id"]
    claimed = service.claim_execution(
        created_id,
        worker_id="director",
        role_id="director",
        run_id="run-queue-retry",
        selection_source="task_id_lookup",
    )
    assert claimed["success"] is True
    failed = service.fail_execution(
        created_id,
        session_id=str(claimed["session"]["session_id"]),
        error="temporary platform failure",
    )
    assert failed["success"] is True

    time.sleep(0.02)
    reset = service.update_task_row(created_id, status="pending")
    assert reset is not None
    assert reset["status"] == "pending"

    queued_rows = service.list_task_rows(include_terminal=False)
    assert [row["id"] for row in queued_rows] == [created_id]
    assert queued_rows[0]["status"] == "pending"
    assert (
        queued_rows[0]["metadata"]["runtime_execution"]["session_projection_authority"]
        == "row_reset_after_terminal_session"
    )

    reclaimed = service.claim_next_execution(
        worker_id="director",
        role_id="director",
        run_id="run-queue-retry-2",
        selection_source="queue",
    )

    assert reclaimed["success"] is True
    assert reclaimed["task"]["id"] == created_id
    assert reclaimed["task"]["status"] == "in_progress"


def test_augment_task_row_authorizes_terminal_session_superseded_from_passed_row(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``_augment_task_row`` must not re-read raw TaskBoard state for retry authority.

    The file-backed row passed into the helper is the row authority at this
    boundary. When it carries a sanctioned ``terminal_reset_at`` newer than
    the terminal session timestamp, the row must authorize superseding the
    stale terminal session even if raw ``TaskBoard.get`` is unavailable.
    """

    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = TaskRuntimeService(str(workspace))

    created = service.create_task_row(subject="augment row owns retry authority")
    created_id = int(created["id"])
    claimed = service.claim_execution(
        created_id,
        worker_id="director",
        role_id="director",
        run_id="run-augment-row-authority",
        selection_source="task_id_lookup",
    )
    assert claimed["success"] is True

    failed = service.fail_execution(
        created_id,
        session_id=str(claimed["session"]["session_id"]),
        error="retryable execution failure",
    )
    assert failed["success"] is True

    time.sleep(0.02)
    reset = service.update_task_row(created_id, status="pending")
    assert reset is not None
    assert reset["status"] == "pending"

    file_backed_row = json.loads(_task_file_path(workspace, created_id).read_text(encoding="utf-8"))
    session = TaskExecutionSession.from_dict(
        json.loads(_session_file_path(workspace, created_id).read_text(encoding="utf-8"))
    )
    reset_at = float(file_backed_row["metadata"]["terminal_reset_at"])
    session_terminal_at = terminal_session_timestamp(session)
    assert session_terminal_at is not None
    assert reset_at > session_terminal_at

    def reject_raw_board_get(_task_id: object) -> object:
        raise AssertionError("_augment_task_row must use the passed row, not raw TaskBoard.get")

    monkeypatch.setattr(service._board, "get", reject_raw_board_get)

    augmented = service._augment_task_row(file_backed_row)

    assert augmented["status"] == "pending"
    assert augmented["raw_status"] == "pending"
    assert augmented["claimed_by"] == ""
    runtime_execution = augmented["metadata"]["runtime_execution"]
    assert runtime_execution["effective_status"] == "pending"
    assert runtime_execution["superseded_terminal_session_status"] == "failed"
    assert runtime_execution["session_projection_authority"] == "row_reset_after_terminal_session"


def test_find_terminal_session_snapshot_reads_row_metadata_without_raw_board_get(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Terminal metadata fallback must use row projection, not raw TaskBoard.get.

    The session file deliberately contains a non-terminal session with the same
    id, so the only matching terminal snapshot lives in the monkeypatched
    task-row ``metadata.runtime_execution`` projection. If the fallback reaches
    for ``self._board.get`` directly, the sentinel below raises and catches the
    retired dependency.
    """

    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = TaskRuntimeService(str(workspace))

    created = service.create_task_row(subject="terminal metadata fallback")
    task_id = int(created["id"])
    terminal_session = TaskExecutionSession.create(
        task_id=task_id,
        role_id="director",
        worker_id="director-worker",
        run_id="run-terminal-row-metadata",
        lease_ttl_seconds=120,
        attempt=1,
        resume_count=0,
        origin="unit",
        selection_source="task_id_lookup",
    )
    terminal_session.status = "failed"
    terminal_session.last_error = "terminal snapshot preserved in task row metadata"
    terminal_session.released_at = terminal_session.last_heartbeat_at

    incoming = TaskExecutionSession.from_dict(
        {
            **terminal_session.to_dict(),
            "status": "active",
            "last_error": "",
            "released_at": "",
        }
    )
    updated = service._board.update(
        task_id,
        metadata={"runtime_execution": terminal_session.to_dict()},
        allow_dependency_status=True,
    )
    assert updated is not None
    assert isinstance(updated.metadata, dict)
    assert updated.metadata["runtime_execution"]["session_id"] == terminal_session.session_id
    _session_file_path(workspace, task_id).write_text(
        json.dumps(incoming.to_dict(), ensure_ascii=False),
        encoding="utf-8",
    )
    assert _session_file_path(workspace, task_id).exists()

    def projected_file_rows(*, include_terminal: bool = True) -> list[dict[str, Any]]:
        assert include_terminal is True
        return [
            {
                "id": str(task_id),
                "metadata": {"runtime_execution": terminal_session.to_dict()},
            }
        ]

    def reject_raw_board_get(_task_id: object) -> object:
        raise AssertionError("_find_terminal_session_snapshot must not call raw TaskBoard.get")

    monkeypatch.setattr(service, "_find_latest_execution_fact_row_for_task", lambda _task_id: None)
    monkeypatch.setattr(service, "_list_file_task_rows", projected_file_rows)
    monkeypatch.setattr(service._board, "get", reject_raw_board_get)

    snapshot = service._find_terminal_session_snapshot(incoming)

    assert snapshot is not None
    assert snapshot.session_id == terminal_session.session_id
    assert snapshot.status == "failed"
    assert snapshot.last_error == terminal_session.last_error


def test_find_projected_runtime_execution_session_reads_row_projection_without_raw_entities(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Projected session lookup must consume row projections, not raw Task entities."""

    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = TaskRuntimeService(str(workspace))

    task_id = 407
    row_session = TaskExecutionSession.create(
        task_id=task_id,
        role_id="director",
        worker_id="director-worker",
        run_id="run-row-projection-session",
        lease_ttl_seconds=120,
        attempt=2,
        resume_count=1,
        origin="unit",
        selection_source="task_id_lookup",
    )
    projected_rows: list[dict[str, Any]] = [
        {
            "id": task_id + 1,
            "metadata": {"runtime_execution": {**row_session.to_dict(), "task_id": task_id + 1}},
        },
        {
            "id": str(task_id),
            "metadata": {"runtime_execution": row_session.to_dict()},
        },
    ]

    def projected_file_rows(*, include_terminal: bool = True) -> list[dict[str, Any]]:
        assert include_terminal is True
        return projected_rows

    def reject_raw_task_entities() -> list[Any]:
        raise AssertionError("_find_projected_runtime_execution_session must not read raw Task entities")

    monkeypatch.setattr(service, "_find_latest_execution_fact_row_for_task", lambda _task_id: None)
    monkeypatch.setattr(service, "_list_file_task_rows", projected_file_rows)
    monkeypatch.setattr(service, "_list_file_task_entities", reject_raw_task_entities)

    session = service._find_projected_runtime_execution_session(task_id)

    assert session is not None
    assert session.session_id == row_session.session_id
    assert session.task_id == task_id
    assert session.role_id == "director"
    assert session.worker_id == "director-worker"
    assert session.attempt == 2
    assert session.resume_count == 1


def test_task_runtime_stale_pending_row_with_newer_terminal_session_still_rejects_reclaim(tmp_path: Path) -> None:
    """A stale row carrying an OLD reset marker must not beat a NEWER terminal session."""
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = TaskRuntimeService(str(workspace))

    created = service.create_task_row(subject="stale row after second failure")
    created_id = created["id"]
    first_claim = service.claim_execution(
        created_id,
        worker_id="director",
        role_id="director",
        run_id="run-first",
        selection_source="task_id_lookup",
    )
    assert first_claim["success"] is True
    assert service.fail_execution(
        created_id,
        session_id=str(first_claim["session"]["session_id"]),
        error="first failure",
    )["success"]

    time.sleep(0.02)
    # Sanctioned retry: stamps the terminal-reset marker.
    assert service.update_task_row(created_id, status="pending") is not None
    second_claim = service.claim_execution(
        created_id,
        worker_id="director",
        role_id="director",
        run_id="run-second",
        selection_source="task_id_lookup",
    )
    assert second_claim["success"] is True
    time.sleep(0.02)
    assert service.fail_execution(
        created_id,
        session_id=str(second_claim["session"]["session_id"]),
        error="second failure",
    )["success"]

    # Stale byte-level rewrite: pending row with the OLD reset marker, while
    # the terminal session on disk is NEWER than that marker.
    task_path = _task_file_path(workspace, created_id)
    stale_payload = json.loads(task_path.read_text(encoding="utf-8"))
    stale_payload["status"] = "pending"
    stale_payload["completed_at"] = None
    task_path.write_text(json.dumps(stale_payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    reloaded = TaskRuntimeService(str(workspace))
    reclaimed = reloaded.claim_execution(
        created_id,
        worker_id="director",
        role_id="director",
        run_id="run-should-not-reclaim",
        selection_source="task_id_lookup",
    )

    assert reclaimed["success"] is False
    assert reclaimed["reason"] == "task_terminal"
    assert reclaimed["reconciled_from_terminal_session"] is True
    assert reclaimed["execution_event"]["ok"] is True
    assert reclaimed["execution_event"]["event_type"] == "terminal_session_reconciled"
    persisted = json.loads(task_path.read_text(encoding="utf-8"))
    assert persisted["status"] == "failed"


def test_task_runtime_stale_ready_row_reconcile_does_not_crash_claim(tmp_path: Path) -> None:
    """A stale byte-level READY rewrite must reconcile to terminal, not crash.

    READY -> failed has no valid state-machine transition; reconciliation must
    fall back to the evidence-based bridge instead of raising
    InvalidTaskStateTransitionError through the claim path.
    """
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = TaskRuntimeService(str(workspace))

    created = service.create_task_row(subject="stale ready row over failed session")
    created_id = created["id"]
    claimed = service.claim_execution(
        created_id,
        worker_id="director",
        role_id="director",
        run_id="run-stale-ready",
        selection_source="task_id_lookup",
    )
    assert claimed["success"] is True
    assert service.fail_execution(
        created_id,
        session_id=str(claimed["session"]["session_id"]),
        error="genuine failure",
    )["success"]

    # Stale writer clobbers the row to READY without the sanctioned reset
    # marker (bypassing the state machine entirely).
    task_path = _task_file_path(workspace, created_id)
    stale_payload = json.loads(task_path.read_text(encoding="utf-8"))
    stale_payload["status"] = "ready"
    stale_payload["completed_at"] = None
    task_path.write_text(json.dumps(stale_payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    reloaded = TaskRuntimeService(str(workspace))
    reclaimed = reloaded.claim_execution(
        created_id,
        worker_id="director",
        role_id="director",
        run_id="run-should-not-reclaim",
        selection_source="task_id_lookup",
    )

    assert reclaimed["success"] is False
    assert reclaimed["reason"] == "task_terminal"
    assert reclaimed["reconciled_from_terminal_session"] is True
    assert "reconcile_error" not in reclaimed
    assert reclaimed["execution_event"]["ok"] is True
    assert reclaimed["execution_event"]["event_type"] == "terminal_session_reconciled"
    persisted = json.loads(task_path.read_text(encoding="utf-8"))
    assert persisted["status"] == "failed"


def test_task_runtime_service_preserves_terminal_session_during_run_cancellation(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = TaskRuntimeService(str(workspace))

    created = service.create_task_row(subject="completed task with stale active session")
    created_id = created["id"]
    claimed = service.claim_execution(
        created_id,
        worker_id="director",
        role_id="director",
        run_id="run-terminal-race",
        selection_source="task_id_lookup",
    )
    assert claimed["success"] is True

    completed = service.complete_execution(
        created_id,
        session_id=str(claimed["session"]["session_id"]),
        result_summary="done",
    )
    assert completed["success"] is True

    session_path = _session_file_path(workspace, created_id)
    stale_session = json.loads(session_path.read_text(encoding="utf-8"))
    stale_session["status"] = "active"
    stale_session["resumable"] = True
    stale_session["last_error"] = ""
    session_path.write_text(json.dumps(stale_session, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    reloaded = TaskRuntimeService(str(workspace))
    suspended = reloaded.suspend_active_executions_for_run(
        "run-terminal-race",
        reason="factory_stage_timeout",
    )

    assert suspended["success"] is True
    assert suspended["suspended_count"] == 0
    persisted_session = json.loads(session_path.read_text(encoding="utf-8"))
    assert persisted_session["status"] == "completed"
    persisted_task = json.loads(_task_file_path(workspace, created_id).read_text(encoding="utf-8"))
    assert persisted_task["status"] == "completed"


def test_task_runtime_stale_metadata_update_does_not_downgrade_completed_row(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    writer = TaskRuntimeService(str(workspace))

    created = writer.create_task_row(subject="completed task")
    created_id = created["id"]
    stale_reader = TaskRuntimeService(str(workspace))
    stale_reader.get_task(created_id)

    claimed = writer.claim_execution(
        created_id,
        worker_id="director",
        role_id="director",
        run_id="run-complete",
        selection_source="task_id_lookup",
    )
    assert claimed["success"] is True
    completed = writer.complete_execution(
        created_id,
        session_id=str(claimed["session"]["session_id"]),
        result_summary="done",
    )
    assert completed["success"] is True

    updated = stale_reader.update_task_row(created_id, metadata={"late_projection": "workspace_quality_gate_failed"})

    assert updated is not None
    assert updated["status"] == "completed"
    task_path = _task_file_path(workspace, created_id)
    persisted = json.loads(task_path.read_text(encoding="utf-8"))
    assert persisted["status"] == "completed"
    assert persisted["metadata"]["late_projection"] == "workspace_quality_gate_failed"


def test_task_runtime_service_refreshes_stale_blocked_row_with_completed_dependencies(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = TaskRuntimeService(str(workspace))

    parent = service.create_task_row(subject="completed prerequisite")
    parent_id = int(parent["id"])
    child = service.create_task_row(
        subject="stale blocked child",
        blocked_by=[parent_id],
        metadata={"resolved_depends_on_task_ids": [parent_id]},
    )
    child_id = child["id"]
    claim_parent = service.claim_execution(
        parent_id,
        worker_id="director",
        role_id="director",
        run_id="run-stale-unblock",
        selection_source="unit",
    )
    assert claim_parent["success"] is True
    completed = service.complete_execution(
        parent_id,
        session_id=str(claim_parent["session"]["session_id"]),
        result_summary="parent done",
    )
    assert completed["success"] is True
    assert completed["dependency_execution_events"][0]["ok"] is True
    assert completed["dependency_execution_events"][0]["event_type"] == "dependencies_unblocked"

    stale = service.update_task_row(child_id, status="blocked")
    assert stale is not None
    assert str(stale["status"]) == "blocked"
    assert stale["blocked_by"] == []

    refresh = service.refresh_dependency_unblocks()
    assert refresh["unblocked_task_ids"] == [child_id]
    assert refresh["execution_events"][0]["ok"] is True
    assert refresh["execution_events"][0]["event_type"] == "dependencies_unblocked"
    fact_rows = service.list_task_rows_from_execution_facts()
    fact_child = next(row for row in fact_rows if row["id"] == child_id)
    assert fact_child["status"] == "pending"
    claim_child = service.claim_next_execution(
        worker_id="director",
        role_id="director",
        run_id="run-stale-unblock",
        selection_source="unit",
    )

    assert claim_child["success"] is True
    assert claim_child["task"]["id"] == child_id


def test_task_runtime_service_records_dependency_blocker_refresh_events(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = TaskRuntimeService(str(workspace))

    completed_parent = service.create_task_row(subject="completed dependency")
    active_parent = service.create_task_row(subject="still active dependency")
    completed_parent_id = int(completed_parent["id"])
    active_parent_id = int(active_parent["id"])
    child = service.create_task_row(
        subject="partially blocked child",
        blocked_by=[completed_parent_id, active_parent_id],
    )
    child_id = int(child["id"])
    stale_child = service.update_task_row(child_id, status="blocked")
    assert stale_child is not None

    claimed = service.claim_execution(
        completed_parent_id,
        worker_id="director",
        role_id="director",
        run_id="run-partial-unblock",
        selection_source="unit",
    )
    assert claimed["success"] is True
    completed = service.complete_execution(
        completed_parent_id,
        session_id=str(claimed["session"]["session_id"]),
        result_summary="dependency done",
    )
    assert completed["success"] is True
    assert completed["dependency_execution_events"][0]["ok"] is True
    assert completed["dependency_execution_events"][0]["event_type"] == "dependency_blockers_refreshed"

    stale_child = service.update_task_row(
        child_id,
        status="blocked",
        blocked_by=[completed_parent_id, active_parent_id],
    )
    assert stale_child is not None
    refresh = service.refresh_dependency_unblocks()

    assert refresh["unblocked_task_ids"] == []
    assert refresh["refreshed_task_ids"] == [child_id]
    assert refresh["execution_events"][0]["ok"] is True
    assert refresh["execution_events"][0]["event_type"] == "dependency_blockers_refreshed"
    child_row = service.get_task(child_id)
    assert child_row is not None
    assert child_row["status"] == "blocked"
    assert child_row["blocked_by"] == [active_parent_id]


def test_task_runtime_service_blocks_missing_dependency_fail_closed(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = TaskRuntimeService(str(workspace))

    child = service.create_task_row(subject="child with missing dependency", blocked_by=[999])
    child_id = child["id"]

    claim = service.claim_execution(
        child_id,
        worker_id="director",
        role_id="director",
        run_id="run-missing-dependency",
        selection_source="unit",
    )

    assert claim["success"] is False
    assert claim["reason"] == "task_blocked"


def test_task_runtime_reset_records_clears_rows_sessions_and_events(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = TaskRuntimeService(str(workspace))

    created = service.create_task_row(subject="reset stale taskboard rows", metadata={"scope": "src/App.tsx"})
    created_id = created["id"]
    claim = service.claim_execution(
        created_id,
        worker_id="director",
        role_id="director",
        run_id="run-reset",
        selection_source="unit",
        external_task_id="task-reset",
    )
    assert claim["success"] is True

    taskboard_event_path = Path(resolve_runtime_path(str(workspace), "runtime/events/taskboard.terminal.events.jsonl"))
    taskboard_event_path.parent.mkdir(parents=True, exist_ok=True)
    taskboard_event_path.write_text('{"event_type":"completed"}\n', encoding="utf-8")

    result = reset_runtime_task_records(str(workspace))

    assert result["failed_count"] == 0
    assert TaskRuntimeService(str(workspace)).list_task_rows() == []
    tasks_dir = Path(resolve_runtime_path(str(workspace), "runtime/tasks"))
    assert not list(tasks_dir.glob("task_*.json"))
    assert (tasks_dir / ".max_id").is_file()
    assert not taskboard_event_path.exists()


def test_task_runtime_reset_rows_for_reexecution_emits_execution_facts(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = TaskRuntimeService(str(workspace))

    created = service.create_task_row(subject="dirty task", metadata={"scope": "src/App.tsx"})
    created_id = created["id"]
    claimed = service.claim_execution(
        created_id,
        worker_id="director",
        role_id="director",
        run_id="run-reset-row",
        selection_source="unit",
    )
    assert claimed["success"] is True
    assert _session_file_path(workspace, created_id).is_file()

    result = service.reset_task_rows_for_reexecution(source="unit.reset")

    assert result["success"] is True
    assert result["reset_files"] == [f"task_{created_id}.json"]
    assert result["deleted_session_files"] == [f"task_{created_id}.session.json"]
    assert not _session_file_path(workspace, created_id).exists()
    task_payload = json.loads(_task_file_path(workspace, created_id).read_text(encoding="utf-8"))
    assert task_payload["status"] == "pending"
    assert task_payload["assignee"] == ""
    assert "runtime_execution" not in task_payload["metadata"]

    events = query_fact_events(QueryFactEventsV1(workspace=str(workspace), stream="task_runtime.execution")).events
    reset_event = next(event for event in events if event.get("event_type") == "reexecution_reset")
    assert reset_event["payload"]["task_id"] == str(created_id)
    assert reset_event["payload"]["details"]["source"] == "unit.reset"


def test_task_runtime_import_rows_for_reexecution_preserves_ids_and_events(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = TaskRuntimeService(str(workspace))
    _session_file_path(workspace, 7).write_text('{"status":"active"}', encoding="utf-8")

    result = service.import_task_rows_for_reexecution(
        [
            {
                "id": 7,
                "subject": "legacy director task",
                "description": "resume existing PM/CE task",
                "status": "in_progress",
                "blocked_by": [],
                "metadata": {
                    "runtime_execution": {"status": "active"},
                    "workflow_run_id": "old-run",
                    "pm_task_id": "TASK-7",
                },
            }
        ],
        source="unit.import",
        source_task_dir="/tmp/source/runtime/tasks",
    )

    assert result["success"] is True
    assert result["imported_files"] == ["task_7.json"]
    assert result["deleted_session_files"] == ["task_7.session.json"]
    assert not _session_file_path(workspace, 7).exists()
    task_payload = json.loads(_task_file_path(workspace, 7).read_text(encoding="utf-8"))
    assert task_payload["id"] == 7
    assert task_payload["status"] == "pending"
    assert task_payload["metadata"] == {"pm_task_id": "TASK-7"}

    next_row = service.create_task_row(subject="next task")
    assert next_row["id"] == 8

    events = query_fact_events(QueryFactEventsV1(workspace=str(workspace), stream="task_runtime.execution")).events
    imported_event = next(event for event in events if event.get("event_type") == "reexecution_imported")
    assert imported_event["payload"]["task_id"] == "7"
    assert imported_event["payload"]["details"]["source"] == "unit.import"
    assert imported_event["payload"]["details"]["source_task_dir"] == "/tmp/source/runtime/tasks"


def test_task_runtime_inspects_reexecution_source_task_rows(tmp_path: Path) -> None:
    task_dir = tmp_path / "runtime" / "tasks"
    task_dir.mkdir(parents=True)
    (task_dir / "task_2.json").write_text(
        json.dumps({"id": 2, "status": "failed", "metadata": {"pm_task_id": "TASK-2"}}),
        encoding="utf-8",
    )
    (task_dir / "task_2.session.json").write_text('{"status":"active"}', encoding="utf-8")
    (task_dir / "task_bad.json").write_text("{not-json", encoding="utf-8")
    (task_dir / "notes.json").write_text("{}", encoding="utf-8")

    inspection = TaskRuntimeService.inspect_reexecution_source_task_rows(task_dir)

    assert inspection["task_rows"] == [{"id": 2, "status": "failed", "metadata": {"pm_task_id": "TASK-2"}}]
    assert inspection["task_files"] == ["task_2.json"]
    assert inspection["task_count"] == 1
    assert isinstance(inspection["latest_mtime"], float)


def test_task_runtime_reexecution_source_reader_rejects_non_task_dir(tmp_path: Path) -> None:
    non_task_dir = tmp_path / "runtime" / "not_tasks"
    non_task_dir.mkdir(parents=True)
    (non_task_dir / "task_1.json").write_text('{"id":1}', encoding="utf-8")

    inspection = TaskRuntimeService.inspect_reexecution_source_task_rows(non_task_dir)

    assert inspection == {
        "task_rows": [],
        "task_files": [],
        "task_count": 0,
        "latest_mtime": 0.0,
    }


def test_task_runtime_service_materializes_legacy_task_and_claims_it(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = TaskRuntimeService(str(workspace))

    row = service.ensure_task_row(
        external_task_id="task-0-director",
        subject="实现账单导出接口",
        description="生成导出模块并补充测试",
        metadata={"scope": "src/billing, tests/"},
    )

    assert str(row["status"]) == "pending"
    assert str(row["metadata"]["external_task_id"]) == "task-0-director"

    claim = service.claim_execution(
        row["id"],
        worker_id="director",
        role_id="director",
        run_id="run-materialized",
        selection_source="materialized_orchestration_task",
        external_task_id="task-0-director",
    )

    assert claim["success"] is True
    claimed_task = claim["task"]
    assert claimed_task["status"] == "in_progress"
    assert claimed_task["claimed_by"] == "director"
    assert claimed_task["workflow_run_id"] == "run-materialized"
    assert str(claim["session"]["session_id"])


def test_ensure_task_row_reuses_fact_overlaid_row_when_raw_row_is_stale(tmp_path: Path) -> None:
    """``ensure_task_row`` must dedupe through the observable read model.

    A raw file row can still be ``pending`` while the latest execution fact
    projects the task as terminal and carries fresher metadata. The ensure
    path must return the fact-overlaid row, not the stale raw TaskBoard row,
    and it must not create a duplicate materialized task.
    """

    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = TaskRuntimeService(str(workspace))
    external_id = "TASK-ENSURE-OVERLAY"

    created = service.create_task_row(
        subject="raw stale ensure row",
        description="file row stays pending while fact overlays completion",
        metadata={"external_task_id": external_id, "owner": "raw"},
    )
    created_id = int(created["id"])

    append_fact_event(
        AppendFactEventCommandV1(
            workspace=str(workspace),
            stream="task_runtime.execution",
            event_type="completed",
            source="runtime.task_runtime",
            task_id=str(created_id),
            run_id="run-ensure-overlaid",
            payload={
                "task_id": str(created_id),
                "run_id": "run-ensure-overlaid",
                "event_type": "completed",
                "status": "completed",
                "execution_state": "completed",
                "task_row_snapshot": {
                    "id": created_id,
                    "task_id": str(created_id),
                    "subject": "fact-overlaid ensure row",
                    "description": "latest execution fact owns the read model",
                    "priority": "HIGH",
                    "metadata": {
                        "external_task_id": external_id,
                        "source_task_id": external_id,
                        "owner": "fact",
                        "source": "task_runtime.row_snapshot",
                    },
                },
            },
        )
    )

    on_disk = json.loads(_task_file_path(workspace, created_id).read_text(encoding="utf-8"))
    assert on_disk["status"] == "pending"
    assert on_disk["metadata"]["owner"] == "raw"

    row = service.ensure_task_row(
        external_task_id=external_id,
        subject="duplicate should not be created",
        metadata={"external_task_id": external_id, "owner": "new"},
    )

    assert row["id"] == created_id
    assert row["status"] == "completed"
    assert row["subject"] == "fact-overlaid ensure row"
    assert row["metadata"]["external_task_id"] == external_id
    assert row["metadata"]["source_task_id"] == external_id
    assert row["metadata"]["owner"] == "fact"
    assert row["metadata"]["source"] == "task_runtime.execution_fact"
    assert row["metadata"]["previous_status"] == "pending"
    assert [file_row["id"] for file_row in service.list_task_rows()] == [created_id]


def test_ensure_task_row_reuses_observable_external_id_when_raw_metadata_lacks_it(tmp_path: Path) -> None:
    """External-id dedupe must also work when only the fact overlay carries it."""

    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = TaskRuntimeService(str(workspace))
    external_id = "TASK-ENSURE-FACT-ONLY-ID"

    created = service.create_task_row(
        subject="raw row without external id",
        description="external id is introduced by the execution fact snapshot",
        metadata={"owner": "raw"},
    )
    created_id = int(created["id"])

    append_fact_event(
        AppendFactEventCommandV1(
            workspace=str(workspace),
            stream="task_runtime.execution",
            event_type="claimed",
            source="runtime.task_runtime",
            task_id=str(created_id),
            run_id="run-ensure-fact-external-id",
            payload={
                "task_id": str(created_id),
                "run_id": "run-ensure-fact-external-id",
                "event_type": "claimed",
                "status": "in_progress",
                "execution_state": "in_progress",
                "session_id": "session-ensure-fact-external-id",
                "task_row_snapshot": {
                    "id": created_id,
                    "task_id": str(created_id),
                    "subject": "observable external id row",
                    "description": "fact snapshot carries the external id",
                    "priority": "HIGH",
                    "metadata": {
                        "external_task_id": external_id,
                        "source_task_id": external_id,
                        "owner": "fact",
                        "source": "task_runtime.row_snapshot",
                    },
                },
            },
        )
    )

    on_disk = json.loads(_task_file_path(workspace, created_id).read_text(encoding="utf-8"))
    assert "external_task_id" not in on_disk["metadata"]

    row = service.ensure_task_row(
        external_task_id=external_id,
        subject="new row must not be materialized",
        metadata={"external_task_id": external_id, "owner": "new"},
    )

    assert row["id"] == created_id
    assert row["status"] == "in_progress"
    assert row["subject"] == "observable external id row"
    assert row["metadata"]["external_task_id"] == external_id
    assert row["metadata"]["source_task_id"] == external_id
    assert row["metadata"]["owner"] == "fact"
    assert row["metadata"]["source"] == "task_runtime.execution_fact"
    assert [file_row["id"] for file_row in service.list_task_rows()] == [created_id]


def test_ensure_task_row_reports_materialized_event_append_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = TaskRuntimeService(str(workspace))
    append_count = 0
    original_append_execution_fact = service._append_execution_fact_with_cas

    def fail_materialized_append(
        *,
        event_type_str: str,
        payload: dict[str, Any],
    ) -> object:
        nonlocal append_count
        append_count += 1
        if event_type_str == "materialized":
            raise RuntimeError("fact stream unavailable")
        return original_append_execution_fact(event_type_str=event_type_str, payload=payload)

    monkeypatch.setattr(service, "_append_execution_fact_with_cas", fail_materialized_append)

    row = service.ensure_task_row(
        external_task_id="task-0-director",
        subject="实现账单导出接口",
        description="生成导出模块并补充测试",
        metadata={"scope": "src/billing, tests/"},
    )

    assert append_count >= 2
    task_id = int(row["id"])
    assert row["status"] == "pending"
    _assert_execution_event_append_failure_with_row_write_receipt(
        row["execution_event"],
        event_type="materialized",
        task_id=task_id,
        task_path=_task_file_path(workspace, task_id),
    )
    assert len(row["execution_events"]) == 2
    assert row["execution_events"][0]["ok"] is True
    assert row["execution_events"][0]["event_type"] == "created"
    assert row["execution_events"][1] == row["execution_event"]


def test_task_runtime_external_task_id_does_not_collide_with_numeric_row(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = TaskRuntimeService(str(workspace))

    stale = service.create_task_row(subject="stale", description="old row")
    stale_id = stale["id"]
    claimed_stale = service.claim_execution(
        stale_id,
        worker_id="test",
        role_id="director",
        selection_source="unit",
    )
    assert claimed_stale["success"] is True
    service.complete_execution(
        stale_id,
        session_id=str(claimed_stale["session"]["session_id"]),
        result_summary="old row",
        metadata={"previous_run": "old"},
    )

    row = service.ensure_task_row(
        external_task_id="TASK-1",
        subject="current PM task",
        metadata={"pm_task_id": "TASK-1"},
    )

    assert row["id"] != stale_id
    external_lookup = service.get_task("TASK-1")
    assert external_lookup is not None
    assert external_lookup["id"] == row["id"]
    stale_lookup = service.get_task(f"task-{stale_id}")
    assert stale_lookup is not None
    assert stale_lookup["id"] == stale_id


def test_task_runtime_service_surfaces_resumable_task_and_reclaims_it(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = TaskRuntimeService(str(workspace))

    created = service.create_task_row(
        subject="实现账单模型",
        description="补齐数据模型和测试",
        metadata={"scope": "src/billing, tests/"},
    )
    created_id = created["id"]

    first_claim = service.claim_execution(
        created_id,
        worker_id="director",
        role_id="director",
        run_id="run-1",
        selection_source="task_id_lookup",
    )
    assert first_claim["success"] is True

    suspended = service.suspend_execution(
        created_id,
        session_id=str(first_claim["session"]["session_id"]),
        reason="director_execution_cancelled",
    )
    assert suspended["success"] is True
    assert suspended["task"]["status"] == "pending"
    assert suspended["task"]["resume_state"] == "resumable"

    selected = service.select_next_task(prefer_resumable=True)
    assert isinstance(selected, dict)
    assert int(selected["id"]) == int(created_id)
    assert selected["resume_state"] == "resumable"

    resumed = service.claim_execution(
        created_id,
        worker_id="director",
        role_id="director",
        run_id="run-2",
        selection_source="resumable_queue_fallback",
    )
    assert resumed["success"] is True
    assert resumed["resumed"] is True
    assert resumed["task"]["status"] == "in_progress"
    assert resumed["task"]["resume_state"] == "resumed"

    completed = service.complete_execution(
        created_id,
        session_id=str(resumed["session"]["session_id"]),
        result_summary="implemented billing model",
    )
    assert completed["success"] is True
    assert completed["task"]["status"] == "completed"


def test_task_runtime_service_suspends_active_sessions_for_cancelled_run(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = TaskRuntimeService(str(workspace))

    cancelled_task = service.create_task_row(subject="cancelled run task")
    cancelled_task_id = cancelled_task["id"]
    other_task = service.create_task_row(subject="other run task")
    other_task_id = other_task["id"]
    cancelled_claim = service.claim_execution(
        cancelled_task_id,
        worker_id="director",
        role_id="director",
        run_id="director-cancelled",
        selection_source="task_id_lookup",
    )
    other_claim = service.claim_execution(
        other_task_id,
        worker_id="director",
        role_id="director",
        run_id="director-other",
        selection_source="task_id_lookup",
    )
    assert cancelled_claim["success"] is True
    assert other_claim["success"] is True

    suspended = service.suspend_active_executions_for_run(
        "director-cancelled",
        reason="factory_stage_timeout",
        metadata={"cancelled_by": "unit-test"},
    )

    assert suspended["success"] is True
    assert suspended["reason"] == "suspended"
    assert suspended["suspended_count"] == 1
    assert suspended["task_ids"] == [str(cancelled_task_id)]
    assert len(suspended["execution_events"]) == 1
    assert suspended["execution_events"][0]["ok"] is True
    assert suspended["execution_events"][0]["event_type"] == "suspended"

    cancelled_heartbeat = service.heartbeat_execution(
        cancelled_task_id,
        session_id=str(cancelled_claim["session"]["session_id"]),
    )
    assert cancelled_heartbeat["success"] is False
    assert cancelled_heartbeat["reason"] == "session_not_active"
    cancelled_row = service.get_task(cancelled_task_id)
    assert cancelled_row is not None
    assert cancelled_row["status"] == "pending"
    assert cancelled_row["resume_state"] == "resumable"
    assert cancelled_row["metadata"]["cancellation_reason"] == "factory_stage_timeout"

    other_heartbeat = service.heartbeat_execution(
        other_task_id,
        session_id=str(other_claim["session"]["session_id"]),
    )
    assert other_heartbeat["success"] is True
    assert other_heartbeat["execution_event"]["ok"] is True
    assert other_heartbeat["execution_event"]["event_type"] == "heartbeat_renewed"


def test_heartbeat_execution_fails_closed_on_event_append_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = TaskRuntimeService(str(workspace))
    created = service.create_task_row(subject="heartbeat append evidence")
    created_id = created["id"]
    task_id = int(created_id)
    claimed = service.claim_execution(
        created_id,
        worker_id="director",
        role_id="director",
        run_id="run-heartbeat-append-failure",
        selection_source="unit",
    )
    assert claimed["success"] is True

    monkeypatch.setattr(service, "_append_execution_fact_with_cas", _raise_fact_stream_unavailable)

    heartbeat = service.heartbeat_execution(
        created_id,
        session_id=str(claimed["session"]["session_id"]),
        context_summary="renew lease after tool dispatch",
    )

    assert heartbeat["success"] is False
    assert heartbeat["reason"] == "execution_event_append_failed"
    assert heartbeat["requested_reason"] == "heartbeat_renewed"
    assert heartbeat["failure_class"] == "ledger_append_failed"
    assert heartbeat["state_mutation_applied"] is True
    _assert_execution_event_append_failure_with_row_write_receipt(
        heartbeat["execution_event"],
        event_type="heartbeat_renewed",
        task_id=task_id,
        task_path=_task_file_path(workspace, task_id),
    )


def test_suspend_active_executions_for_run_fails_closed_on_event_append_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = TaskRuntimeService(str(workspace))
    created = service.create_task_row(subject="cancel with append evidence")
    created_id = created["id"]
    task_id = int(created_id)
    claimed = service.claim_execution(
        created_id,
        worker_id="director",
        role_id="director",
        run_id="run-cancel-append-failure",
        selection_source="unit",
    )
    assert claimed["success"] is True

    monkeypatch.setattr(service, "_append_execution_fact_with_cas", _raise_fact_stream_unavailable)

    suspended = service.suspend_active_executions_for_run(
        "run-cancel-append-failure",
        reason="factory_stage_timeout",
    )

    assert suspended["success"] is False
    assert suspended["reason"] == "execution_event_append_failed"
    assert suspended["failure_class"] == "ledger_append_failed"
    assert suspended["suspended_count"] == 1
    assert suspended["failed"] == [
        {
            "reason": "execution_event_append_failed",
            "failure_class": "ledger_append_failed",
            "event_type": "suspended",
            "error": "fact stream unavailable",
        }
    ]
    execution_events = suspended["execution_events"]
    assert len(execution_events) == 1
    _assert_execution_event_append_failure_with_row_write_receipt(
        execution_events[0],
        event_type="suspended",
        task_id=task_id,
        task_path=_task_file_path(workspace, task_id),
    )


def test_task_runtime_service_persists_sessions_under_canonical_task_namespace(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = TaskRuntimeService(str(workspace))

    created = service.create_task_row(
        subject="persist task session canonically",
        description="ensure runtime/tasks owns both rows and sessions",
    )
    created_id = created["id"]

    claimed = service.claim_execution(
        created_id,
        worker_id="director",
        role_id="director",
        run_id="run-canonical-session",
        selection_source="task_id_lookup",
    )
    assert claimed["success"] is True

    canonical_path = f"runtime/tasks/task_{created_id}.session.json"
    legacy_path = f"runtime/tasks/sessions/task_{created_id}.session.json"

    assert service._kernel_fs.exists(canonical_path)
    assert not service._kernel_fs.exists(legacy_path)


def test_task_runtime_service_ignores_corrupt_session_snapshot(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = TaskRuntimeService(str(workspace))

    created = service.create_task_row(subject="recover corrupt session")
    created_id = created["id"]
    service._kernel_fs.write_json_atomic(
        f"runtime/tasks/task_{created_id}.session.json",
        {
            "session_id": "tx-corrupt",
            "task_id": created_id,
            "role_id": "director",
            "status": "active",
            "lease_expires_at": "2026-01-01T00:02:00+00:00",
        },
        indent=2,
        ensure_ascii=False,
    )

    with caplog.at_level("WARNING"):
        claimed = service.claim_execution(
            created_id,
            worker_id="director",
            role_id="director",
            run_id="run-recovered",
            selection_source="task_id_lookup",
        )

    assert claimed["success"] is True
    assert claimed["reason"] == "claimed"
    assert claimed["session"]["task_id"] == created_id
    assert claimed["session"]["session_id"] != "tx-corrupt"
    assert "Failed to parse task runtime session" in caplog.text
    assert "worker_id" in caplog.text


def test_task_runtime_service_writes_sessions_atomically(tmp_path: Path, monkeypatch) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = TaskRuntimeService(str(workspace))

    created = service.create_task_row(
        subject="persist task session atomically",
        description="session readers must never observe partial JSON writes",
    )
    created_id = created["id"]

    atomic_calls: list[tuple[str, object, int, bool]] = []
    original_atomic_write = service._kernel_fs.write_json_atomic

    def write_json_atomic_spy(
        logical_path: str,
        payload: object,
        *,
        indent: int = 2,
        ensure_ascii: bool = False,
    ) -> object:
        atomic_calls.append((logical_path, payload, indent, ensure_ascii))
        return original_atomic_write(
            logical_path,
            payload,
            indent=indent,
            ensure_ascii=ensure_ascii,
        )

    def reject_non_atomic_session_write(*args: object, **kwargs: object) -> object:
        raise AssertionError("task runtime sessions must use write_json_atomic")

    monkeypatch.setattr(service._kernel_fs, "write_json_atomic", write_json_atomic_spy)
    monkeypatch.setattr(service._kernel_fs, "write_json", reject_non_atomic_session_write)

    claimed = service.claim_execution(
        created_id,
        worker_id="director",
        role_id="director",
        run_id="run-atomic-session",
        selection_source="task_id_lookup",
    )

    assert claimed["success"] is True
    assert atomic_calls
    logical_path, payload, indent, ensure_ascii = atomic_calls[-1]
    assert logical_path == f"runtime/tasks/task_{created_id}.session.json"
    assert isinstance(payload, dict)
    assert indent == 2
    assert ensure_ascii is False


def test_task_runtime_service_emits_execution_events_via_fact_stream(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = TaskRuntimeService(str(workspace))

    created = service.create_task_row(
        subject="emit execution event",
        description="verify task_runtime.execution stream",
    )
    created_id = created["id"]
    created_after_hash = _sha256_utf8_file(_task_file_path(workspace, created_id))
    claimed = service.claim_execution(
        created_id,
        worker_id="director",
        role_id="director",
        run_id="run-fact-stream",
        selection_source="task_id_lookup",
    )
    assert claimed["success"] is True

    completed = service.complete_execution(
        created_id,
        session_id=str(claimed["session"]["session_id"]),
        result_summary="done",
    )
    assert completed["success"] is True

    event_path = Path(resolve_runtime_path(str(workspace), "runtime/events/task_runtime.execution.jsonl"))
    assert event_path.is_file()
    content = event_path.read_text(encoding="utf-8")
    assert '"stream":"task_runtime.execution"' in content
    assert '"event_type":"created"' in content
    assert '"event_type":"completed"' in content

    events = query_fact_events(QueryFactEventsV1(workspace=str(workspace), stream="task_runtime.execution")).events
    event_types = [str(event.get("event_type") or "") for event in events]
    assert event_types[:3] == ["created", "claimed", "completed"]
    created_event = next(event for event in events if event.get("event_type") == "created")
    assert created_event["payload"]["execution_state"] == "pending"
    created_details = created_event["payload"]["details"]
    assert created_details["source"] == "runtime.task_runtime.create"
    created_receipt = _assert_execution_event_row_write_receipt(
        created_event["payload"],
        task_id=created_id,
        task_path=_task_file_path(workspace, created_id),
    )
    assert created_receipt["after_hash"] == created_after_hash

    completed_event = next(event for event in events if event.get("event_type") == "completed")
    payload = completed_event["payload"]
    assert payload["execution_state"] == "completed"
    assert payload["attempt"] == 1
    assert payload["resume_count"] == 0
    assert payload["last_result_summary"] == "done"
    assert payload["lease_expires_at"]


def test_task_runtime_execution_event_append_uses_fact_stream_expected_seq(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    expected_seq_values: list[int | None] = []
    real_append_fact_event = service_module.append_fact_event

    def recording_append(command: AppendFactEventCommandV1) -> object:
        expected_seq_values.append(command.expected_seq)
        return real_append_fact_event(command)

    monkeypatch.setattr(service_module, "append_fact_event", recording_append)
    service = TaskRuntimeService(str(workspace))

    created = service.create_task_row(subject="expected seq event")
    created_id = created["id"]
    claimed = service.claim_execution(
        created_id,
        worker_id="director",
        role_id="director",
        run_id="run-expected-seq",
        selection_source="task_id_lookup",
    )
    assert claimed["success"] is True
    completed = service.complete_execution(
        created_id,
        session_id=str(claimed["session"]["session_id"]),
        result_summary="done",
    )
    assert completed["success"] is True

    assert expected_seq_values[:3] == [1, 2, 3]
    events = query_fact_events(QueryFactEventsV1(workspace=str(workspace), stream="task_runtime.execution")).events
    assert [int(event["seq"]) for event in events[:3]] == [1, 2, 3]


def test_task_runtime_execution_event_append_retries_expected_seq_drift(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    real_append_fact_event = service_module.append_fact_event
    expected_seq_values: list[int | None] = []
    injected_race = False

    def racing_append(command: AppendFactEventCommandV1) -> object:
        nonlocal injected_race
        expected_seq_values.append(command.expected_seq)
        if not injected_race:
            injected_race = True
            real_append_fact_event(
                AppendFactEventCommandV1(
                    workspace=str(workspace),
                    stream="task_runtime.execution",
                    event_type="external_concurrent",
                    payload={
                        "event_type": "external_concurrent",
                        "task_id": "external",
                        "status": "in_progress",
                    },
                    source="test.concurrent_writer",
                    expected_seq=command.expected_seq,
                )
            )
            raise FactStreamError(
                "simulated expected_seq drift",
                code="expected_seq_drift",
                details={"expected_seq": command.expected_seq},
            )
        return real_append_fact_event(command)

    monkeypatch.setattr(service_module, "append_fact_event", racing_append)
    service = TaskRuntimeService(str(workspace))

    created = service.create_task_row(subject="expected seq drift retry")

    assert created["id"] == 1
    assert expected_seq_values[:2] == [1, 2]
    events = query_fact_events(QueryFactEventsV1(workspace=str(workspace), stream="task_runtime.execution")).events
    assert [str(event.get("event_type") or "") for event in events[:2]] == [
        "external_concurrent",
        "created",
    ]
    assert [int(event["seq"]) for event in events[:2]] == [1, 2]


def test_task_runtime_update_and_reopen_emit_execution_events(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = TaskRuntimeService(str(workspace))

    created = service.create_task_row(subject="emit row update events")
    created_id = created["id"]
    child = service.create_task_row(subject="dependent row", blocked_by=[int(created_id)])
    child_id = int(child["id"])
    updated = service.update_task_row(created_id, metadata={"qa": "failed"})
    assert updated is not None
    claimed = service.claim_execution(
        created_id,
        worker_id="test",
        role_id="director",
        selection_source="unit",
    )
    assert claimed["success"] is True
    completed = service.complete_execution(
        created_id,
        session_id=str(claimed["session"]["session_id"]),
        result_summary="done",
    )
    assert completed["success"] is True
    unblocked_child = service.get_task(child_id)
    assert unblocked_child is not None
    assert unblocked_child["status"] == "pending"
    assert unblocked_child["blocked_by"] == []
    reopened = service.reopen_task_row(created_id, reason="qa_rework")
    assert reopened is not None

    events = query_fact_events(QueryFactEventsV1(workspace=str(workspace), stream="task_runtime.execution")).events
    event_types = [str(event.get("event_type") or "") for event in events]
    assert "updated" in event_types
    assert "reopened" in event_types
    assert "downstream_dependency_reblocked" in event_types

    updated_event = next(event for event in events if event.get("event_type") == "updated")
    assert updated_event["payload"]["status"] == "pending"
    assert updated_event["payload"]["details"]["metadata_updated"] is True

    reopened_event = next(event for event in events if event.get("event_type") == "reopened")
    assert reopened_event["payload"]["details"]["reason"] == "qa_rework"
    assert reopened_event["payload"]["execution_state"] == "pending"

    reblocked_event = next(event for event in events if event.get("event_type") == "downstream_dependency_reblocked")
    assert reblocked_event["payload"]["task_row_snapshot"]["id"] == child_id
    assert reblocked_event["payload"]["details"]["reopened_task_id"] == int(created_id)
    assert reblocked_event["payload"]["details"]["active_blockers"] == [int(created_id)]
    child_after = service.get_task(child_id)
    assert child_after is not None
    assert child_after["status"] == "blocked"
    assert child_after["blocked_by"] == [int(created_id)]


def test_task_runtime_rework_exhaustion_failure_is_owner_transition(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = TaskRuntimeService(str(workspace))

    created = service.create_task_row(subject="qa-owned rework exhausted")
    created_id = int(created["id"])
    child = service.create_task_row(subject="dependent row", blocked_by=[created_id])
    child_id = int(child["id"])
    claimed = service.claim_execution(
        created_id,
        worker_id="director-worker",
        role_id="director",
        run_id="run-qa",
        selection_source="test",
    )
    assert claimed["success"] is True
    completed = service.complete_execution(
        created_id,
        session_id=str(claimed["session"]["session_id"]),
        result_summary="director completed",
    )
    assert completed["success"] is True

    failed = service.fail_task_row_after_rework_exhausted(
        created_id,
        reason="qa_rework_retry_exhausted",
        metadata={"qa_last_verdict": "FAIL"},
        source="qa_verdict",
    )

    assert failed is not None
    assert failed["status"] == "failed"
    assert failed["metadata"]["qa_last_verdict"] == "FAIL"
    assert failed["metadata"]["runtime_execution"]["status"] == "failed"
    assert failed["metadata"]["runtime_execution"]["last_error"] == "qa_rework_retry_exhausted"
    execution_event_types = [str(event.get("event_type") or "") for event in failed.get("execution_events", [])]
    assert execution_event_types[-2:] == ["downstream_dependency_reblocked", "failed"]

    child_after = service.get_task(child_id)
    assert child_after is not None
    assert child_after["status"] == "blocked"
    assert child_after["blocked_by"] == [created_id]

    events = query_fact_events(QueryFactEventsV1(workspace=str(workspace), stream="task_runtime.execution")).events
    event_types = [str(event.get("event_type") or "") for event in events]
    assert "reopened" in event_types
    assert "failed" in event_types
    failed_event = events[-1]
    assert failed_event["event_type"] == "failed"
    assert failed_event["payload"]["execution_state"] == "failed"
    assert (
        failed_event["payload"]["details"].items()
        >= {
            "reason": "qa_rework_retry_exhausted",
            "source": "qa_verdict",
            "rework_exhausted": True,
        }.items()
    )
    _assert_execution_event_row_write_receipt(
        failed_event["payload"],
        task_id=created_id,
        task_path=_task_file_path(workspace, created_id),
    )


def test_task_runtime_dedup_cancel_is_owner_transition(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = TaskRuntimeService(str(workspace))

    primary = service.create_task_row(subject="primary task")
    duplicate = service.create_task_row(subject="duplicate task")
    duplicate_id = int(duplicate["id"])
    duplicate_task = service._board.get(duplicate_id)
    assert duplicate_task is not None
    helper_calls: list[object] = []
    direct_board_get_calls: list[object] = []

    def task_entity_for_owner_terminal_transition(task_id: object) -> tuple[int | None, Any | None]:
        helper_calls.append(task_id)
        return service.normalize_task_id(task_id), duplicate_task

    def reject_direct_board_get(task_id: object) -> Any:
        direct_board_get_calls.append(task_id)
        raise AssertionError(
            "cancel_task_row_for_deduplication must read task entities through "
            "_task_entity_for_owner_terminal_transition"
        )

    monkeypatch.setattr(
        service,
        "_task_entity_for_owner_terminal_transition",
        task_entity_for_owner_terminal_transition,
    )
    monkeypatch.setattr(service._board, "get", reject_direct_board_get)

    cancelled = service.cancel_task_row_for_deduplication(
        f"task-{duplicate_id}",
        primary_task_id=primary["id"],
        reason="pm_duplicate_subject",
        metadata={"dedup_source": "pm_adapter"},
        source="pm_adapter",
    )

    assert helper_calls == [f"task-{duplicate_id}"]
    assert direct_board_get_calls == []
    assert cancelled is not None
    assert "success" not in cancelled
    assert cancelled["status"] == "cancelled"
    assert cancelled["metadata"]["dedup_merged_into"] == primary["id"]
    assert cancelled["metadata"]["dedup_reason"] == "pm_duplicate_subject"
    assert cancelled["metadata"]["dedup_source"] == "pm_adapter"
    execution_event = cancelled["execution_event"]
    assert execution_event["ok"] is True
    assert execution_event["event_type"] == "cancelled"
    assert execution_event["fact_stream"] == "task_runtime.execution"
    assert isinstance(execution_event["published"], bool)
    assert isinstance(execution_event["fact_event_seq"], int)
    assert str(execution_event["fact_event_id"]).strip()
    assert cancelled["execution_events"] == [execution_event]

    events = query_fact_events(QueryFactEventsV1(workspace=str(workspace), stream="task_runtime.execution")).events
    cancelled_event = events[-1]
    assert cancelled_event["event_type"] == "cancelled"
    assert cancelled_event["payload"]["execution_state"] == "cancelled"
    assert (
        cancelled_event["payload"]["details"].items()
        >= {
            "reason": "pm_duplicate_subject",
            "source": "pm_adapter",
            "dedup_merged_into": str(primary["id"]),
        }.items()
    )
    _assert_execution_event_row_write_receipt(
        cancelled_event["payload"],
        task_id=duplicate_id,
        task_path=_task_file_path(workspace, duplicate_id),
    )


def test_task_runtime_role_adapter_failure_is_owner_transition(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = TaskRuntimeService(str(workspace))

    created = service.create_task_row(subject="pm planning task")
    created_id = int(created["id"])
    created_task = service._board.get(created_id)
    assert created_task is not None
    helper_calls: list[object] = []
    direct_board_get_calls: list[object] = []

    def task_entity_for_owner_terminal_transition(task_id: object) -> tuple[int | None, Any | None]:
        helper_calls.append(task_id)
        return service.normalize_task_id(task_id), created_task

    def reject_direct_board_get(task_id: object) -> Any:
        direct_board_get_calls.append(task_id)
        raise AssertionError(
            "fail_task_row_from_role_adapter must read task entities through _task_entity_for_owner_terminal_transition"
        )

    monkeypatch.setattr(
        service,
        "_task_entity_for_owner_terminal_transition",
        task_entity_for_owner_terminal_transition,
    )
    monkeypatch.setattr(service._board, "get", reject_direct_board_get)

    failed = service.fail_task_row_from_role_adapter(
        f"task-{created_id}",
        reason="pm_runtime_exception",
        metadata={"pm_error": "llm kernel offline"},
        role_id="pm",
        source="pm_adapter",
        failure_class="pm_runtime_exception",
    )

    assert helper_calls == [f"task-{created_id}"]
    assert direct_board_get_calls == []
    assert failed is not None
    assert "success" not in failed
    assert failed["status"] == "failed"
    assert failed["metadata"]["pm_error"] == "llm kernel offline"
    assert failed["metadata"]["role_adapter_failure_reason"] == "pm_runtime_exception"
    assert failed["metadata"]["role_adapter_failure_role"] == "pm"
    assert failed["metadata"]["role_adapter_failure_class"] == "pm_runtime_exception"
    execution_event = failed["execution_event"]
    assert execution_event["ok"] is True
    assert execution_event["event_type"] == "failed"
    assert execution_event["fact_stream"] == "task_runtime.execution"
    assert isinstance(execution_event["published"], bool)
    assert isinstance(execution_event["fact_event_seq"], int)
    assert str(execution_event["fact_event_id"]).strip()
    assert failed["execution_events"] == [execution_event]

    events = query_fact_events(QueryFactEventsV1(workspace=str(workspace), stream="task_runtime.execution")).events
    failed_event = events[-1]
    assert failed_event["event_type"] == "failed"
    assert failed_event["payload"]["execution_state"] == "failed"
    assert (
        failed_event["payload"]["details"].items()
        >= {
            "reason": "pm_runtime_exception",
            "source": "pm_adapter",
            "role": "pm",
            "failure_class": "pm_runtime_exception",
        }.items()
    )
    _assert_execution_event_row_write_receipt(
        failed_event["payload"],
        task_id=created_id,
        task_path=_task_file_path(workspace, created_id),
    )


def test_reopen_task_row_reports_event_append_failure_without_persisting_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = TaskRuntimeService(str(workspace))
    created = service.create_task_row(subject="reopen with append evidence")
    created_id = created["id"]
    task_id = int(created_id)
    updated = service.update_task_row(created_id, metadata={"qa": "failed"})
    assert updated is not None
    claimed = service.claim_execution(
        created_id,
        worker_id="test",
        role_id="director",
        selection_source="unit",
    )
    assert claimed["success"] is True
    completed = service.complete_execution(
        created_id,
        session_id=str(claimed["session"]["session_id"]),
        result_summary="done",
    )
    assert completed["success"] is True

    monkeypatch.setattr(service, "_append_execution_fact_with_cas", _raise_fact_stream_unavailable)

    row = service.reopen_task_row(created_id, reason="qa_rework")

    assert row is not None
    assert row["status"] == "pending"
    _assert_execution_event_append_failure_with_row_write_receipt(
        row["execution_event"],
        event_type="reopened",
        task_id=task_id,
        task_path=_task_file_path(workspace, task_id),
    )
    persisted = service.get_task(row["id"])
    assert persisted is not None
    assert "execution_event" not in persisted
    assert "execution_event" not in persisted["metadata"]


def test_task_runtime_factory_event_projects_fact_stream_receipt(
    tmp_path: Path,
    monkeypatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = TaskRuntimeService(str(workspace))
    published: dict[str, object] = {}

    class Publisher:
        def publish(self, *, subject: str, payload: dict[str, object]) -> bool:
            published["subject"] = subject
            published["payload"] = payload
            return True

    import polaris.infrastructure.log_pipeline.jetstream_publisher as publisher_module

    monkeypatch.setattr(
        publisher_module,
        "get_log_jetstream_publisher",
        lambda: Publisher(),
    )

    created = service.create_task_row(
        subject="project fact receipt",
        description="factory execution event should point at the fact stream event",
        metadata={"factory_run_id": "factory_123456789abc"},
    )
    created_id = created["id"]
    published.clear()

    claimed = service.claim_execution(
        created_id,
        worker_id="director",
        role_id="director",
        run_id="director-123456789abc",
        selection_source="task_id_lookup",
    )

    assert claimed["success"] is True
    envelope = published["payload"]
    assert isinstance(envelope, dict)
    payload = envelope["payload"]
    assert isinstance(payload, dict)
    assert payload["event_type"] == "claimed"
    assert payload["execution_state"] == "in_progress"
    assert payload["attempt"] == 1
    assert payload["resume_count"] == 0
    assert payload["lease_expires_at"]
    assert payload["fact_event_id"]
    assert payload["fact_stream"] == "task_runtime.execution"
    assert payload["fact_storage_path"] == "runtime/events/task_runtime.execution.jsonl"


def test_task_runtime_execution_event_without_factory_run_is_not_published(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = TaskRuntimeService(str(workspace))

    created = service.create_task_row(subject="non factory execution event")
    task_id = int(created["id"])

    execution_event = created["execution_event"]
    assert execution_event["ok"] is True
    assert execution_event["event_type"] == "created"
    assert execution_event["fact_event_id"]
    assert execution_event["fact_stream"] == "task_runtime.execution"
    assert execution_event["published"] is False
    assert "publish_error" not in execution_event
    _assert_execution_event_row_write_receipt(
        execution_event,
        task_id=task_id,
        task_path=_task_file_path(workspace, task_id),
    )


def test_task_runtime_factory_event_publish_false_is_projected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = TaskRuntimeService(str(workspace))

    class Publisher:
        def publish(self, *, subject: str, payload: dict[str, object]) -> bool:
            return False

    import polaris.infrastructure.log_pipeline.jetstream_publisher as publisher_module

    monkeypatch.setattr(
        publisher_module,
        "get_log_jetstream_publisher",
        lambda: Publisher(),
    )

    created = service.create_task_row(
        subject="factory publisher returned false",
        metadata={"factory_run_id": "factory_123456789abc"},
    )
    task_id = int(created["id"])

    execution_event = created["execution_event"]
    assert execution_event["ok"] is True
    assert execution_event["event_type"] == "created"
    assert execution_event["fact_event_id"]
    assert execution_event["fact_stream"] == "task_runtime.execution"
    assert execution_event["published"] is False
    assert execution_event["publish_error"] == "factory_execution_event_publish_returned_false"
    _assert_execution_event_row_write_receipt(
        execution_event,
        task_id=task_id,
        task_path=_task_file_path(workspace, task_id),
    )


def test_task_runtime_factory_event_preserves_payload_director_run_id(
    tmp_path: Path,
    monkeypatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = TaskRuntimeService(str(workspace))
    published: dict[str, object] = {}

    class Publisher:
        def publish(self, *, subject: str, payload: dict[str, object]) -> bool:
            published["subject"] = subject
            published["payload"] = payload
            return True

    import polaris.infrastructure.log_pipeline.jetstream_publisher as publisher_module

    monkeypatch.setattr(
        publisher_module,
        "get_log_jetstream_publisher",
        lambda: Publisher(),
    )

    ok = service._publish_factory_execution_event(
        {
            "run_id": "director-123456789abc",
            "factory_run_id": "factory_123456789abc",
            "task_id": "task-1",
            "event_type": "completed",
            "status": "completed",
        }
    )

    assert ok is True
    envelope = published["payload"]
    assert isinstance(envelope, dict)
    assert envelope["run_id"] == "factory_123456789abc"
    assert envelope["channel"] == "event.factory:factory_123456789abc"
    payload = envelope["payload"]
    assert isinstance(payload, dict)
    assert payload["run_id"] == "director-123456789abc"
    assert payload["factory_run_id"] == "factory_123456789abc"
    assert payload["director_run_id"] == "director-123456789abc"


def test_create_task_row_projects_fact_event_seq_matching_fact_stream(tmp_path: Path) -> None:
    """``create_task_row`` must project a positive ``fact_event_seq`` that matches the
    seq stored in the fact stream entry. The seq must NOT be fabricated on
    the failure path.
    """
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = TaskRuntimeService(str(workspace))

    row = service.create_task_row(subject="project fact_event_seq")

    execution_event = row["execution_event"]
    assert isinstance(execution_event, dict)
    assert execution_event["ok"] is True
    assert execution_event["event_type"] == "created"
    assert isinstance(execution_event.get("fact_event_seq"), int)
    assert execution_event["fact_event_seq"] >= 1

    events = query_fact_events(QueryFactEventsV1(workspace=str(workspace), stream="task_runtime.execution")).events
    created_event = next(event for event in events if event.get("event_type") == "created")
    assert int(created_event["seq"]) == execution_event["fact_event_seq"]


def test_claim_and_complete_execution_projects_fact_event_seq_consistently(tmp_path: Path) -> None:
    """Claim + complete must publish execution_event.fact_event_seq that is consistent
    with the ``query_fact_events`` seq for the same stream event.
    """
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = TaskRuntimeService(str(workspace))

    created = service.create_task_row(subject="seq claim+complete")
    created_id = int(created["id"])

    claimed = service.claim_execution(
        created_id,
        worker_id="director",
        role_id="director",
        run_id="run-seq-claim",
        selection_source="unit",
    )
    assert claimed["success"] is True
    claim_event = claimed["execution_event"]
    assert claim_event["ok"] is True
    assert claim_event["event_type"] == "claimed"
    assert isinstance(claim_event.get("fact_event_seq"), int)
    assert claim_event["fact_event_seq"] >= 1

    completed = service.complete_execution(
        created_id,
        session_id=str(claimed["session"]["session_id"]),
        result_summary="done",
    )
    assert completed["success"] is True
    completed_event = completed["execution_event"]
    assert completed_event["ok"] is True
    assert completed_event["event_type"] == "completed"
    assert isinstance(completed_event.get("fact_event_seq"), int)
    assert completed_event["fact_event_seq"] > claim_event["fact_event_seq"]

    events = query_fact_events(QueryFactEventsV1(workspace=str(workspace), stream="task_runtime.execution")).events
    seq_by_type: dict[str, int] = {}
    for event in events:
        event_type = str(event.get("event_type") or "")
        seq_by_type[event_type] = int(event["seq"])

    assert seq_by_type["created"] == int(created["execution_event"]["fact_event_seq"])
    assert seq_by_type["claimed"] == int(claim_event["fact_event_seq"])
    assert seq_by_type["completed"] == int(completed_event["fact_event_seq"])


def test_execution_event_does_not_fabricate_fact_event_seq_on_append_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed execution fact append must omit ``fact_event_seq`` from the
    public ``execution_event`` projection so consumers cannot latch onto a
    phantom seq.
    """
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = TaskRuntimeService(str(workspace))

    monkeypatch.setattr(service, "_append_execution_fact_with_cas", _raise_fact_stream_unavailable)

    row = service.create_task_row(subject="fail append seq projection")
    task_id = int(row["id"])
    execution_event = row["execution_event"]
    assert isinstance(execution_event, dict)
    assert execution_event["ok"] is False
    assert execution_event["event_type"] == "created"
    assert "fact_event_seq" not in execution_event
    _assert_execution_event_append_failure_with_row_write_receipt(
        execution_event,
        event_type="created",
        task_id=task_id,
        task_path=_task_file_path(workspace, task_id),
    )


def test_execution_event_does_not_fabricate_fact_event_seq_on_publish_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If ``_publish_factory_execution_event`` raises after a successful append,
    the public ``execution_event`` still exposes ``fact_event_seq`` because the
    seq was already allocated by the fact stream; the helper projects it as
    positive evidence regardless of publish path so the failure shape remains
    transparent.
    """
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = TaskRuntimeService(str(workspace))

    def fail_publish(_payload: dict[str, object]) -> bool:
        raise RuntimeError("publish down")

    monkeypatch.setattr(service, "_publish_factory_execution_event", fail_publish)

    row = service.create_task_row(subject="publish-failure seq")
    task_id = int(row["id"])
    execution_event = row["execution_event"]
    assert isinstance(execution_event, dict)
    assert execution_event["published"] is False
    assert execution_event["publish_error"] == "publish down"
    # The fact stream accepted the event even if publish failed, so fact_event_seq
    # is projected. The publish_error/published fields carry the honest verdict.
    assert isinstance(execution_event.get("fact_event_seq"), int)
    assert execution_event["fact_event_seq"] >= 1
    _assert_execution_event_row_write_receipt(
        execution_event,
        task_id=task_id,
        task_path=_task_file_path(workspace, task_id),
    )


def test_execution_event_omits_fact_event_seq_when_appended_seq_is_none(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If ``FactEventAppendedV1.appended_seq`` is ``None`` (e.g. future idempotent
    hits that opt out of CAS), the public projection must still omit
    ``fact_event_seq`` rather than emit a fabricated ``0`` or ``-1``.
    """
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = TaskRuntimeService(str(workspace))
    workspace_path = str(workspace)

    def _make_appended() -> Any:
        class _Appended:
            event_id = "evt-no-seq"
            workspace = workspace_path
            stream = "task_runtime.execution"
            storage_path = "runtime/events/task_runtime.execution.jsonl"
            appended_at = "2026-01-01T00:00:00+00:00"
            appended_seq = None

        return _Appended()

    def append_no_seq(_command: object) -> Any:
        return _make_appended()

    monkeypatch.setattr(service_module, "append_fact_event", append_no_seq)

    row = service.create_task_row(subject="no-seq append")
    execution_event = row["execution_event"]
    assert isinstance(execution_event, dict)
    assert execution_event["ok"] is True
    assert "fact_event_seq" not in execution_event


def test_list_task_rows_from_execution_facts_projects_fact_event_seq_matching_event_seq(
    tmp_path: Path,
) -> None:
    """``list_task_rows_from_execution_facts`` must copy the queried Fact Stream
    event wrapper ``seq`` onto the projected row as ``fact_event_seq`` when the
    payload lacks a valid positive seq, and the value must match the queried
    event's seq exactly. The seq must never be fabricated.
    """

    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = TaskRuntimeService(str(workspace))

    append_fact_event(
        AppendFactEventCommandV1(
            workspace=str(workspace),
            stream="task_runtime.execution",
            event_type="claimed",
            source="runtime.task_runtime",
            task_id="TASK-SEQ",
            run_id="run-seq-read",
            payload={
                "task_id": "TASK-SEQ",
                "event_type": "claimed",
                "status": "in_progress",
                "execution_state": "in_progress",
                "session_id": "session-seq",
                "task_row_snapshot": {
                    "id": "TASK-SEQ",
                    "task_id": "TASK-SEQ",
                    "subject": "fact-derived row",
                },
            },
        )
    )

    rows = service.list_task_rows_from_execution_facts()
    assert len(rows) == 1
    row = rows[0]

    # Top-level fact_event_seq must be projected and must match the wrapper
    # seq returned by the FactStream query — proving the read-side copy is
    # sourced from the event envelope, not fabricated.
    events = query_fact_events(QueryFactEventsV1(workspace=str(workspace), stream="task_runtime.execution")).events
    seq_by_task: dict[str, int] = {}
    for event in events:
        task_id = str(event.get("task_id") or "").strip()
        if task_id:
            seq_by_task[task_id] = int(event["seq"])

    assert row["fact_event_seq"] == seq_by_task["TASK-SEQ"]
    assert isinstance(row["fact_event_seq"], int)
    assert row["fact_event_seq"] >= 1


def test_list_task_rows_from_execution_facts_preserves_payload_fact_event_seq(tmp_path: Path) -> None:
    """When the persisted fact payload already carries a valid positive
    ``fact_event_seq``, the read model must keep that value rather than
    overwrite it with the wrapper seq.
    """

    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = TaskRuntimeService(str(workspace))

    append_fact_event(
        AppendFactEventCommandV1(
            workspace=str(workspace),
            stream="task_runtime.execution",
            event_type="claimed",
            source="runtime.task_runtime",
            task_id="TASK-PRESET",
            run_id="run-preset",
            payload={
                "task_id": "TASK-PRESET",
                "event_type": "claimed",
                "status": "in_progress",
                "execution_state": "in_progress",
                "session_id": "session-preset",
                "fact_event_seq": 999,
                "task_row_snapshot": {
                    "id": "TASK-PRESET",
                    "task_id": "TASK-PRESET",
                    "subject": "preset seq row",
                },
            },
        )
    )

    rows = service.list_task_rows_from_execution_facts()
    assert len(rows) == 1
    assert rows[0]["fact_event_seq"] == 999


def test_list_task_rows_from_execution_facts_uses_latest_fact_window(tmp_path: Path) -> None:
    """When the fact stream has more events than the requested window, the read
    model must project the latest window, not the earliest one.

    Otherwise a long-running task can keep showing a stale status even though
    later ``task_runtime.execution`` facts are the authoritative state source.
    """

    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = TaskRuntimeService(str(workspace))

    for event_type, status in (
        ("created", "pending"),
        ("claimed", "in_progress"),
        ("completed", "completed"),
    ):
        append_fact_event(
            AppendFactEventCommandV1(
                workspace=str(workspace),
                stream="task_runtime.execution",
                event_type=event_type,
                source="runtime.task_runtime",
                task_id="TASK-WINDOW",
                run_id="run-window",
                payload={
                    "task_id": "TASK-WINDOW",
                    "event_type": event_type,
                    "status": status,
                    "execution_state": status,
                    "task_row_snapshot": {
                        "id": "TASK-WINDOW",
                        "task_id": "TASK-WINDOW",
                        "subject": "latest window row",
                    },
                },
            )
        )

    rows = service.list_task_rows_from_execution_facts(limit=2)
    assert len(rows) == 1
    assert rows[0]["status"] == "completed"
    assert rows[0]["execution_state"] == "completed"
    assert rows[0]["fact_event_seq"] == 3


def test_list_task_rows_from_execution_facts_omits_fact_event_seq_when_wrapper_seq_invalid(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When both the payload ``fact_event_seq`` and the wrapper ``seq`` are
    missing/invalid, the projected row must NOT fabricate a seq field.
    """

    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = TaskRuntimeService(str(workspace))

    append_fact_event(
        AppendFactEventCommandV1(
            workspace=str(workspace),
            stream="task_runtime.execution",
            event_type="claimed",
            source="runtime.task_runtime",
            task_id="TASK-INVALID",
            run_id="run-invalid",
            payload={
                "task_id": "TASK-INVALID",
                "event_type": "claimed",
                "status": "in_progress",
                "execution_state": "in_progress",
                "task_row_snapshot": {
                    "id": "TASK-INVALID",
                    "task_id": "TASK-INVALID",
                    "subject": "invalid seq row",
                },
            },
        )
    )

    events = list(
        query_fact_events(QueryFactEventsV1(workspace=str(workspace), stream="task_runtime.execution")).events
    )
    assert events, "fact stream must contain the appended event"

    # Strip seq/fact_event_seq from the queried event to simulate an event
    # record that has no seq evidence to copy.
    def fake_query_fact_events(query: QueryFactEventsV1) -> Any:
        result = original_query_fact_events(query)
        scrubbed: list[dict[str, object]] = []
        for event in result.events:
            stripped = dict(event)
            stripped.pop("seq", None)
            payload = dict(stripped.get("payload") or {})
            payload.pop("fact_event_seq", None)
            stripped["payload"] = payload
            scrubbed.append(stripped)
        return type(result)(
            workspace=result.workspace,
            stream=result.stream,
            events=tuple(scrubbed),
            total=result.total,
            next_offset=result.next_offset,
        )

    original_query_fact_events = service_module.query_fact_events
    monkeypatch.setattr(service_module, "query_fact_events", fake_query_fact_events)

    rows = service.list_task_rows_from_execution_facts()
    assert len(rows) == 1
    assert "fact_event_seq" not in rows[0]


def test_list_observable_task_rows_preserves_fact_event_seq_overlay(tmp_path: Path) -> None:
    """The observable overlay must keep the fact-derived ``fact_event_seq``
    field visible on the merged row, matching the queried event wrapper seq
    for the latest event and never dropping it during the file-row overlay
    merge.
    """

    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = TaskRuntimeService(str(workspace))
    created = service.create_task_row(
        subject="Overlay preserves fact_event_seq",
        description="file row overlaid by fact row",
    )
    task_id = str(created["id"])

    append_fact_event(
        AppendFactEventCommandV1(
            workspace=str(workspace),
            stream="task_runtime.execution",
            event_type="claimed",
            source="runtime.task_runtime",
            task_id=task_id,
            run_id="run-overlay-seq",
            payload={
                "task_id": task_id,
                "event_type": "claimed",
                "status": "in_progress",
                "execution_state": "in_progress",
                "session_id": "session-overlay-seq",
                "task_row_snapshot": created,
            },
        )
    )

    rows = service.list_observable_task_rows()
    assert len(rows) == 1
    row = rows[0]

    # The overlay must carry the LATEST fact_event_seq from the fact stream —
    # the ``claimed`` event (seq=2) is later than the original ``created``
    # event (seq=1) emitted by create_task_row.
    latest_seq = max(
        int(event["seq"])
        for event in query_fact_events(
            QueryFactEventsV1(workspace=str(workspace), stream="task_runtime.execution")
        ).events
        if str(event.get("task_id") or "").strip() == task_id
    )
    assert row["fact_event_seq"] == latest_seq
    assert isinstance(row["fact_event_seq"], int)
    # Overlay must not have dropped other projection fields.
    assert row["status"] == "in_progress"
    assert row["running"] is True
    assert row["metadata"]["previous_status"] == "pending"
    assert row["metadata"]["source"] == "task_runtime.execution_fact"


def test_dependent_rows_blocked_by_reads_fact_overlaid_observable_rows(tmp_path: Path) -> None:
    """Dependency fan-out evidence must read fact-overlaid observable rows.

    The raw dependent rows intentionally stay stale with no persisted
    ``blocked_by`` relation. Only the latest ``task_runtime.execution`` fact
    snapshot declares the dependency, so a file-only implementation of
    ``_dependent_rows_blocked_by`` cannot see it.
    """

    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = TaskRuntimeService(str(workspace))

    parent = service.create_task_row(subject="observable dependency parent")
    parent_id = int(parent["id"])
    dependent = service.create_task_row(
        subject="stale dependent file row",
        description="raw row has no blocked_by; fact snapshot owns dependency",
    )
    dependent_id = int(dependent["id"])
    malformed = service.create_task_row(subject="malformed blocker snapshot")
    malformed_id = int(malformed["id"])

    raw_dependent = json.loads(_task_file_path(workspace, dependent_id).read_text(encoding="utf-8"))
    raw_malformed = json.loads(_task_file_path(workspace, malformed_id).read_text(encoding="utf-8"))
    assert raw_dependent["status"] == "pending"
    assert raw_dependent["blocked_by"] == []
    assert raw_malformed["blocked_by"] == []

    append_fact_event(
        AppendFactEventCommandV1(
            workspace=str(workspace),
            stream="task_runtime.execution",
            event_type="dependency_blocked",
            source="runtime.task_runtime",
            task_id=str(dependent_id),
            run_id="run-fact-overlaid-dependent",
            payload={
                "task_id": str(dependent_id),
                "run_id": "run-fact-overlaid-dependent",
                "event_type": "dependency_blocked",
                "status": "blocked",
                "execution_state": "blocked",
                "task_row_snapshot": {
                    "id": dependent_id,
                    "task_id": str(dependent_id),
                    "subject": "fact-overlaid dependent row",
                    "description": "observable snapshot owns blocked_by",
                    "blocked_by": [parent_id],
                    "metadata": {"source": "task_runtime.row_snapshot", "projection": "execution_fact"},
                },
            },
        )
    )
    append_fact_event(
        AppendFactEventCommandV1(
            workspace=str(workspace),
            stream="task_runtime.execution",
            event_type="dependency_blocked",
            source="runtime.task_runtime",
            task_id=str(malformed_id),
            run_id="run-fact-overlaid-malformed-blocker",
            payload={
                "task_id": str(malformed_id),
                "run_id": "run-fact-overlaid-malformed-blocker",
                "event_type": "dependency_blocked",
                "status": "blocked",
                "execution_state": "blocked",
                "task_row_snapshot": {
                    "id": malformed_id,
                    "task_id": str(malformed_id),
                    "subject": "malformed blocker row",
                    "blocked_by": {"not-a-task": parent_id},
                    "metadata": {"source": "task_runtime.row_snapshot"},
                },
            },
        )
    )

    dependent_rows = service._dependent_rows_blocked_by(parent_id)

    assert [int(row["id"]) for row in dependent_rows] == [dependent_id]
    row = dependent_rows[0]
    assert row["status"] == "blocked"
    assert row["blocked_by"] == [parent_id]
    assert row["subject"] == "fact-overlaid dependent row"
    assert row["metadata"]["source"] == "task_runtime.execution_fact"
    assert row["metadata"]["previous_status"] == "pending"
    assert row["metadata"]["projection"] == "execution_fact"

    persisted_dependent = json.loads(_task_file_path(workspace, dependent_id).read_text(encoding="utf-8"))
    assert persisted_dependent["status"] == "pending"
    assert persisted_dependent["blocked_by"] == []


# ---------------------------------------------------------------------------
# WS2 Execution Ledger SSoT convergence — selection must respect terminal facts
# ---------------------------------------------------------------------------
#
# These tests pin the contract that ``list_ready_task_rows``,
# ``select_next_task``, and ``claim_next_execution`` consult the observable
# task-runtime read model (file row overlaid with the latest
# ``task_runtime.execution`` fact) instead of relying on a stale pending file
# row alone. They fail if the selection APIs ever fall back to file-only
# state when a newer execution fact projects the task as terminal.


def _append_terminal_fact_event(
    workspace: Path,
    *,
    task_id: str,
    event_type: str,
    status: str,
    run_id: str,
) -> None:
    """Append a fact-only terminal event without mutating the file row.

    The payload deliberately omits ``task_row_snapshot`` for the row-id fact so
    the read projection still carries the file-row's id while picking up the
    terminal ``execution_state`` from the event payload itself.
    """

    append_fact_event(
        AppendFactEventCommandV1(
            workspace=str(workspace),
            stream="task_runtime.execution",
            event_type=event_type,
            source="runtime.task_runtime",
            task_id=task_id,
            run_id=run_id,
            payload={
                "task_id": task_id,
                "run_id": run_id,
                "event_type": event_type,
                "status": status,
                "execution_state": status,
            },
        )
    )


def test_file_task_rows_project_to_observable_rows_across_refresh_suspend_and_reset(
    tmp_path: Path,
) -> None:
    """Raw file-backed rows must remain the common source for public projections.

    This pins the helper extraction boundary without depending on the helper
    name: existing file rows are still visible, observable reads still overlay
    execution facts without mutating stale blockers, list_task_rows still owns
    dependency refresh, suspend still projects resumable execution state, and
    reexecution reset still walks every persisted task row.
    """

    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = TaskRuntimeService(str(workspace))

    parent = service.create_task_row(subject="file-backed parent")
    parent_id = int(parent["id"])
    child = service.create_task_row(
        subject="file-backed child",
        blocked_by=[parent_id],
    )
    child_id = int(child["id"])

    file_rows = {int(row["id"]): row for row in service.list_task_rows()}
    assert set(file_rows) == {parent_id, child_id}
    assert file_rows[parent_id]["status"] == "pending"
    assert file_rows[child_id]["status"] == "blocked"
    assert file_rows[child_id]["blocked_by"] == [parent_id]

    _append_terminal_fact_event(
        workspace,
        task_id=str(parent_id),
        event_type="completed",
        status="completed",
        run_id="run-file-row-helper-regression",
    )

    observable = {int(row["id"]): row for row in service.list_observable_task_rows()}
    assert observable[parent_id]["status"] == "completed"
    assert observable[parent_id]["metadata"]["source"] == "task_runtime.execution_fact"
    assert observable[child_id]["status"] == "blocked"
    assert observable[child_id]["blocked_by"] == [parent_id]

    persisted_child = json.loads(_task_file_path(workspace, child_id).read_text(encoding="utf-8"))
    assert persisted_child["status"] == "blocked"
    assert persisted_child["blocked_by"] == [parent_id]

    refreshed_rows = {int(row["id"]): row for row in service.list_task_rows()}
    assert refreshed_rows[child_id]["status"] == "pending"
    assert refreshed_rows[child_id]["blocked_by"] == []

    refreshed_child = json.loads(_task_file_path(workspace, child_id).read_text(encoding="utf-8"))
    assert refreshed_child["status"] == "pending"
    assert refreshed_child["blocked_by"] == []

    claimed = service.claim_execution(
        child_id,
        worker_id="director",
        role_id="director",
        run_id="run-file-row-helper-claim",
        selection_source="unit",
    )
    assert claimed["success"] is True

    suspended = service.suspend_execution(
        child_id,
        session_id=str(claimed["session"]["session_id"]),
        reason="unit_regression",
    )
    assert suspended["success"] is True
    assert suspended["task"]["status"] == "pending"
    assert suspended["task"]["resume_state"] == "resumable"
    assert _session_file_path(workspace, child_id).is_file()

    suspended_observable = {int(row["id"]): row for row in service.list_observable_task_rows()}
    assert suspended_observable[child_id]["status"] == "pending"
    assert suspended_observable[child_id]["resume_state"] == "resumable"
    assert suspended_observable[child_id]["metadata"]["source"] == "task_runtime.execution_fact"

    reset = service.reset_task_rows_for_reexecution(source="unit.file-row-helper")

    assert reset["success"] is True
    assert set(reset["reset_files"]) == {f"task_{parent_id}.json", f"task_{child_id}.json"}
    assert reset["deleted_session_files"] == [f"task_{child_id}.session.json"]
    assert not _session_file_path(workspace, child_id).exists()

    reset_rows = {int(row["id"]): row for row in service.list_observable_task_rows()}
    assert set(reset_rows) == {parent_id, child_id}
    assert reset_rows[parent_id]["status"] == "pending"
    assert reset_rows[child_id]["status"] == "pending"
    assert reset_rows[child_id]["blocked_by"] == []


def test_list_ready_task_rows_skips_file_pending_row_with_terminal_fact(tmp_path: Path) -> None:
    """``list_ready_task_rows`` must drop a pending file row whose latest fact
    is terminal — without rewriting the underlying file row.

    Test setup:
      * file row stays at status="pending" (ready candidate).
      * newer ``task_runtime.execution`` fact projects the same task as
        ``completed``.
    Expected: the stale pending file row is NOT returned by
    ``list_ready_task_rows`` because the observable model is terminal.
    """

    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = TaskRuntimeService(str(workspace))

    created = service.create_task_row(subject="stale pending row over terminal fact")
    created_id = str(created["id"])

    # Sanity check: a fresh pending row is ready.
    initial_ready = service.list_ready_task_rows()
    assert [row["id"] for row in initial_ready] == [int(created_id)]
    assert initial_ready[0]["status"] == "pending"

    # Append a newer terminal fact WITHOUT mutating the file row.
    _append_terminal_fact_event(
        workspace,
        task_id=created_id,
        event_type="completed",
        status="completed",
        run_id="run-fact-completed",
    )

    # File row on disk is still pending.
    on_disk = json.loads(_task_file_path(workspace, created_id).read_text(encoding="utf-8"))
    assert on_disk["status"] == "pending"

    ready_rows = service.list_ready_task_rows()
    assert all(int(row["id"]) != int(created_id) for row in ready_rows), (
        "stale pending file row must not be returned once a terminal fact exists; "
        f"got {[row['id'] for row in ready_rows]}"
    )

    # Observable model confirms the terminal verdict.
    observable = service.list_observable_task_rows()
    matching = [row for row in observable if int(row["id"]) == int(created_id)]
    assert matching, "observable rows must still surface the task id"
    assert matching[0]["status"] == "completed"


def test_observable_task_row_stats_count_terminal_fact_overlay(tmp_path: Path) -> None:
    """Observable stats must count terminal facts over stale file rows."""

    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = TaskRuntimeService(str(workspace))

    created = service.create_task_row(subject="observable stats terminal fact")
    created_id = str(created["id"])

    raw_before = service.list_task_rows()
    assert raw_before[0]["status"] == "pending"

    _append_terminal_fact_event(
        workspace,
        task_id=created_id,
        event_type="completed",
        status="completed",
        run_id="run-fact-stats-completed",
    )

    on_disk = json.loads(_task_file_path(workspace, created_id).read_text(encoding="utf-8"))
    assert on_disk["status"] == "pending"

    observable_stats = service.get_observable_task_row_stats()
    compatibility_stats = service.get_task_row_stats()

    assert observable_stats == compatibility_stats
    assert observable_stats["total"] == 1
    assert observable_stats["pending"] == 0
    assert observable_stats["ready"] == 0
    assert observable_stats["completed"] == 1

    raw_after = service.list_task_rows()
    assert raw_after[0]["status"] == "pending"


def test_select_next_task_with_requested_id_rejects_stale_pending_file_row(tmp_path: Path) -> None:
    """``select_next_task(requested_task_id=...)`` must not return a stale
    pending file row when the latest observable fact for that task is
    terminal.
    """

    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = TaskRuntimeService(str(workspace))

    created = service.create_task_row(subject="select_next_task terminal fact rejection")
    created_id = str(created["id"])

    _append_terminal_fact_event(
        workspace,
        task_id=created_id,
        event_type="failed",
        status="failed",
        run_id="run-fact-failed",
    )

    # File row stays pending; observable model is failed.
    on_disk = json.loads(_task_file_path(workspace, created_id).read_text(encoding="utf-8"))
    assert on_disk["status"] == "pending"
    observable = service.list_observable_task_rows()
    assert observable[0]["status"] == "failed"

    selected = service.select_next_task(requested_task_id=created_id)

    assert selected is None, (
        "select_next_task must NOT return a stale pending file row when the "
        f"latest observable fact is terminal; got {selected!r}"
    )


def test_claim_next_execution_skips_stale_pending_row_with_terminal_fact(tmp_path: Path) -> None:
    """``claim_next_execution`` must skip a stale pending file row whose
    latest observable fact is terminal and claim another available task
    instead.
    """

    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = TaskRuntimeService(str(workspace))

    stale = service.create_task_row(subject="stale pending file row over terminal fact")
    stale_id = str(stale["id"])
    fresh = service.create_task_row(subject="fresh available task")
    fresh_id = str(fresh["id"])

    _append_terminal_fact_event(
        workspace,
        task_id=stale_id,
        event_type="completed",
        status="completed",
        run_id="run-fact-completed-skip",
    )

    on_disk = json.loads(_task_file_path(workspace, stale_id).read_text(encoding="utf-8"))
    assert on_disk["status"] == "pending"
    observable = {int(row["id"]): row for row in service.list_observable_task_rows()}
    assert observable[int(stale_id)]["status"] == "completed"
    assert observable[int(fresh_id)]["status"] == "pending"

    claimed = service.claim_next_execution(
        worker_id="director",
        role_id="director",
        run_id="run-claim-skip-stale",
        selection_source="queue",
    )

    assert claimed["success"] is True
    assert claimed["task"]["id"] == int(fresh_id)
    assert claimed["task"]["status"] == "in_progress"
    # The stale task must NOT have been claimed or had its file row mutated.
    persisted_stale = json.loads(_task_file_path(workspace, stale_id).read_text(encoding="utf-8"))
    assert persisted_stale["status"] == "pending"
    attempted_ids = [
        int(attempt["task_id"]) for attempt in (claimed.get("attempts") or []) if attempt.get("task_id") is not None
    ]
    assert int(stale_id) not in attempted_ids, (
        f"stale pending row with terminal fact must not appear in claim attempts; got attempts={attempted_ids!r}"
    )


def test_refresh_dependency_unblocks_overlays_execution_fact_status(tmp_path: Path) -> None:
    """``refresh_dependency_unblocks`` must treat a parent as completed when
    its latest ``task_runtime.execution`` fact says so — even when the file
    row is left stale/pending and ``complete_execution`` was never called.

    Test setup:
      * Parent file row stays at ``status=pending`` (never claimed, never
        completed through the service).
      * Child is created with ``blocked_by=[parent_id]`` so the on-disk row
        is ``status=blocked`` with ``blocked_by=[parent_id]``.
      * A ``task_runtime.execution`` fact is appended for the parent whose
        payload carries ``event_type="completed"`` /
        ``status="completed"`` / ``execution_state="completed"`` plus a
        full ``task_row_snapshot`` so the projection is consistent.
    Expected:
      * ``refresh_dependency_unblocks()`` unblocks the child:
        ``unblocked_task_ids`` contains the child id, the persisted file
        row moves to ``pending`` with ``blocked_by=[]``, and a
        ``dependencies_unblocked`` execution event is recorded.
    """

    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = TaskRuntimeService(str(workspace))

    parent = service.create_task_row(subject="fact-only completed parent")
    parent_id = str(parent["id"])
    child = service.create_task_row(
        subject="child blocked on fact-completed parent",
        blocked_by=[parent["id"]],
    )
    child_id = int(child["id"])

    # Sanity: the on-disk child row is blocked against the pending parent.
    raw_parent = json.loads(_task_file_path(workspace, parent_id).read_text(encoding="utf-8"))
    raw_child = json.loads(_task_file_path(workspace, child_id).read_text(encoding="utf-8"))
    assert raw_parent["status"] == "pending"
    assert raw_child["status"] == "blocked"
    assert raw_child["blocked_by"] == [parent["id"]]

    # Append a terminal execution fact for the parent WITHOUT going through
    # the service APIs — the file row stays stale/pending on disk.
    append_fact_event(
        AppendFactEventCommandV1(
            workspace=str(workspace),
            stream="task_runtime.execution",
            event_type="completed",
            source="runtime.task_runtime",
            task_id=parent_id,
            run_id="run-fact-completed-parent",
            payload={
                "task_id": parent_id,
                "run_id": "run-fact-completed-parent",
                "event_type": "completed",
                "status": "completed",
                "execution_state": "completed",
                "session_id": "session-fact-completed",
                "task_row_snapshot": {
                    "id": parent_id,
                    "task_id": parent_id,
                    "subject": parent["subject"],
                    "description": parent.get("description", ""),
                    "priority": "HIGH",
                    "metadata": {"source": "task_runtime.row_snapshot"},
                },
            },
        )
    )

    refresh = service.refresh_dependency_unblocks()

    assert child_id in refresh["unblocked_task_ids"], (
        f"child must be unblocked once parent fact projects as completed; got "
        f"unblocked_task_ids={refresh['unblocked_task_ids']!r}"
    )
    assert refresh["unblocked_count"] == 1
    matching_events = [
        event for event in refresh["execution_events"] if event.get("event_type") == "dependencies_unblocked"
    ]
    assert matching_events, "refresh must record a dependencies_unblocked execution event"
    assert matching_events[0]["ok"] is True

    persisted_child = json.loads(_task_file_path(workspace, child_id).read_text(encoding="utf-8"))
    assert persisted_child["status"] == "pending"
    assert persisted_child["blocked_by"] == []


def test_claim_execution_rejects_stale_pending_row_with_terminal_execution_fact(
    tmp_path: Path,
) -> None:
    """``claim_execution(task_id)`` must reject a stale pending file row when
    the latest ``task_runtime.execution`` fact for the same task is terminal,
    without mutating the file row.

    Regression: a direct ``claim_execution(task_id)`` call used to consult only
    the file-backed ``TaskBoard`` row. If an external orchestrator appended a
    terminal fact (e.g. ``completed``) without going through
    ``complete_execution``, the raw row stayed ``pending`` and the claim would
    silently re-acquire a task whose authoritative state is already terminal.
    The direct claim path must treat the latest execution fact as
    authoritative and reject the claim with ``task_terminal`` while leaving the
    stale file row untouched (read-model veto, not a hidden mutation).
    """

    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = TaskRuntimeService(str(workspace))

    created = service.create_task_row(subject="claim direct rejects stale pending over terminal fact")
    created_id = int(created["id"])

    # Sanity: the file row is initially ready/pending and no terminal fact exists.
    initial_ready = service.list_ready_task_rows()
    assert [int(row["id"]) for row in initial_ready] == [created_id]
    assert initial_ready[0]["status"] == "pending"

    # Append a newer terminal fact WITHOUT going through the service APIs.
    # ``complete_execution`` is intentionally NOT called and no session file
    # exists on disk; the file row stays stale/pending.
    _append_terminal_fact_event(
        workspace,
        task_id=str(created_id),
        event_type="completed",
        status="completed",
        run_id="run-fact-completed-claim-direct",
    )

    # File row on disk is still pending; observable model is terminal.
    on_disk = json.loads(_task_file_path(workspace, created_id).read_text(encoding="utf-8"))
    assert on_disk["status"] == "pending"
    observable = service.list_observable_task_rows()
    matching = [row for row in observable if int(row["id"]) == created_id]
    assert matching, "observable rows must still surface the task id"
    assert matching[0]["status"] == "completed"

    claimed = service.claim_execution(
        created_id,
        worker_id="director",
        role_id="director",
        run_id="run-claim-direct-over-terminal-fact",
        selection_source="task_id_lookup",
    )

    assert claimed["success"] is False
    assert claimed["reason"] == "task_terminal"
    # The returned task row is the projected fact row — its status reflects the
    # terminal verdict from the execution fact stream.
    assert isinstance(claimed.get("task"), dict)
    assert claimed["task"]["status"] == "completed"
    # The rejection must surface that the execution fact is authoritative so
    # callers can distinguish read-model vetoes from raw-row terminal states.
    assert claimed.get("execution_fact_authoritative") is True
    assert claimed.get("source") == "task_runtime.execution_fact"
    assert claimed.get("fact_status") == "completed"

    # The raw file row must remain pending: this is a read-model veto, not a
    # hidden mutation. The next ``complete_execution`` / ``reopen`` flow is
    # still free to act on the file row.
    persisted = json.loads(_task_file_path(workspace, created_id).read_text(encoding="utf-8"))
    assert persisted["status"] == "pending", (
        "claim_execution must not mutate the file row when the rejection is "
        "anchored on the execution fact stream; the raw row stays pending so "
        "the eventual owner path can run the sanctioned state transition"
    )


def test_claim_execution_refreshes_dependency_unblocks_from_execution_fact(
    tmp_path: Path,
) -> None:
    """``claim_execution(child_id)`` must refresh dependency unblocks before
    reading the child row, so a child whose parent is only complete in the
    latest ``task_runtime.execution`` fact becomes directly claimable.

    Regression: the direct ``claim_execution(task_id)`` path used to skip the
    ``refresh_dependency_unblocks`` projection, so a child blocked against a
    parent that completed only via the fact stream would be rejected with
    ``task_blocked`` even though the parent was already complete. The direct
    path now refreshes first, then claims, so the child row reaches
    ``in_progress`` in a single call.
    """

    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = TaskRuntimeService(str(workspace))

    # Parent file row stays at status="pending" — never claimed, never
    # completed through the service APIs.
    parent = service.create_task_row(subject="fact-only completed parent for direct claim")
    parent_id = int(parent["id"])
    # Child is created with blocked_by=[parent_id], so the on-disk child row
    # is status="blocked" with blocked_by=[parent_id].
    child = service.create_task_row(
        subject="child blocked on fact-completed parent",
        blocked_by=[parent_id],
    )
    child_id = int(child["id"])

    # Sanity: the on-disk child row is blocked against the pending parent and
    # no terminal fact exists yet.
    raw_parent = json.loads(_task_file_path(workspace, parent_id).read_text(encoding="utf-8"))
    raw_child = json.loads(_task_file_path(workspace, child_id).read_text(encoding="utf-8"))
    assert raw_parent["status"] == "pending"
    assert raw_child["status"] == "blocked"
    assert raw_child["blocked_by"] == [parent_id]

    # Append a terminal execution fact for the parent WITHOUT going through the
    # service APIs. The file row stays stale/pending on disk.
    _append_terminal_fact_event(
        workspace,
        task_id=str(parent_id),
        event_type="completed",
        status="completed",
        run_id="run-fact-completed-parent-direct-claim",
    )

    # File row on disk still pending; observable model says the parent is
    # completed, but it remains a read-only projection. The child stays blocked
    # until the claim path performs the sanctioned refresh.
    on_disk_parent = json.loads(_task_file_path(workspace, parent_id).read_text(encoding="utf-8"))
    assert on_disk_parent["status"] == "pending"
    on_disk_child = json.loads(_task_file_path(workspace, child_id).read_text(encoding="utf-8"))
    assert on_disk_child["status"] == "blocked", (
        "claim_execution must be the path that triggers the unblock refresh on "
        "the file row; until then the persisted child row stays blocked"
    )
    observable = {int(row["id"]): row for row in service.list_observable_task_rows()}
    assert observable[parent_id]["status"] == "completed"
    assert observable[child_id]["status"] == "blocked"
    assert observable[child_id]["blocked_by"] == [parent_id]

    # Direct ``claim_execution(child_id)`` must refresh dependency unblocks
    # first, see the parent as completed via the fact overlay, unblock the
    # child, and then claim it in one call.
    claimed = service.claim_execution(
        child_id,
        worker_id="director",
        role_id="director",
        run_id="run-claim-child-direct",
        selection_source="task_id_lookup",
    )

    assert claimed["success"] is True, (
        "claim_execution must refresh dependency unblocks before checking "
        "dependencies so a fact-only-completed parent unblocks the child; got "
        f"{claimed!r}"
    )
    assert claimed["reason"] == "claimed"
    assert isinstance(claimed.get("task"), dict)
    assert int(claimed["task"]["id"]) == child_id
    assert claimed["task"]["status"] == "in_progress"

    # Persisted child row must reach ``in_progress`` and have its blockers
    # cleared: refresh_dependency_unblocks cleared ``blocked_by`` to ``[]``
    # and the subsequent claim step moved the row to ``in_progress``.
    persisted_child = json.loads(_task_file_path(workspace, child_id).read_text(encoding="utf-8"))
    assert persisted_child["status"] == "in_progress"
    assert persisted_child["blocked_by"] == []


# ---------------------------------------------------------------------------
# get_task observable fact-overlay regression
# ---------------------------------------------------------------------------
#
# ``TaskRuntimeService.get_task`` is the canonical public read projection for
# a single task row. It MUST surface the latest ``task_runtime.execution`` fact
# overlay (the same converged view that ``list_observable_task_rows`` exposes)
# rather than the raw ``TaskBoard`` row that lives on disk. Without the
# overlay, downstream consumers reading a single task directly would observe a
# stale ``pending`` row while the authoritative ``completed`` fact exists in
# the execution ledger, defeating the read-model convergence the rest of the
# selection/claim paths now rely on.


def test_get_task_returns_fact_overlaid_status_for_numeric_task_id(tmp_path: Path) -> None:
    """``get_task(task_id)`` must surface the latest ``task_runtime.execution``
    fact overlay for a numeric task id.

    Regression: a stale ``pending`` file row must NOT be returned as the
    authoritative status when a newer ``completed`` fact exists in the
    execution ledger. The raw file row remains untouched — the overlay is a
    read-model convergence, not a hidden mutation.
    """

    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = TaskRuntimeService(str(workspace))

    created = service.create_task_row(
        subject="get_task numeric overlay",
        description="file row stays pending while fact overlays to completed",
    )
    created_id = int(created["id"])

    # Sanity: file row is initially pending and get_task agrees.
    on_disk_before = json.loads(_task_file_path(workspace, created_id).read_text(encoding="utf-8"))
    assert on_disk_before["status"] == "pending"
    initial_row = service.get_task(created_id)
    assert isinstance(initial_row, dict)
    assert initial_row["status"] == "pending"

    # Append a newer terminal fact WITHOUT going through the service APIs.
    # ``complete_execution`` is intentionally NOT called; no session file is
    # written; the file row stays stale/pending on disk.
    _append_terminal_fact_event(
        workspace,
        task_id=str(created_id),
        event_type="completed",
        status="completed",
        run_id="run-fact-completed-get-task",
    )

    # Raw file row on disk must remain pending: the overlay is read-only.
    on_disk_after = json.loads(_task_file_path(workspace, created_id).read_text(encoding="utf-8"))
    assert on_disk_after["status"] == "pending", (
        "get_task must not mutate the file row when overlaying the execution "
        "fact stream; the raw row stays pending so the eventual owner path "
        "can run the sanctioned state transition"
    )

    # ``get_task`` MUST now surface the fact-overlaid status, not the stale
    # pending file row.
    overlaid = service.get_task(created_id)
    assert isinstance(overlaid, dict)
    assert overlaid["status"] == "completed", (
        f"get_task must surface the latest task_runtime.execution fact status; got status={overlaid.get('status')!r}"
    )
    assert overlaid["id"] == created_id
    # The fact-overlay marker must be present so consumers can distinguish a
    # file-row status from a fact-overlaid status.
    assert overlaid.get("metadata", {}).get("source") == "task_runtime.execution_fact"
    assert overlaid.get("metadata", {}).get("previous_status") == "pending"


def test_get_task_returns_fact_overlaid_status_for_external_task_id(tmp_path: Path) -> None:
    """``get_task(external_task_id)`` must surface the latest
    ``task_runtime.execution`` fact overlay for an external token such as
    ``TASK-EXT`` when the payload's ``task_row_snapshot`` preserves the
    external id in metadata.
    """

    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = TaskRuntimeService(str(workspace))

    external_id = "TASK-EXT"
    created = service.create_task_row(
        subject="get_task external overlay",
        description="external-task-id lookup must also overlay facts",
        metadata={"external_task_id": external_id, "source_task_id": external_id},
    )
    created_id = int(created["id"])

    # Sanity: the external-id lookup hits the same row before any fact is
    # appended.
    initial_lookup = service.get_task(external_id)
    assert isinstance(initial_lookup, dict)
    assert initial_lookup["id"] == created_id
    assert initial_lookup["status"] == "pending"

    # Append a terminal execution fact for the SAME numeric task whose
    # ``task_row_snapshot`` preserves the external id in metadata. The fact's
    # ``task_id`` is the numeric file-row id so the observable read model
    # merges the fact onto the file row; the external id stays discoverable
    # through the snapshot metadata.
    append_fact_event(
        AppendFactEventCommandV1(
            workspace=str(workspace),
            stream="task_runtime.execution",
            event_type="completed",
            source="runtime.task_runtime",
            task_id=str(created_id),
            run_id="run-fact-completed-external",
            payload={
                "task_id": str(created_id),
                "run_id": "run-fact-completed-external",
                "event_type": "completed",
                "status": "completed",
                "execution_state": "completed",
                "task_row_snapshot": {
                    "id": created_id,
                    "task_id": str(created_id),
                    "subject": "external overlay row",
                    "description": "fact snapshot preserves external id",
                    "metadata": {
                        "external_task_id": external_id,
                        "source_task_id": external_id,
                        "source": "task_runtime.row_snapshot",
                    },
                },
            },
        )
    )

    # Raw file row on disk remains pending.
    on_disk_after = json.loads(_task_file_path(workspace, created_id).read_text(encoding="utf-8"))
    assert on_disk_after["status"] == "pending"

    # ``get_task(external_id)`` must surface the fact-overlaid status, not the
    # stale pending file row, while keeping the external id discoverable.
    overlaid = service.get_task(external_id)
    assert isinstance(overlaid, dict)
    assert overlaid["status"] == "completed", (
        "get_task must surface the latest task_runtime.execution fact status "
        f"for external id {external_id!r}; got status={overlaid.get('status')!r}"
    )
    # The overlay merges the fact onto the file row, so the observable row id
    # is the file-row's numeric id while the external id stays reachable
    # through metadata.
    assert overlaid["id"] == created_id
    assert str(overlaid["metadata"].get("external_task_id") or "") == external_id
    assert str(overlaid["metadata"].get("source_task_id") or "") == external_id
    assert overlaid["metadata"].get("source") == "task_runtime.execution_fact"
    assert overlaid["metadata"].get("previous_status") == "pending"


# ---------------------------------------------------------------------------
# task_exists observable fact-overlay regression
# ---------------------------------------------------------------------------
#
# ``TaskRuntimeService.task_exists`` is the public existence probe consumed by
# role adapters (e.g. ``_board_task_exists`` / ``_update_board_task``). It MUST
# consult the observable read model — i.e. the file row overlaid with the
# latest ``task_runtime.execution`` fact — instead of probing ``self._board``
# alone. Otherwise a task whose existence is only attested by the execution
# ledger (fact-only projection) is silently invisible to roles adapters that
# use ``task_exists`` as a precondition for writes, defeating the read-model
# convergence that ``get_task`` / ``list_observable_task_rows`` already pin.


def test_task_exists_returns_true_for_fact_only_numeric_task_id(tmp_path: Path) -> None:
    """``task_exists(numeric_id)`` must return ``True`` when a
    ``task_runtime.execution`` fact attests to that id, even when no file row
    has ever been created.

    Regression: ``task_exists`` used to consult ``self._board.get(normalized)``
    only, so any fact-only task was reported as ``False`` — making role
    adapters bypass the existence check, write a duplicate file row, and break
    the fact-only read-model convergence. The probe must consult the same
    observable read model ``get_task`` uses, so any task visible to
    ``list_observable_task_rows`` is also visible to ``task_exists``.
    """

    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = TaskRuntimeService(str(workspace))

    fact_only_id = 4242

    # Sanity: no file row exists for the chosen id and the raw board probe
    # agrees the task is absent.
    assert not _task_file_path(workspace, fact_only_id).exists()
    assert service.task_exists(fact_only_id) is False, (
        "sanity precondition failed: a brand-new workspace must not contain an arbitrary numeric task id"
    )

    # Append a task_runtime.execution fact WITHOUT creating a file row. The
    # payload's ``task_row_snapshot.id`` is the same numeric id, so the
    # observable read model can resolve the task purely from the ledger.
    append_fact_event(
        AppendFactEventCommandV1(
            workspace=str(workspace),
            stream="task_runtime.execution",
            event_type="claimed",
            source="runtime.task_runtime",
            task_id=str(fact_only_id),
            run_id="run-fact-only-existence",
            payload={
                "task_id": str(fact_only_id),
                "run_id": "run-fact-only-existence",
                "event_type": "claimed",
                "status": "in_progress",
                "execution_state": "in_progress",
                "session_id": "session-fact-only-existence",
                "task_row_snapshot": {
                    "id": fact_only_id,
                    "task_id": str(fact_only_id),
                    "subject": "fact-only existence probe",
                    "description": "task_exists must see this row via the fact stream",
                    "priority": "HIGH",
                    "metadata": {"source": "task_runtime.row_snapshot"},
                },
            },
        )
    )

    # Observable rows confirm the fact-only projection: this is the same
    # read model ``task_exists`` must now consult.
    observable = {int(row["id"]): row for row in service.list_observable_task_rows()}
    assert fact_only_id in observable, (
        f"observable rows must surface the fact-only task; got ids={sorted(observable.keys())!r}"
    )
    assert observable[fact_only_id]["status"] == "in_progress"

    # File row is still absent on disk — this is a fact-only existence.
    assert not _task_file_path(workspace, fact_only_id).exists(), (
        "test invariant: the file row must not be created; task_exists must consult the observable read model"
    )

    # The contract: ``task_exists`` must report the fact-only task as
    # existing. This is the regression the production code must satisfy.
    assert service.task_exists(fact_only_id) is True, (
        "task_exists must consult the observable read model so a fact-only "
        "task_runtime.execution projection is reported as existing; got "
        f"task_exists({fact_only_id}) == False"
    )

    # The same verdict must hold for the canonical ``task-<id>`` token used by
    # role adapters when they normalize ids through ``normalize_task_id``.
    assert service.task_exists(f"task-{fact_only_id}") is True, (
        "task_exists must accept the canonical task-<id> token and consult the observable read model"
    )


def test_task_exists_keeps_true_when_observable_overlays_terminal_fact(tmp_path: Path) -> None:
    """``task_exists`` must stay ``True`` for an existing file row once a
    newer ``task_runtime.execution`` fact overlays it, mirroring the
    ``get_task`` fact-overlay contract.

    Regression: role adapters call ``task_exists`` before writes; if the
    probe stops consulting the observable model, an externally-attached
    terminal fact that does NOT mutate the file row would let the probe drift
    away from what ``get_task`` reports. The probe must keep reporting
    existence for any task the observable model still surfaces, regardless of
    the fact-overlaid status (pending, in_progress, terminal — all still
    surface the id).
    """

    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = TaskRuntimeService(str(workspace))

    created = service.create_task_row(
        subject="task_exists overlay",
        description="file row stays pending; fact overlays status",
    )
    created_id = int(created["id"])

    # Baseline: the file row exists and the probe agrees.
    assert service.task_exists(created_id) is True

    # Append a terminal fact WITHOUT going through the service APIs.
    _append_terminal_fact_event(
        workspace,
        task_id=str(created_id),
        event_type="completed",
        status="completed",
        run_id="run-fact-completed-task-exists",
    )

    # File row stays pending on disk: the fact overlay is read-only.
    on_disk = json.loads(_task_file_path(workspace, created_id).read_text(encoding="utf-8"))
    assert on_disk["status"] == "pending"

    # Observable model surfaces the file row id with the fact-overlaid
    # status; the task is still present.
    observable = {int(row["id"]): row for row in service.list_observable_task_rows()}
    assert observable[created_id]["status"] == "completed"

    # ``task_exists`` must keep returning ``True`` for the task id because
    # the observable model still surfaces it. Failing here means the probe
    # falls back to raw board state and ignores the fact overlay.
    assert service.task_exists(created_id) is True, (
        "task_exists must remain True for any task still surfaced by the "
        "observable read model; the fact overlay must not make the probe "
        "report the task as absent"
    )


def test_task_exists_returns_false_for_unknown_task_id_when_facts_present(tmp_path: Path) -> None:
    """``task_exists`` must keep returning ``False`` for ids that the
    observable read model never surfaces — even when the fact stream has
    facts for OTHER tasks.

    This pins the negative side of the contract: the probe must not become a
    blanket ``True`` once any fact exists in the stream. Only ids that the
    observable read model actually projects get reported as existing.
    """

    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = TaskRuntimeService(str(workspace))

    fact_only_id = 9001

    # Pre-condition: the fact stream is empty for this id; probe must
    # already report False.
    assert service.task_exists(fact_only_id) is False

    append_fact_event(
        AppendFactEventCommandV1(
            workspace=str(workspace),
            stream="task_runtime.execution",
            event_type="claimed",
            source="runtime.task_runtime",
            task_id=str(fact_only_id),
            run_id="run-fact-only-existence-negative",
            payload={
                "task_id": str(fact_only_id),
                "run_id": "run-fact-only-existence-negative",
                "event_type": "claimed",
                "status": "in_progress",
                "execution_state": "in_progress",
                "session_id": "session-fact-only-existence-negative",
                "task_row_snapshot": {
                    "id": fact_only_id,
                    "task_id": str(fact_only_id),
                    "subject": "fact-only existence (negative side)",
                    "priority": "HIGH",
                    "metadata": {"source": "task_runtime.row_snapshot"},
                },
            },
        )
    )

    # The fact-only id is now projectable: the probe returns True.
    assert service.task_exists(fact_only_id) is True

    # An unrelated id remains unknown: the probe still returns False. This
    # pins the negative side of the contract — the probe must scope the
    # fact lookup to the queried id, not the whole stream.
    other_id = 9999
    assert other_id != fact_only_id
    assert service.task_exists(other_id) is False, (
        "task_exists must keep returning False for ids the observable model "
        "does not surface; a fact-only presence must not make the probe "
        "report unrelated ids as existing"
    )


# ---------------------------------------------------------------------------
# _task_has_unresolved_dependencies observable fact-overlay regression
# ---------------------------------------------------------------------------
#
# ``TaskRuntimeService._task_has_unresolved_dependencies`` is the per-task
# blocker probe consulted by the claim path (``claim_execution`` ->
# ``task_blocked`` rejection). It MUST consult the same fact-overlay-aware
# status projection that ``refresh_dependency_unblocks`` /
# ``_fact_overlaid_dependency_status_rows`` expose, instead of reading the raw
# ``self._board.get(dep_id).status`` alone. Without the overlay, a child
# blocked against a parent whose authoritative completion lives only in the
# ``task_runtime.execution`` fact stream stays ``task_blocked`` even after
# ``refresh_dependency_unblocks`` has unblocked the file row, defeating the
# read-model convergence the rest of the selection/claim paths now rely on.


def test_task_has_unresolved_dependencies_uses_fact_overlay_for_completed_parent(
    tmp_path: Path,
) -> None:
    """``_task_has_unresolved_dependencies`` must treat a parent dependency as
    resolved when the latest ``task_runtime.execution`` fact overlays it as
    ``completed``, even when the raw file-backed parent row stays pending.

    Test setup:
      * Parent file row stays ``status=pending`` (no claim / no completion via
        the service APIs).
      * Child file row stays ``status=blocked`` with ``blocked_by=[parent_id]``
        (no ``refresh_dependency_unblocks`` is called — we want the helper to
        see the stale file row alone).
      * A newer ``task_runtime.execution`` ``completed`` fact is appended for
        the parent, so the overlay projects the parent as ``completed``.
    Expected:
      * ``_task_has_unresolved_dependencies(child_task)`` returns ``False``
        because the overlay is authoritative; the raw ``self._board.get``
        path would still see the stale pending parent and incorrectly return
        ``True``.
    """

    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = TaskRuntimeService(str(workspace))

    parent = service.create_task_row(subject="fact-overlay completed parent")
    parent_id = int(parent["id"])
    child = service.create_task_row(
        subject="child blocked on fact-completed parent",
        blocked_by=[parent["id"]],
    )
    child_id = int(child["id"])

    # Sanity: raw file rows are pending/blocked and no terminal fact exists
    # yet. The board cache must still report the raw (pre-refresh) state.
    raw_parent = json.loads(_task_file_path(workspace, parent_id).read_text(encoding="utf-8"))
    raw_child = json.loads(_task_file_path(workspace, child_id).read_text(encoding="utf-8"))
    assert raw_parent["status"] == "pending"
    assert raw_child["status"] == "blocked"
    assert raw_child["blocked_by"] == [parent_id]

    cached_child = service._board.get(child_id)
    assert cached_child is not None
    assert list(cached_child.blocked_by) == [parent_id]
    cached_parent_before = service._board.get(parent_id)
    assert cached_parent_before is not None
    assert cached_parent_before.status.value == "pending", (
        "test invariant: parent cache must still be pending before the fact is appended"
    )

    # Pre-overlay: helper must keep returning True because the raw dependency
    # row is still pending. This pins the baseline the regression is measured
    # against.
    assert service._task_has_unresolved_dependencies(cached_child) is True, (
        "with no execution fact overlay the helper must still see the raw pending parent as an unresolved blocker"
    )

    # Append a terminal execution fact for the parent WITHOUT going through
    # the service APIs. The file row stays stale/pending on disk and the
    # in-memory board cache is also untouched.
    _append_terminal_fact_event(
        workspace,
        task_id=str(parent_id),
        event_type="completed",
        status="completed",
        run_id="run-fact-completed-dep-helper",
    )

    # File row on disk is still pending; observable model is completed.
    on_disk_parent = json.loads(_task_file_path(workspace, parent_id).read_text(encoding="utf-8"))
    assert on_disk_parent["status"] == "pending"
    observable_parent = service._fact_overlaid_dependency_status_rows()
    assert observable_parent[parent_id].value == "completed", (
        f"fact-overlay projection must report parent as completed; got {observable_parent[parent_id]!r}"
    )

    # The board cache of the parent is still pending — the helper must NOT
    # consult ``self._board.get(parent).status`` alone, or it would still
    # return True here.
    cached_parent_after = service._board.get(parent_id)
    assert cached_parent_after is not None
    assert cached_parent_after.status.value == "pending", (
        "test invariant: parent board cache must remain pending; the helper must "
        "rely on the fact-overlay projection, not on raw board state"
    )

    # The same cached child instance must now resolve to False because the
    # overlay projects the parent as completed.
    resolved_child = service._board.get(child_id)
    assert resolved_child is not None
    has_unresolved = service._task_has_unresolved_dependencies(resolved_child)
    assert has_unresolved is False, (
        "_task_has_unresolved_dependencies must consult the fact-overlay "
        "projection so a fact-only-completed parent unblocks the child even "
        "when the raw TaskBoard cache still reports the parent as pending"
    )


def test_task_has_unresolved_dependencies_returns_true_when_dependency_missing(
    tmp_path: Path,
) -> None:
    """``_task_has_unresolved_dependencies`` must return ``True`` when a child
    lists a dependency that the fact-overlay projection never surfaces — i.e.
    the dependency is missing from the read model.

    This pins the negative side of the contract: the helper must NOT silently
    treat a missing overlay entry as resolved; it must keep flagging the
    blocker so the claim path continues to reject the row.
    """

    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = TaskRuntimeService(str(workspace))

    missing_parent_id = 7777
    created = service.create_task_row(
        subject="child blocked against missing parent",
        blocked_by=[missing_parent_id],
    )
    child_id = int(created["id"])

    cached_child = service._board.get(child_id)
    assert cached_child is not None
    assert list(cached_child.blocked_by) == [missing_parent_id]

    # Sanity: the fact-overlay projection never surfaces the missing parent.
    overlay = service._fact_overlaid_dependency_status_rows()
    assert missing_parent_id not in overlay, (
        "test invariant: a missing-parent dependency must not appear in the overlay projection"
    )

    # The helper MUST still report the dependency as unresolved.
    assert service._task_has_unresolved_dependencies(cached_child) is True, (
        "_task_has_unresolved_dependencies must keep returning True when the "
        "dependency is absent from the overlay projection; a missing entry "
        "must not be silently treated as resolved"
    )


def test_task_has_unresolved_dependencies_returns_true_when_overlay_status_not_completed(
    tmp_path: Path,
) -> None:
    """``_task_has_unresolved_dependencies`` must return ``True`` when the
    fact-overlay projection shows a non-``completed`` status for the parent
    (e.g. ``in_progress`` or another intermediate state) — only the
    ``completed`` verdict should clear the blocker.
    """

    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    service = TaskRuntimeService(str(workspace))

    parent = service.create_task_row(subject="non-completed overlay parent")
    parent_id = int(parent["id"])
    child = service.create_task_row(
        subject="child blocked on in_progress parent",
        blocked_by=[parent["id"]],
    )
    child_id = int(child["id"])

    # Append an intermediate (non-terminal, non-completed) execution fact for
    # the parent. The overlay must project the parent as ``in_progress``,
    # not ``completed``, so the dependency must remain unresolved.
    append_fact_event(
        AppendFactEventCommandV1(
            workspace=str(workspace),
            stream="task_runtime.execution",
            event_type="claimed",
            source="runtime.task_runtime",
            task_id=str(parent_id),
            run_id="run-fact-in-progress-dep-helper",
            payload={
                "task_id": str(parent_id),
                "run_id": "run-fact-in-progress-dep-helper",
                "event_type": "claimed",
                "status": "in_progress",
                "execution_state": "in_progress",
                "session_id": "session-fact-in-progress-dep-helper",
                "task_row_snapshot": {
                    "id": parent_id,
                    "task_id": str(parent_id),
                    "subject": parent["subject"],
                    "description": parent.get("description", ""),
                    "priority": "HIGH",
                    "metadata": {"source": "task_runtime.row_snapshot"},
                },
            },
        )
    )

    # Sanity: the overlay projects the parent as in_progress (not completed).
    overlay = service._fact_overlaid_dependency_status_rows()
    assert overlay[parent_id].value == "in_progress", (
        f"overlay must project in_progress for a non-terminal fact; got {overlay[parent_id]!r}"
    )

    cached_child = service._board.get(child_id)
    assert cached_child is not None

    # The helper must still report the dependency as unresolved because the
    # overlay status is not ``completed``.
    assert service._task_has_unresolved_dependencies(cached_child) is True, (
        "_task_has_unresolved_dependencies must keep returning True when the "
        "fact-overlay status for the parent is anything other than 'completed'; "
        "only a completed overlay status clears the blocker"
    )
