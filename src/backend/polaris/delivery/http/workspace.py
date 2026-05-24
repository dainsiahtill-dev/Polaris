"""Workspace resolution helpers for HTTP delivery surfaces."""

from __future__ import annotations

import os
from contextlib import suppress
from copy import copy
from pathlib import Path
from typing import Any
from unittest.mock import Mock


def _workspace_text(value: Any) -> str:
    if isinstance(value, Mock):
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, os.PathLike):
        try:
            return os.fsdecode(value).strip()
        except (TypeError, ValueError):
            return ""
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


def settings_with_workspace_override(settings: Any, requested: Any = "") -> Any:
    """Return settings or a shallow clone pinned to an explicit workspace request."""

    target_workspace = requested_or_active_workspace(settings, requested)
    active_workspace = active_workspace_value(settings)
    if not target_workspace or target_workspace == active_workspace:
        return settings

    cloned = copy(settings)
    try:
        cloned.workspace = Path(target_workspace)
    except (TypeError, ValueError):
        cloned.workspace = target_workspace
    if hasattr(cloned, "workspace_path"):
        with suppress(AttributeError, TypeError, ValueError):
            cloned.workspace_path = target_workspace
    return cloned


__all__ = ["active_workspace_value", "requested_or_active_workspace", "settings_with_workspace_override"]
