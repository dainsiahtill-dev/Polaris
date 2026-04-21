"""File locking utilities for polaris Loop.

This module provides atomic file locking operations with proper resource management
and stale lock detection.
"""

from __future__ import annotations

import logging
import os
import platform
import time
from contextlib import contextmanager
from typing import TYPE_CHECKING

from polaris.kernelone import _runtime_config
from polaris.kernelone.constants import (
    DEFAULT_LOCK_TIMEOUT_SECONDS,
    JSONL_LOCK_STALE_SECONDS,
    LOCK_POLL_INTERVAL_SECONDS,
)

if TYPE_CHECKING:
    from collections.abc import Generator

# Configure module logger
logger = logging.getLogger(__name__)

# Lock configuration from environment (KERNELONE_* primary, POLARIS_* fallback)
# Use _runtime_config for consistent fallback resolution
_JSONL_LOCK_STALE_SEC = _runtime_config.resolve_env_float("jsonl_lock_stale_sec") or JSONL_LOCK_STALE_SECONDS


def _pid_alive(pid: int) -> bool:
    """Check if a process is alive by PID.

    Platform notes:
    - POSIX: ``os.kill(pid, 0)`` is reliable.
      ``ProcessLookupError`` → dead; ``PermissionError`` → alive (no signal permission).
    - Windows: ``os.kill(pid, 0)`` semantics differ; ``PermissionError`` does NOT
      reliably distinguish alive vs dead.  We use ``psutil.pid_exists()`` when
      available, otherwise fall back conservatively to True (treat as alive, do not
      delete lock) to avoid erroneously wiping a live owner's lock.
    """
    if pid <= 0:
        return False

    if platform.system() == "Windows":
        try:
            import psutil  # type: ignore[import-untyped]

            return psutil.pid_exists(pid)
        except (ImportError, RuntimeError, ValueError):
            # psutil not available or failed: conservative — assume process is
            # alive so we do not delete a lock that may belong to a live process.
            logger.debug(
                "psutil check failed on Windows; treating pid %d as alive (conservative).",
                pid,
            )
            return True

    # POSIX path
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # Process exists but current user cannot signal it.
        return True
    except OSError:
        return False
    return True


def _read_lock_metadata(lock_path: str) -> tuple[int | None, float | None]:
    try:
        with open(lock_path, encoding="utf-8") as handle:
            raw = str(handle.read() or "").strip()
    except (RuntimeError, ValueError):
        return None, None
    if not raw:
        return None, None
    parts = raw.split()
    if len(parts) < 2:
        return None, None
    try:
        pid = int(parts[0])
    except (RuntimeError, ValueError):
        pid = None
    try:
        created_at = float(parts[1])
    except (RuntimeError, ValueError):
        created_at = None
    return pid, created_at


def acquire_lock_fd(
    lock_path: str, timeout_sec: float = DEFAULT_LOCK_TIMEOUT_SECONDS, poll_sec: float = LOCK_POLL_INTERVAL_SECONDS
) -> int | None:
    """Acquire a file lock using OS-level exclusive file creation.

    Args:
        lock_path: Path to the lock file
        timeout_sec: Maximum time to wait for lock acquisition
        poll_sec: Polling interval between attempts

    Returns:
        File descriptor if lock acquired, None otherwise
    """
    parent = os.path.dirname(lock_path)
    if parent:
        try:
            os.makedirs(parent, exist_ok=True)
        except OSError as e:
            logger.warning(f"Failed to create lock parent directory {parent}: {e}")
            return None
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        try:
            fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_RDWR)
            os.write(fd, f"{os.getpid()} {time.time()}".encode())
            return fd
        except FileExistsError:
            _handle_stale_lock(lock_path)
            time.sleep(poll_sec)
        except OSError as e:
            logger.warning(f"Failed to acquire lock {lock_path}: {e}")
            return None
        except (RuntimeError, ValueError) as e:
            logger.error(f"Unexpected error acquiring lock {lock_path}: {e}")
            return None
    logger.debug(f"Lock acquisition timeout for {lock_path}")
    return None


def _handle_stale_lock(lock_path: str) -> None:
    """Check and remove stale lock files.

    Args:
        lock_path: Path to the lock file to check
    """
    stale_sec = _JSONL_LOCK_STALE_SEC
    if not stale_sec or stale_sec <= 0:
        return

    try:
        mtime = os.path.getmtime(lock_path)
        owner_pid, created_at = _read_lock_metadata(lock_path)
        lock_age = time.time() - (created_at if created_at is not None else mtime)
        if lock_age <= stale_sec:
            return
        if owner_pid is not None and _pid_alive(owner_pid):
            logger.debug(
                "Skipping stale lock removal: owner pid still alive (%s, %s)",
                owner_pid,
                lock_path,
            )
            return
        try:
            os.remove(lock_path)
            logger.info(f"Removed stale lock file: {lock_path}")
        except FileNotFoundError:
            pass  # Already removed by another process
        except OSError as e:
            logger.warning(f"Failed to remove stale lock {lock_path}: {e}")
    except FileNotFoundError:
        pass  # Lock already removed
    except OSError as e:
        logger.debug(f"Could not check mtime for {lock_path}: {e}")


def release_lock_fd(fd: int | None, lock_path: str) -> None:
    """Release a file lock and clean up the lock file.

    Args:
        fd: File descriptor to close (may be None if lock was not acquired)
        lock_path: Path to the lock file to remove
    """
    if fd is not None:
        try:
            os.close(fd)
        except OSError as e:
            logger.debug(f"Error closing lock fd for {lock_path}: {e}")

    try:
        os.remove(lock_path)
    except FileNotFoundError:
        pass  # Already removed
    except OSError as e:
        logger.warning(f"Failed to remove lock file {lock_path}: {e}")


@contextmanager
def file_lock(
    lock_path: str, timeout_sec: float = DEFAULT_LOCK_TIMEOUT_SECONDS, poll_sec: float = LOCK_POLL_INTERVAL_SECONDS
) -> Generator[bool, None, None]:
    """Context manager for acquiring and releasing file locks.

    Args:
        lock_path: Path to the lock file
        timeout_sec: Maximum time to wait for lock acquisition
        poll_sec: Polling interval between attempts

    Yields:
        True if lock was acquired, False otherwise

    Example:
        with file_lock("/path/to/file.lock") as acquired:
            if acquired:
                # Perform atomic operations
                pass
            else:
                # Handle lock acquisition failure
                pass
    """
    fd = None
    try:
        fd = acquire_lock_fd(lock_path, timeout_sec, poll_sec)
        yield fd is not None
    finally:
        if fd is not None:
            release_lock_fd(fd, lock_path)


def is_lock_stale(lock_path: str, stale_threshold_sec: float | None = None) -> bool:
    """Check if a lock file is stale (existed longer than threshold).

    Args:
        lock_path: Path to the lock file
        stale_threshold_sec: Threshold in seconds (uses env default if not specified)

    Returns:
        True if lock is stale, False otherwise
    """
    threshold = stale_threshold_sec or _JSONL_LOCK_STALE_SEC
    if not threshold or threshold <= 0:
        return False

    try:
        mtime = os.path.getmtime(lock_path)
        return time.time() - mtime > threshold
    except (FileNotFoundError, OSError):
        return False


def force_remove_lock(lock_path: str) -> bool:
    """Forcefully remove a lock file (use with caution).

    Args:
        lock_path: Path to the lock file

    Returns:
        True if lock was removed or didn't exist, False on error
    """
    try:
        if os.path.exists(lock_path):
            os.remove(lock_path)
            logger.info(f"Force removed lock file: {lock_path}")
        return True
    except OSError as e:
        logger.error(f"Failed to force remove lock {lock_path}: {e}")
        return False
