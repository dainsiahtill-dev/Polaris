# ruff: noqa: E402
"""Tests for polaris.domain.services.todo_service module."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

BACKEND_DIR = str(Path(__file__).resolve().parents[4])
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

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
    def test_enum_values(self) -> None:
        assert TodoStatus.PENDING == "pending"
        assert TodoStatus.IN_PROGRESS == "in_progress"
        assert TodoStatus.COMPLETED == "completed"


class TestPriority:
    def test_enum_values(self) -> None:
        assert Priority.CRITICAL == "critical"
        assert Priority.HIGH == "high"
        assert Priority.MEDIUM == "medium"
        assert Priority.LOW == "low"


class TestTodoItem:
    def test_creation_defaults(self, tmp_path) -> None:
        item = TodoItem(id="t1", content="Test task")
        assert item.id == "t1"
        assert item.content == "Test task"
        assert item.status == TodoStatus.PENDING
        assert item.priority == Priority.MEDIUM
        assert item.tags == []
        assert item.completed_at is None

    def test_text_alias(self) -> None:
        item = TodoItem(id="t1", content="hello")
        assert item.text == "hello"
        assert item.content == "hello"

    def test_to_dict(self) -> None:
        item = TodoItem(id="t1", content="hello", status=TodoStatus.COMPLETED, priority=Priority.HIGH, tags=["urgent"])
        d = item.to_dict()
        assert d["id"] == "t1"
        assert d["content"] == "hello"
        assert d["status"] == "completed"
        assert d["priority"] == "high"
        assert d["tags"] == ["urgent"]
        assert d["completed_at"] is None  # Only set via mark_done, not at init

    def test_from_dict(self) -> None:
        item = TodoItem.from_dict(
            {
                "id": "t1",
                "content": "hello",
                "status": "in_progress",
                "priority": "critical",
                "tags": ["a"],
                "completed_at": 123.0,
            }
        )
        assert item.id == "t1"
        assert item.status == TodoStatus.IN_PROGRESS
        assert item.priority == Priority.CRITICAL

    def test_from_dict_with_text_field(self) -> None:
        item = TodoItem.from_dict({"id": "t1", "text": "hello", "status": "pending"})
        assert item.content == "hello"
        assert item.text == "hello"

    def test_from_dict_missing_content(self) -> None:
        item = TodoItem.from_dict({"id": "t1", "status": "pending"})
        assert item.content == ""


class TestNagReminder:
    def test_should_nag_default(self) -> None:
        nag = NagReminder()
        assert nag.should_nag() is False

    def test_should_nag_at_threshold(self) -> None:
        nag = NagReminder(rounds_since_update=3)
        assert nag.should_nag() is True

    def test_should_nag_already_triggered(self) -> None:
        nag = NagReminder(rounds_since_update=5, nag_triggered=True)
        assert nag.should_nag() is False

    def test_reset(self) -> None:
        nag = NagReminder(rounds_since_update=5, nag_triggered=True)
        nag.reset()
        assert nag.rounds_since_update == 0
        assert nag.nag_triggered is False


class TestTodoService:
    @pytest.fixture(autouse=True)
    def reset_singleton(self):
        reset_todo_service()
        yield
        reset_todo_service()

    def test_add_item(self, tmp_path) -> None:
        svc = TodoService(tmp_path / "todo.json")
        item = svc.add_item("Buy milk")
        assert item.content == "Buy milk"
        assert item.status == TodoStatus.PENDING
        assert svc.get_item(item.id) is not None

    def test_add_item_with_custom_id(self, tmp_path) -> None:
        svc = TodoService(tmp_path / "todo.json")
        item = svc.add_item("Buy milk", item_id="custom-1")
        assert item.id == "custom-1"

    def test_add_item_max_limit(self, tmp_path) -> None:
        svc = TodoService(tmp_path / "todo.json")
        for i in range(svc.MAX_ITEMS):
            svc.add_item(f"task {i}")
        with pytest.raises(ValueError, match="Maximum"):
            svc.add_item("overflow")

    def test_add_item_with_priority_string(self, tmp_path) -> None:
        svc = TodoService(tmp_path / "todo.json")
        item = svc.add_item("urgent", priority="high")
        assert item.priority == Priority.HIGH

    def test_add_item_with_priority_enum(self, tmp_path) -> None:
        svc = TodoService(tmp_path / "todo.json")
        item = svc.add_item("urgent", priority=Priority.CRITICAL)
        assert item.priority == Priority.CRITICAL

    def test_get_item_missing(self, tmp_path) -> None:
        svc = TodoService(tmp_path / "todo.json")
        assert svc.get_item("missing") is None

    def test_list_items(self, tmp_path) -> None:
        svc = TodoService(tmp_path / "todo.json")
        svc.add_item("a")
        svc.add_item("b")
        assert len(svc.list_items()) == 2

    def test_list_items_filtered(self, tmp_path) -> None:
        svc = TodoService(tmp_path / "todo.json")
        svc.add_item("a")
        svc.add_item("b")
        svc.mark_done(svc._items[0].id)
        completed = svc.list_items(status="completed")
        assert len(completed) == 1

    def test_mark_done(self, tmp_path) -> None:
        svc = TodoService(tmp_path / "todo.json")
        item = svc.add_item("task")
        result = svc.mark_done(item.id)
        assert result is not None
        assert result.status == TodoStatus.COMPLETED
        assert result.completed_at is not None

    def test_mark_done_missing(self, tmp_path) -> None:
        svc = TodoService(tmp_path / "todo.json")
        assert svc.mark_done("missing") is None

    def test_mark_in_progress(self, tmp_path) -> None:
        svc = TodoService(tmp_path / "todo.json")
        item = svc.add_item("task")
        result = svc.mark_in_progress(item.id)
        assert result is not None
        assert result.status == TodoStatus.IN_PROGRESS

    def test_mark_in_progress_conflict(self, tmp_path) -> None:
        svc = TodoService(tmp_path / "todo.json")
        item1 = svc.add_item("task1")
        item2 = svc.add_item("task2")
        svc.mark_in_progress(item1.id)
        with pytest.raises(ValueError, match="already in_progress"):
            svc.mark_in_progress(item2.id)

    def test_get_next_item_in_progress(self, tmp_path) -> None:
        svc = TodoService(tmp_path / "todo.json")
        item = svc.add_item("task")
        svc.mark_in_progress(item.id)
        assert svc.get_next_item().id == item.id

    def test_get_next_item_priority(self, tmp_path) -> None:
        svc = TodoService(tmp_path / "todo.json")
        svc.add_item("low", priority=Priority.LOW)
        svc.add_item("high", priority=Priority.HIGH)
        next_item = svc.get_next_item()
        assert next_item.priority == Priority.HIGH

    def test_update_item_text(self, tmp_path) -> None:
        svc = TodoService(tmp_path / "todo.json")
        item = svc.add_item("old")
        svc.update_item(item.id, text="new")
        assert svc.get_item(item.id).content == "new"

    def test_update_item_status(self, tmp_path) -> None:
        svc = TodoService(tmp_path / "todo.json")
        item = svc.add_item("task")
        svc.update_item(item.id, status=TodoStatus.COMPLETED)
        assert svc.get_item(item.id).status == TodoStatus.COMPLETED

    def test_update_item_missing(self, tmp_path) -> None:
        svc = TodoService(tmp_path / "todo.json")
        assert svc.update_item("missing", text="new") is None

    def test_set_in_progress_conflict(self, tmp_path) -> None:
        svc = TodoService(tmp_path / "todo.json")
        item1 = svc.add_item("task1")
        item2 = svc.add_item("task2")
        svc.set_in_progress(item1.id)
        with pytest.raises(ValueError, match="already in_progress"):
            svc.set_in_progress(item2.id)

    def test_complete_item(self, tmp_path) -> None:
        svc = TodoService(tmp_path / "todo.json")
        item = svc.add_item("task")
        svc.complete_item(item.id)
        assert svc.get_item(item.id).status == TodoStatus.COMPLETED

    def test_remove_item(self, tmp_path) -> None:
        svc = TodoService(tmp_path / "todo.json")
        item = svc.add_item("task")
        assert svc.remove_item(item.id) is True
        assert svc.get_item(item.id) is None

    def test_remove_item_missing(self, tmp_path) -> None:
        svc = TodoService(tmp_path / "todo.json")
        assert svc.remove_item("missing") is False

    def test_get_items_filtered(self, tmp_path) -> None:
        svc = TodoService(tmp_path / "todo.json")
        item = svc.add_item("task")
        svc.mark_done(item.id)
        assert len(svc.get_items(TodoStatus.COMPLETED)) == 1
        assert len(svc.get_items(TodoStatus.PENDING)) == 0

    def test_get_in_progress(self, tmp_path) -> None:
        svc = TodoService(tmp_path / "todo.json")
        assert svc.get_in_progress() is None
        item = svc.add_item("task")
        svc.mark_in_progress(item.id)
        assert svc.get_in_progress() is not None

    def test_check_stall_no_in_progress(self, tmp_path) -> None:
        svc = TodoService(tmp_path / "todo.json")
        assert svc.check_stall() is None

    def test_check_stall_not_stalled(self, tmp_path) -> None:
        svc = TodoService(tmp_path / "todo.json")
        item = svc.add_item("task")
        svc.mark_in_progress(item.id)
        assert svc.check_stall() is None

    def test_on_round_complete_no_in_progress(self, tmp_path) -> None:
        svc = TodoService(tmp_path / "todo.json")
        assert svc.on_round_complete() is None
        assert svc._round_counter == 1

    def test_on_round_complete_nag(self, tmp_path) -> None:
        svc = TodoService(tmp_path / "todo.json")
        item = svc.add_item("task")
        svc.mark_in_progress(item.id)
        svc.on_round_complete()
        svc.on_round_complete()
        msg = svc.on_round_complete()
        assert msg is not None
        assert "NAG REMINDER" in msg

    def test_reset_nag(self, tmp_path) -> None:
        svc = TodoService(tmp_path / "todo.json")
        item = svc.add_item("task")
        svc.mark_in_progress(item.id)
        svc.on_round_complete()
        svc.on_round_complete()
        svc.on_round_complete()
        svc.reset_nag()
        assert svc._nag.rounds_since_update == 0

    def test_get_summary(self, tmp_path) -> None:
        svc = TodoService(tmp_path / "todo.json")
        svc.add_item("a")
        svc.add_item("b")
        svc.mark_done(svc._items[0].id)
        summary = svc.get_summary()
        assert summary["total"] == 2
        assert summary["completed"] == 1
        assert summary["pending"] == 1

    def test_persistence(self, tmp_path) -> None:
        state_file = tmp_path / "todo.json"
        svc1 = TodoService(state_file)
        svc1.add_item("task")
        svc2 = TodoService(state_file)
        assert len(svc2._items) == 1
        assert svc2._items[0].content == "task"

    def test_singleton(self, tmp_path) -> None:
        reset_todo_service()
        svc1 = get_todo_service(state_file=tmp_path / "todo.json")
        svc2 = get_todo_service()
        assert svc1 is svc2

    def test_load_corrupted_state(self, tmp_path) -> None:
        state_file = tmp_path / "todo.json"
        state_file.write_text("not json", encoding="utf-8")
        svc = TodoService(state_file)
        assert svc._items == []
