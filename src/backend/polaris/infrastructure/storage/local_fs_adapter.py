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

from polaris.kernelone.fs.contracts import DurabilityMode, FileDurabilityError, validate_durability
from polaris.kernelone.fs.types import AtomicWriteBindingV1, FileWriteReceipt


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

    def write_managed_text_atomic(self, binding: AtomicWriteBindingV1, content: str, *, encoding: str = "utf-8") -> int:
        """Commit an owner-bound write without legacy temporary-file fallback."""
        from polaris.kernelone.fs.guarded_atomic_write import guarded_atomic_write

        if type(self).write_managed_text_atomic is LocalFileSystemAdapter.write_managed_text_atomic and (
            type(self).write_text is not LocalFileSystemAdapter.write_text or "write_text" in vars(self)
        ):
            raise NotImplementedError("legacy write policy requires an explicit managed atomic implementation")
        if encoding.strip().lower().replace("_", "-") != "utf-8":
            raise ValueError("managed atomic writes require UTF-8")
        return guarded_atomic_write(binding, content.encode("utf-8"))

    def append_text(
        self,
        path: str,
        content: str,
        *,
        encoding: str = "utf-8",
        durability: DurabilityMode = "buffered",
    ) -> int:
        """Append text and complete the selected durability stage before success."""

        normalized_durability = validate_durability(durability)
        data = str(content)
        stage = "prepare"
        target = Path(path)
        file_existed = target.exists()
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            stage = "write"
            # Preserve Python's native text newline behavior for legacy buffered
            # callers; JSONL producers already provide their own trailing newline.
            with open(path, "a", encoding=encoding) as handle:
                handle.write(data)
                if normalized_durability in {"flush", "fsync"}:
                    stage = "flush"
                    handle.flush()
                if normalized_durability == "fsync":
                    stage = "fsync"
                    os.fsync(handle.fileno())
            if normalized_durability == "fsync" and not file_existed:
                stage = "parent_fsync"
                self._fsync_created_file_parent(target.parent)
        except OSError as exc:
            raise FileDurabilityError(
                path=path,
                durability=normalized_durability,
                stage=stage,
            ) from exc
        return len(data.encode(encoding))

    @staticmethod
    def _fsync_created_file_parent(parent: Path) -> None:
        """Persist a new POSIX directory entry; Windows has no matching guarantee."""

        if os.name == "nt":
            return
        flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        fd = os.open(str(parent), flags)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)

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
        """Remove a file.

        Only a genuinely absent file is tolerated. ``missing_ok`` decides how
        that absence is reported; it never converts a real operation failure
        into a success.  A permission denial, an I/O error or an unknown
        outcome propagates unchanged so the caller keeps the true cause and can
        decide whether the effect state is unknown.

        Args:
            path: Absolute or relative path of the file to remove.
            missing_ok: When True an absent file reports True (the file is
                gone, which is the requested end state); when False an absent
                file reports False.

        Returns:
            True when the file was removed or was already absent and
            ``missing_ok`` is True; False when the file was already absent and
            ``missing_ok`` is False.

        Raises:
            OSError: The removal failed for any reason other than the file
                being absent.  The original exception is propagated unchanged.
        """
        try:
            os.remove(path)
            return True
        except FileNotFoundError:
            return bool(missing_ok)
