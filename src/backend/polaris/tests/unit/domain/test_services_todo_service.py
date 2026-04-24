"""Tests for polaris.domain.services.todo_service."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
from polaris.domain.services.todo_service import (
    NagReminder,
    Priority,
    TodoItem,
    TodoService,
    TodoStatus,
    get_todo_service,
    reset_todo_service,
)


class TestTodoStatus:
    def test_values(self) -> None:
        assert TodoStatus.PENDING.value == "pending"
        assert TodoStatus.IN_PROGRESS.value == "in_progress"
        assert TodoStatus.COMPLETED.value == "completed"


class TestPriority:
    def test_values(self) -> None:
        assert Priority.CRITICAL.value == "critical"
        assert Priority.HIGH.value == "high"
        assert Priority.MEDIUM.value == "medium"
        assert Priority.LOW.value == "low"


class TestTodoItem:
    def test_defaults(self) -> None:
        item = TodoItem(id="1", content="test")
        assert item.status == TodoStatus.PENDING
        assert item.priority == Priority.MEDIUM

    def test_text_alias(self) -> None:
        item = TodoItem(id="1", content="test")
        assert item.text == "test"

    def test_to_dict(self) -> None:
        item = TodoItem(id="1", content="test")
        d = item.to_dict()
        assert d["id"] == "1"
        assert d["content"] == "test"
        assert d["text"] == "test"

    def test_from_dict(self) -> None:
        item = TodoItem.from_dict({"id": "1", "content": "test", "status": "completed"})
        assert item.status == TodoStatus.COMPLETED

    def test_from_dict_with_text_fallback(self) -> None:
        item = TodoItem.from_dict({"id": "1", "text": "fallback"})
        assert item.content == "fallback"


class TestNagReminder:
    def test_should_nag(self) -> None:
        nag = NagReminder(rounds_since_update=3)
        assert nag.should_nag(threshold=3) is True

    def test_should_not_nag(self) -> None:
        nag = NagReminder(rounds_since_update=2)
        assert nag.should_nag(threshold=3) is False

    def test_should_not_nag_if_triggered(self) -> None:
        nag = NagReminder(rounds_since_update=5, nag_triggered=True)
        assert nag.should_nag(threshold=3) is False

    def test_reset(self) -> None:
        nag = NagReminder(rounds_since_update=5, nag_triggered=True)
        nag.reset()
        assert nag.rounds_since_update == 0
        assert nag.nag_triggered is False


class TestTodoService:
    def test_add_item(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            svc = TodoService(state_file=Path(tmpdir) / "todo.json")
            item = svc.add_item("Test task")
            assert item.content == "Test task"
            assert item.status == TodoStatus.PENDING

    def test_add_item_with_priority(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            svc = TodoService(state_file=Path(tmpdir) / "todo.json")
            item = svc.add_item("Test", priority=Priority.HIGH)
            assert item.priority == Priority.HIGH

    def test_add_item_string_priority(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            svc = TodoService(state_file=Path(tmpdir) / "todo.json")
            item = svc.add_item("Test", priority="high")
            assert item.priority == Priority.HIGH

    def test_max_items(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            svc = TodoService(state_file=Path(tmpdir) / "todo.json")
            for i in range(svc.MAX_ITEMS):
                svc.add_item(f"Task {i}")
            with pytest.raises(ValueError, match="Maximum"):
                svc.add_item("Overflow")

    def test_get_item(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            svc = TodoService(state_file=Path(tmpdir) / "todo.json")
            svc.add_item("Test", item_id="t1")
            item = svc.get_item("t1")
            assert item is not None
            assert item.content == "Test"

    def test_get_item_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            svc = TodoService(state_file=Path(tmpdir) / "todo.json")
            assert svc.get_item("missing") is None

    def test_list_items(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            svc = TodoService(state_file=Path(tmpdir) / "todo.json")
            svc.add_item("A")
            svc.add_item("B")
            assert len(svc.list_items()) == 2

    def test_list_items_filtered(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            svc = TodoService(state_file=Path(tmpdir) / "todo.json")
            svc.add_item("A")
            svc.add_item("B")
            svc.mark_done(svc.list_items()[0].id)
            pending = svc.list_items(status="pending")
            completed = svc.list_items(status="completed")
            assert len(pending) == 1
            assert len(completed) == 1

    def test_mark_done(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            svc = TodoService(state_file=Path(tmpdir) / "todo.json")
            item = svc.add_item("Test")
            result = svc.mark_done(item.id)
            assert result is not None
            assert result.status == TodoStatus.COMPLETED
            assert result.completed_at is not None

    def test_mark_done_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            svc = TodoService(state_file=Path(tmpdir) / "todo.json")
            assert svc.mark_done("missing") is None

    def test_mark_in_progress(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            svc = TodoService(state_file=Path(tmpdir) / "todo.json")
            item = svc.add_item("Test")
            result = svc.mark_in_progress(item.id)
            assert result is not None
            assert result.status == TodoStatus.IN_PROGRESS

    def test_mark_in_progress_conflict(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            svc = TodoService(state_file=Path(tmpdir) / "todo.json")
            item1 = svc.add_item("A")
            item2 = svc.add_item("B")
            svc.mark_in_progress(item1.id)
            with pytest.raises(ValueError, match="already in_progress"):
                svc.mark_in_progress(item2.id)

    def test_get_next_item(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            svc = TodoService(state_file=Path(tmpdir) / "todo.json")
            svc.add_item("Low", priority=Priority.LOW)
            svc.add_item("High", priority=Priority.HIGH)
            next_item = svc.get_next_item()
            assert next_item is not None
            assert next_item.content == "High"

    def test_get_next_item_in_progress(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            svc = TodoService(state_file=Path(tmpdir) / "todo.json")
            item = svc.add_item("Test")
            svc.mark_in_progress(item.id)
            next_item = svc.get_next_item()
            assert next_item is not None
            assert next_item.id == item.id

    def test_update_item(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            svc = TodoService(state_file=Path(tmpdir) / "todo.json")
            item = svc.add_item("Old")
            updated = svc.update_item(item.id, text="New")
            assert updated is not None
            assert updated.content == "New"

    def test_update_item_status(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            svc = TodoService(state_file=Path(tmpdir) / "todo.json")
            item = svc.add_item("Test")
            updated = svc.update_item(item.id, status=TodoStatus.COMPLETED)
            assert updated is not None
            assert updated.status == TodoStatus.COMPLETED

    def test_set_in_progress(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            svc = TodoService(state_file=Path(tmpdir) / "todo.json")
            item = svc.add_item("Test")
            result = svc.set_in_progress(item.id)
            assert result is not None
            assert result.status == TodoStatus.IN_PROGRESS

    def test_complete_item(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            svc = TodoService(state_file=Path(tmpdir) / "todo.json")
            item = svc.add_item("Test")
            result = svc.complete_item(item.id)
            assert result is not None
            assert result.status == TodoStatus.COMPLETED

    def test_remove_item(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            svc = TodoService(state_file=Path(tmpdir) / "todo.json")
            item = svc.add_item("Test")
            assert svc.remove_item(item.id) is True
            assert svc.get_item(item.id) is None

    def test_remove_item_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            svc = TodoService(state_file=Path(tmpdir) / "todo.json")
            assert svc.remove_item("missing") is False

    def test_get_items(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            svc = TodoService(state_file=Path(tmpdir) / "todo.json")
            svc.add_item("A")
            svc.add_item("B")
            assert len(svc.get_items()) == 2
            assert len(svc.get_items(TodoStatus.PENDING)) == 2

    def test_get_in_progress(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            svc = TodoService(state_file=Path(tmpdir) / "todo.json")
            item = svc.add_item("Test")
            assert svc.get_in_progress() is None
            svc.mark_in_progress(item.id)
            assert svc.get_in_progress() is not None

    def test_check_stall_no_in_progress(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            svc = TodoService(state_file=Path(tmpdir) / "todo.json")
            assert svc.check_stall() is None

    def test_on_round_complete_no_in_progress(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            svc = TodoService(state_file=Path(tmpdir) / "todo.json")
            assert svc.on_round_complete() is None

    def test_on_round_complete_triggers_nag(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            svc = TodoService(state_file=Path(tmpdir) / "todo.json")
            item = svc.add_item("Test")
            svc.mark_in_progress(item.id)
            # mark_in_progress resets nag and sets last_in_progress_id
            # Each on_round_complete increments rounds_since_update
            # Need NAG_THRESHOLD_ROUNDS calls to trigger
            msg = None
            for _ in range(svc.NAG_THRESHOLD_ROUNDS):
                msg = svc.on_round_complete()
            assert msg is not None
            assert "NAG REMINDER" in msg

    def test_reset_nag(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            svc = TodoService(state_file=Path(tmpdir) / "todo.json")
            item = svc.add_item("Test")
            svc.mark_in_progress(item.id)
            for _ in range(svc.NAG_THRESHOLD_ROUNDS + 1):
                svc.on_round_complete()
            svc.reset_nag()
            assert svc._nag.nag_triggered is False
            assert svc._nag.rounds_since_update == 0

    def test_get_summary(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            svc = TodoService(state_file=Path(tmpdir) / "todo.json")
            svc.add_item("A")
            svc.add_item("B")
            svc.mark_done(svc.list_items()[0].id)
            summary = svc.get_summary()
            assert summary["total"] == 2
            assert summary["completed"] == 1
            assert summary["pending"] == 1

    def test_persistence(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            state_file = Path(tmpdir) / "todo.json"
            svc1 = TodoService(state_file=state_file)
            svc1.add_item("Persisted")
            # Create new instance with same file
            svc2 = TodoService(state_file=state_file)
            items = svc2.list_items()
            assert len(items) == 1
            assert items[0].content == "Persisted"

    def test_to_dict(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            svc = TodoService(state_file=Path(tmpdir) / "todo.json")
            svc.add_item("A")
            d = svc.to_dict()
            assert "items" in d
            assert "summary" in d


class TestGlobalFunctions:
    def test_get_and_reset(self) -> None:
        reset_todo_service()
        svc1 = get_todo_service(state_file=Path("/tmp/test_todo.json"))
        svc2 = get_todo_service()
        assert svc1 is svc2
        reset_todo_service()
        svc3 = get_todo_service(state_file=Path("/tmp/test_todo.json"))
        assert svc3 is not svc1
