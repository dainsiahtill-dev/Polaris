"""KernelOne file system contracts.

Provides the canonical file system interface for KernelOne. All file I/O
MUST go through this contract to ensure:
- Explicit UTF-8 encoding on all text operations.
- Atomic write semantics via write_json_atomic and atomic write_text.
- Consistent error handling (no silent failure).

Path type: All paths in this contract use str for consistency across platforms.
Implementations should handle str -> internal path type conversion.
"""

from __future__ import annotations

import json
import os
import tempfile
from typing import Any, Protocol, runtime_checkable

from polaris.kernelone.fs.types import FileWriteReceipt


@runtime_checkable
class KernelFileSystemAdapter(Protocol):
    """Protocol for KernelOne file system operations.

    All implementations MUST:
    - Use UTF-8 encoding explicitly on all text operations.
    - Treat missing files gracefully (no bare FileNotFoundError propagation).
    - Support both text and binary operations.
    - Implement write_json_atomic for all writes of structured data.

    Design note: atomic writes use a temp-file-then-rename pattern so that
    readers never see a partial file. The rename is atomic on POSIX;
    on Windows it is not fully atomic but still prevents most read-during-write.

    Path type: All paths use str for cross-platform consistency.
    """

    def read_text(self, path: str, *, encoding: str = "utf-8") -> str:
        """Read entire file as string.

        Raises:
            FileNotFoundError: If path does not exist.
        """
        ...

    def read_bytes(self, path: str) -> bytes:
        """Read entire file as bytes.

        Raises:
            FileNotFoundError: If path does not exist.
        """
        ...

    def write_text(
        self,
        path: str,
        content: str,
        *,
        encoding: str = "utf-8",
        atomic: bool = False,
    ) -> int:
        """Write string content to file.

        Args:
            path: Target file path. Parent directories are created if missing.
            content: String content to write.
            encoding: Text encoding. Default is UTF-8.
            atomic: If True, writes to a temp file then renames atomically.
                Use for config files and state files where partial writes are dangerous.
                Default is False (direct write).

        Returns:
            Number of bytes written (post-encoding).

        Raises:
            OSError: If the write fails after retries.
        """
        ...

    def write_bytes(self, path: str, content: bytes) -> int:
        """Write bytes content to file.

        Parent directories are created if missing.
        """
        ...

    def append_text(self, path: str, content: str, *, encoding: str = "utf-8") -> int:
        """Append string content to end of file.

        Creates the file if it does not exist.
        """
        ...

    def write_json_atomic(self, path: str, data: Any, *, indent: int = 2) -> FileWriteReceipt:
        """Serialize data to JSON and write atomically.

        This is the preferred method for writing structured configuration,
        state, or artifact data. Uses a temp-file-then-rename pattern so
        readers never see a partial JSON document.

        Args:
            path: Target JSON file path. Parent directories are created.
            data: Any JSON-serializable Python object.
            indent: JSON indentation spaces. Default is 2. Use 0 for compact.

        Returns:
            FileWriteReceipt with path and byte count.

        Raises:
            TypeError: If data is not JSON-serializable.
            OSError: If the write or rename fails.
        """
        ...

    def exists(self, path: str) -> bool:
        """Return True if path exists (file or directory)."""
        ...

    def is_file(self, path: str) -> bool:
        """Return True if path is a regular file."""
        ...

    def is_dir(self, path: str) -> bool:
        """Return True if path is a directory."""
        ...

    def remove(self, path: str, *, missing_ok: bool = True) -> bool:
        """Remove a file.

        Args:
            path: File to remove.
            missing_ok: If True (default), no error if file does not exist.

        Returns:
            True if the file was removed, False if it did not exist.
        """
        ...


def _atomic_write_text(path: str, content: str, encoding: str = "utf-8") -> int:
    """Standalone atomic write helper: write to temp file then rename.

    Used by implementations that need the atomic pattern outside a Protocol.
    All paths are str for cross-platform consistency.
    """
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    suffix = f".{os.path.basename(path)}.tmp"
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding=encoding,
        suffix=suffix,
        dir=parent or ".",
        delete=False,
    ) as tmp:
        tmp.write(content)
        tmp_name = tmp.name
    os.replace(tmp_name, path)  # Atomic on POSIX; best-effort on Windows
    return len(content.encode(encoding, errors="replace"))


def _atomic_write_json(path: str, data: Any, indent: int = 2) -> FileWriteReceipt:
    """Standalone atomic JSON write helper.

    Serializes data to a temp file using the JSON format, then atomically
    replaces the target. This function is the canonical reference implementation
    for write_json_atomic.
    """
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    encoded = json.dumps(data, indent=indent, ensure_ascii=False).encode("utf-8")
    suffix = f".{os.path.basename(path)}.tmp"
    with tempfile.NamedTemporaryFile(mode="wb", suffix=suffix, dir=parent or ".", delete=False) as tmp:
        tmp.write(encoded)
        tmp_name = tmp.name
    os.replace(tmp_name, path)
    return FileWriteReceipt(
        logical_path=path,
        absolute_path=os.path.abspath(path),
        bytes_written=len(encoded),
        atomic=True,
    )
