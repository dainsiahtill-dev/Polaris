"""Public helpers for consuming TaskRuntime row evidence."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any


def task_row_execution_event_failure(row: Mapping[str, Any]) -> dict[str, Any] | None:
    """Return failed TaskRuntime execution-event evidence from a row projection.

    TaskRuntime row writers may persist the row but fail to append the
    authoritative ``task_runtime.execution`` fact. Consumers that dispatch work
    or report a state transition as successful must check this projection before
    advancing downstream state.
    """

    events: list[Mapping[str, Any]] = []
    execution_event = row.get("execution_event")
    if isinstance(execution_event, Mapping):
        events.append(execution_event)

    execution_events = row.get("execution_events")
    if isinstance(execution_events, Sequence) and not isinstance(execution_events, (str, bytes, bytearray)):
        events.extend(item for item in execution_events if isinstance(item, Mapping))

    for event in events:
        if event.get("ok") is False:
            return dict(event)
    return None
