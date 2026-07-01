"""Run-id resolution for role-kernel streaming turns."""

from __future__ import annotations

import json
import logging
import os
import uuid

from polaris.kernelone.storage import resolve_storage_roots

logger = logging.getLogger(__name__)


def resolve_stream_run_id(request_run_id: str | None, workspace: str) -> str:
    """Resolve a stream run id from the request, workspace metadata, or a new id."""
    requested = str(request_run_id or "").strip()
    if requested:
        return requested

    workspace_path = str(workspace or "").strip() or os.getcwd()
    try:
        roots = resolve_storage_roots(workspace_path)
        latest_run_file = os.path.join(roots.runtime_root, "latest_run.json")
        if os.path.isfile(latest_run_file):
            with open(latest_run_file, encoding="utf-8") as handle:
                payload = json.load(handle)
            if isinstance(payload, dict) and payload.get("run_id"):
                return str(payload.get("run_id", "")).strip()
    except (RuntimeError, ValueError):
        logger.warning("Failed to resolve stream run_id from latest_run.json", exc_info=True)

    return f"auto_{uuid.uuid4().hex[:12]}"


__all__ = ["resolve_stream_run_id"]
