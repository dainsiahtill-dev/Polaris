import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from polaris.cells.runtime.task_runtime.internal import task_board as task_board_module
from polaris.cells.runtime.task_runtime.internal.task_board import (
    InvalidTaskStateTransitionError,
    TaskBoard,
    TaskStatus,
)
from polaris.kernelone.storage import resolve_runtime_path


def test_task_board_concurrent_create_ids_are_unique(tmp_path) -> None:
    board = TaskBoard(str(tmp_path))

    def _create(index: int) -> int:
        return board.create(subject=f"task-{index}").id

    with ThreadPoolExecutor(max_workers=12) as pool:
        ids = list(pool.map(_create, range(60)))

    assert len(ids) == 60
    assert len(set(ids)) == 60


def test_task_board_save_retries_windows_permission_error_and_cleans_temp(tmp_path, monkeypatch) -> None:
    board = TaskBoard(str(tmp_path))
    task = board.create(subject="windows atomic save")
    sources: list[Path] = []
    calls = 0
    original_replace = task_board_module.os.replace

    def flaky_replace(src: object, dst: object) -> None:
        nonlocal calls
        sources.append(Path(str(src)))
        calls += 1
        if calls == 1:
            raise PermissionError(5, "Access is denied", str(src))
        original_replace(src, dst)

    monkeypatch.setattr(task_board_module.os, "replace", flaky_replace)

    updated = board.update_status(
        task.id,
        TaskStatus.IN_PROGRESS,
        allow_execution_status=True,
    )

    assert updated is not None
    assert updated.status == TaskStatus.IN_PROGRESS
    assert calls == 2
    assert sources[0].name.startswith(f".task_{task.id}.")
    assert sources[0].suffix == ".tmp"
    tasks_dir = Path(resolve_runtime_path(str(tmp_path), "runtime/tasks"))
    assert not (tasks_dir / f"task_{task.id}.tmp").exists()
    assert not list(tasks_dir.glob(f".task_{task.id}.*.tmp"))


def test_task_board_load_all_ignores_execution_session_files(tmp_path) -> None:
    board = TaskBoard(str(tmp_path))
    task = board.create(subject="load only task rows")
    tasks_dir = Path(resolve_runtime_path(str(tmp_path), "runtime/tasks"))
    (tasks_dir / f"task_{task.id}.session.json").write_text(
        '{"session_id":"session-1","task_id":1}\n',
        encoding="utf-8",
    )

    reloaded = TaskBoard(str(tmp_path))

    assert reloaded.get(task.id) is not None
    assert len(reloaded.list_all()) == 1


def test_task_board_load_all_normalizes_external_task_ids_without_claim_api(tmp_path) -> None:
    TaskBoard(str(tmp_path))
    tasks_dir = Path(resolve_runtime_path(str(tmp_path), "runtime/tasks"))
    (tasks_dir / "task_1.json").write_text(
        json.dumps(
            {
                "id": "TASK-1",
                "subject": "legacy PM task",
                "description": "created from PM contract",
                "status": "pending",
                "created_at": 1.0,
                "blocked_by": [],
                "blocks": ["TASK-3"],
                "metadata": {"pm_task_id": "TASK-1"},
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    reloaded = TaskBoard(str(tmp_path))
    task = reloaded.get(1)

    assert task is not None
    assert task.id == 1
    assert task.blocks == [3]
    assert task.metadata["pm_task_id"] == "TASK-1"
    assert task.metadata["external_task_id"] == "TASK-1"
    with pytest.raises(RuntimeError, match=r"TaskBoard\.claim is retired"):
        reloaded.claim(1, "director")


def test_task_board_load_all_preserves_legacy_partial_rows(tmp_path) -> None:
    TaskBoard(str(tmp_path))
    tasks_dir = Path(resolve_runtime_path(str(tmp_path), "runtime/tasks"))
    (tasks_dir / "task_1.json").write_text(
        json.dumps(
            {
                "id": "TASK-1",
                "status": "failed",
                "metadata": {
                    "last_execution_error": "director_materialization_quality_failed",
                    "pm_task_id": "TASK-1",
                },
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    reloaded = TaskBoard(str(tmp_path))
    task = reloaded.get(1)

    assert task is not None
    assert task.id == 1
    assert task.subject == "task-1"
    assert task.status == TaskStatus.FAILED
    assert task.created_at == 0.0
    assert task.metadata["external_task_id"] == "TASK-1"
    assert task.metadata["last_execution_error"] == "director_materialization_quality_failed"


def test_task_board_rejects_invalid_transition(tmp_path) -> None:
    board = TaskBoard(str(tmp_path))
    task = board.create(subject="transition-check")

    updated = board.update_status(task.id, TaskStatus.COMPLETED, allow_terminal_status=True)
    assert updated is not None
    assert updated.status == TaskStatus.COMPLETED

    with pytest.raises(InvalidTaskStateTransitionError):
        board.update_status(task.id, TaskStatus.PENDING)


def test_task_board_rejects_terminal_status_without_owner_authorization(tmp_path) -> None:
    board = TaskBoard(str(tmp_path))
    task = board.create(subject="terminal-owner-guard")

    with pytest.raises(RuntimeError, match="taskboard_execution_status_requires_task_runtime_owner_transition"):
        board.update_status(task.id, TaskStatus.IN_PROGRESS)

    with pytest.raises(RuntimeError, match="taskboard_execution_status_requires_task_runtime_owner_transition"):
        board.update(task.id, status=TaskStatus.CLAIMED)

    with pytest.raises(RuntimeError, match=r"TaskBoard\.claim is retired"):
        board.claim(task.id, "director")

    with pytest.raises(RuntimeError, match="terminal_taskboard_status_requires_task_runtime_owner_transition"):
        board.update_status(task.id, TaskStatus.COMPLETED)

    with pytest.raises(RuntimeError, match="terminal_taskboard_status_requires_task_runtime_owner_transition"):
        board.update(task.id, status=TaskStatus.FAILED)

    with pytest.raises(RuntimeError, match=r"TaskBoard\.complete is retired"):
        board.complete(task.id)

    with pytest.raises(RuntimeError, match=r"TaskBoard\.fail is retired"):
        board.fail(task.id, reason="boom")

    reopen_target = board.create(subject="terminal-reopen-guard")
    completed = board.update_status(
        reopen_target.id,
        TaskStatus.COMPLETED,
        allow_terminal_status=True,
    )
    assert completed is not None
    with pytest.raises(RuntimeError, match="taskboard_reopen_requires_task_runtime_owner_transition"):
        board.reopen(reopen_target.id, reason="qa_rework")

    current = board.get(task.id)
    assert current is not None
    assert current.status == TaskStatus.PENDING


def test_task_board_reopen_demotes_completed_task_back_to_pending(tmp_path) -> None:
    board = TaskBoard(str(tmp_path))
    parent = board.create(subject="parent-task")
    child = board.create(subject="child-task", blocked_by=[parent.id])

    board.update_status(parent.id, TaskStatus.COMPLETED, allow_terminal_status=True)
    child_after_parent_completion = board.get(child.id)
    assert child_after_parent_completion is not None
    assert parent.id in child_after_parent_completion.blocked_by
    assert child_after_parent_completion.status == TaskStatus.BLOCKED

    reopened = board.reopen(parent.id, reason="qa_rework", allow_terminal_reopen=True)
    assert reopened is not None
    assert reopened.status == TaskStatus.PENDING
    assert reopened.completed_at is None
    assert reopened.started_at is None

    child_after_reopen = board.get(child.id)
    assert child_after_reopen is not None
    assert parent.id in child_after_reopen.blocked_by
    assert child_after_reopen.status == TaskStatus.BLOCKED


def test_task_board_failed_prerequisite_keeps_dependents_blocked(tmp_path) -> None:
    """A FAILED prerequisite must NOT unblock its downstream dependents.

    Only a SUCCESSFUL completion may flip BLOCKED -> PENDING. Otherwise workers
    would pick up tasks whose dependency never produced its required artifacts,
    violating the DAG dependency guarantee.
    """
    board = TaskBoard(str(tmp_path))
    upstream = board.create(subject="failing-prerequisite")
    downstream = board.create(subject="dependent-task", blocked_by=[upstream.id])

    board.update_status(upstream.id, TaskStatus.FAILED, allow_terminal_status=True)

    downstream_after = board.get(downstream.id)
    assert downstream_after is not None
    assert downstream_after.status == TaskStatus.BLOCKED
    assert upstream.id in downstream_after.blocked_by
    assert all(t.id != downstream.id for t in board.list_ready())


def test_task_board_cancelled_prerequisite_keeps_dependents_blocked(tmp_path) -> None:
    """A CANCELLED prerequisite must also leave its dependents BLOCKED."""
    board = TaskBoard(str(tmp_path))
    upstream = board.create(subject="cancelled-prerequisite")
    downstream = board.create(subject="dependent-task", blocked_by=[upstream.id])

    board.update_status(upstream.id, TaskStatus.CANCELLED, allow_terminal_status=True)

    downstream_after = board.get(downstream.id)
    assert downstream_after is not None
    assert downstream_after.status == TaskStatus.BLOCKED
    assert upstream.id in downstream_after.blocked_by


def test_task_board_completed_prerequisite_keeps_dependent_rows_local(tmp_path) -> None:
    """Raw TaskBoard completion is row-local; TaskRuntimeService unblocks deps."""
    board = TaskBoard(str(tmp_path))
    upstream = board.create(subject="successful-prerequisite")
    downstream = board.create(subject="dependent-task", blocked_by=[upstream.id])

    board.update_status(upstream.id, TaskStatus.COMPLETED, allow_terminal_status=True)

    downstream_after = board.get(downstream.id)
    assert downstream_after is not None
    assert downstream_after.status == TaskStatus.BLOCKED
    assert upstream.id in downstream_after.blocked_by
    assert all(t.id != downstream.id for t in board.list_ready())


def test_task_board_create_keeps_reverse_dependencies_row_local(tmp_path) -> None:
    """Raw TaskBoard create must not mutate blocker rows behind the ledger."""
    board = TaskBoard(str(tmp_path))
    upstream = board.create(subject="upstream")
    downstream = board.create(subject="downstream", blocked_by=[upstream.id])

    upstream_after = board.get(upstream.id)

    assert downstream.blocked_by == [upstream.id]
    assert upstream_after is not None
    assert upstream_after.blocks == []


def test_task_board_reopen_keeps_downstream_rows_local(tmp_path) -> None:
    """Raw TaskBoard reopen must not re-block downstream rows behind the ledger."""
    board = TaskBoard(str(tmp_path))
    parent = board.create(subject="parent")
    child = board.create(subject="child")
    linked = board.update_blocks(parent.id, [child.id])
    assert linked is not None
    completed = board.update_status(parent.id, TaskStatus.COMPLETED, allow_terminal_status=True)
    assert completed is not None

    reopened = board.reopen(parent.id, reason="qa_rework", allow_terminal_reopen=True)
    child_after = board.get(child.id)

    assert reopened is not None
    assert reopened.status == TaskStatus.PENDING
    assert child_after is not None
    assert child_after.status == TaskStatus.PENDING
    assert child_after.blocked_by == []


def test_task_board_repeated_complete_is_idempotent_no_op(tmp_path) -> None:
    """Re-applying the same terminal status must be a no-op.

    Guards against clobbering the original completed_at and appending a
    duplicate terminal event (e.g. an LLM tool retry calling complete() twice).
    """
    board = TaskBoard(str(tmp_path))
    task = board.create(subject="idempotent-complete")

    first = board.update_status(
        task.id,
        TaskStatus.COMPLETED,
        result_summary="done",
        allow_terminal_status=True,
    )
    assert first is not None
    assert first.status == TaskStatus.COMPLETED
    first_completed_at = first.completed_at
    assert first_completed_at is not None

    event_path = Path(resolve_runtime_path(str(tmp_path), "runtime/events/taskboard.terminal.events.jsonl"))
    assert event_path.exists()
    events_after_first = [line for line in event_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(events_after_first) == 1

    second = board.update_status(
        task.id,
        TaskStatus.COMPLETED,
        result_summary="retry",
        allow_terminal_status=True,
    )
    assert second is not None
    assert second.status == TaskStatus.COMPLETED
    # completed_at must be unchanged on the no-op re-entry.
    assert second.completed_at == first_completed_at

    events_after_second = [line for line in event_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    # No duplicate terminal event emitted.
    assert len(events_after_second) == 1
