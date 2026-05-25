"""Workspace filters for role-kernel LLM event delivery endpoints."""

from __future__ import annotations

from typing import Any

from polaris.delivery.http.workspace import workspace_values_match


def _event_workspaces(event: Any) -> list[Any]:
    metadata = getattr(event, "metadata", None)
    if not isinstance(metadata, dict):
        return []

    extra_fields = metadata.get("extra_fields")
    if not isinstance(extra_fields, dict):
        extra_fields = {}

    return [
        metadata.get("workspace"),
        extra_fields.get("workspace"),
    ]


def _event_matches_workspace(event: Any, workspace: str) -> bool:
    return any(workspace_values_match(candidate, workspace) for candidate in _event_workspaces(event))


def filter_llm_events_by_workspace(events: list[Any], workspace: str) -> list[Any]:
    """Return events explicitly tagged with the requested workspace."""

    if not str(workspace or "").strip():
        return events
    return [event for event in events if _event_matches_workspace(event, workspace)]


__all__ = ["filter_llm_events_by_workspace"]
