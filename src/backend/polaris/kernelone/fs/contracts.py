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
from typing import Any, Literal, Protocol, TypeAlias, cast, runtime_checkable

from polaris.kernelone.fs.types import (
    AtomicWriteBindingV1,
    FileWriteReceipt,
    NamespaceBindingV1,
    NamespaceCreateReceiptV1,
)

DurabilityMode: TypeAlias = Literal["buffered", "flush", "fsync"]


class FileDurabilityError(OSError):
    """Raised when an explicitly requested append durability stage fails."""

    def __init__(self, *, path: str, durability: DurabilityMode, stage: str) -> None:
        super().__init__(
            f"append durability failed path={path!r} durability={durability!r} stage={stage!r}",
        )
        self.path = path
        self.durability = durability
        self.stage = stage


class NamespaceOperationUnavailableError(NotImplementedError, PermissionError):
    """The adapter cannot perform a namespace-bound operation.

    This is the pre-effect refusal of the managed namespace capability.  It
    is raised *before* any physical effect, so an adapter that cannot honour
    the namespace contract never causes an unmanaged write.

    It inherits from both ``NotImplementedError`` and ``PermissionError`` so
    that existing callers which treat "the filesystem refused" and "the
    filesystem cannot do this" as refusals keep their current behaviour,
    while the concrete type stays distinguishable.
    """


def validate_durability(value: str) -> DurabilityMode:
    """Return one supported durability mode or reject an unknown value."""

    if value not in {"buffered", "flush", "fsync"}:
        raise ValueError("durability must be one of: buffered, flush, fsync")
    return cast(DurabilityMode, value)


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

    def write_managed_text_atomic(self, binding: AtomicWriteBindingV1, content: str, *, encoding: str = "utf-8") -> int:
        """Write through an explicit owner-bound native atomic capability.

        Missing capability rejects before effects. Implementations must not
        fall back to an unmanaged or non-atomic writer after a failure.
        """
        raise NotImplementedError("managed atomic writes are unavailable")

    def read_managed_namespace_bytes(self, binding: NamespaceBindingV1) -> bytes:
        """Read one leaf that is bound to a logical namespace root.

        The implementation must reach the leaf through retained no-follow
        descriptors inside ``binding.namespace_root`` so that a symbolic link
        in the chain is refused instead of followed.  Missing capability
        rejects before any effect.
        """
        raise NotImplementedError("managed namespace reads are unavailable")

    def write_managed_namespace_bytes(self, binding: NamespaceBindingV1, content: bytes) -> int:
        """Replace one leaf that is bound to a logical namespace root.

        The implementation must reach the leaf through retained no-follow
        descriptors inside ``binding.namespace_root``.  Missing capability
        rejects before any effect, and a failure after a possible effect must
        surface an explicit unknown effect rather than a false success.
        """
        raise NotImplementedError("managed namespace writes are unavailable")

    def write_managed_namespace_bytes_atomic(self, binding: NamespaceBindingV1, content: bytes) -> int:
        """Atomically replace one leaf bound to a logical namespace root.

        The atomic guarantee is owned by the implementation.  Missing
        capability rejects before any effect, and no fallback to an unmanaged
        or non-atomic writer is permitted after a failure.
        """
        raise NotImplementedError("managed namespace atomic writes are unavailable")

    def append_managed_namespace_bytes(
        self,
        binding: NamespaceBindingV1,
        content: bytes,
        *,
        durability: DurabilityMode = "buffered",
    ) -> int:
        """Append to one leaf bound to a logical namespace root.

        The requested durability stage must complete before success is
        reported.  Missing capability rejects before any effect.
        """
        raise NotImplementedError("managed namespace appends are unavailable")

    def remove_managed_namespace(self, binding: NamespaceBindingV1, *, missing_ok: bool = True) -> bool:
        """Remove one leaf entry bound to a logical namespace root.

        ``missing_ok`` selects how an absent leaf is reported.  It must never
        turn a real operation failure into a success, and it must never
        remove the target of a symbolic link.
        """
        raise NotImplementedError("managed namespace removal is unavailable")

    def create_managed_namespace_bytes(
        self,
        binding: NamespaceBindingV1,
        content: bytes,
        *,
        max_bytes: int,
    ) -> NamespaceCreateReceiptV1:
        """Create one leaf bound to a logical namespace root, only when absent.

        This is the *create-only* capability: it never overwrites and never
        removes an existing leaf.  When the leaf already exists the existing
        content is preserved and the receipt reports ``created=False``.

        Missing capability rejects before any effect.  A refusal must precede
        every compound creation effect, so a denied create leaves no
        directory, no temporary file and no partially created tree.  A
        failure after a possible creation must surface an explicit unknown
        effect rather than a false success, and the operation must never be
        replayed.
        """
        raise NotImplementedError("managed namespace creation is unavailable")

    def append_text(
        self,
        path: str,
        content: str,
        *,
        encoding: str = "utf-8",
        durability: DurabilityMode = "buffered",
    ) -> int:
        """Append string content to end of file.

        Creates the file if it does not exist. ``flush`` flushes Python's
        userspace buffer; ``fsync`` additionally makes the file descriptor
        durable before returning. Implementations must raise on a requested
        durability-stage failure and must not return a success byte count.
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

        Only an absent file is tolerated. ``missing_ok`` selects how that
        absence is reported; it must never turn a real operation failure into
        a success. Any other failure (permission denial, I/O error, or an
        unknown outcome) must propagate its original error so the caller can
        observe the true cause and decide whether the effect state is unknown.

        Args:
            path: Target file path.
            missing_ok: When True an absent file reports True (the file is
                gone, which is the requested end state); when False an absent
                file reports False.

        Returns:
            True when the file was removed or was already absent and
            ``missing_ok`` is True; False when the file was already absent and
            ``missing_ok`` is False.

        Raises:
            OSError: The removal failed for any reason other than the file
                being absent.
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
