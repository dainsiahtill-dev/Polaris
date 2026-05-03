"""Storage adapter for unified file operations.

Provides a clean interface for file system operations with proper
abstraction for testing and future storage backend variations.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
from typing import TYPE_CHECKING, Any, Protocol

from polaris.kernelone.fs.text_ops import ensure_parent_dir
from polaris.kernelone.storage import resolve_storage_roots

if TYPE_CHECKING:
    from collections.abc import Iterator

logger = logging.getLogger(__name__)


class FileLocker(Protocol):
    """Protocol for file locking mechanisms."""

    def acquire(self) -> bool: ...
    def release(self) -> None: ...


class StorageAdapter:
    """Abstract base class for storage operations.

    Provides unified interface for:
    - Path resolution
    - Atomic file operations
    - JSON/JSONL operations
    - Directory management
    """

    def __init__(self, workspace: str) -> None:
        self._workspace = os.path.abspath(workspace)
        self._roots = resolve_storage_roots(self._workspace)
        self._lock = threading.RLock()

    @property
    def workspace(self) -> str:
        """Get the workspace path."""
        return self._workspace

    @property
    def runtime_root(self) -> str:
        """Get the runtime root directory."""
        return self._roots.runtime_root

    @property
    def persistent_root(self) -> str:
        """Get the persistent workspace storage root."""
        return self._roots.workspace_persistent_root

    @property
    def config_root(self) -> str:
        """Get the global config root."""
        return self._roots.config_root

    def resolve_path(self, logical_path: str) -> str:
        """Resolve a logical path to absolute path.

        Args:
            logical_path: Path with prefix like "runtime/...", "workspace/...", "config/..."

        Returns:
            Absolute file system path
        """
        if logical_path.startswith("runtime/"):
            rel = logical_path[len("runtime/") :]
            return os.path.join(self.runtime_root, rel)
        elif logical_path.startswith("workspace/"):
            rel = logical_path[len("workspace/") :]
            return os.path.join(self.persistent_root, rel)
        elif logical_path.startswith("config/"):
            rel = logical_path[len("config/") :]
            return os.path.join(self.config_root, rel)
        else:
            raise ValueError(f"Unsupported path prefix: {logical_path}")

    def ensure_dir(self, path: str) -> str:
        """Ensure directory exists, creating if necessary.

        Args:
            path: Directory path (can be logical or absolute)

        Returns:
            Absolute path to directory
        """
        abs_path = self._to_absolute(path)
        ensure_parent_dir(abs_path)
        return abs_path

    def read_text(self, path: str, encoding: str = "utf-8") -> str | None:
        """Read text file contents.

        Args:
            path: File path (can be logical or absolute)
            encoding: Text encoding

        Returns:
            File contents or None if file doesn't exist
        """
        abs_path = self._to_absolute(path)
        try:
            with open(abs_path, encoding=encoding) as f:
                return f.read()
        except FileNotFoundError:
            return None
        except (RuntimeError, ValueError):
            logger.exception("read_text failed: path=%s", abs_path)
            return None

    def write_text(self, path: str, content: str, encoding: str = "utf-8", atomic: bool = True) -> None:
        """Write text to file.

        Args:
            path: File path (can be logical or absolute)
            content: Text content to write
            encoding: Text encoding
            atomic: If True, write atomically using temp file
        """
        abs_path = self._to_absolute(path)
        self.ensure_dir(os.path.dirname(abs_path))

        if atomic:
            # Use tempfile with random suffix to prevent symlink attacks
            tmp_dir = os.path.dirname(abs_path) or "."
            with tempfile.NamedTemporaryFile(
                mode="w",
                dir=tmp_dir,
                suffix=".tmp",
                encoding=encoding,
                newline="\n",
                delete=False,
            ) as f:
                tmp_path = f.name
                f.write(content)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, abs_path)
        else:
            with open(abs_path, "w", encoding=encoding, newline="\n") as f:
                f.write(content)

    def read_json(self, path: str) -> dict[str, Any] | None:
        """Read and parse JSON file.

        Args:
            path: File path (can be logical or absolute)

        Returns:
            Parsed JSON data or None if file doesn't exist or is invalid
        """
        content = self.read_text(path)
        if content is None:
            return None
        try:
            data = json.loads(content)
            return data if isinstance(data, dict) else {}
        except json.JSONDecodeError:
            return {}

    def write_json(self, path: str, data: dict[str, Any], atomic: bool = True, indent: int = 2) -> None:
        """Write data as JSON file.

        Args:
            path: File path (can be logical or absolute)
            data: Data to serialize
            atomic: If True, write atomically
            indent: JSON indentation
        """
        content = json.dumps(data, ensure_ascii=False, indent=indent)
        self.write_text(path, content + "\n", atomic=atomic)

    def append_jsonl(self, path: str, record: dict[str, Any]) -> None:
        """Append a record to JSONL file.

        Args:
            path: File path (can be logical or absolute)
            record: Record to append
        """
        abs_path = self._to_absolute(path)
        self.ensure_dir(os.path.dirname(abs_path))

        with open(abs_path, "a", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    def read_jsonl(self, path: str, max_size_mb: float = 10.0) -> list[dict[str, Any]]:
        """Read all records from JSONL file.

        Args:
            path: File path (can be logical or absolute)
            max_size_mb: Maximum file size to read (default 10MB). Files larger than this
                will raise MemoryError to prevent OOM.

        Returns:
            List of records
        """
        abs_path = self._to_absolute(path)
        if not os.path.exists(abs_path):
            return []
        size_mb = os.path.getsize(abs_path) / (1024 * 1024)
        if size_mb > max_size_mb:
            raise MemoryError(f"JSONL file too large: {size_mb:.1f}MB > {max_size_mb}MB limit")

        content = self.read_text(path)
        if content is None:
            return []

        records = []
        for line in content.strip().split("\n"):
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return records

    def read_jsonl_iter(self, path: str, max_size_mb: float = 10.0) -> Iterator[dict[str, Any]]:
        """Iterate records from JSONL file using streaming (memory-efficient).

        Args:
            path: File path (can be logical or absolute)
            max_size_mb: Maximum file size to read (default 10MB)

        Yields:
            Records one at a time without loading entire file into memory.
        """
        abs_path = self._to_absolute(path)
        if not os.path.exists(abs_path):
            return
        size_mb = os.path.getsize(abs_path) / (1024 * 1024)
        if size_mb > max_size_mb:
            raise MemoryError(f"JSONL file too large: {size_mb:.1f}MB > {max_size_mb}MB limit")

        with open(abs_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    continue

    def exists(self, path: str) -> bool:
        """Check if path exists.

        Args:
            path: Path to check (can be logical or absolute)

        Returns:
            True if path exists
        """
        abs_path = self._to_absolute(path)
        return os.path.exists(abs_path)

    def is_file(self, path: str) -> bool:
        """Check if path is a file.

        Args:
            path: Path to check (can be logical or absolute)

        Returns:
            True if path is a file
        """
        abs_path = self._to_absolute(path)
        return os.path.isfile(abs_path)

    def is_dir(self, path: str) -> bool:
        """Check if path is a directory.

        Args:
            path: Path to check (can be logical or absolute)

        Returns:
            True if path is a directory
        """
        abs_path = self._to_absolute(path)
        return os.path.isdir(abs_path)

    def list_files(self, path: str, pattern: str = "*") -> list[str]:
        """List files in directory matching pattern.

        Args:
            path: Directory path (can be logical or absolute)
            pattern: Glob pattern to match

        Returns:
            List of absolute file paths
        """
        from glob import glob

        abs_path = self._to_absolute(path)
        search_path = os.path.join(abs_path, pattern)
        return glob(search_path)

    def delete(self, path: str, recursive: bool = False) -> bool:
        """Delete file or directory.

        Args:
            path: Path to delete (can be logical or absolute)
            recursive: If True, delete directories recursively

        Returns:
            True if deletion was successful
        """
        import shutil

        abs_path = self._to_absolute(path)
        try:
            if os.path.isfile(abs_path):
                os.remove(abs_path)
                return True
            elif os.path.isdir(abs_path):
                if recursive:
                    shutil.rmtree(abs_path)
                else:
                    os.rmdir(abs_path)
                return True
            return False
        except (RuntimeError, ValueError):
            logger.exception("delete failed: path=%s", abs_path)
            return False

    def _to_absolute(self, path: str) -> str:
        """Convert path to absolute path.

        If path starts with a logical prefix (runtime/, workspace/, config/),
        resolve it. Otherwise, treat as relative to workspace or absolute.
        """
        path = path.strip()

        # Check for logical path prefixes
        if path.startswith(("runtime/", "workspace/", "config/")):
            return self.resolve_path(path)

        # Already absolute — validate it stays within workspace boundary
        if os.path.isabs(path):
            # Use commonpath to ensure the path is within workspace
            try:
                common = os.path.commonpath([path, self._workspace])
                if common == self._workspace or path.startswith(self._workspace + os.sep):
                    return path
            except ValueError:
                # commonpath raises ValueError on paths on different drives (Windows)
                pass
            raise ValueError(f"Path '{path}' is outside workspace boundary")

        # Relative to workspace
        return os.path.join(self._workspace, path)


class FileSystemAdapter(StorageAdapter):
    """Default file system storage adapter."""

    pass


# Global adapter cache
_adapter_cache: dict[str, StorageAdapter] = {}
_cache_lock = threading.Lock()


def get_storage_adapter(workspace: str) -> StorageAdapter:
    """Get or create a storage adapter for the given workspace.

    Args:
        workspace: Workspace directory path

    Returns:
        StorageAdapter instance
    """
    abs_workspace = os.path.abspath(workspace)

    with _cache_lock:
        if abs_workspace not in _adapter_cache:
            _adapter_cache[abs_workspace] = FileSystemAdapter(abs_workspace)
        return _adapter_cache[abs_workspace]


def clear_adapter_cache() -> None:
    """Clear the adapter cache. Useful for testing."""
    with _cache_lock:
        _adapter_cache.clear()
