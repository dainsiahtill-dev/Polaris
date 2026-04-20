"""Control-flag utilities with registry-based stop signal resolution."""

from __future__ import annotations

import logging
import os
import re
from threading import RLock

from polaris.kernelone.constants import RoleId

from .fsync_mode import IO_FSYNC_ENV, is_fsync_enabled

try:
    from polaris.kernelone.storage import normalize_logical_rel_path, resolve_ramdisk_root
    from polaris.kernelone.storage.io_paths import build_cache_root, resolve_artifact_path
except ImportError:  # pragma: no cover - script-mode fallback
    from polaris.kernelone.storage import normalize_logical_rel_path, resolve_ramdisk_root  # type: ignore
    from polaris.kernelone.storage.io_paths import build_cache_root, resolve_artifact_path  # type: ignore

logger = logging.getLogger(__name__)

_PAUSE_FILE_NAME = "pause.flag"
_INTERRUPT_FILE_NAME = "interrupt.notice.md"
_IO_FSYNC_ENV = IO_FSYNC_ENV
_SIGNAL_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")

# Registry: signal name -> logical path. Keep defaults for current callers.
# Note: Using RoleId values as keys for type safety.
_STOP_SIGNAL_REGISTRY: dict[str, str] = {
    "stop": "runtime/control/stop.flag",
    RoleId.PM.value: "runtime/control/pm.stop.flag",
    RoleId.DIRECTOR.value: "runtime/control/director.stop.flag",
}
_STOP_SIGNAL_REGISTRY_LOCK = RLock()


def _fsync_enabled() -> bool:
    return is_fsync_enabled()


def _normalize_signal_name(signal_name: str) -> str:
    token = str(signal_name or "").strip().lower()
    if not token or not _SIGNAL_NAME_RE.fullmatch(token):
        raise ValueError(f"Invalid stop signal name: {signal_name}")
    return token


def _validate_control_logical_path(logical_path: str) -> str:
    normalized = normalize_logical_rel_path(logical_path)
    if not (normalized == "runtime/control" or normalized.startswith("runtime/control/")):
        raise ValueError(f"Unsupported control flag path: {logical_path}")
    return normalized


def register_stop_signal(signal_name: str, logical_path: str | None = None) -> str:
    """Register a stop signal name and its logical flag path."""
    token = _normalize_signal_name(signal_name)
    candidate = logical_path or f"runtime/control/{token}.stop.flag"
    normalized = _validate_control_logical_path(candidate)
    with _STOP_SIGNAL_REGISTRY_LOCK:
        _STOP_SIGNAL_REGISTRY[token] = normalized
    return normalized


def unregister_stop_signal(signal_name: str) -> bool:
    """Unregister a non-default stop signal."""
    token = _normalize_signal_name(signal_name)
    # Cannot unregister reserved stop signals
    if token in {"stop", RoleId.PM.value, RoleId.DIRECTOR.value}:
        return False
    with _STOP_SIGNAL_REGISTRY_LOCK:
        return _STOP_SIGNAL_REGISTRY.pop(token, None) is not None


def list_stop_signals() -> dict[str, str]:
    """Return a snapshot of registered stop signals."""
    with _STOP_SIGNAL_REGISTRY_LOCK:
        return dict(_STOP_SIGNAL_REGISTRY)


def control_flag_path(workspace: str, logical_path: str) -> str:
    cache_root = build_cache_root(resolve_ramdisk_root(None), workspace)
    normalized = _validate_control_logical_path(logical_path)
    return resolve_artifact_path(workspace, cache_root, normalized)


def stop_flag_path_for(workspace: str, signal_name: str) -> str:
    token = _normalize_signal_name(signal_name)
    with _STOP_SIGNAL_REGISTRY_LOCK:
        logical_path = _STOP_SIGNAL_REGISTRY.get(token)
    if not logical_path:
        logical_path = register_stop_signal(token)
    return control_flag_path(workspace, logical_path)


def stop_requested_for(workspace: str, signal_name: str) -> bool:
    try:
        return os.path.exists(stop_flag_path_for(workspace, signal_name))
    except (RuntimeError, ValueError) as exc:
        logger.warning(
            "stop_requested_for: path resolution failed for signal=%r workspace=%r — "
            "fail-safe: treating as stop requested. error=%s",
            signal_name,
            workspace,
            exc,
        )
        return True


def clear_stop_flag_for(workspace: str, signal_name: str) -> None:
    try:
        path = stop_flag_path_for(workspace, signal_name)
    except (RuntimeError, ValueError):
        logger.warning("Failed to get stop flag path for workspace: %s", workspace)
        return
    try:
        if path and os.path.exists(path):
            os.remove(path)
    except (RuntimeError, ValueError) as exc:
        logger.debug("Failed to remove stop flag %s: %s", path, exc)


def stop_flag_path(workspace: str) -> str:
    """Legacy alias for PM stop signal path."""
    return stop_flag_path_for(workspace, RoleId.PM.value)


def stop_requested(workspace: str) -> bool:
    """Legacy alias for PM stop signal check."""
    return stop_requested_for(workspace, RoleId.PM.value)


def clear_stop_flag(workspace: str) -> None:
    """Legacy alias for PM stop signal clear."""
    clear_stop_flag_for(workspace, RoleId.PM.value)


def pause_flag_path(workspace: str) -> str:
    return control_flag_path(workspace, f"runtime/control/{_PAUSE_FILE_NAME}")


def pause_requested(workspace: str) -> bool:
    try:
        return os.path.exists(pause_flag_path(workspace))
    except (RuntimeError, ValueError):
        return False


def interrupt_notice_path(workspace: str) -> str:
    return control_flag_path(workspace, f"runtime/control/{_INTERRUPT_FILE_NAME}")


def director_stop_flag_path(workspace: str) -> str:
    """Legacy alias for Director stop signal path."""
    return stop_flag_path_for(workspace, RoleId.DIRECTOR.value)


def director_stop_requested(workspace: str) -> bool:
    """Legacy alias for Director stop signal check."""
    return stop_requested_for(workspace, RoleId.DIRECTOR.value)


def clear_director_stop_flag(workspace: str) -> None:
    """Legacy alias for Director stop signal clear."""
    clear_stop_flag_for(workspace, RoleId.DIRECTOR.value)


PAUSE_FILE_NAME = _PAUSE_FILE_NAME
INTERRUPT_FILE_NAME = _INTERRUPT_FILE_NAME
