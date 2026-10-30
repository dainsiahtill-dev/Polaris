"""WorkingStateManager - manages structured working state for ContextOS."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from polaris.kernelone.context.context_os.models_v2 import WorkingStateV2 as WorkingState


class WorkingStateManager:
    """Manages active working state, entities, and pending follow-ups.

    This is the mutable-but-controlled workspace for a turn.
    """

    def __init__(self, workspace: str = ".") -> None:
        self.workspace = workspace
        self._state: dict[str, Any] = {}
        self._working_state: WorkingState = WorkingState()

    def replace(self, working_state: WorkingState | dict[str, Any]) -> WorkingState:
        """Replace the canonical working-state view."""
        resolved = (
            WorkingState.from_mapping(working_state) if isinstance(working_state, dict) else deepcopy(working_state)
        )
        self._working_state = deepcopy(resolved)
        self._state["working_state"] = self.export()
        return self.current()

    def current(self) -> WorkingState:
        """Return the current canonical working state."""
        return deepcopy(self._working_state)

    def export(self) -> dict[str, Any]:
        """Export the canonical working state as a plain mapping."""
        return self.current().to_dict()

    def update(self, key: str, value: Any) -> None:
        """Update a working state key."""
        if key == "working_state":
            self.replace(value)
            return
        self._state[key] = deepcopy(value)

    def get(self, key: str) -> Any:
        """Retrieve a working state key."""
        if key == "working_state":
            return self.export()
        return deepcopy(self._state.get(key))

    def snapshot(self) -> dict[str, Any]:
        """Return a shallow snapshot of current working state."""
        snapshot = deepcopy(self._state)
        if snapshot.get("working_state") is None and self._working_state != WorkingState():
            snapshot["working_state"] = self.export()
        return snapshot
