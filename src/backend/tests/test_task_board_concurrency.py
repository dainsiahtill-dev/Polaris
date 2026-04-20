from concurrent.futures import ThreadPoolExecutor

import pytest
from polaris.cells.runtime.task_runtime.public.task_board_contract import (
    InvalidTaskStateTransitionError,
    TaskBoard,
    TaskStatus,
)


def test_task_board_concurrent_create_ids_are_unique(tmp_path) -> None:
    board = TaskBoard(str(tmp_path))

    def _create(index: int) -> int:
        return board.create(subject=f"task-{index}").id

    with ThreadPoolExecutor(max_workers=12) as pool:
        ids = list(pool.map(_create, range(60)))

    assert len(ids) == 60
    assert len(set(ids)) == 60


def test_task_board_rejects_invalid_transition(tmp_path) -> None:
    board = TaskBoard(str(tmp_path))
    task = board.create(subject="transition-check")

    updated = board.update_status(task.id, TaskStatus.COMPLETED)
    assert updated is not None
    assert updated.status == TaskStatus.COMPLETED

    with pytest.raises(InvalidTaskStateTransitionError):
        board.update_status(task.id, TaskStatus.PENDING)


def test_task_board_reopen_demotes_completed_task_back_to_pending(tmp_path) -> None:
    board = TaskBoard(str(tmp_path))
    parent = board.create(subject="parent-task")
    child = board.create(subject="child-task", blocked_by=[parent.id])

    board.update_status(parent.id, TaskStatus.COMPLETED)
    child_after_unblock = board.get(child.id)
    assert child_after_unblock is not None
    assert parent.id not in child_after_unblock.blocked_by
    assert child_after_unblock.status == TaskStatus.PENDING

    reopened = board.reopen(parent.id, reason="qa_rework")
    assert reopened is not None
    assert reopened.status == TaskStatus.PENDING
    assert reopened.completed_at is None
    assert reopened.started_at is None

    child_after_reopen = board.get(child.id)
    assert child_after_reopen is not None
    assert parent.id in child_after_reopen.blocked_by
    assert child_after_reopen.status == TaskStatus.BLOCKED
