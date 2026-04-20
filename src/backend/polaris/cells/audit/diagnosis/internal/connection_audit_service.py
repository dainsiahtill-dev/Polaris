from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any

from polaris.kernelone.audit import (
    KernelAuditEventType,
    KernelAuditRuntime,
)
from polaris.kernelone.audit.validators import SYSTEM_ROLE
from polaris.kernelone.utils import utc_now_iso

_logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return utc_now_iso()


def _normalize_details(details: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(details, dict):
        return {}
    normalized: dict[str, Any] = {}
    for key, value in details.items():
        try:
            json.dumps(value, ensure_ascii=False)
            normalized[str(key)] = value
        except OSError:
            normalized[str(key)] = str(value)
    return normalized


def write_ws_connection_event_sync(
    *,
    workspace: str,
    cache_root: str,
    endpoint: str,
    connection_id: str,
    event: str,
    details: dict[str, Any] | None = None,
) -> str:
    """Emit WS lifecycle event via KernelAuditRuntime only (no JSONL dual-write)."""
    workspace_token = str(workspace or "").strip()
    if not workspace_token:
        return ""

    record = {
        "timestamp": _now_iso(),
        "endpoint": str(endpoint or "").strip(),
        "connection_id": str(connection_id or "").strip(),
        "event": str(event or "").strip().lower(),
        "details": _normalize_details(details),
    }

    runtime_root = str(cache_root or "").strip()
    try:
        runtime = KernelAuditRuntime.get_instance(Path(runtime_root).resolve())
        runtime.emit_event(
            event_type=KernelAuditEventType.POLICY_CHECK,
            role=SYSTEM_ROLE,
            workspace=workspace_token,
            task_id=f"ws-{record['connection_id']}",
            action={"name": "ws_connection_event", "result": "success"},
            data={
                "endpoint": record["endpoint"],
                "event": record["event"],
                "details": record["details"],
            },
            context={"origin": "ws_runtime"},
        )
    except OSError as exc:
        # Best-effort: runtime event emission failure should not propagate.
        _logger.debug("ws_runtime event write failed (best-effort): %s", exc)
    return ""


async def write_ws_connection_event(
    *,
    workspace: str,
    cache_root: str,
    endpoint: str,
    connection_id: str,
    event: str,
    details: dict[str, Any] | None = None,
) -> str:
    return await asyncio.to_thread(
        write_ws_connection_event_sync,
        workspace=workspace,
        cache_root=cache_root,
        endpoint=endpoint,
        connection_id=connection_id,
        event=event,
        details=details,
    )
