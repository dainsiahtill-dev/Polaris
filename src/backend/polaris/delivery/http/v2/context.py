"""Context viewer API — retrieve stored LLM context snapshots by hash."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, Request
from polaris.cells.context.engine.public import (
    QueryFinalProviderRequestAuditV1,
    query_final_provider_request_audit,
)
from polaris.delivery.http.schemas.context import (
    ContextStoreStatsResponse,
    SweepReportResponse,
    SweepRequest,
)
from polaris.delivery.http.workspace import requested_or_active_workspace
from polaris.kernelone._runtime_config import get_workspace_metadata_dir_name
from polaris.kernelone.llm.engine.context_store_retention import (
    ContextStoreRetention,
    ContextStoreRetentionConfig,
    SweepReport,
)
from polaris.kernelone.llm.engine.internal.context_hash import (
    validate_context_hash,
)
from polaris.kernelone.storage.io_paths import resolve_storage_roots
from polaris.kernelone.storage.layout import default_kernelone_cache_base, workspace_key

from ._shared import StructuredHTTPException, get_state, require_auth
from .workspace_acl import WORKSPACE_HEADER, check_advisory_workspace_acl

logger = logging.getLogger(__name__)

router = APIRouter()
WORKSPACE_QUERY_PARAM = "workspace"


def _context_store_breakdown(root: str | Path, stats: dict[str, Any]) -> dict[str, Any]:
    """Return a JSON-friendly per-root store breakdown."""
    return {
        "contexts_root": str(root),
        "file_count": int(stats.get("file_count") or 0),
        "total_bytes": int(stats.get("total_bytes") or 0),
        "oldest_mtime": stats.get("oldest_mtime"),
        "newest_mtime": stats.get("newest_mtime"),
    }


def _context_stats_response(stats: dict[str, Any], last_sweep_report: dict[str, Any] | None) -> dict[str, Any]:
    """Build a stats response from the current KernelOne context store only."""
    contexts_root = str(stats.get("contexts_root") or "")
    return {
        "workspace": stats["workspace"],
        "contexts_root": contexts_root,
        "file_count": stats["file_count"],
        "total_bytes": stats["total_bytes"],
        "oldest_mtime": stats["oldest_mtime"],
        "newest_mtime": stats["newest_mtime"],
        "primary_store": _context_store_breakdown(contexts_root, stats),
        "config": stats["config"],
        "last_sweep_at": stats["last_sweep_at"],
        "last_sweep_report": last_sweep_report,
    }


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


def _context_snapshot_candidates(workspace: str, canonical_hash: str) -> list[tuple[str, Path]]:
    """Return bounded storage candidates for a ContextOS snapshot hash."""
    candidates: list[tuple[str, Path]] = []
    seen: set[str] = set()

    def add(source: str, runtime_root: str | Path) -> None:
        file_path = Path(runtime_root) / "contexts" / canonical_hash[:2] / canonical_hash
        path_key = str(file_path)
        if path_key in seen:
            return
        seen.add(path_key)
        candidates.append((source, file_path))

    add("active_runtime_root", Path(resolve_storage_roots(workspace).runtime_root))
    add("kernelone_system_cache", _default_kernelone_runtime_root(workspace))
    return candidates


def _default_kernelone_runtime_root(workspace: str) -> Path:
    workspace_abs = os.path.abspath(os.path.expanduser(str(workspace or os.getcwd())))
    cache_base = Path(default_kernelone_cache_base())
    metadata_dir = get_workspace_metadata_dir_name()
    cache_parts = cache_base.as_posix().split("/")
    projects_root = cache_base / "projects" if metadata_dir in cache_parts else cache_base / metadata_dir / "projects"
    return projects_root / workspace_key(workspace_abs) / "runtime"


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
    return _context_stats_response(retention.get_stats(), last_sweep_report=None)


@router.get("/v2/context/{hash}/final-request", dependencies=[Depends(require_auth)])
def get_context_final_request_by_hash(request: Request, hash: str) -> dict[str, Any]:
    """Retrieve final provider request audit evidence for a stored context snapshot."""

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
    check_advisory_workspace_acl(
        request=request,
        settings=settings,
        code="WORKSPACE_FORBIDDEN",
        message="Context snapshot belongs to a different workspace",
    )
    workspace = _resolve_workspace(request)
    result = query_final_provider_request_audit(
        QueryFinalProviderRequestAuditV1(
            workspace=workspace,
            context_snapshot_ref=canonical_hash,
        )
    )
    if result.ok:
        return result.payload

    status_code = 500
    if result.status == "invalid_ref":
        status_code = 400
    elif result.status == "not_found":
        status_code = 404
    elif result.status == "missing_provider_request":
        status_code = 409
    raise StructuredHTTPException(
        status_code=status_code,
        code=result.error_code or "FINAL_PROVIDER_REQUEST_AUDIT_UNAVAILABLE",
        message=result.error_message or "Final provider request audit is unavailable.",
        details=result.payload if isinstance(result.payload, dict) else {},
    )


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

    storage_source = ""
    file_path: Path | None = None
    candidates = _context_snapshot_candidates(workspace, canonical_hash)
    for candidate_source, candidate_path in candidates:
        if candidate_path.is_file():
            storage_source = candidate_source
            file_path = candidate_path
            break

    if file_path is None:
        raise StructuredHTTPException(
            status_code=404,
            code="CONTEXT_NOT_FOUND",
            message=f"Context snapshot not found for hash {canonical_hash}",
            details={
                "context_hash": canonical_hash,
                "workspace": workspace,
                "searched_paths": [{"source": source, "context_path": str(path)} for source, path in candidates],
            },
        )
    payload = _load_context_payload(file_path, canonical_hash)

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
    """Resolve the admin gate from the environment. Default enabled in dev."""
    raw = os.environ.get(ADMIN_ENV_FLAG)
    if raw is None:
        return True
    return str(raw).strip().lower() in ("1", "true", "yes", "on")


def _resolve_workspace(request: Request) -> str:
    """Resolve the workspace used for ContextOS snapshot storage.

    ContextOS real-time events can be viewed from a launcher/control surface
    that is not bound to the same workspace as the snapshot producer.  The
    explicit ``?workspace=`` query parameter is therefore the canonical
    per-request selector for reads; ``X-ContextOS-Workspace`` remains an
    advisory compatibility hint for callers that still use the older header.
    """
    state = get_state(request)
    requested = _request_workspace_token(request)
    return requested_or_active_workspace(state.settings, requested)


def _request_workspace_token(request: Request) -> str:
    query_value = request.query_params.get(WORKSPACE_QUERY_PARAM)
    if isinstance(query_value, str) and query_value.strip():
        return query_value
    header_value = request.headers.get(WORKSPACE_HEADER)
    if isinstance(header_value, str) and header_value.strip():
        return header_value
    return ""


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
    counter = retention._read_sweep_state()
    last_report: dict[str, Any] | None = None
    last_sweep_report = counter.get("last_sweep_report")
    if isinstance(last_sweep_report, dict):
        last_report = last_sweep_report
    return _context_stats_response(retention.get_stats(), last_sweep_report=last_report)


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
