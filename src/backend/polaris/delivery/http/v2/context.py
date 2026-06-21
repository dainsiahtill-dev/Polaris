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
from polaris.kernelone.storage import StorageLayout
from polaris.kernelone.storage.io_paths import build_cache_root

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

    workspace_raw = settings.workspace
    workspace = str(workspace_raw) if isinstance(workspace_raw, str) else workspace_raw
    workspace = workspace or "."

    layout = StorageLayout(workspace=workspace, runtime_base=build_cache_root("", workspace))
    shard = canonical_hash[:2]
    # Defence in depth: route through StorageLayout.resolve_artifact_path so
    # normalize_logical_rel_path + _join_under reject any path traversal or
    # unsupported prefix — even if get_path is later loosened.
    file_path = layout.resolve_artifact_path(f"runtime/contexts/{shard}/{canonical_hash}")

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
    workspace = str(workspace_raw) if isinstance(workspace_raw, str) else workspace_raw
    return workspace or "."


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
    "/v2/context/stats",
    response_model=ContextStoreStatsResponse,
    dependencies=[Depends(require_auth)],
)
def get_context_stats(request: Request) -> dict[str, Any]:
    """Return basic stats about the ``runtime/contexts/`` tree.

    This is a lightweight endpoint that doesn't require admin privileges.
    It returns basic statistics but doesn't include sweep functionality
    or detailed sweep reports.
    """
    workspace = _resolve_workspace(request)
    retention = _build_retention(workspace)
    stats = retention.get_stats()
    return {
        "workspace": stats["workspace"],
        "contexts_root": stats["contexts_root"],
        "file_count": stats["file_count"],
        "total_bytes": stats["total_bytes"],
        "oldest_mtime": stats["oldest_mtime"],
        "newest_mtime": stats["newest_mtime"],
        "config": stats["config"],
        "last_sweep_at": stats["last_sweep_at"],
        "last_sweep_report": None,
    }


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
    stats = retention.get_stats()
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
