"""Context viewer API — retrieve stored LLM context snapshots by hash."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, Request
from polaris.delivery.http.schemas.context import (
    ContextStoreStatsResponse,
    SweepReportResponse,
    SweepRequest,
)
from polaris.kernelone.llm.engine.context_store_retention import (
    ContextStoreRetention,
    ContextStoreRetentionConfig,
    SweepReport,
)
from polaris.kernelone.llm.engine.internal.context_hash import (
    validate_context_hash,
)
from polaris.kernelone.storage.io_paths import resolve_storage_roots

from ._shared import StructuredHTTPException, get_state, require_auth
from .workspace_acl import check_advisory_workspace_acl

logger = logging.getLogger(__name__)

router = APIRouter()


def _legacy_context_file_candidates(canonical_hash: str) -> list[Path]:
    """Return bounded legacy Polaris runtime candidates for a context hash.

    Older factory/bench workers can write per-LLM context snapshots under
    ``~/.cache/polaris/.polaris/projects`` while the current reader resolves
    active workspaces through KernelOne's runtime root.  This compatibility
    lookup is deliberately limited to that legacy runtime root and the exact
    hash shard; it is not a general cross-workspace filesystem scan.
    """
    shard = canonical_hash[:2]
    projects_root = Path.home() / ".cache" / "polaris" / ".polaris" / "projects"
    if not projects_root.is_dir():
        return []
    pattern = f"*/runtime/projects/*/runtime/contexts/{shard}/{canonical_hash}"
    return [path for path in projects_root.glob(pattern) if path.is_file()]


def _legacy_contexts_root_for_stats(contexts_root: str) -> Path | None:
    """Return the bounded legacy contexts root paired with ``contexts_root``."""
    active_root = Path(str(contexts_root or ""))
    if active_root.name != "contexts" or active_root.parent.name != "runtime":
        return None
    project_runtime_root = active_root.parent
    project_root = project_runtime_root.parent
    project_key = project_root.name
    if not project_key:
        return None
    candidate = project_runtime_root / "projects" / project_key / "runtime" / "contexts"
    if candidate == active_root:
        return None
    return candidate


def _scan_context_tree(root: Path) -> dict[str, Any]:
    """Collect a cheap stats snapshot for a bounded context tree."""
    if not root.is_dir():
        return {
            "file_count": 0,
            "total_bytes": 0,
            "oldest_mtime": None,
            "newest_mtime": None,
        }

    file_count = 0
    total_bytes = 0
    oldest_mtime: float | None = None
    newest_mtime: float | None = None
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        try:
            stat = path.stat()
        except OSError:
            continue
        file_count += 1
        total_bytes += int(stat.st_size)
        mtime = float(stat.st_mtime)
        if oldest_mtime is None or mtime < oldest_mtime:
            oldest_mtime = mtime
        if newest_mtime is None or mtime > newest_mtime:
            newest_mtime = mtime

    return {
        "file_count": file_count,
        "total_bytes": total_bytes,
        "oldest_mtime": oldest_mtime,
        "newest_mtime": newest_mtime,
    }


def _context_store_breakdown(root: str | Path, stats: dict[str, Any]) -> dict[str, Any]:
    """Return a JSON-friendly per-root store breakdown."""
    return {
        "contexts_root": str(root),
        "file_count": int(stats.get("file_count") or 0),
        "total_bytes": int(stats.get("total_bytes") or 0),
        "oldest_mtime": stats.get("oldest_mtime"),
        "newest_mtime": stats.get("newest_mtime"),
    }


def _merge_legacy_context_stats(stats: dict[str, Any]) -> dict[str, Any]:
    """Merge active stats with the legacy nested factory context tree.

    Factory runs can still write snapshots under
    ``runtime/projects/<project-key>/runtime/contexts``. The hash reader
    already supports that bounded legacy path, so the stats endpoint must count
    it too; otherwise ContextOS reports zero snapshots while individual refs are
    readable.
    """
    merged = dict(stats)
    contexts_root = str(stats.get("contexts_root") or "")
    merged["primary_store"] = _context_store_breakdown(contexts_root, stats)
    legacy_root = _legacy_contexts_root_for_stats(str(stats.get("contexts_root") or ""))
    if legacy_root is None:
        merged["legacy_store"] = None
        return merged

    legacy = _scan_context_tree(legacy_root)
    legacy_file_count = int(legacy["file_count"])
    merged["legacy_store"] = _context_store_breakdown(legacy_root, legacy)
    if legacy_file_count <= 0:
        return merged

    merged["file_count"] = int(stats.get("file_count") or 0) + legacy_file_count
    merged["total_bytes"] = int(stats.get("total_bytes") or 0) + int(legacy["total_bytes"])

    active_oldest = stats.get("oldest_mtime")
    legacy_oldest = legacy.get("oldest_mtime")
    merged["oldest_mtime"] = (
        legacy_oldest
        if active_oldest is None
        else active_oldest
        if legacy_oldest is None
        else min(float(active_oldest), float(legacy_oldest))
    )

    active_newest = stats.get("newest_mtime")
    legacy_newest = legacy.get("newest_mtime")
    merged["newest_mtime"] = (
        legacy_newest
        if active_newest is None
        else active_newest
        if legacy_newest is None
        else max(float(active_newest), float(legacy_newest))
    )

    config = dict(stats.get("config") or {})
    config["legacy_contexts_root"] = str(legacy_root)
    config["legacy_file_count"] = legacy_file_count
    merged["config"] = config
    return merged


def _load_context_payload(file_path: Path, canonical_hash: str) -> dict[str, Any]:
    try:
        with open(file_path, encoding="utf-8") as handle:
            payload = json.load(handle)
    except (RuntimeError, ValueError) as exc:
        logger.error("Failed to read context snapshot %s: %s", canonical_hash, exc)
        raise StructuredHTTPException(
            status_code=500,
            code="CONTEXT_READ_ERROR",
            message="Failed to read context snapshot",
        ) from exc

    if not isinstance(payload, dict):
        raise StructuredHTTPException(
            status_code=500,
            code="CONTEXT_FORMAT_ERROR",
            message="Context snapshot has invalid format",
        )
    return payload


@router.get(
    "/v2/context/stats",
    response_model=ContextStoreStatsResponse,
    dependencies=[Depends(require_auth)],
)
def get_context_stats(request: Request) -> dict[str, Any]:
    """Return basic stats about the ``runtime/contexts/`` tree.

    This static route must be registered before ``/v2/context/{hash}``;
    otherwise Starlette treats ``stats`` as a snapshot hash and the request
    fails with ``INVALID_HASH`` before reaching the stats handler.
    """
    workspace = _resolve_workspace(request)
    retention = _build_retention(workspace)
    stats = _merge_legacy_context_stats(retention.get_stats())
    return {
        "workspace": stats["workspace"],
        "contexts_root": stats["contexts_root"],
        "file_count": stats["file_count"],
        "total_bytes": stats["total_bytes"],
        "oldest_mtime": stats["oldest_mtime"],
        "newest_mtime": stats["newest_mtime"],
        "primary_store": stats.get("primary_store"),
        "legacy_store": stats.get("legacy_store"),
        "config": stats["config"],
        "last_sweep_at": stats["last_sweep_at"],
        "last_sweep_report": None,
    }


@router.get("/v2/context/{hash}", dependencies=[Depends(require_auth)])
def get_context_by_hash(request: Request, hash: str) -> dict[str, Any]:
    """Retrieve a stored context snapshot by its SHA-256 hash reference.

    Args:
        hash: The 24-character truncated SHA-256 hash key.

    Returns:
        The stored context payload with enriched metadata.

    Raises:
        StructuredHTTPException: 404 if not found, 400 if hash is invalid,
            403 if ``X-ContextOS-Workspace`` explicitly targets another workspace.
    """
    try:
        canonical_hash = validate_context_hash(hash)
    except ValueError as exc:
        raise StructuredHTTPException(
            status_code=400,
            code="INVALID_HASH",
            message="Hash must be a 24-character hexadecimal string",
        ) from exc

    state = get_state(request)
    settings = state.settings

    # Advisory ACL: only enforces when the caller explicitly names a different
    # workspace via the X-ContextOS-Workspace header. Single-tenant desktop
    # (no header) preserves the previous behaviour.
    check_advisory_workspace_acl(
        request=request,
        settings=settings,
        code="WORKSPACE_FORBIDDEN",
        message="Context snapshot belongs to a different workspace",
    )

    workspace = _resolve_workspace(request)

    shard = canonical_hash[:2]
    file_path = Path(resolve_storage_roots(workspace).runtime_root) / "contexts" / shard / canonical_hash

    storage_source = "active_workspace"
    if os.path.isfile(file_path):
        payload = _load_context_payload(Path(file_path), canonical_hash)
    else:
        legacy_candidates = _legacy_context_file_candidates(canonical_hash)
        if not legacy_candidates:
            raise StructuredHTTPException(
                status_code=404,
                code="CONTEXT_NOT_FOUND",
                message=f"Context snapshot not found for hash {canonical_hash}",
            )
        storage_source = "legacy_runtime"
        payload = _load_context_payload(legacy_candidates[0], canonical_hash)

    if not isinstance(payload, dict):
        raise StructuredHTTPException(
            status_code=500,
            code="CONTEXT_FORMAT_ERROR",
            message="Context snapshot has invalid format",
        )

    messages = payload.get("messages")
    message_count = len(messages) if isinstance(messages, list) else 0
    content_str = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))

    return {
        "schema_version": payload.get("schema_version", 1),
        "hash": canonical_hash,
        "trace_id": payload.get("trace_id"),
        "call_id": payload.get("call_id"),
        "messages": messages,
        "stored_at": payload.get("stored_at"),
        "message_count": message_count,
        "total_chars": len(content_str),
        "storage_source": storage_source,
    }


# ---------------------------------------------------------------------------
# Admin endpoints — opt-in surface for inspecting and forcing a sweep.
# Gated by KERNELONE_CONTEXT_ADMIN_ENABLED (default false).
# ---------------------------------------------------------------------------
ADMIN_ENV_FLAG = "KERNELONE_CONTEXT_ADMIN_ENABLED"


def _admin_enabled() -> bool:
    """Resolve the opt-in admin gate from the environment."""
    raw = os.environ.get(ADMIN_ENV_FLAG)
    if raw is None:
        return False
    return str(raw).strip().lower() in ("1", "true", "yes", "on")


def _resolve_workspace(request: Request) -> str:
    """Mirror the workspace resolution used by ``get_context_by_hash``."""
    state = get_state(request)
    workspace_raw = state.settings.workspace
    return str(workspace_raw) if workspace_raw else "."


def _build_retention(workspace: str) -> ContextStoreRetention:
    """Construct a fresh :class:`ContextStoreRetention` for the workspace.

    The admin endpoints never share state with the on-read gate: each
    call instantiates a fresh instance so an admin-triggered sweep and a
    producer-driven sweep can never race on a shared ``_last_sweep_at``
    field.
    """
    settings = None
    try:
        from polaris.bootstrap.config import get_settings

        settings = get_settings()
    except (ImportError, RuntimeError, ValueError) as exc:
        logger.debug("context_admin: failed to load settings: %s", exc)
    config: ContextStoreRetentionConfig | None = None
    if settings is not None:
        runtime_cfg = getattr(settings, "runtime", None)
        if runtime_cfg is not None:
            config = getattr(runtime_cfg, "context_store_retention", None)
    return ContextStoreRetention(workspace=workspace, config=config)


@router.get(
    "/v2/context/admin/stats",
    response_model=ContextStoreStatsResponse,
    dependencies=[Depends(require_auth)],
)
def get_context_admin_stats(request: Request) -> dict[str, Any]:
    """Return stats about the ``runtime/contexts/`` tree.

    Gated by :data:`ADMIN_ENV_FLAG` — when the env var is unset (the
    default), the endpoint returns ``404 NOT_FOUND`` so the surface is
    invisible until the operator explicitly enables it.
    """
    if not _admin_enabled():
        raise StructuredHTTPException(
            status_code=404,
            code="ADMIN_DISABLED",
            message="Context admin surface is disabled. Set KERNELONE_CONTEXT_ADMIN_ENABLED=1 to enable.",
        )
    workspace = _resolve_workspace(request)
    retention = _build_retention(workspace)
    stats = _merge_legacy_context_stats(retention.get_stats())
    counter = retention._read_sweep_state()
    last_report: dict[str, Any] | None = None
    last_sweep_report = counter.get("last_sweep_report")
    if isinstance(last_sweep_report, dict):
        last_report = last_sweep_report
    return {
        "workspace": stats["workspace"],
        "contexts_root": stats["contexts_root"],
        "file_count": stats["file_count"],
        "total_bytes": stats["total_bytes"],
        "oldest_mtime": stats["oldest_mtime"],
        "newest_mtime": stats["newest_mtime"],
        "primary_store": stats.get("primary_store"),
        "legacy_store": stats.get("legacy_store"),
        "config": stats["config"],
        "last_sweep_at": stats["last_sweep_at"],
        "last_sweep_report": last_report,
    }


@router.post(
    "/v2/context/admin/sweep",
    response_model=SweepReportResponse,
    dependencies=[Depends(require_auth)],
)
def post_context_admin_sweep(request: Request, body: SweepRequest | None = None) -> dict[str, Any]:
    """Force a retention sweep against ``runtime/contexts/``.

    The sweep is unconditional — it does not consult the throttle
    window. Body is optional; an empty body runs an ``"admin"`` sweep.
    """
    if not _admin_enabled():
        raise StructuredHTTPException(
            status_code=404,
            code="ADMIN_DISABLED",
            message="Context admin surface is disabled. Set KERNELONE_CONTEXT_ADMIN_ENABLED=1 to enable.",
        )
    workspace = _resolve_workspace(request)
    retention = _build_retention(workspace)
    triggers = (body.triggers if body and body.triggers else None) or ["admin"]
    report: SweepReport = retention.sweep(triggers=triggers)
    return {
        "scanned_files": report.scanned_files,
        "removed_files": report.removed_files,
        "removed_bytes": report.removed_bytes,
        "kept_files": report.kept_files,
        "total_bytes_after": report.total_bytes_after,
        "elapsed_ms": report.elapsed_ms,
        "triggers": list(report.triggers),
    }
