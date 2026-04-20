from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from polaris.kernelone.storage import (
    UNSUPPORTED_PATH_PREFIX,
    normalize_logical_rel_path,
    resolve_logical_path,
    resolve_storage_roots,
)
from polaris.kernelone.utils import utc_now_str

from .contracts import FileWriteReceipt, KernelFileSystemAdapter

_CHANNEL_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+$")


class KernelFileSystem:
    """Kernel-level file system boundary for all file effects."""

    def __init__(self, workspace: str, adapter: KernelFileSystemAdapter) -> None:
        self.workspace = str(Path(workspace).resolve())
        self._adapter = adapter

    def resolve_path(self, logical_path: str) -> Path:
        normalized = self.to_logical_path(logical_path)
        return Path(resolve_logical_path(self.workspace, normalized))

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
        return self._adapter.read_text(str(self.resolve_path(logical_path)), encoding=encoding)

    def read_bytes(self, logical_path: str) -> bytes:
        return self._adapter.read_bytes(str(self.resolve_path(logical_path)))

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
        path = self.resolve_path(normalized)
        size = self._adapter.write_text(str(path), text, encoding=encoding)
        return FileWriteReceipt(
            logical_path=normalized,
            absolute_path=str(path),
            bytes_written=size,
        )

    def append_text(
        self,
        logical_path: str,
        content: str,
        *,
        encoding: str = "utf-8",
    ) -> FileWriteReceipt:
        self._require_utf8_encoding(encoding)
        normalized = self.to_logical_path(logical_path)
        text = str(content)
        path = self.resolve_path(normalized)
        size = self._adapter.append_text(str(path), text, encoding=encoding)
        return FileWriteReceipt(
            logical_path=normalized,
            absolute_path=str(path),
            bytes_written=size,
        )

    def write_bytes(self, logical_path: str, content: bytes) -> FileWriteReceipt:
        normalized = self.to_logical_path(logical_path)
        path = self.resolve_path(normalized)
        payload = bytes(content)
        size = self._adapter.write_bytes(str(path), payload)
        return FileWriteReceipt(
            logical_path=normalized,
            absolute_path=str(path),
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

    def append_jsonl(self, logical_path: str, payload: dict[str, Any]) -> FileWriteReceipt:
        if not isinstance(payload, dict):
            raise TypeError("jsonl payload must be a dict")
        line = json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n"
        return self.append_text(logical_path, line, encoding="utf-8")

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
        return self._adapter.remove(str(self.resolve_path(normalized)), missing_ok=missing_ok)

    # ---------------------------------------------------------------------
    # Workspace-scoped APIs for LLM tooling paths (relative to workspace root)
    # ---------------------------------------------------------------------

    def resolve_workspace_path(self, relative_or_absolute_path: str) -> Path:
        raw = str(relative_or_absolute_path or "").strip()
        if not raw:
            raise ValueError("workspace path is required")
        candidate = Path(raw)
        workspace_root = Path(self.workspace).resolve()
        target = candidate.resolve() if candidate.is_absolute() else (workspace_root / candidate).resolve()
        if not self._is_within_root(root=workspace_root, target=target):
            raise ValueError(f"{UNSUPPORTED_PATH_PREFIX}: {relative_or_absolute_path}")
        return target

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
        roots = resolve_storage_roots(self.workspace)
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
