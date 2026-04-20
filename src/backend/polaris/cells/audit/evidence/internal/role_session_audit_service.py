"""Role session audit service built on KernelOne file and audit runtime."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from polaris.infrastructure.storage import LocalFileSystemAdapter
from polaris.kernelone.audit import (
    KernelAuditEventType,
    KernelAuditRuntime,
)
from polaris.kernelone.audit.validators import SYSTEM_ROLE
from polaris.kernelone.fs import KernelFileSystem
from polaris.kernelone.storage import resolve_storage_roots


class RoleSessionAuditService:
    """Write and query role-session audit events."""

    EVENT_TYPES = {
        "session_created",
        "session_resumed",
        "message_sent",
        "message_received",
        "artifact_created",
        "artifact_deleted",
        "artifact_exported",
        "workflow_exported",
        "error_occurred",
        "session_closed",
    }

    def __init__(self, workspace: Path) -> None:
        self.workspace = Path(workspace).resolve()
        roots = resolve_storage_roots(str(self.workspace))
        self._runtime = KernelAuditRuntime.get_instance(Path(roots.runtime_root))
        self._fs = KernelFileSystem(str(self.workspace), LocalFileSystemAdapter())

    def _get_audit_logical_path(self, session_id: str) -> str:
        session_token = str(session_id or "").strip()
        if not session_token:
            raise ValueError("session_id is required")
        return f"workspace/role_sessions/{session_token}/audit/events.jsonl"

    def append_audit_event(
        self,
        session_id: str,
        event_type: str,
        details: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        event_name = str(event_type or "").strip()
        payload = {
            "id": uuid.uuid4().hex,
            "type": event_name,
            "details": dict(details or {}),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        logical_path = self._get_audit_logical_path(session_id)
        self._fs.append_jsonl(logical_path, payload)
        self._runtime.emit_event(
            event_type=KernelAuditEventType.POLICY_CHECK,
            role=SYSTEM_ROLE,
            workspace=str(self.workspace),
            task_id=f"role-session-{session_id}",
            action={"name": event_name, "result": "success"},
            data={"session_id": session_id, "details": payload["details"]},
            context={"origin": "role_session"},
        )
        return payload

    def get_events(
        self,
        session_id: str,
        event_type: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        logical_path = self._get_audit_logical_path(session_id)
        if not self._fs.exists(logical_path):
            return []
        raw = self._fs.read_text(logical_path, encoding="utf-8")
        rows: list[dict[str, Any]] = []
        start = max(0, int(offset or 0))
        max_rows = max(1, int(limit or 100))
        for line in raw.splitlines():
            text = line.strip()
            if not text:
                continue
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError:
                continue
            if not isinstance(parsed, dict):
                continue
            if event_type and str(parsed.get("type") or "") != str(event_type):
                continue
            rows.append(parsed)
        return rows[start : start + max_rows]

    def get_event_count(self, session_id: str, event_type: str | None = None) -> int:
        """Get the count of events for a session without loading all events into memory.

        This method uses streaming line counting to avoid O(n) memory usage.
        For large event logs, this is significantly more efficient than get_events().

        Args:
            session_id: The session ID to count events for
            event_type: Optional event type filter

        Returns:
            The number of matching events
        """
        logical_path = self._get_audit_logical_path(session_id)
        if not self._fs.exists(logical_path):
            return 0

        # Get the physical path and read directly for streaming
        physical_path = self._fs.resolve_path(logical_path)
        count = 0

        try:
            with open(physical_path, encoding="utf-8") as f:
                for line in f:
                    text = line.strip()
                    if not text:
                        continue
                    try:
                        parsed = json.loads(text)
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(parsed, dict):
                        continue
                    if event_type and str(parsed.get("type") or "") != str(event_type):
                        continue
                    count += 1
        except OSError:
            return 0

        return count

    def export_audit_log(self, session_id: str, target_file: Path) -> Path:
        events = self.get_events(session_id, limit=100000, offset=0)
        payload = {
            "session_id": session_id,
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "event_count": len(events),
            "events": events,
        }
        receipt = self._fs.write_json(str(Path(target_file).resolve()), payload, indent=2, ensure_ascii=False)
        return Path(receipt.absolute_path)
