from __future__ import annotations

import json
import os
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any, TextIO, cast

from polaris.kernelone.exceptions import PathSecurityError
from polaris.kernelone.storage import (
    UNSUPPORTED_PATH_PREFIX,
    normalize_logical_rel_path,
    resolve_logical_path,
    resolve_storage_roots,
    resolve_storage_roots_read_only,
)
from polaris.kernelone.utils import utc_now_str

from .contracts import (
    DurabilityMode,
    FileWriteReceipt,
    KernelFileSystemAdapter,
    NamespaceOperationUnavailableError,
    validate_durability,
)
from .guarded_directory import guarded_create_directory
from .guarded_metadata import list_guarded_directory, read_guarded_metadata, read_guarded_symlink
from .guarded_remove_tree import guarded_remove_tree
from .text_ops import open_guarded_text_log_append
from .types import (
    AtomicWriteBindingV1,
    DirectoryCreateReceipt,
    DirectoryListingV1,
    FileMetadataV1,
    NamespaceBindingV1,
    NamespaceCreateReceiptV1,
    SymlinkObservationV1,
)

_CHANNEL_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+$")

#: Default upper bound of one created workspace file.  The bound is a
#: resource ceiling, not an admission quota: a caller may pass its own
#: ``max_bytes``.
_DEFAULT_CREATE_MAX_BYTES = 1_048_576


class KernelFileSystem:
    """Kernel-level file system boundary for all file effects."""

    def __init__(
        self,
        workspace: str,
        adapter: KernelFileSystemAdapter,
        *,
        read_only_paths: bool = False,
        runtime_owner_workspace: str | None = None,
    ) -> None:
        # This selects non-initializing path resolution; write authorization
        # remains with the caller and the normal filesystem effect policies.
        self._read_only_paths = read_only_paths
        self._physical_workspace = str(Path(workspace).absolute())
        self.workspace = str(Path(workspace).resolve())
        if runtime_owner_workspace is not None and not str(runtime_owner_workspace).strip():
            raise ValueError("runtime_owner_workspace must be an explicit non-empty path")
        self._runtime_owner_workspace = (
            self._physical_workspace
            if runtime_owner_workspace is None
            else str(Path(runtime_owner_workspace).absolute())
        )
        self._adapter = adapter

    def resolve_path(self, logical_path: str) -> Path:
        normalized = self.to_logical_path(logical_path)
        if self._read_only_paths:
            return Path(resolve_logical_path(self.workspace, normalized, read_only=True))
        return Path(resolve_logical_path(self.workspace, normalized))

    def _namespace_binding(self, normalized: str) -> NamespaceBindingV1:
        """Bind one normalized logical path to its configured namespace root.

        The binding reuses the roots that ``storage.layout`` actually selected
        for this workspace.  It never re-derives a canonical root of its own
        and never bypasses the host runtime-root validation that the layout
        performs, so an unsupported runtime configuration still fails in the
        layout rather than silently producing a different namespace here.

        The returned relative path is the part *below* the namespace root.
        The consumer walks that chain itself with retained no-follow
        descriptors, so a symbolic link anywhere in it is refused instead of
        followed.
        """
        prefix, _, remainder = normalized.partition("/")
        roots = (resolve_storage_roots_read_only if self._read_only_paths else resolve_storage_roots)(self.workspace)
        if prefix == "runtime":
            namespace_root = roots.runtime_project_root
        elif prefix == "workspace":
            namespace_root = roots.project_persistent_root
        elif prefix == "config":
            namespace_root = roots.config_root
        else:  # pragma: no cover - normalize_logical_rel_path already refused it.
            raise ValueError(f"{UNSUPPORTED_PATH_PREFIX}: {normalized}")
        namespace_root = os.path.abspath(namespace_root)
        relative = remainder.strip("/")
        if not relative:
            raise ValueError(f"{UNSUPPORTED_PATH_PREFIX}: {normalized}")
        return NamespaceBindingV1(
            namespace_root=namespace_root,
            relative_path=relative,
            logical_prefix=prefix,
        )

    def to_logical_path(self, logical_or_absolute_path: str) -> str:
        raw = str(logical_or_absolute_path or "").strip()
        if not raw:
            raise ValueError("logical path is required")
        if os.path.isabs(raw):
            return self._logical_from_absolute_path(raw)
        return normalize_logical_rel_path(raw)

    def exists(self, logical_path: str) -> bool:
        return self._adapter.exists(str(self.resolve_path(logical_path)))

    def read_text(self, logical_path: str, *, encoding: str = "utf-8") -> str:
        self._require_utf8_encoding(encoding)
        normalized = self.to_logical_path(logical_path)
        binding = self._namespace_binding(normalized)
        content = self._read_managed_namespace_bytes(binding)
        return content.decode(encoding)

    def read_bytes(self, logical_path: str) -> bytes:
        normalized = self.to_logical_path(logical_path)
        binding = self._namespace_binding(normalized)
        return self._read_managed_namespace_bytes(binding)

    def read_json(self, logical_path: str) -> Any:
        return json.loads(self.read_text(logical_path, encoding="utf-8"))

    def write_text(
        self,
        logical_path: str,
        content: str,
        *,
        encoding: str = "utf-8",
    ) -> FileWriteReceipt:
        self._require_utf8_encoding(encoding)
        normalized = self.to_logical_path(logical_path)
        text = str(content)
        binding = self._namespace_binding(normalized)
        size = self._write_managed_namespace_bytes(binding, text.encode(encoding))
        return FileWriteReceipt(
            logical_path=normalized,
            absolute_path=binding.physical_path(),
            bytes_written=size,
        )

    def write_text_atomic(
        self,
        logical_path: str,
        content: str,
        *,
        encoding: str = "utf-8",
    ) -> FileWriteReceipt:
        """Write UTF-8 text through the adapter's managed atomic capability.

        The adapter owns the atomic guarantee.  When the adapter cannot
        honour the managed namespace capability the request is rejected
        *before* any physical effect, so a non-compliant adapter can never
        be silently replaced by an unmanaged physical write.

        The capability is bound once and the write uses that same callable.
        The callable is never re-resolved, so the write cannot reach a
        different writer than the one that was validated.

        Any failure raised by the adapter propagates unchanged.  The write is
        never retried and never re-executed through a second writer: the
        caller must observe the true outcome (including an unknown effect
        state) instead of a replayed success.
        """
        self._require_utf8_encoding(encoding)
        writer = self._bind_managed_namespace_capability("write_managed_namespace_bytes_atomic")
        normalized = self.to_logical_path(logical_path)
        text = str(content)
        binding = self._namespace_binding(normalized)
        size = int(writer(binding, text.encode(encoding)))
        return FileWriteReceipt(
            logical_path=normalized,
            absolute_path=binding.physical_path(),
            bytes_written=size,
            atomic=True,
        )

    def append_text(
        self,
        logical_path: str,
        content: str,
        *,
        encoding: str = "utf-8",
        durability: DurabilityMode = "buffered",
    ) -> FileWriteReceipt:
        """Append UTF-8 text with the caller-selected durability contract."""

        self._require_utf8_encoding(encoding)
        normalized_durability = validate_durability(durability)
        normalized = self.to_logical_path(logical_path)
        text = str(content)
        binding = self._namespace_binding(normalized)
        size = self._append_managed_namespace_bytes(binding, text.encode(encoding), durability=normalized_durability)
        return FileWriteReceipt(
            logical_path=normalized,
            absolute_path=binding.physical_path(),
            bytes_written=size,
        )

    def write_bytes(self, logical_path: str, content: bytes) -> FileWriteReceipt:
        normalized = self.to_logical_path(logical_path)
        binding = self._namespace_binding(normalized)
        payload = bytes(content)
        size = self._write_managed_namespace_bytes(binding, payload)
        return FileWriteReceipt(
            logical_path=normalized,
            absolute_path=binding.physical_path(),
            bytes_written=size,
        )

    def write_json(
        self,
        logical_path: str,
        payload: Any,
        *,
        indent: int = 2,
        ensure_ascii: bool = False,
    ) -> FileWriteReceipt:
        data = json.dumps(payload, ensure_ascii=ensure_ascii, indent=indent)
        return self.write_text(logical_path, data + "\n", encoding="utf-8")

    def write_json_atomic(
        self,
        logical_path: str,
        payload: Any,
        *,
        indent: int = 2,
        ensure_ascii: bool = False,
    ) -> FileWriteReceipt:
        """Write a JSON payload through the adapter's managed atomic capability.

        The adapter owns the atomic guarantee.  When the adapter cannot
        honour the managed namespace capability the request is rejected
        *before* any physical effect, so a non-compliant adapter can never
        be silently replaced by an unmanaged physical write.

        The capability is bound once and the write uses that same callable.
        The callable is never re-resolved, so the write cannot reach a
        different writer than the one that was validated.

        Any failure raised by the adapter propagates unchanged.  The write is
        never retried and never re-executed through a second writer.
        """
        writer = self._bind_managed_namespace_capability("write_managed_namespace_bytes_atomic")
        normalized = self.to_logical_path(logical_path)
        binding = self._namespace_binding(normalized)
        data = json.dumps(payload, ensure_ascii=ensure_ascii, indent=indent) + "\n"
        size = int(writer(binding, data.encode("utf-8")))
        return FileWriteReceipt(
            logical_path=normalized,
            absolute_path=binding.physical_path(),
            bytes_written=size,
            atomic=True,
        )

    def append_jsonl(
        self,
        logical_path: str,
        payload: dict[str, Any],
        *,
        durability: DurabilityMode = "buffered",
    ) -> FileWriteReceipt:
        """Append one UTF-8 JSONL record with explicit durability selection."""

        if not isinstance(payload, dict):
            raise TypeError("jsonl payload must be a dict")
        line = json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n"
        return self.append_text(logical_path, line, encoding="utf-8", durability=durability)

    def append_evidence_record(
        self,
        channel: str,
        payload: dict[str, Any],
    ) -> FileWriteReceipt:
        safe_channel = self._safe_channel(channel)
        record = dict(payload)
        record.setdefault("timestamp", self._utc_now_iso())
        return self.append_jsonl(f"runtime/events/{safe_channel}.jsonl", record)

    def append_log_line(self, channel: str, line: str) -> FileWriteReceipt:
        safe_channel = self._safe_channel(channel)
        safe_line = str(line).replace("\r", "").rstrip("\n")
        text = f"{self._utc_now_iso()} {safe_line}\n"
        return self.append_text(f"runtime/logs/{safe_channel}.log", text, encoding="utf-8")

    def remove(self, logical_path: str, *, missing_ok: bool = True) -> bool:
        normalized = self.to_logical_path(logical_path)
        binding = self._namespace_binding(normalized)
        return self._remove_managed_namespace(binding, missing_ok=missing_ok)

    # ---------------------------------------------------------------------
    # Workspace-scoped APIs for LLM tooling paths (relative to workspace root)
    # ---------------------------------------------------------------------

    def resolve_workspace_path(self, relative_or_absolute_path: str) -> Path:
        raw = str(relative_or_absolute_path or "").strip()
        if not raw:
            raise ValueError("workspace path is required")
        candidate = Path(raw)
        workspace_root = Path(self.workspace).resolve()

        # SECURITY: Check for symlinks in path components BEFORE resolving.
        # This prevents symlink traversal attacks where a symlink inside the
        # workspace points to a path outside, bypassing the boundary check.
        if candidate.is_absolute():
            # For absolute paths, check if any component is a symlink
            self._check_no_symlink_in_path(candidate)
        else:
            # For relative paths, check components relative to workspace
            base = workspace_root
            for part in candidate.parts:
                base = base / part
                # Check if the path so far is a symlink (before final resolution)
                if base.is_symlink():
                    raise PathSecurityError(f"Symlink detected in path: {relative_or_absolute_path}")

        # Now safe to resolve - boundary check will catch if target escapes
        target = candidate.resolve() if candidate.is_absolute() else (workspace_root / candidate).resolve()
        if not self._is_within_root(root=workspace_root, target=target):
            raise ValueError(f"{UNSUPPORTED_PATH_PREFIX}: {relative_or_absolute_path}")
        return target

    def _check_no_symlink_in_path(self, path: Path) -> None:
        """Verify no component of the path is a symlink.

        This prevents symlink traversal attacks where a symlink inside the
        workspace points outside, allowing access to arbitrary files.

        Args:
            path: The absolute path to check

        Raises:
            PathSecurityError: If any path component is a symlink
        """
        if not path.is_absolute():
            raise ValueError("_check_no_symlink_in_path requires absolute path")

        # Walk through each directory component and check for symlinks
        # Handle both Unix-style (parts start with /) and Windows-style (with drive letter)
        parts = path.parts
        current = Path(parts[0])
        for part in parts[1:]:
            current = current / part
            if current.is_symlink():
                raise PathSecurityError(f"Symlink detected in absolute path: {path}")

    def to_workspace_relative_path(self, relative_or_absolute_path: str) -> str:
        target = self.resolve_workspace_path(relative_or_absolute_path)
        workspace_root = Path(self.workspace).resolve()
        relative = target.relative_to(workspace_root).as_posix()
        return "." if relative in {"", "."} else relative

    def workspace_exists(self, relative_or_absolute_path: str) -> bool:
        path = self.resolve_workspace_path(relative_or_absolute_path)
        return self._adapter.exists(str(path))

    def workspace_is_file(self, relative_or_absolute_path: str) -> bool:
        path = self.resolve_workspace_path(relative_or_absolute_path)
        return self._adapter.is_file(str(path))

    def workspace_is_dir(self, relative_or_absolute_path: str) -> bool:
        path = self.resolve_workspace_path(relative_or_absolute_path)
        return self._adapter.is_dir(str(path))

    def workspace_lstat(self, relative_or_absolute_path: str) -> FileMetadataV1:
        """Return no-follow metadata without provisioning or opening leaf content."""
        relative = (
            "."
            if relative_or_absolute_path == self._physical_workspace
            else self._lexical_workspace_relative_path(relative_or_absolute_path)
        )
        return read_guarded_metadata(self._physical_workspace, relative)

    def workspace_list_directory(self, relative_or_absolute_path: str, *, max_entries: int) -> DirectoryListingV1:
        """Return bounded, verified membership; incomplete subsets have no global order."""
        relative = (
            "."
            if relative_or_absolute_path == self._physical_workspace
            else self._lexical_workspace_relative_path(relative_or_absolute_path)
        )
        return list_guarded_directory(self._physical_workspace, relative, max_entries=max_entries)

    def workspace_readlink(
        self, relative_or_absolute_path: str, *, expected: FileMetadataV1 | None = None
    ) -> SymlinkObservationV1:
        """Return exact link text as data, with optional prior-identity binding."""
        relative = (
            "."
            if relative_or_absolute_path == self._physical_workspace
            else self._lexical_workspace_relative_path(relative_or_absolute_path)
        )
        return read_guarded_symlink(self._physical_workspace, relative, expected=expected)

    def workspace_mkdir(
        self,
        relative_or_absolute_path: str,
        *,
        parents: bool = False,
        exist_ok: bool = False,
    ) -> DirectoryCreateReceipt:
        """Create a physical directory through retained, no-follow descriptors."""
        relative = self._lexical_workspace_relative_path(relative_or_absolute_path)
        return guarded_create_directory(self._physical_workspace, relative, parents=parents, exist_ok=exist_ok)

    def workspace_open_log_append(self, relative_or_absolute_path: str) -> TextIO:
        """Transfer ownership of one guarded UTF-8 append handle to the caller."""
        relative = self._lexical_workspace_relative_path(relative_or_absolute_path)
        return open_guarded_text_log_append(self._physical_workspace, relative)

    def _bind_managed_namespace_capability(self, capability: str) -> Callable[..., Any]:
        """Bind the adapter's managed namespace capability before any effect.

        The capability must exist and must be callable.  A missing or
        non-callable capability is refused *before* the target namespace is
        resolved and before any physical effect, so an adapter that cannot
        honour the namespace contract never produces an unmanaged write.

        The validated callable is returned so the operation that follows uses
        *that exact object*.  Re-resolving the attribute would hand the
        effect to a possibly different callable than the one that was
        validated, which is how a writer that cannot accept the managed call
        shape could be reached after the refusal.
        """
        operation = getattr(self._adapter, capability, None)
        if not callable(operation):
            raise NamespaceOperationUnavailableError(
                f"adapter lacks a callable {capability!r} managed namespace capability"
            )
        return cast(Callable[..., Any], operation)

    def _read_managed_namespace_bytes(self, binding: NamespaceBindingV1) -> bytes:
        """Read one namespace-bound leaf through the adapter's capability."""
        reader = self._bind_managed_namespace_capability("read_managed_namespace_bytes")
        return bytes(reader(binding))

    def _write_managed_namespace_bytes(self, binding: NamespaceBindingV1, payload: bytes) -> int:
        """Replace one namespace-bound leaf through the adapter's capability."""
        writer = self._bind_managed_namespace_capability("write_managed_namespace_bytes")
        return int(writer(binding, payload))

    def _write_managed_namespace_bytes_atomic(self, binding: NamespaceBindingV1, payload: bytes) -> int:
        """Atomically replace one namespace-bound leaf through the adapter."""
        writer = self._bind_managed_namespace_capability("write_managed_namespace_bytes_atomic")
        return int(writer(binding, payload))

    def _append_managed_namespace_bytes(
        self,
        binding: NamespaceBindingV1,
        payload: bytes,
        *,
        durability: DurabilityMode,
    ) -> int:
        """Append to one namespace-bound leaf through the adapter's capability."""
        appender = self._bind_managed_namespace_capability("append_managed_namespace_bytes")
        return int(appender(binding, payload, durability=durability))

    def _remove_managed_namespace(self, binding: NamespaceBindingV1, *, missing_ok: bool) -> bool:
        """Remove one namespace-bound leaf entry through the adapter."""
        remover = self._bind_managed_namespace_capability("remove_managed_namespace")
        return bool(remover(binding, missing_ok=missing_ok))

    def _lexical_workspace_relative_path(self, path: str) -> str:
        """Keep the requested path intact for the no-follow descriptor walker.

        Legacy resolution can follow an alias introduced after its symlink check
        and authorize a different in-root path. Only strip an exact absolute root
        prefix here; the guarded primitive validates all remaining components.
        """
        if not isinstance(path, str) or not path:
            raise ValueError("workspace path is required")
        if not os.path.isabs(path):
            return path
        prefix = self._physical_workspace.rstrip("/") + "/"
        if not path.startswith(prefix):
            raise ValueError(f"{UNSUPPORTED_PATH_PREFIX}: {path}")
        return path[len(prefix) :]

    def workspace_read_text(
        self,
        relative_or_absolute_path: str,
        *,
        encoding: str = "utf-8",
    ) -> str:
        self._require_utf8_encoding(encoding)
        path = self.resolve_workspace_path(relative_or_absolute_path)
        return self._adapter.read_text(str(path), encoding=encoding)

    def workspace_read_bytes(self, relative_or_absolute_path: str) -> bytes:
        path = self.resolve_workspace_path(relative_or_absolute_path)
        return self._adapter.read_bytes(str(path))

    def workspace_write_text(
        self,
        relative_or_absolute_path: str,
        content: str,
        *,
        encoding: str = "utf-8",
    ) -> FileWriteReceipt:
        self._require_utf8_encoding(encoding)
        path = self.resolve_workspace_path(relative_or_absolute_path)
        text = str(content)
        size = self._adapter.write_text(str(path), text, encoding=encoding)
        relative = self.to_workspace_relative_path(str(path))
        return FileWriteReceipt(
            logical_path=relative,
            absolute_path=str(path),
            bytes_written=size,
        )

    def workspace_write_text_atomic(
        self,
        relative_or_absolute_path: str,
        content: str,
        *,
        encoding: str = "utf-8",
    ) -> FileWriteReceipt:
        self._require_utf8_encoding(encoding)
        path = self.resolve_workspace_path(relative_or_absolute_path)
        text = str(content)
        # Complete fallible receipt projection before the adapter can commit.
        relative = self.to_workspace_relative_path(str(path))
        writer = getattr(self._adapter, "write_managed_text_atomic", None)
        if not callable(writer):
            raise NotImplementedError("adapter lacks managed atomic write capability")
        size = writer(
            AtomicWriteBindingV1(self._physical_workspace, relative, self._runtime_owner_workspace),
            text,
            encoding=encoding,
        )
        return FileWriteReceipt(
            logical_path=relative,
            absolute_path=str(path),
            bytes_written=size,
            atomic=True,
        )

    def workspace_append_text(
        self,
        relative_or_absolute_path: str,
        content: str,
        *,
        encoding: str = "utf-8",
    ) -> FileWriteReceipt:
        self._require_utf8_encoding(encoding)
        path = self.resolve_workspace_path(relative_or_absolute_path)
        text = str(content)
        size = self._adapter.append_text(str(path), text, encoding=encoding)
        relative = self.to_workspace_relative_path(str(path))
        return FileWriteReceipt(
            logical_path=relative,
            absolute_path=str(path),
            bytes_written=size,
        )

    def workspace_write_bytes(
        self,
        relative_or_absolute_path: str,
        content: bytes,
    ) -> FileWriteReceipt:
        path = self.resolve_workspace_path(relative_or_absolute_path)
        payload = bytes(content)
        size = self._adapter.write_bytes(str(path), payload)
        relative = self.to_workspace_relative_path(str(path))
        return FileWriteReceipt(
            logical_path=relative,
            absolute_path=str(path),
            bytes_written=size,
        )

    def workspace_create_text(
        self,
        relative_or_absolute_path: str,
        content: str,
        *,
        encoding: str = "utf-8",
        max_bytes: int | None = None,
    ) -> NamespaceCreateReceiptV1:
        """Create one workspace file only when it does not exist yet.

        This is the generic *create-only* producer.  It never overwrites and
        never removes an existing file: when the leaf already exists its
        content is preserved and the receipt reports ``created=False``.

        The effect is owned by the adapter.  A capability or policy refusal
        happens *before* any effect, so a denied create leaves no directory,
        no temporary file and no partially created tree.  The legacy writer
        is never used as an authorization probe.

        A failure after a possible creation surfaces an explicit unknown
        effect; the create is never replayed and never rolled back.

        Args:
            relative_or_absolute_path: The workspace-relative target path.
            content: The UTF-8 text to create the file with.
            encoding: Text encoding; only UTF-8 is accepted.
            max_bytes: Optional upper bound of the created content.

        Returns:
            A receipt that reports whether this call created the file.

        Raises:
            NamespaceOperationUnavailableError: The adapter cannot honour the
                managed create capability.
            NamespaceBoundaryError: The path is not a usable workspace leaf.
        """
        self._require_utf8_encoding(encoding)
        relative = self._lexical_workspace_relative_path(relative_or_absolute_path)
        payload = str(content).encode(encoding)
        bound = int(max_bytes) if max_bytes is not None else max(len(payload), _DEFAULT_CREATE_MAX_BYTES)
        creator = self._bind_managed_namespace_capability("create_managed_namespace_bytes")
        binding = NamespaceBindingV1(
            namespace_root=self._physical_workspace,
            relative_path=relative,
            logical_prefix="workspace",
        )
        return cast(NamespaceCreateReceiptV1, creator(binding, payload, max_bytes=bound))

    def workspace_remove_tree(self, relative_or_absolute_path: str, *, expected: FileMetadataV1) -> None:
        """Clean a physically drained owned directory using its original identity."""
        relative = self._lexical_workspace_relative_path(relative_or_absolute_path)
        guarded_remove_tree(self._physical_workspace, relative, expected=expected)

    def workspace_remove(
        self,
        relative_or_absolute_path: str,
        *,
        missing_ok: bool = True,
    ) -> bool:
        path = self.resolve_workspace_path(relative_or_absolute_path)
        return self._adapter.remove(str(path), missing_ok=missing_ok)

    def _logical_from_absolute_path(self, absolute_path: str) -> str:
        absolute = os.path.abspath(absolute_path)
        roots = (resolve_storage_roots_read_only if self._read_only_paths else resolve_storage_roots)(self.workspace)
        candidates = (
            ("runtime", roots.runtime_root),
            ("workspace", roots.workspace_persistent_root),
            ("config", roots.config_root),
        )
        for prefix, root in candidates:
            root_abs = os.path.abspath(root)
            try:
                if os.path.commonpath([root_abs, absolute]) != root_abs:
                    continue
            except ValueError:  # os.path.commonpath raises ValueError on cross-drive paths (Windows)
                continue
            rel = os.path.relpath(absolute, root_abs).replace("\\", "/")
            if rel in {".", ""}:
                return prefix
            return f"{prefix}/{rel}"
        raise ValueError(f"{UNSUPPORTED_PATH_PREFIX}: {absolute_path}")

    def _is_within_root(self, *, root: Path, target: Path) -> bool:
        root_abs = os.path.abspath(str(root))
        target_abs = os.path.abspath(str(target))
        try:
            return os.path.commonpath([root_abs, target_abs]) == root_abs
        except ValueError:  # os.path.commonpath raises ValueError on cross-drive paths (Windows)
            return False

    def _safe_channel(self, value: str) -> str:
        token = str(value or "").strip()
        if not token:
            raise ValueError("channel is required")
        if not _CHANNEL_PATTERN.fullmatch(token):
            raise ValueError(f"invalid channel: {value}")
        return token

    def _require_utf8_encoding(self, value: str) -> None:
        if str(value).strip().lower().replace("_", "-") != "utf-8":
            raise ValueError("KernelFileSystem only allows UTF-8 text encoding")

    def _utc_now_iso(self) -> str:
        return utc_now_str()
