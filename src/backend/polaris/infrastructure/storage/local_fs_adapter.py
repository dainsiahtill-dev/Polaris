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
from typing import Any, cast

from polaris.kernelone.fs.contracts import (
    DurabilityMode,
    FileDurabilityError,
    NamespaceOperationUnavailableError,
    validate_durability,
)
from polaris.kernelone.fs.types import (
    AtomicWriteBindingV1,
    FileWriteReceipt,
    NamespaceBindingV1,
    NamespaceCreateReceiptV1,
)


def _owner_name(owner: object | None) -> str:
    """Describe the owner of an effective method in a stable, readable way."""
    if owner is None:
        return "the production default"
    if isinstance(owner, type):
        return f"{owner.__module__}.{owner.__qualname__}"
    return getattr(owner, "__qualname__", None) or type(owner).__qualname__


class LocalFileSystemAdapter:
    """Local disk adapter for KernelFileSystem.

    Implements the KernelFileSystemAdapter protocol using local disk storage.
    All paths use str for cross-platform consistency.
    """

    # The legacy methods that a managed namespace capability would bypass.
    # A subclass that overrides one of them carries its own policy for that
    # operation and must therefore own the corresponding managed
    # capability explicitly.
    _LEGACY_POLICY_METHODS: dict[str, tuple[str, ...]] = {
        "read_managed_namespace_bytes": ("read_text", "read_bytes"),
        "write_managed_namespace_bytes": ("write_text", "write_bytes"),
        "write_managed_namespace_bytes_atomic": ("write_text", "write_bytes"),
        "append_managed_namespace_bytes": ("append_text",),
        "remove_managed_namespace": ("remove",),
        "create_managed_namespace_bytes": ("write_text", "write_bytes"),
    }

    def _require_namespace_opt_in(self, capability: str) -> None:
        """Refuse the inherited managed namespace capability for a policy owner.

        The *effective* policy owner decides, not the concrete class body.
        The owner of a method is resolved through the whole MRO and through
        the instance dictionary, which covers the three shapes that matter:

        * a subclass that overrides a legacy method owns that policy, and the
          managed capability must be owned at least as specifically;
        * a subclass that inherits an *opted-in* managed capability inherits
          that legitimate opt-in, because the opt-in and the policy it
          authorises travel together;
        * an instance that replaces a legacy method at runtime owns that
          policy, even though the class body says nothing about it.

        The check compares how specific each owner is.  A managed capability
        owned by a *less* specific owner than the legacy policy is not the
        owner of that policy and the call is refused *before* any effect.
        That is what stops a stale opt-in from an intermediate class from
        being inherited past a policy change made by a child class.

        The legacy writer is never called to find out whether it would
        refuse: that would be an authorization probe with a side effect.
        """
        legacy_methods = self._LEGACY_POLICY_METHODS.get(capability, ())
        if not legacy_methods:
            return
        managed_owner = self._effective_owner(capability)
        for name in legacy_methods:
            legacy_owner = self._effective_owner(name)
            if legacy_owner is None:
                # The legacy method is the production default: it carries no
                # adapter-specific policy, so there is nothing to preserve.
                continue
            if managed_owner is not None and managed_owner[0] <= legacy_owner[0]:
                # The managed capability is owned at least as specifically as
                # the legacy policy it authorises.
                continue
            raise NamespaceOperationUnavailableError(
                f"adapter {type(self).__name__} carries a {name!r} policy owned by "
                f"{_owner_name(legacy_owner[1])} and must therefore own {capability!r} explicitly; "
                "a legacy policy must be preserved"
            )

    def _require_managed_text_atomic_opt_in(self) -> None:
        """Refuse the inherited atomic text capability for a legacy policy owner.

        The atomic text capability is owner-bound rather than namespace-bound,
        so it keeps its own refusal type.  The *ownership* rule is the same
        one the namespace capabilities use: the managed capability must be
        owned at least as specifically as the legacy policy it would bypass.
        """
        for name in ("write_text", "write_bytes"):
            legacy_owner = self._effective_owner(name)
            if legacy_owner is None:
                continue
            managed_owner = self._effective_owner("write_managed_text_atomic")
            if managed_owner is not None and managed_owner[0] <= legacy_owner[0]:
                continue
            raise NotImplementedError("legacy write policy requires an explicit managed atomic implementation")

    def _effective_owner(self, name: str) -> tuple[int, object] | None:
        """Return how specifically ``name`` is owned, and by what.

        The tuple is ``(specificity, owner)``.  A smaller specificity is a
        *more* specific owner: an instance attribute is ``-1`` and a class
        body is its index in the MRO, so a derived class is more specific
        than the class it derives from.

        ``None`` means the production default in this class: the base class
        implementation, which carries no adapter-specific policy.
        """
        instance = vars(self)
        if name in instance:
            return (-1, instance[name])
        for index, klass in enumerate(type(self).__mro__):
            if name in vars(klass):
                owner = vars(klass)[name]
                if owner is getattr(LocalFileSystemAdapter, name, None):
                    # The production default in this base class carries no
                    # adapter-specific policy.
                    return None
                return (index, owner)
        return None

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

        self._require_managed_text_atomic_opt_in()
        if encoding.strip().lower().replace("_", "-") != "utf-8":
            raise ValueError("managed atomic writes require UTF-8")
        return cast("int", (guarded_atomic_write(binding, content.encode("utf-8"))))

    def read_managed_namespace_bytes(self, binding: NamespaceBindingV1) -> bytes:
        """Read one namespace-bound leaf through retained no-follow descriptors."""
        from polaris.kernelone.fs.guarded_namespace_ops import guarded_namespace_read_bytes

        self._require_namespace_opt_in("read_managed_namespace_bytes")
        return cast("bytes", (guarded_namespace_read_bytes(binding)))

    def write_managed_namespace_bytes(self, binding: NamespaceBindingV1, content: bytes) -> int:
        """Replace one namespace-bound leaf through a retained parent descriptor."""
        from polaris.kernelone.fs.guarded_namespace_ops import guarded_namespace_write_bytes

        self._require_namespace_opt_in("write_managed_namespace_bytes")
        return cast("int", (guarded_namespace_write_bytes(binding, content)))

    def write_managed_namespace_bytes_atomic(self, binding: NamespaceBindingV1, content: bytes) -> int:
        """Atomically replace one namespace-bound leaf inside its own parent."""
        from polaris.kernelone.fs.guarded_namespace_ops import guarded_namespace_write_bytes_atomic

        self._require_namespace_opt_in("write_managed_namespace_bytes_atomic")
        return cast("int", (guarded_namespace_write_bytes_atomic(binding, content)))

    def append_managed_namespace_bytes(
        self,
        binding: NamespaceBindingV1,
        content: bytes,
        *,
        durability: DurabilityMode = "buffered",
    ) -> int:
        """Append to one namespace-bound leaf and complete the durability stage."""
        from polaris.kernelone.fs.guarded_namespace_ops import guarded_namespace_append_bytes

        self._require_namespace_opt_in("append_managed_namespace_bytes")
        return cast("int", (guarded_namespace_append_bytes(binding, content, durability=durability)))

    def remove_managed_namespace(self, binding: NamespaceBindingV1, *, missing_ok: bool = True) -> bool:
        """Remove one namespace-bound leaf entry without following any link."""
        from polaris.kernelone.fs.guarded_namespace_ops import guarded_namespace_remove

        self._require_namespace_opt_in("remove_managed_namespace")
        return cast("bool", (guarded_namespace_remove(binding, missing_ok=missing_ok)))

    def create_managed_namespace_bytes(
        self,
        binding: NamespaceBindingV1,
        content: bytes,
        *,
        max_bytes: int,
    ) -> NamespaceCreateReceiptV1:
        """Create one namespace-bound leaf only when it is absent."""
        from polaris.kernelone.fs.guarded_namespace_ops import guarded_namespace_create_bytes

        self._require_namespace_opt_in("create_managed_namespace_bytes")
        return guarded_namespace_create_bytes(binding, content, max_bytes=max_bytes)

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
