"""Unified JSONL operations for Harborpilot Loop.

This module provides atomic JSONL file operations with buffering support,
proper locking, and memory leak protection.
"""

from __future__ import annotations

import atexit
import json
import logging
import os
import time
from collections.abc import ItemsView, KeysView
from threading import Lock, Timer
from typing import Any

from polaris.kernelone import _runtime_config
from polaris.kernelone.constants import (
    DEFAULT_LOCK_TIMEOUT_SECONDS,
    JSONL_BUFFER_TTL_SECONDS,
    JSONL_FLUSH_BATCH_SIZE,
    JSONL_FLUSH_INTERVAL_SECONDS,
    JSONL_LOCK_STALE_SECONDS,
    JSONL_MAX_BUFFER_SIZE,
    JSONL_MAX_PATHS,
    SEEK_BUFFER_SIZE,
)

from ..fsync_mode import is_fsync_enabled
from ..text_ops import ensure_parent_dir
from .locking import acquire_lock_fd, release_lock_fd

# Configure module logger
logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════
# Module-level configuration (initialized at import time)
# Uses _runtime_config for KERNELONE_* / POLARIS_* fallback
# ═══════════════════════════════════════════════════════════════════
_JSONL_LOCK_STALE_SEC: float = _runtime_config.resolve_env_float("jsonl_lock_stale_sec") or JSONL_LOCK_STALE_SECONDS
_JSONL_BUFFER_ENABLED: bool = _runtime_config.resolve_env_bool("jsonl_buffered")
_JSONL_FLUSH_INTERVAL: float = _runtime_config.resolve_env_float("jsonl_flush_interval") or JSONL_FLUSH_INTERVAL_SECONDS
_JSONL_FLUSH_BATCH: int = _runtime_config.resolve_env_int("jsonl_flush_batch") or JSONL_FLUSH_BATCH_SIZE
_JSONL_MAX_BUFFER: int = _runtime_config.resolve_env_int("jsonl_max_buffer") or JSONL_MAX_BUFFER_SIZE
_JSONL_BUFFER_TTL_SEC: float = _runtime_config.resolve_env_float("jsonl_buffer_ttl") or JSONL_BUFFER_TTL_SECONDS
_JSONL_MAX_PATHS: int = _runtime_config.resolve_env_int("jsonl_max_paths") or JSONL_MAX_PATHS
_JSONL_CLEANUP_INTERVAL_SEC: float = 60.0

# ═══════════════════════════════════════════════════════════════════
# Module state
# ═══════════════════════════════════════════════════════════════════


class _BoundedJsonlBuffer:
    """Bounded dict that enforces max path limit at insert time, not just at cleanup."""

    def __init__(self, max_size: int) -> None:
        self._max_size = max_size
        self._data: dict[str, dict[str, Any]] = {}
        self._access_order: list[str] = []

    def get(self, key: str) -> dict[str, Any] | None:
        return self._data.get(key)

    def setdefault(self, key: str, default: dict[str, Any]) -> dict[str, Any]:
        if key not in self._data:
            self._evict_if_needed()
            self._data[key] = default
            self._access_order.append(key)
        return self._data[key]

    def pop(self, key: str, default: Any = None) -> Any:
        result = self._data.pop(key, default)
        if key in self._access_order:
            self._access_order.remove(key)
        return result

    def items(self) -> ItemsView[str, dict[str, Any]]:
        return self._data.items()

    def keys(self) -> KeysView[str]:
        return self._data.keys()

    def __len__(self) -> int:
        return len(self._data)

    def __contains__(self, key: str) -> bool:
        return key in self._data

    def clear(self) -> None:
        self._data.clear()
        self._access_order.clear()

    def __getitem__(self, key: str) -> dict[str, Any]:
        return self._data[key]

    def __setitem__(self, key: str, value: dict[str, Any]) -> None:
        if key not in self._data:
            self._evict_if_needed()
            self._access_order.append(key)
        self._data[key] = value

    def _evict_if_needed(self) -> None:
        while len(self._data) >= self._max_size and self._access_order:
            oldest_key = self._access_order.pop(0)
            self._data.pop(oldest_key, None)


_JSONL_BUFFER: _BoundedJsonlBuffer = _BoundedJsonlBuffer(max_size=_JSONL_MAX_PATHS)
_JSONL_BUFFER_LOCK = Lock()
_JSONL_ATEXIT_REGISTERED = False
_JSONL_CLEANUP_TIMER: Timer | None = None
_JSONL_LAST_ACCESS: dict[str, float] = {}


def _fsync_enabled() -> bool:
    """Check if fsync is enabled based on shared fsync mode settings."""
    return is_fsync_enabled()


def _update_access_time(path: str) -> None:
    """Update the last access time for a buffered path."""
    _JSONL_LAST_ACCESS[path] = time.time()


def _cleanup_jsonl_buffer() -> None:
    """Clean up expired buffer entries to prevent memory leaks."""
    global _JSONL_CLEANUP_TIMER

    try:
        now = time.time()
        paths_to_remove: list[str] = []
        paths_to_flush: list[str] = []

        with _JSONL_BUFFER_LOCK:
            # Check each path's age and access time
            for path, state in list(_JSONL_BUFFER.items()):
                last_access = _JSONL_LAST_ACCESS.get(path, 0)
                age_sec = now - last_access

                # If over TTL, mark for flush and removal
                if age_sec > _JSONL_BUFFER_TTL_SEC:
                    if state.get("lines"):
                        paths_to_flush.append(path)
                    paths_to_remove.append(path)

            # Check path count limit
            if len(_JSONL_BUFFER) > _JSONL_MAX_PATHS:
                sorted_paths = sorted(_JSONL_BUFFER.keys(), key=lambda p: _JSONL_LAST_ACCESS.get(p, 0))
                excess = len(_JSONL_BUFFER) - _JSONL_MAX_PATHS
                for path in sorted_paths[:excess]:
                    if path not in paths_to_remove:
                        path_state: dict[str, Any] | None = _JSONL_BUFFER.get(path)
                        if path_state and path_state.get("lines"):
                            paths_to_flush.append(path)
                        paths_to_remove.append(path)

        # Flush outside of lock, but only for the targeted path.
        for path in paths_to_flush:
            try:
                _flush_jsonl_buffered_path(
                    path,
                    force=True,
                    lock_timeout_sec=DEFAULT_LOCK_TIMEOUT_SECONDS,
                )
            except (RuntimeError, ValueError) as e:
                logger.debug(f"Failed to flush buffer for {path}: {e}")

        # Remove expired entries only when they are empty. Failed flushes must
        # keep their buffered state to avoid silent data loss.
        with _JSONL_BUFFER_LOCK:
            for path in paths_to_remove:
                buffer_state: dict[str, Any] | None = _JSONL_BUFFER.get(path)
                lines = buffer_state.get("lines") if isinstance(buffer_state, dict) else None
                if lines:
                    continue
                _JSONL_BUFFER.pop(path, None)
                _JSONL_LAST_ACCESS.pop(path, None)

    except (RuntimeError, ValueError) as e:
        logger.debug(f"Error during buffer cleanup: {e}")
    finally:
        # Restart timer
        try:
            _JSONL_CLEANUP_TIMER = Timer(_JSONL_CLEANUP_INTERVAL_SEC, _cleanup_jsonl_buffer)
            _JSONL_CLEANUP_TIMER.daemon = True
            _JSONL_CLEANUP_TIMER.start()
        except (RuntimeError, ValueError) as e:
            logger.debug(f"Failed to restart cleanup timer: {e}")


def _start_cleanup_timer() -> None:
    """Start the periodic cleanup timer."""
    global _JSONL_CLEANUP_TIMER
    if _JSONL_CLEANUP_TIMER is None:
        try:
            _JSONL_CLEANUP_TIMER = Timer(_JSONL_CLEANUP_INTERVAL_SEC, _cleanup_jsonl_buffer)
            _JSONL_CLEANUP_TIMER.daemon = True
            _JSONL_CLEANUP_TIMER.start()
        except (RuntimeError, ValueError) as e:
            logger.debug(f"Failed to start cleanup timer: {e}")


def _register_jsonl_flush() -> None:
    """Register atexit handler for flushing buffers."""
    global _JSONL_ATEXIT_REGISTERED
    if _JSONL_ATEXIT_REGISTERED:
        return
    try:
        atexit.register(lambda: flush_jsonl_buffers(force=True))
        _JSONL_ATEXIT_REGISTERED = True
        _start_cleanup_timer()
    except (RuntimeError, ValueError) as e:
        logger.debug(f"Failed to register atexit handler: {e}")
        _JSONL_ATEXIT_REGISTERED = True


# ═══════════════════════════════════════════════════════════════════
# Lock-based atomic operations
# ═══════════════════════════════════════════════════════════════════


def append_jsonl_atomic(path: str, obj: dict[str, Any], lock_timeout_sec: float = DEFAULT_LOCK_TIMEOUT_SECONDS) -> None:
    """Append a JSON object to a JSONL file atomically using file locking.

    Args:
        path: Path to the JSONL file
        obj: Dictionary to append as JSON line
        lock_timeout_sec: Maximum time to wait for lock acquisition

    Raises:
        TimeoutError: If the file lock cannot be acquired in time.
    """
    if not path:
        return

    ensure_parent_dir(path)
    lock_path = path + ".lock"
    fd = acquire_lock_fd(lock_path, timeout_sec=lock_timeout_sec)

    if fd is None:
        raise TimeoutError(f"Timed out acquiring JSONL lock for atomic append: {path}")

    try:
        line = json.dumps(obj, ensure_ascii=False) + "\n"
        with open(path, "a", encoding="utf-8", newline="\n") as handle:
            handle.write(line)
            handle.flush()
            if _fsync_enabled():
                os.fsync(handle.fileno())
    except OSError as e:
        logger.error(f"Failed to write to {path}: {e}")
    finally:
        release_lock_fd(fd, lock_path)


def _flush_jsonl_path(path: str, lines: list[str], lock_timeout_sec: float) -> bool:
    """Flush buffered lines to a specific JSONL file.

    Args:
        path: Path to the JSONL file
        lines: Lines to write
        lock_timeout_sec: Maximum time to wait for lock acquisition

    Returns:
        True if successful, False otherwise
    """
    if not lines:
        return True

    ensure_parent_dir(path)
    lock_path = path + ".lock"
    fd = acquire_lock_fd(lock_path, timeout_sec=lock_timeout_sec)
    if fd is None:
        return False

    try:
        with open(path, "a", encoding="utf-8", newline="\n") as handle:
            handle.write("".join(lines))
            handle.flush()
            if _fsync_enabled():
                os.fsync(handle.fileno())
        return True
    except OSError as e:
        logger.error(f"Failed to flush to {path}: {e}")
        return False
    finally:
        release_lock_fd(fd, lock_path)


def _should_flush_state(state: dict[str, Any], now: float, force: bool) -> bool:
    """Return whether a buffered state should be flushed now."""
    lines = state.get("lines") or []
    if not lines:
        return False
    if force:
        return True
    last_flush = float(state.get("last_flush") or 0.0)
    return not (len(lines) < _JSONL_FLUSH_BATCH and now - last_flush < _JSONL_FLUSH_INTERVAL)


def _snapshot_buffered_lines(
    path: str,
    *,
    now: float,
    force: bool,
) -> list[str]:
    """Take a safe snapshot of lines pending flush for a specific path."""
    with _JSONL_BUFFER_LOCK:
        state = _JSONL_BUFFER.get(path)
        if not isinstance(state, dict):
            return []
        if not _should_flush_state(state, now, force):
            return []
        return list(state.get("lines") or [])


def _commit_flushed_lines(
    path: str,
    flushed_lines: list[str],
    *,
    flushed_at: float,
) -> None:
    """Commit a successful flush without dropping lines appended concurrently."""
    if not flushed_lines:
        return

    with _JSONL_BUFFER_LOCK:
        state = _JSONL_BUFFER.get(path)
        if not isinstance(state, dict):
            return

        current_lines = list(state.get("lines") or [])
        prefix_length = 0
        max_prefix = min(len(current_lines), len(flushed_lines))
        while prefix_length < max_prefix and current_lines[prefix_length] == flushed_lines[prefix_length]:
            prefix_length += 1

        if prefix_length < len(flushed_lines):
            logger.warning(
                "JSONL buffer for %s changed during flush; preserved %s trailing lines",
                path,
                len(current_lines) - prefix_length,
            )

        state["lines"] = current_lines[prefix_length:]
        state["last_flush"] = flushed_at


def _flush_jsonl_buffered_path(
    path: str,
    *,
    force: bool,
    lock_timeout_sec: float,
) -> bool:
    """Flush buffered lines for one path while preserving concurrent appends."""
    now = time.time()
    lines_to_write = _snapshot_buffered_lines(path, now=now, force=force)
    if not lines_to_write:
        return True

    ok = False
    try:
        ok = bool(_flush_jsonl_path(path, lines_to_write, lock_timeout_sec))
    except (RuntimeError, ValueError) as e:
        logger.debug(f"Failed to flush {path}: {e}")
        ok = False

    if ok:
        _commit_flushed_lines(path, lines_to_write, flushed_at=time.time())
    return ok


# ═══════════════════════════════════════════════════════════════════
# Buffered operations
# ═══════════════════════════════════════════════════════════════════


def configure_jsonl_buffer(
    buffered: bool | None = None,
    flush_interval_sec: float | None = None,
    flush_batch: int | None = None,
    max_buffer: int | None = None,
    lock_stale_sec: float | None = None,
    buffer_ttl_sec: float | None = None,
    max_paths: int | None = None,
    cleanup_interval_sec: float | None = None,
) -> None:
    """Configure JSONL buffering behavior.

    Args:
        buffered: Enable/disable buffering
        flush_interval_sec: Interval between automatic flushes
        flush_batch: Number of lines to trigger batch flush
        max_buffer: Maximum number of lines to keep in buffer
        lock_stale_sec: Lock file stale timeout in seconds
        buffer_ttl_sec: Buffer entry TTL in seconds
        max_paths: Maximum number of tracked file paths
        cleanup_interval_sec: Cleanup timer interval in seconds
    """
    global \
        _JSONL_BUFFER_ENABLED, \
        _JSONL_FLUSH_INTERVAL, \
        _JSONL_FLUSH_BATCH, \
        _JSONL_MAX_BUFFER, \
        _JSONL_LOCK_STALE_SEC, \
        _JSONL_BUFFER_TTL_SEC, \
        _JSONL_MAX_PATHS, \
        _JSONL_CLEANUP_INTERVAL_SEC
    if buffered is not None:
        _JSONL_BUFFER_ENABLED = bool(buffered)
    if flush_interval_sec is not None:
        _JSONL_FLUSH_INTERVAL = float(flush_interval_sec)
    if flush_batch is not None:
        _JSONL_FLUSH_BATCH = int(flush_batch)
    if max_buffer is not None:
        _JSONL_MAX_BUFFER = int(max_buffer)
    if lock_stale_sec is not None:
        _JSONL_LOCK_STALE_SEC = float(lock_stale_sec)
    if buffer_ttl_sec is not None:
        _JSONL_BUFFER_TTL_SEC = float(buffer_ttl_sec)
    if max_paths is not None:
        _JSONL_MAX_PATHS = int(max_paths)
    if cleanup_interval_sec is not None:
        _JSONL_CLEANUP_INTERVAL_SEC = float(cleanup_interval_sec)


def flush_jsonl_buffers(force: bool = False, lock_timeout_sec: float = DEFAULT_LOCK_TIMEOUT_SECONDS) -> None:
    """Flush all buffered JSONL data to disk.

    Args:
        force: Force flush regardless of batch size or interval
        lock_timeout_sec: Maximum time to wait for lock acquisition
    """
    with _JSONL_BUFFER_LOCK:
        paths = list(_JSONL_BUFFER.keys())

    for path in paths:
        _flush_jsonl_buffered_path(
            path,
            force=force,
            lock_timeout_sec=lock_timeout_sec,
        )


def append_jsonl(
    path: str,
    obj: dict[str, Any],
    lock_timeout_sec: float = DEFAULT_LOCK_TIMEOUT_SECONDS,
    buffered: bool | None = None,
) -> None:
    """Append a JSON object to a JSONL file with optional buffering.

    Args:
        path: Path to the JSONL file
        obj: Dictionary to append as JSON line
        lock_timeout_sec: Maximum time to wait for lock acquisition
        buffered: Override default buffering behavior
    """
    if not path:
        return

    if buffered is None:
        buffered = _JSONL_BUFFER_ENABLED
    if not buffered:
        append_jsonl_atomic(path, obj, lock_timeout_sec=lock_timeout_sec)
        return

    _register_jsonl_flush()
    ensure_parent_dir(path)
    line = json.dumps(obj, ensure_ascii=False) + "\n"

    needs_preflush = False
    excess_count = 0

    with _JSONL_BUFFER_LOCK:
        state = _JSONL_BUFFER.setdefault(path, {"lines": [], "last_flush": time.time()})
        state["lines"].append(line)
        _update_access_time(path)
        if len(state["lines"]) > _JSONL_MAX_BUFFER:
            excess_count = len(state["lines"]) - _JSONL_MAX_BUFFER
            needs_preflush = True

    if needs_preflush:
        logger.warning(
            "JSONL buffer overflow for %s: flushing %d lines before truncation to prevent data loss",
            path,
            excess_count,
        )
        _flush_jsonl_buffered_path(path, force=True, lock_timeout_sec=lock_timeout_sec)
        with _JSONL_BUFFER_LOCK:
            buffered_state: dict[str, Any] | None = _JSONL_BUFFER.get(path)
            if buffered_state and len(buffered_state["lines"]) > _JSONL_MAX_BUFFER:
                buffered_state["lines"] = buffered_state["lines"][-_JSONL_MAX_BUFFER:]

    flush_jsonl_buffers(force=False, lock_timeout_sec=lock_timeout_sec)


# ═══════════════════════════════════════════════════════════════════
# Sequence number management
# ═══════════════════════════════════════════════════════════════════


def scan_last_seq(path: str, key: str = "seq") -> int:
    """Scan the last sequence number from a JSONL file.

    Args:
        path: Path to the JSONL file
        key: Key to look for in JSON objects

    Returns:
        Last sequence number found, or 0 if none found
    """
    if not path or not os.path.exists(path):
        return 0
    try:
        with open(path, "rb") as f:
            try:
                f.seek(-SEEK_BUFFER_SIZE, os.SEEK_END)
            except OSError:
                f.seek(0)
            lines = f.readlines()

        for line in reversed(lines):
            if not line.strip():
                continue
            try:
                text = line.decode("utf-8", errors="ignore")
                data = json.loads(text)
                if isinstance(data, dict) and key in data:
                    return int(data[key])
            except (json.JSONDecodeError, ValueError, KeyError):
                continue
    except OSError as e:
        logger.debug(f"Failed to scan {path} for seq: {e}")
    return 0


def _read_seq_file(path: str) -> int:
    """Read sequence number from a dedicated seq file.

    Args:
        path: Path to the sequence file

    Returns:
        Sequence number, or 0 if not found
    """
    if not path or not os.path.exists(path):
        return 0
    try:
        with open(path, encoding="utf-8") as handle:
            raw = handle.read().strip()
        if not raw:
            return 0
        return int(raw)
    except (OSError, ValueError) as e:
        logger.debug(f"Failed to read seq file {path}: {e}")
        return 0


def _write_seq_file(path: str, value: int) -> None:
    """Write sequence number to a dedicated seq file.

    Args:
        path: Path to the sequence file
        value: Sequence number to write
    """
    try:
        ensure_parent_dir(path)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(str(int(value)))
    except OSError as e:
        logger.warning(f"Failed to write seq file {path}: {e}")


def _next_seq_for_path(path: str, current: int, key: str = "seq") -> int:
    """Get the next sequence number for a path.

    Args:
        path: Path to the JSONL file
        current: Current sequence number
        key: Key to look for in JSON objects

    Returns:
        Next sequence number
    """
    if not path:
        return current

    seq_path = path + ".seq"
    lock_path = seq_path + ".lock"
    fd = acquire_lock_fd(lock_path, timeout_sec=2.0)
    if fd is None:
        return current

    try:
        existing = _read_seq_file(seq_path)
        if existing <= 0:
            existing = scan_last_seq(path, key=key)
        next_val = max(existing, current) + 1
        _write_seq_file(seq_path, next_val)
        return next_val
    except (RuntimeError, ValueError) as e:
        logger.warning(f"Failed to get next seq for {path}: {e}")
        return current
    finally:
        release_lock_fd(fd, lock_path)
