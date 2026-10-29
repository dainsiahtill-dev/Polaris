"""Context snapshot storage candidate resolution.

This module is platform infrastructure, not a Bench-specific adapter.  Instance
registry records may be produced by the launcher, factory_bench, or external
agent-driven starts; ContextOS uses them only to resolve the runtime root for a
workspace-bound context snapshot.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from polaris.cells.instances.public.service import query_instance_records
from polaris.kernelone._runtime_config import get_workspace_metadata_dir_name
from polaris.kernelone.fs import (
    GuardedMetadataError,
    GuardedRegularFileSnapshotError,
    GuardedRegularFileSnapshotV1,
    KernelFileSystem,
    get_default_adapter,
    read_guarded_regular_file_snapshot,
)
from polaris.kernelone.llm.engine.internal.context_hash import validate_context_hash
from polaris.kernelone.storage import resolve_storage_roots_read_only
from polaris.kernelone.storage.layout import default_kernelone_cache_base, workspace_key


@dataclass(frozen=True, slots=True)
class ContextSnapshotRead:
    storage_source: str
    context_path: Path
    searched_paths: tuple[dict[str, str], ...]
    snapshot: GuardedRegularFileSnapshotV1
    payload: dict[str, Any]


class ContextSnapshotReadError(RuntimeError):
    """A failed read with workspace-bound discovery and physical failure evidence."""

    def __init__(self, *, status: str, code: str, message: str, details: dict[str, Any]) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.details = details


def _iter_context_snapshot_candidates(
    workspace: str, context_hash: str, registry_errors: list[dict[str, Any]]
) -> Iterator[tuple[str, Path]]:
    seen: set[str] = set()

    def candidate(source: str, runtime_root: str | Path) -> Iterator[tuple[str, Path]]:
        path = Path(os.path.abspath(str(runtime_root))) / "contexts" / context_hash[:2] / context_hash
        if str(path) not in seen:
            seen.add(str(path))
            yield source, path

    yield from candidate("active_runtime_root", resolve_storage_roots_read_only(workspace).runtime_root)
    for source, root in instance_runtime_root_candidates(workspace, registry_errors=registry_errors):
        yield from candidate(source, root)
    yield from candidate("kernelone_system_cache", default_kernelone_runtime_root(workspace))


def context_snapshot_candidates(workspace: str, context_hash: str) -> list[tuple[str, Path]]:
    """Return ordered candidates; unavailable registry discovery fails explicitly."""
    canonical_hash = validate_context_hash(context_hash)
    registry_errors: list[dict[str, Any]] = []
    candidates = list(_iter_context_snapshot_candidates(workspace, canonical_hash, registry_errors))
    if registry_errors:
        raise ContextSnapshotReadError(
            status="unavailable",
            code="context_snapshot_discovery_unavailable",
            message="Instance registry discovery is unavailable.",
            details={
                "workspace": workspace,
                "context_hash": canonical_hash,
                "registry_errors": registry_errors,
                "searched_paths": [{"source": source, "context_path": str(path)} for source, path in candidates],
            },
        )
    return candidates


def read_context_snapshot(workspace: str, context_hash: str) -> ContextSnapshotRead:
    """Read first-present immutable bytes via KFS, without provisioning or fallback on damage."""
    canonical_hash = validate_context_hash(context_hash)
    registry_errors: list[dict[str, Any]] = []
    searched_paths: list[dict[str, str]] = []
    details: dict[str, Any] = {"workspace": workspace, "context_hash": canonical_hash, "searched_paths": searched_paths}
    for source, path in _iter_context_snapshot_candidates(workspace, canonical_hash, registry_errors):
        searched_paths.append({"source": source, "context_path": str(path)})
        root = path.parents[2]
        relative = path.relative_to(root).as_posix()
        fs = KernelFileSystem(str(root), get_default_adapter(), read_only_paths=True)
        try:
            metadata = fs.workspace_lstat(relative)
        except GuardedMetadataError as exc:
            if exc.code == "guarded_metadata_missing":
                continue
            raise ContextSnapshotReadError(
                status="unreadable",
                code="context_snapshot_unreadable",
                message=str(exc),
                details={
                    **details,
                    "context_path": str(path),
                    "storage_source": source,
                    "kfs_error": {"code": exc.code, "details": exc.details},
                },
            ) from exc
        selected = {**details, "context_path": str(path), "storage_source": source}
        try:
            if not stat.S_ISREG(metadata.mode) or metadata.nlink != 1:
                raise ContextSnapshotReadError(
                    status="unreadable",
                    code="context_snapshot_unsafe_file",
                    message="Context snapshot must be a single-link regular file.",
                    details=selected,
                )
            snapshot = read_guarded_regular_file_snapshot(str(root), relative, max(1, metadata.size))
            fields = ("device", "inode", "size", "mtime_ns", "ctime_ns", "root_device", "root_inode")
            if any(getattr(metadata, field) != getattr(snapshot, field) for field in fields):
                raise ContextSnapshotReadError(
                    status="unreadable",
                    code="context_snapshot_identity_drift",
                    message="Context snapshot changed after metadata observation.",
                    details=selected,
                )
        except (GuardedRegularFileSnapshotError, OSError, ValueError) as exc:
            raise ContextSnapshotReadError(
                status="unreadable",
                code="context_snapshot_unreadable",
                message=str(exc),
                details={
                    **selected,
                    "kfs_error": {"code": getattr(exc, "code", "read_failed"), "details": getattr(exc, "details", {})},
                },
            ) from exc
        if hashlib.sha256(snapshot.content).hexdigest()[:24] != canonical_hash:
            raise ContextSnapshotReadError(
                status="unreadable",
                code="context_snapshot_hash_mismatch",
                message="Context snapshot bytes do not match the requested hash.",
                details=selected,
            )
        try:
            payload = json.loads(snapshot.content.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ContextSnapshotReadError(
                status="unreadable",
                code="context_snapshot_unreadable",
                message="Context snapshot is not valid UTF-8 JSON.",
                details=selected,
            ) from exc
        if not isinstance(payload, dict):
            raise ContextSnapshotReadError(
                status="invalid_snapshot",
                code="context_snapshot_invalid_format",
                message="Context snapshot payload must be an object.",
                details=selected,
            )
        return ContextSnapshotRead(source, path, tuple(searched_paths), snapshot, payload)
    if registry_errors:
        raise ContextSnapshotReadError(
            status="unavailable",
            code="context_snapshot_discovery_unavailable",
            message="Context snapshot lookup is incomplete because instance registry discovery is unavailable.",
            details={**details, "registry_errors": registry_errors},
        )
    raise ContextSnapshotReadError(
        status="not_found",
        code="context_snapshot_not_found",
        message=f"Context snapshot not found for hash {canonical_hash}.",
        details=details,
    )


def default_kernelone_runtime_root(workspace: str) -> Path:
    """Return the default KernelOne runtime root for ``workspace``."""
    workspace_abs = os.path.abspath(os.path.expanduser(str(workspace or os.getcwd())))
    cache_base = Path(default_kernelone_cache_base())
    metadata_dir = get_workspace_metadata_dir_name()
    cache_parts = cache_base.as_posix().split("/")
    projects_root = cache_base / "projects" if metadata_dir in cache_parts else cache_base / metadata_dir / "projects"
    return cast("Path", (projects_root / workspace_key(workspace_abs) / "runtime"))


def instance_runtime_root_candidates(
    workspace: str, *, registry_errors: list[dict[str, Any]] | None = None
) -> list[tuple[str, Path]]:
    """Return registered instance runtime roots that exactly match ``workspace``."""
    requested_workspace = _normalize_workspace(workspace)
    if not requested_workspace:
        return []
    try:
        records = query_instance_records()
    except (OSError, RuntimeError, ValueError) as exc:
        if registry_errors is not None:
            registry_errors.append({"code": "instance_registry_unavailable", "detail": str(exc)})
        return []
    if isinstance(records, dict):
        if registry_errors is not None:
            registry_errors.append(records["error"])
        return []

    matches: list[dict[str, Any]] = []
    for record in records:
        if not isinstance(record, dict):
            continue
        record_workspace = _normalize_workspace(record.get("workspace"))
        runtime_root = _string_field(record.get("runtime_root"))
        if record_workspace == requested_workspace and runtime_root:
            matches.append(record)

    def sort_key(record: dict[str, Any]) -> tuple[int, str]:
        status = _string_field(record.get("status")).lower()
        running_rank = 0 if status == "running" else 1
        updated = _string_field(record.get("updated_at") or record.get("last_started_at") or record.get("created_at"))
        return (running_rank, updated)

    matches.sort(key=sort_key)
    return [
        (
            f"instance_runtime_root:{_string_field(record.get('instance_id')) or 'unknown'}",
            Path(_string_field(record["runtime_root"])),
        )
        for record in matches
    ]


def _normalize_workspace(value: object) -> str:
    token = _string_field(value)
    if not token:
        return ""
    return os.path.abspath(os.path.expanduser(token)).rstrip(os.sep)


def _string_field(value: object) -> str:
    return str(value or "").strip() if isinstance(value, str) or value is not None else ""
