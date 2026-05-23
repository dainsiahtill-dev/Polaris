"""Workspace resolution helpers for HTTP delivery surfaces."""

from __future__ import annotations

from os import PathLike
from typing import Any


def _workspace_text(value: Any) -> str:
    if isinstance(value, (str, PathLike)):
        return str(value or "").strip()
    return ""


def active_workspace_value(settings: Any) -> str:
    """Resolve active desktop workspace with legacy fallback."""
    for attr in ("workspace_path", "workspace"):
        text = _workspace_text(getattr(settings, attr, ""))
        if text:
            return text
    return ""


def requested_or_active_workspace(settings: Any, requested: Any) -> str:
    """Resolve an explicit workspace request or fall back to active desktop state."""
    requested_text = _workspace_text(requested)
    normalized_requested = requested_text.replace("\\", "/")
    if requested_text and normalized_requested not in {".", "./"}:
        return requested_text

    active_text = active_workspace_value(settings)
    if active_text:
        return active_text
    return requested_text or "."


__all__ = ["active_workspace_value", "requested_or_active_workspace"]
