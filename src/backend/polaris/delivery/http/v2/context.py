"""Context viewer API — retrieve stored LLM context snapshots by hash."""

import json
import logging
import os
from typing import Any

from fastapi import APIRouter, Depends, Request
from polaris.kernelone.storage import StorageLayout

from ._shared import StructuredHTTPException, get_state, require_auth

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/v2/context/{hash}", dependencies=[Depends(require_auth)])
def get_context_by_hash(request: Request, hash: str) -> dict[str, Any]:
    """Retrieve a stored context snapshot by its SHA-256 hash reference.

    Args:
        hash: The 24-character truncated SHA-256 hash key.

    Returns:
        The stored context payload with enriched metadata.

    Raises:
        StructuredHTTPException: 404 if not found, 400 if hash is invalid.
    """
    # Validate hash format (24-char hex)
    if not hash or len(hash) != 24 or not all(c in "0123456789abcdef" for c in hash.lower()):
        raise StructuredHTTPException(
            status_code=400,
            code="INVALID_HASH",
            message="Hash must be a 24-character hexadecimal string",
        )

    state = get_state(request)
    workspace_raw = state.settings.workspace
    workspace = str(workspace_raw) if isinstance(workspace_raw, str) else workspace_raw
    workspace = workspace or "."

    layout = StorageLayout(workspace=workspace)
    shard = hash[:2]
    file_path = layout.get_path("runtime", f"contexts/{shard}/{hash}")

    if not os.path.isfile(file_path):
        raise StructuredHTTPException(
            status_code=404,
            code="CONTEXT_NOT_FOUND",
            message=f"Context snapshot not found for hash {hash}",
        )

    try:
        with open(file_path, encoding="utf-8") as handle:
            payload = json.load(handle)
    except (RuntimeError, ValueError) as e:
        logger.error(f"Failed to read context snapshot {hash}: {e}")
        raise StructuredHTTPException(
            status_code=500,
            code="CONTEXT_READ_ERROR",
            message="Failed to read context snapshot",
        ) from e

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
        "hash": hash,
        "trace_id": payload.get("trace_id"),
        "call_id": payload.get("call_id"),
        "messages": messages,
        "stored_at": payload.get("stored_at"),
        "message_count": message_count,
        "total_chars": len(content_str),
    }
