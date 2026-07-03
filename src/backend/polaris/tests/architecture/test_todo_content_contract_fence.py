"""Fences for TodoItem content/text field convergence."""

from __future__ import annotations

from polaris.domain.services.todo_service import TodoItem


def test_todo_item_writes_only_content_but_reads_old_text_state() -> None:
    """Todo state may migrate old text input, but new output uses content only."""
    migrated = TodoItem.from_dict({"id": "todo-1", "text": "old payload"})

    assert migrated.content == "old payload"
    assert not hasattr(migrated, "text")
    assert migrated.to_dict()["content"] == "old payload"
    assert "text" not in migrated.to_dict()
