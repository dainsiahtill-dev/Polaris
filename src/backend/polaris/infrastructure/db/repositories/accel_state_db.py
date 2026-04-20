"""Accel file-state SQLite store (migrated from legacy accel storage)."""

from __future__ import annotations

import hashlib
import sqlite3
import time
from dataclasses import dataclass
from functools import lru_cache
from typing import TYPE_CHECKING

from polaris.infrastructure.db.adapters import SqliteAdapter
from polaris.kernelone.constants import ONE_MB_BYTES
from polaris.kernelone.db import KernelDatabase

if TYPE_CHECKING:
    from collections.abc import Iterable
    from pathlib import Path


@dataclass
class FileState:
    path: str
    mtime_ns: int
    size: int
    content_hash: str
    lang: str


def _connect(db_path: Path, timeout_seconds: int = 30) -> sqlite3.Connection:
    """Connect to SQLite database with timeout and retry logic."""
    db_path.parent.mkdir(parents=True, exist_ok=True)

    # Try multiple times with increasing backoff
    max_attempts = 3
    last_exc: sqlite3.OperationalError | None = None
    for attempt in range(max_attempts):
        try:
            conn = _kernel_db_for(str(db_path.parent)).sqlite(
                str(db_path),
                timeout_seconds=float(timeout_seconds),
                isolation_level="IMMEDIATE",
                check_same_thread=False,
                pragmas={"busy_timeout": 5000},
                ensure_parent=True,
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS file_state (
                  path TEXT PRIMARY KEY,
                  mtime_ns INTEGER NOT NULL,
                  size INTEGER NOT NULL,
                  content_hash TEXT NOT NULL,
                  lang TEXT NOT NULL,
                  updated_utc TEXT NOT NULL
                )
                """
            )
            return conn
        except sqlite3.OperationalError as exc:
            last_exc = exc
            if "database is locked" in str(exc).lower() and attempt < max_attempts - 1:
                # Exponential backoff: 1s, 2s, 4s
                backoff = 2**attempt
                time.sleep(backoff)
                continue
            else:
                raise RuntimeError(f"Failed to connect to database after {max_attempts} attempts: {exc}") from exc
    raise RuntimeError(
        f"Failed to connect to database after {max_attempts} attempts: {last_exc or 'unknown error'}"
    ) from last_exc


@lru_cache(maxsize=64)
def _kernel_db_for(workspace: str) -> KernelDatabase:
    return KernelDatabase(
        workspace,
        sqlite_adapter=SqliteAdapter(),
        allow_unmanaged_absolute=True,
    )


def clear_kernel_db_cache() -> None:
    """Clear the kernel database cache for test isolation.

    This function clears the lru_cache on _kernel_db_for to prevent
    database connections from leaking between tests.
    """
    _kernel_db_for.cache_clear()


def compute_hash(file_path: Path, max_file_size: int = 50 * 1024 * 1024) -> str:
    """Compute file hash with size protection to prevent blocking on large files."""
    # Check file size first
    try:
        file_size = file_path.stat().st_size
        if file_size > max_file_size:
            # For large files, use a faster hash based on metadata
            metadata = f"{file_path}:{file_size}:{file_path.stat().st_mtime_ns}"
            return hashlib.sha256(metadata.encode()).hexdigest()
    except OSError:
        # If we can't stat the file, use path as fallback
        return hashlib.sha256(str(file_path).encode()).hexdigest()

    digest = hashlib.sha256()
    try:
        with file_path.open("rb") as handle:
            bytes_read = 0
            chunk_size = ONE_MB_BYTES  # 1MB chunks

            while bytes_read < file_size:
                chunk = handle.read(chunk_size)
                if not chunk:
                    break
                digest.update(chunk)
                bytes_read += len(chunk)

                # Safety check to prevent reading too much
                if bytes_read > max_file_size:
                    break

    except (OSError, PermissionError):
        # Fall back to metadata-based hash if read fails
        metadata = f"{file_path}:{file_size}"
        return hashlib.sha256(metadata.encode()).hexdigest()

    return digest.hexdigest()


def load_state(db_path: Path, timeout_seconds: int = 30) -> dict[str, FileState]:
    """Load state from database with timeout protection."""
    start_time = time.perf_counter()

    try:
        conn = _connect(db_path, timeout_seconds)
        try:
            # Check if we're already spending too much time
            if time.perf_counter() - start_time > timeout_seconds:
                raise RuntimeError("Database connection timeout")

            rows = conn.execute("SELECT path, mtime_ns, size, content_hash, lang FROM file_state").fetchall()
            return {
                row[0]: FileState(path=row[0], mtime_ns=int(row[1]), size=int(row[2]), content_hash=row[3], lang=row[4])
                for row in rows
            }
        finally:
            conn.close()
    except (RuntimeError, ValueError) as exc:
        # Return empty state on database errors to prevent blocking
        import logging

        logger = logging.getLogger("accel_state_db")
        logger.warning(f"Failed to load state from {db_path}: {exc}, returning empty state")
        return {}


def upsert_state(db_path: Path, states: Iterable[FileState], updated_utc: str, timeout_seconds: int = 30) -> None:
    """Upsert state to database with timeout protection."""
    if not states:
        return

    start_time = time.perf_counter()

    try:
        conn = _connect(db_path, timeout_seconds)
        try:
            # Check if we're already spending too much time
            if time.perf_counter() - start_time > timeout_seconds:
                raise RuntimeError("Database connection timeout")

            conn.executemany(
                """
                INSERT INTO file_state(path, mtime_ns, size, content_hash, lang, updated_utc)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(path) DO UPDATE SET
                  mtime_ns=excluded.mtime_ns,
                  size=excluded.size,
                  content_hash=excluded.content_hash,
                  lang=excluded.lang,
                  updated_utc=excluded.updated_utc
                """,
                [(item.path, item.mtime_ns, item.size, item.content_hash, item.lang, updated_utc) for item in states],
            )
            conn.commit()
        finally:
            conn.close()
    except (RuntimeError, ValueError) as exc:
        # Log error but don't raise to prevent blocking
        import logging

        logger = logging.getLogger("accel_state_db")
        logger.warning(f"Failed to upsert state to {db_path}: {exc}, continuing without persistence")


def delete_paths(db_path: Path, paths: list[str], timeout_seconds: int = 30) -> None:
    """Delete paths from database with timeout protection."""
    if not paths:
        return

    start_time = time.perf_counter()

    try:
        conn = _connect(db_path, timeout_seconds)
        try:
            # Check if we're already spending too much time
            if time.perf_counter() - start_time > timeout_seconds:
                raise RuntimeError("Database connection timeout")

            conn.executemany("DELETE FROM file_state WHERE path = ?", [(path,) for path in paths])
            conn.commit()
        finally:
            conn.close()
    except (RuntimeError, ValueError) as exc:
        # Log error but don't raise to prevent blocking
        import logging

        logger = logging.getLogger("accel_state_db")
        logger.warning(f"Failed to delete paths from {db_path}: {exc}, continuing without deletion")


__all__ = ["FileState", "compute_hash", "delete_paths", "load_state", "upsert_state"]
