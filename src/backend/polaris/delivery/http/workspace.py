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
    (HFS+/APFS default) and Windows (NTFS) the filesystem reports the same
    directory for ``/Foo`` and ``/foo``. WSL with the ``case=off`` drvfs mount
    option behaves the same way. On case-sensitive filesystems (default
    Linux ext4, etc.) the same strings refer to different paths and must NOT
    be folded.
    """
    if sys.platform == "darwin":
        return True
    if sys.platform.startswith("win") or sys.platform.startswith("cygwin"):
        return True
    # WSL: detected via /proc/version when available. Treat WSL as
    # case-insensitive by default because the default drvfs mount is
    # case=off, which is what the platform heuristic here cares about.
    proc_version = Path("/proc/version")
    if proc_version.exists():
        try:
            text = proc_version.read_text(encoding="utf-8", errors="ignore").lower()
        except OSError:
            text = ""
        if "microsoft" in text or "wsl" in text:
            return True
    return False


_CASE_INSENSITIVE_FS = _is_case_insensitive_platform()


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
    (macOS HFS+/APFS, Windows NTFS, WSL with ``case=off``) the two sides
    are lowercased before equality so ``/Foo`` and ``/foo`` resolve to the
    same directory and are treated as equal. On case-sensitive filesystems
    (default Linux ext4, etc.) the original casing is preserved so
    ``/Foo`` and ``/foo`` remain distinct.

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


__all__ = [
    "active_workspace_value",
    "comparable_workspace_value",
    "requested_or_active_workspace",
    "settings_with_workspace_override",
    "workspace_values_match",
]
