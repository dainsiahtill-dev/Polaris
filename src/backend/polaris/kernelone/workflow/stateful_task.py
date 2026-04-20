"""StatefulTask - Base class for activities with intermediate state.

This module provides a base class for activities that need to maintain
state across executions and integrate with the checkpoint system.

Design principles:
1. Activities subclass StatefulTask and implement execute()
2. State is serialized to dict for checkpointing
3. load_state/save_state methods hook into the checkpoint system
4. Supports both sync and async state persistence

References:
- CheckpointManager: kernelone/workflow/checkpoint_manager.py
- ActivityRunner: kernelone/workflow/activity_runner.py
- SagaWorkflowEngine: kernelone/workflow/saga_engine.py
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class TaskStateSnapshot:
    """A serializable snapshot of task state for checkpointing.

    This is the standard format for storing task state in checkpoints,
    compatible with CheckpointManager.task_states_snapshot.
    """

    task_id: str
    status: str = "pending"
    attempt: int = 0
    result: dict[str, Any] | None = None
    error: str = ""
    intermediate_state: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)


class StatefulTask(ABC):
    """Base class for stateful activities that support checkpointing.

    Subclass this to create activities that need to maintain state
    across invocations and integrate with the checkpoint system.

    Usage with ActivityRunner:
        runner = ActivityRunner()
        task = MyStatefulTask()
        runner.register_handler("my_task", task.execute)

    Usage with SagaWorkflowEngine:
        # State is automatically checkpointed via CheckpointManager
        task_states[spec.task_id] = task.get_runtime_state()

    Example:
        class DataProcessingTask(StatefulTask):
            def __init__(self, task_id: str):
                self._task_id = task_id
                self._progress = 0
                self._processed_items: list[str] = []

            async def execute(self, input: dict[str, Any]) -> dict[str, Any]:
                items = input.get("items", [])
                for item in items:
                    await self._process_item(item)
                return {
                    "progress": self._progress,
                    "processed_count": len(self._processed_items),
                }

            def save_state(self) -> dict[str, Any]:
                return {
                    "progress": self._progress,
                    "processed_items": self._processed_items,
                }

            def load_state(self, state: dict[str, Any]) -> None:
                self._progress = state.get("progress", 0)
                self._processed_items = state.get("processed_items", [])

            def get_runtime_state(self) -> TaskStateSnapshot:
                return TaskStateSnapshot(
                    task_id=self._task_id,
                    status="in_progress",
                    intermediate_state=self.save_state(),
                )
    """

    def __init__(self, task_id: str | None = None) -> None:
        """Initialize StatefulTask.

        Args:
            task_id: Optional task identifier for tracking.
        """
        self._task_id = task_id or ""
        self._attempt = 0

    @property
    def task_id(self) -> str:
        """Task identifier."""
        return self._task_id

    @abstractmethod
    async def execute(self, input: dict[str, Any]) -> dict[str, Any]:
        """Execute the activity logic.

        Override this method in your subclass. This is the main
        entry point called by ActivityRunner.

        Args:
            input: Activity input payload from the workflow.

        Returns:
            Result dictionary that will be stored as task output.
        """
        ...

    def save_state(self) -> dict[str, Any]:
        """Save current state for checkpointing.

        Override this method to serialize your task's intermediate
        state. The returned dict must be JSON-serializable.

        Default implementation returns empty dict (no intermediate state).

        Returns:
            Dictionary containing serializable state.
        """
        return {}

    def load_state(self, state: dict[str, Any]) -> None:
        """Load state from a checkpoint.

        Override this method to restore your task's intermediate
        state from a checkpoint. Default implementation is a no-op
        for tasks that don't need state restoration.

        Args:
            state: Dictionary previously returned by save_state().
        """

    def get_runtime_state(self) -> TaskStateSnapshot:
        """Get current runtime state for CheckpointManager.

        This creates a TaskStateSnapshot that integrates with the
        workflow's checkpoint system.

        Override to provide custom status or add metadata.

        Returns:
            TaskStateSnapshot for checkpointing.
        """
        return TaskStateSnapshot(
            task_id=self._task_id,
            status="in_progress",
            intermediate_state=self.save_state(),
        )

    @classmethod
    def from_snapshot(
        cls,
        snapshot: TaskStateSnapshot,
        **kwargs: Any,
    ) -> StatefulTask:
        """Create a StatefulTask instance from a checkpoint snapshot.

        This is a factory method that:
        1. Creates a new instance using kwargs
        2. Loads the intermediate state from the snapshot

        Override if your __init__ signature differs from the standard.

        Args:
            snapshot: TaskStateSnapshot from a checkpoint.
            **kwargs: Additional arguments passed to __init__.

        Returns:
            New StatefulTask instance with restored state.
        """
        task = cls(**kwargs)
        task.load_state(snapshot.intermediate_state)
        return task


class StatefulTaskMixin(StatefulTask):
    """Concrete base class that can be directly instantiated.

    This mixin provides a default implementation that stores state
    in a dict, making it easy to use without subclassing.

    Example:
        task = StatefulTaskMixin(task_id="t1")
        task.state["counter"] = 0

        async def increment(input):
            task.state["counter"] += 1
            return {"counter": task.state["counter"]}

        task.execute = increment
    """

    def __init__(
        self,
        task_id: str | None = None,
        initial_state: dict[str, Any] | None = None,
    ) -> None:
        """Initialize with mutable state dict.

        Args:
            task_id: Optional task identifier.
            initial_state: Optional initial state dict.
        """
        super().__init__(task_id=task_id)
        self._state: dict[str, Any] = initial_state or {}

    def save_state(self) -> dict[str, Any]:
        """Save current state dict."""
        return dict(self._state)

    def load_state(self, state: dict[str, Any]) -> None:
        """Load state dict from checkpoint."""
        self._state = dict(state) if state else {}

    @property
    def state(self) -> dict[str, Any]:
        """Direct access to the state dict for convenience."""
        return self._state


__all__ = [
    "StatefulTask",
    "StatefulTaskMixin",
    "TaskStateSnapshot",
]
