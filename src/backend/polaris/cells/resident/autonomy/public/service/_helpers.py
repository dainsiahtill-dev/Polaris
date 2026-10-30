"""Shared helpers for resident.autonomy public service."""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from polaris.cells.audit.evidence.public.service import EvidenceBundleService, create_evidence_bundle_service
from polaris.cells.resident.autonomy.internal.resident_runtime_service import get_resident_service
from polaris.cells.resident.autonomy.public import service as _service_pkg

logger = logging.getLogger("polaris.cells.resident.autonomy.public.service")
_JETSTREAM_PUBLISH_FALSE_VALUES = {"0", "false", "no", "off", "disabled"}
_RESIDENT_STATUS_CHANNEL = "status.resident"


def _jetstream_publish_enabled() -> bool:
    raw = str(os.environ.get("KERNELONE_JETSTREAM_PUBLISH") or "").strip().lower()
    return bool(raw) and raw not in _JETSTREAM_PUBLISH_FALSE_VALUES


def publish_resident_status_update(
    *, workspace: str, action: str, status_payload: dict[str, Any] | None = None, detail: dict[str, Any] | None = None
) -> bool:
    """Publish the latest Resident AGI projection to runtime.v2."""
    if not _jetstream_publish_enabled():
        return False
    workspace_token = str(workspace or "").strip()
    if not workspace_token:
        return False
    try:
        roots = _service_pkg.resolve_storage_roots(workspace_token)
        workspace_key = str(getattr(roots, "workspace_key", "") or "").strip()
        if not workspace_key:
            return False
        resident_status = status_payload or get_resident_service(workspace_token).get_status(include_details=True)
        now = datetime.now(timezone.utc)
        event_id = f"resident-status-{uuid4().hex[:12]}"
        envelope = {
            "schema_version": "runtime.v2",
            "event_id": event_id,
            "workspace_key": workspace_key,
            "run_id": str((detail or {}).get("run_id") or ""),
            "channel": _RESIDENT_STATUS_CHANNEL,
            "kind": "resident_status_update",
            "ts": now.isoformat(),
            "cursor": 0,
            "trace_id": event_id,
            "payload": {
                "action": str(action or "updated").strip() or "updated",
                "workspace": workspace_token,
                "resident": resident_status,
                "projection": resident_status,
                "detail": dict(detail or {}),
                "role_id": "resident_agi",
                "runtime_foundation": "roles.runtime + ContextOS + TransactionKernel",
            },
            "meta": {
                "source": "resident.autonomy",
                "role_id": "resident_agi",
                "runtime_foundation": "roles.runtime + ContextOS + TransactionKernel",
                "channel": f"runtime.v2.{_RESIDENT_STATUS_CHANNEL}",
            },
        }
        return _service_pkg.get_log_jetstream_publisher().publish(
            subject=f"hp.runtime.{workspace_key}.{_RESIDENT_STATUS_CHANNEL}", payload=envelope
        )
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        logger.debug("Resident AGI status JetStream publish failed: %s", exc)
        return False


def get_evidence_service() -> EvidenceBundleService:
    """Return the canonical evidence bundle service."""
    return create_evidence_bundle_service()


def _merge_non_empty_strings(*groups: list[Any] | tuple[Any, ...]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for group in groups:
        for value in group:
            token = str(value or "").strip()
            if not token or token in seen:
                continue
            seen.add(token)
            result.append(token)
    return result
