"""Workspace resolution helpers for HTTP delivery surfaces."""

from __future__ import annotations

import os
import sys
from contextlib import suppress
from copy import copy
from pathlib import Path
from typing import Any
from unittest.mock import Mock


def _is_case_insensitive_platform() -> bool:
    """Return True when the runtime platform treats path casing as case-insensitive.

    Used to decide whether workspace equality should ignore case. On macOS
    (HFS+/APFS default) and Windows (NTFS) the common filesystem semantics
    are case-insensitive. Linux, including WSL Linux paths such as ``/home``,
    remains case-sensitive by default and must not be folded globally.
    """
    if sys.platform == "darwin":
        return True
    if sys.platform.startswith("win") or sys.platform.startswith("cygwin"):
        return True
    return False


_CASE_INSENSITIVE_FS = _is_case_insensitive_platform()
INSTANCE_WORKSPACE_BINDING_ENV = "KERNELONE_INSTANCE_WORKSPACE"


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
    """Resolve active desktop workspace from compatible settings fields."""
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


def comparable_workspace_value(value: Any) -> str:
    """Normalize a workspace value for equality checks without changing API payloads."""

    text = _workspace_text(value)
    if not text:
        return ""
    normalized = text.replace("\\", "/").rstrip("/")
    if normalized in {".", "./"}:
        return "."
    try:
        return str(Path(text).expanduser().resolve()).replace("\\", "/").rstrip("/")
    except (OSError, RuntimeError, ValueError):
        return normalized


def workspace_values_match(left: Any, right: Any) -> bool:
    """Return whether two workspace tokens identify the same workspace.

    Comparison is platform-aware: on case-insensitive filesystems
    (macOS HFS+/APFS, Windows NTFS) the two sides are lowercased before
    equality so ``/Foo`` and ``/foo`` resolve to the same directory and are
    treated as equal. On case-sensitive filesystems (default Linux ext4,
    WSL Linux paths, etc.) the original casing is preserved so ``/Foo`` and
    ``/foo`` remain distinct.

    Rationale: callers (HTTP routers, ACL filters, snapshot guards) pass
    workspace strings that may originate from user input, request bodies,
    or persisted canonical paths. ``Path.resolve()`` keeps the supplied
    case, so naive ``str.lower()``/``.casefold()`` on every platform would
    falsely unify distinct directories on case-sensitive filesystems
    (e.g. two project folders whose names differ only by case). The
    platform gate here matches the actual underlying filesystem semantics.
    """

    left_value = comparable_workspace_value(left)
    right_value = comparable_workspace_value(right)
    if not left_value or not right_value:
        return False
    if _CASE_INSENSITIVE_FS:
        return left_value.lower() == right_value.lower()
    return left_value == right_value


def process_bound_workspace() -> str:
    """Return immutable workspace selected when this backend process started."""

    return _workspace_text(os.environ.get(INSTANCE_WORKSPACE_BINDING_ENV, ""))


def workspace_binding_conflict(requested: Any) -> tuple[str, str] | None:
    """Return ``(bound, requested)`` when a request crosses process ownership."""

    bound = process_bound_workspace()
    requested_text = _workspace_text(requested)
    if not bound or not requested_text or workspace_values_match(bound, requested_text):
        return None
    return bound, requested_text


def enforce_process_bound_workspace(settings: Any) -> bool:
    """Pin a settings object to the backend process workspace.

    ``KERNELONE_INSTANCE_WORKSPACE`` is the instance authority.  A stale
    settings object must never initialize a backend against another
    workspace: resident services would bind to that foreign workspace before
    the HTTP rebind guards can run.  Return whether a correction was applied.
    """

    bound = process_bound_workspace()
    if not bound:
        return False
    active = active_workspace_value(settings)
    if active and workspace_values_match(bound, active):
        return False
    settings.workspace = Path(bound)
    if hasattr(settings, "workspace_path"):
        with suppress(AttributeError, TypeError, ValueError):
            settings.workspace_path = bound
    return True


__all__ = [
    "active_workspace_value",
    "comparable_workspace_value",
    "enforce_process_bound_workspace",
    "process_bound_workspace",
    "requested_or_active_workspace",
    "settings_with_workspace_override",
    "workspace_binding_conflict",
    "workspace_values_match",
]
