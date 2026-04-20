"""Enhanced Todo service with Nag reminders for Polaris backend.

Extends the basic TodoManager with round-based nag reminders.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any


class TodoStatus(str, Enum):
    """Todo item status."""

    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"


class Priority(str, Enum):
    """Todo item priority."""

    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


@dataclass
class TodoItem:
    """A todo item."""

    id: str
    content: str  # Alias for text, for backward compatibility
    status: TodoStatus = TodoStatus.PENDING
    priority: Priority = Priority.MEDIUM
    tags: list[str] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    completed_at: float | None = None

    @property
    def text(self) -> str:
        """Backward compatibility alias for content."""
        return self.content

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "content": self.content,
            "text": self.text,  # For backward compatibility
            "status": self.status.value,
            "priority": self.priority.value,
            "tags": self.tags,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "completed_at": self.completed_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TodoItem:
        # Support both 'content' and 'text' fields for backward compatibility
        content = data.get("content") or data.get("text", "")
        return cls(
            id=data["id"],
            content=content,
            status=TodoStatus(data.get("status", "pending")),
            priority=Priority(data.get("priority", "medium")),
            tags=data.get("tags", []),
            created_at=data.get("created_at", time.time()),
            updated_at=data.get("updated_at", time.time()),
            completed_at=data.get("completed_at"),
        )


@dataclass
class NagReminder:
    """Nag reminder state."""

    rounds_since_update: int = 0
    last_in_progress_id: str = ""
    nag_triggered: bool = False

    def should_nag(self, threshold: int = 3) -> bool:
        """Check if nag should be triggered."""
        return self.rounds_since_update >= threshold and not self.nag_triggered

    def reset(self) -> None:
        """Reset nag state."""
        self.rounds_since_update = 0
        self.nag_triggered = False


class TodoService:
    """Enhanced Todo service with Nag reminders.

    Features:
    - Standard todo CRUD operations
    - Time-based stall detection
    - Round-based nag reminders
    - Persistence
    """

    MAX_ITEMS = 20
    STALL_THRESHOLD_SECONDS = 300  # 5 minutes
    NAG_THRESHOLD_ROUNDS = 3

    def __init__(
        self,
        state_file: Path | str,
        events_file: Path | str | None = None,
    ) -> None:
        """Initialize todo service.

        Args:
            state_file: Path to state file
            events_file: Path to events file (optional)
        """
        self.state_file = Path(state_file)
        self.events_file = Path(events_file) if events_file else None
        self._items: list[TodoItem] = []
        self._nag = NagReminder()
        self._round_counter = 0

        self._load_state()

    def _load_state(self) -> None:
        """Load state from file."""
        if self.state_file.exists():
            try:
                data = json.loads(self.state_file.read_text(encoding="utf-8"))
                self._items = [TodoItem.from_dict(item) for item in data.get("items", [])]
                nag_data = data.get("nag", {})
                self._nag = NagReminder(
                    rounds_since_update=nag_data.get("rounds_since_update", 0),
                    last_in_progress_id=nag_data.get("last_in_progress_id", ""),
                    nag_triggered=nag_data.get("nag_triggered", False),
                )
            except (RuntimeError, ValueError):
                self._items = []
                self._nag = NagReminder()

    def _save_state(self) -> None:
        """Save state to file."""
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "items": [item.to_dict() for item in self._items],
            "nag": {
                "rounds_since_update": self._nag.rounds_since_update,
                "last_in_progress_id": self._nag.last_in_progress_id,
                "nag_triggered": self._nag.nag_triggered,
            },
            "updated_at": time.time(),
        }
        self.state_file.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def _emit_event(self, event_type: str, payload: dict[str, Any]) -> None:
        """Emit an event."""
        if not self.events_file:
            return

        self.events_file.parent.mkdir(parents=True, exist_ok=True)
        event = {
            "type": event_type,
            "timestamp": time.time(),
            "payload": payload,
        }
        with self.events_file.open("a", encoding="utf-8") as f:
            f.write(json.dumps(event) + "\n")

    def add_item(
        self,
        content: str,
        item_id: str | None = None,
        priority: Priority | str | None = None,
        tags: list[str] | None = None,
    ) -> TodoItem:
        """Add a new todo item.

        Args:
            content: Item content/text
            item_id: Optional item ID (generated if not provided)
            priority: Optional priority (default: medium)
            tags: Optional list of tags

        Returns:
            Created item
        """
        if len(self._items) >= self.MAX_ITEMS:
            raise ValueError(f"Maximum {self.MAX_ITEMS} items allowed")

        # Convert string priority to enum
        if priority is None:
            priority = Priority.MEDIUM
        elif isinstance(priority, str):
            priority = Priority(priority.lower())

        item = TodoItem(
            id=item_id or f"item-{len(self._items) + 1}",
            content=content,
            priority=priority,
            tags=tags or [],
        )
        self._items.append(item)
        self._save_state()
        self._emit_event("item_added", item.to_dict())
        return item

    def get_item(self, item_id: str) -> TodoItem | None:
        """Get a todo item by ID.

        Args:
            item_id: Item ID

        Returns:
            Item or None if not found
        """
        for item in self._items:
            if item.id == item_id:
                return item
        return None

    def list_items(self, status: str | None = None) -> list[TodoItem]:
        """List all todo items, optionally filtered by status.

        Args:
            status: Optional status filter

        Returns:
            List of items
        """
        if status:
            status_enum = TodoStatus(status.lower())
            return self.get_items(status_enum)
        return self._items.copy()

    def mark_done(self, item_id: str) -> TodoItem | None:
        """Mark an item as completed.

        Args:
            item_id: Item ID

        Returns:
            Updated item or None if not found
        """
        for item in self._items:
            if item.id == item_id:
                item.status = TodoStatus.COMPLETED
                item.completed_at = time.time()
                item.updated_at = time.time()
                self._save_state()
                self._emit_event("item_completed", item.to_dict())
                return item
        return None

    def mark_in_progress(self, item_id: str) -> TodoItem | None:
        """Mark an item as in_progress.

        Args:
            item_id: Item ID

        Returns:
            Updated item or None if not found

        Raises:
            ValueError: If another item is already in_progress
        """
        # Check if another item is already in_progress
        for item in self._items:
            if item.status == TodoStatus.IN_PROGRESS and item.id != item_id:
                raise ValueError(f"Item '{item.id}' is already in_progress")

        for item in self._items:
            if item.id == item_id:
                item.status = TodoStatus.IN_PROGRESS
                item.updated_at = time.time()
                self._nag.reset()
                self._nag.last_in_progress_id = item_id
                self._save_state()
                self._emit_event("item_in_progress", item.to_dict())
                return item
        return None

    def get_next_item(self) -> TodoItem | None:
        """Get the next recommended item to work on.

        Returns the highest priority pending item, or the in_progress item.

        Returns:
            Next item or None
        """
        # First, return in_progress item if any
        in_progress = self.get_in_progress()
        if in_progress:
            return in_progress

        # Then return highest priority pending item
        pending = [i for i in self._items if i.status == TodoStatus.PENDING]
        if not pending:
            return None

        # Sort by priority (critical > high > medium > low)
        priority_order = {
            Priority.CRITICAL: 0,
            Priority.HIGH: 1,
            Priority.MEDIUM: 2,
            Priority.LOW: 3,
        }
        pending.sort(key=lambda x: priority_order.get(x.priority, 2))
        return pending[0]

    def update_item(
        self,
        item_id: str,
        text: str | None = None,
        status: TodoStatus | str | None = None,
    ) -> TodoItem | None:
        """Update a todo item.

        Args:
            item_id: Item ID
            text: New text (optional)
            status: New status (optional)

        Returns:
            Updated item or None if not found
        """
        for item in self._items:
            if item.id == item_id:
                if text is not None:
                    # Update content (the text property is read-only, so update content)
                    item.content = text
                if status is not None:
                    if isinstance(status, str):
                        status = TodoStatus(status)

                    # Reset nag on status change
                    if item.status != status:
                        self._nag.reset()
                        if status == TodoStatus.IN_PROGRESS:
                            self._nag.last_in_progress_id = item_id

                    item.status = status

                item.updated_at = time.time()
                self._save_state()
                self._emit_event("item_updated", item.to_dict())
                return item

        return None

    def set_in_progress(self, item_id: str) -> TodoItem | None:
        """Set an item as in_progress.

        Args:
            item_id: Item ID

        Returns:
            Updated item or None
        """
        # Check if another item is already in_progress
        for item in self._items:
            if item.status == TodoStatus.IN_PROGRESS and item.id != item_id:
                raise ValueError(f"Item '{item.id}' is already in_progress")

        return self.update_item(item_id, status=TodoStatus.IN_PROGRESS)

    def complete_item(self, item_id: str) -> TodoItem | None:
        """Mark an item as completed.

        Args:
            item_id: Item ID

        Returns:
            Updated item or None
        """
        return self.update_item(item_id, status=TodoStatus.COMPLETED)

    def remove_item(self, item_id: str) -> bool:
        """Remove an item.

        Args:
            item_id: Item ID

        Returns:
            True if removed
        """
        for idx, item in enumerate(self._items):
            if item.id == item_id:
                self._items.pop(idx)
                self._save_state()
                self._emit_event("item_removed", {"id": item_id})
                return True
        return False

    def get_items(self, status: TodoStatus | None = None) -> list[TodoItem]:
        """Get items, optionally filtered by status.

        Args:
            status: Filter by status

        Returns:
            List of items
        """
        if status:
            return [item for item in self._items if item.status == status]
        return self._items.copy()

    def get_in_progress(self) -> TodoItem | None:
        """Get the currently in_progress item.

        Returns:
            In progress item or None
        """
        for item in self._items:
            if item.status == TodoStatus.IN_PROGRESS:
                return item
        return None

    def check_stall(self) -> dict[str, Any] | None:
        """Check for stalled tasks (time-based).

        Returns:
            Stall info or None
        """
        in_progress = self.get_in_progress()
        if not in_progress:
            return None

        elapsed = time.time() - in_progress.updated_at
        if elapsed > self.STALL_THRESHOLD_SECONDS:
            return {
                "item_id": in_progress.id,
                "text": in_progress.text,
                "elapsed_seconds": elapsed,
                "stall_detected": True,
            }

        return None

    def on_round_complete(self) -> str | None:
        """Call when a round completes to update nag counter.

        Returns:
            Nag message if triggered, None otherwise
        """
        self._round_counter += 1

        in_progress = self.get_in_progress()
        if not in_progress:
            self._nag.reset()
            self._save_state()
            return None

        # Check if in_progress item changed
        if in_progress.id != self._nag.last_in_progress_id:
            self._nag.reset()
            self._nag.last_in_progress_id = in_progress.id

        self._nag.rounds_since_update += 1

        # Check if nag should trigger
        if self._nag.should_nag(self.NAG_THRESHOLD_ROUNDS):
            self._nag.nag_triggered = True
            self._save_state()
            return (
                f"⚠️ NAG REMINDER: Task '{in_progress.text}' has been in_progress "
                f"for {self._nag.rounds_since_update} rounds without update. "
                f"Please update your progress or mark as completed."
            )

        self._save_state()
        return None

    def reset_nag(self) -> None:
        """Reset nag state."""
        self._nag.reset()
        self._save_state()

    def get_summary(self) -> dict[str, Any]:
        """Get todo list summary."""
        total = len(self._items)
        completed = len([i for i in self._items if i.status == TodoStatus.COMPLETED])
        pending = len([i for i in self._items if i.status == TodoStatus.PENDING])
        in_progress = self.get_in_progress()

        return {
            "total": total,
            "completed": completed,
            "pending": pending,
            "in_progress": in_progress.id if in_progress else None,
            "nag_rounds": self._nag.rounds_since_update,
        }

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "items": [item.to_dict() for item in self._items],
            "summary": self.get_summary(),
        }


# Singleton instance
_todo_service_instance: TodoService | None = None


def get_todo_service(
    state_file: Path | str | None = None,
    events_file: Path | str | None = None,
) -> TodoService:
    """Get or create TodoService singleton.

    Args:
        state_file: Path to state file (required for first initialization)
        events_file: Path to events file (optional)

    Returns:
        TodoService instance
    """
    global _todo_service_instance
    if _todo_service_instance is None:
        if state_file is None:
            # Use default location
            from polaris.kernelone.storage import (
                resolve_workspace_persistent_path,
            )

            workspace = "."
            state_path = resolve_workspace_persistent_path(workspace, "workspace/brain/todo_state.json")
            events_path = resolve_workspace_persistent_path(workspace, "workspace/brain/todo_events.jsonl")
            state_file = state_path
            events_file = events_path

        _todo_service_instance = TodoService(state_file, events_file)
    return _todo_service_instance


def reset_todo_service() -> None:
    """Reset the singleton (mainly for testing)."""
    global _todo_service_instance
    _todo_service_instance = None
