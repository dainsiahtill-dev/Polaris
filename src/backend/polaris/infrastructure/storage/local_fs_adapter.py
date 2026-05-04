"""Local filesystem adapter for KernelFileSystem.

This adapter implements the KernelFileSystemAdapter protocol using local disk storage.
Paths use str for cross-platform consistency.
"""

from __future__ import annotations

import contextlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any

from polaris.kernelone.fs.types import FileWriteReceipt


class LocalFileSystemAdapter:
    """Local disk adapter for KernelFileSystem.

    Implements the KernelFileSystemAdapter protocol using local disk storage.
    All paths use str for cross-platform consistency.
    """

    def read_text(self, path: str, *, encoding: str = "utf-8") -> str:
        """Read entire file as string."""
        return Path(path).read_text(encoding=encoding)

    def read_bytes(self, path: str) -> bytes:
        """Read entire file as bytes."""
        return Path(path).read_bytes()

    def write_text(self, path: str, content: str, *, encoding: str = "utf-8", atomic: bool = False) -> int:
        """Write string content to file."""
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        data = str(content)
        if atomic:
            # Atomic write: write to temp file then rename
            tmp_path: Path | None = None
            try:
                with tempfile.NamedTemporaryFile(
                    mode="w", encoding=encoding, delete=False, dir=str(Path(path).parent)
                ) as tmp:
                    tmp.write(data)
                    tmp_path = Path(tmp.name)
                tmp_path.replace(path)
                tmp_path = None
            finally:
                if tmp_path:
                    with contextlib.suppress(OSError):
                        tmp_path.unlink(missing_ok=True)
        else:
            with open(path, "w", encoding=encoding) as handle:
                handle.write(data)
        return len(data.encode(encoding))

    def write_bytes(self, path: str, content: bytes, *, atomic: bool = False) -> int:
        """Write bytes content to file."""
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        data = bytes(content)
        if atomic:
            tmp_path: Path | None = None
            try:
                with tempfile.NamedTemporaryFile(mode="wb", delete=False, dir=str(Path(path).parent)) as tmp:
                    tmp.write(data)
                    tmp_path = Path(tmp.name)
                tmp_path.replace(path)
                tmp_path = None
            finally:
                if tmp_path:
                    with contextlib.suppress(OSError):
                        tmp_path.unlink(missing_ok=True)
        else:
            with open(path, "wb") as handle:
                handle.write(data)
        return len(data)

    def append_text(self, path: str, content: str, *, encoding: str = "utf-8") -> int:
        """Append string content to end of file."""
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        data = str(content)
        with open(path, "a", encoding=encoding) as handle:
            handle.write(data)
        return len(data.encode(encoding))

    def write_json_atomic(self, path: str, data: Any, *, indent: int = 2) -> FileWriteReceipt:
        """Serialize data to JSON and write atomically."""
        payload = json.dumps(data, ensure_ascii=False, indent=indent) + "\n"
        size = self.write_text(path, payload, encoding="utf-8", atomic=True)
        return FileWriteReceipt(logical_path=os.path.basename(path), absolute_path=path, bytes_written=size)

    def exists(self, path: str) -> bool:
        """Return True if path exists."""
        return os.path.exists(path)

    def is_file(self, path: str) -> bool:
        """Return True if path is a regular file."""
        return os.path.isfile(path)

    def is_dir(self, path: str) -> bool:
        """Return True if path is a directory."""
        return os.path.isdir(path)

    def remove(self, path: str, *, missing_ok: bool = True) -> bool:
        """Remove a file."""
        try:
            os.remove(path)
            return True
        except OSError:
            return bool(missing_ok)
