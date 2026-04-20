"""Bridge from llm/toolkit/audit.py session logs to KernelAuditRuntime.

Replays isolated LLM protocol audit sessions into the unified audit chain.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from polaris.kernelone.audit.contracts import KernelAuditEvent, KernelAuditEventType

logger = logging.getLogger(__name__)


class LLMuditBridge:
    """Bridges llm/toolkit/audit.py sessions into KernelAuditRuntime.

    Use this to unify LLM protocol audit sessions with the main audit chain.
    """

    def __init__(self, runtime: Any | None = None) -> None:
        self._runtime = runtime

    @property
    def runtime(self) -> Any:
        if self._runtime is None:
            from polaris.kernelone.audit.runtime import KernelAuditRuntime

            self._runtime = KernelAuditRuntime.get_instance(Path.cwd())
        return self._runtime

    def replay_session(self, session_path: Path) -> int:
        """Replay one session file into the audit chain.

        Returns:
            Number of events successfully bridged.
        """
        if not session_path.exists():
            logger.debug("Session file not found (skipping): %s", session_path)
            return 0

        try:
            raw = session_path.read_text(encoding="utf-8")
        except (RuntimeError, ValueError) as exc:
            logger.warning("Failed to read session file %s: %s", session_path, exc)
            return 0

        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            logger.warning("Invalid JSON in session file %s: %s", session_path, exc)
            return 0

        session_id = str(data.get("session_id", session_path.stem))
        workspace = str(data.get("workspace", str(Path.cwd())))
        events = data.get("events", [])

        bridged = 0
        for event_data in events:
            try:
                ev = self._bridge_event(event_data, session_id, workspace)
                self.runtime.emit_event(
                    event_type=ev.event_type,
                    role="llm_audit_bridge",
                    workspace=workspace,
                    task_id="",
                    run_id=session_id,
                    resource=ev.resource,
                    action=ev.action,
                    data={**ev.data, "source_event_id": ev.event_id},
                    context={**ev.context, "bridge_session": session_id},
                )
                bridged += 1
            except (RuntimeError, ValueError) as exc:
                logger.debug("Failed to bridge event in %s: %s", session_path, exc)
                continue

        return bridged

    def _bridge_event(
        self,
        event_data: dict[str, Any],
        session_id: str,
        workspace: str,
    ) -> KernelAuditEvent:
        """Convert llm/toolkit/audit.py event to KernelAuditEvent."""
        from polaris.kernelone.audit.contracts import KernelAuditEvent, KernelAuditEventType

        event_type_raw = str(event_data.get("event_type", ""))
        type_map: dict[str, KernelAuditEventType] = {
            "operation_start": KernelAuditEventType.TOOL_EXECUTION,
            "operation_complete": KernelAuditEventType.TOOL_EXECUTION,
            "operation_error": KernelAuditEventType.TASK_FAILED,
            "parse_error": KernelAuditEventType.TASK_FAILED,
            "validation_start": KernelAuditEventType.VERIFICATION,
            "validation_complete": KernelAuditEventType.VERIFICATION,
            "apply_start": KernelAuditEventType.FILE_CHANGE,
            "apply_complete": KernelAuditEventType.FILE_CHANGE,
            "parse_start": KernelAuditEventType.LLM_CALL,
            "parse_complete": KernelAuditEventType.LLM_CALL,
            "rollback": KernelAuditEventType.TASK_FAILED,
        }
        kernel_type = type_map.get(event_type_raw, KernelAuditEventType.LLM_CALL)

        timestamp_raw = event_data.get("timestamp", "")
        try:
            if isinstance(timestamp_raw, str) and timestamp_raw.strip():
                ts = datetime.fromisoformat(timestamp_raw.replace("Z", "+00:00"))
            else:
                ts = datetime.now(timezone.utc)
        except (RuntimeError, ValueError):
            ts = datetime.now(timezone.utc)

        success = bool(event_data.get("success", True))
        result_str = "success" if success else "failure"
        error_msg = str(event_data.get("error_message", ""))

        return KernelAuditEvent(
            event_id=f"llm-bridge-{session_id[:8]}-{event_data.get('tool_name', 'unk')[:8]}",
            timestamp=ts,
            event_type=kernel_type,
            version="2.0",
            source={
                "role": "llm_audit_bridge",
                "workspace": workspace,
                "original_session": session_id,
            },
            task={},
            resource={
                "type": "llm_protocol_session",
                "session_id": session_id,
                "tool_name": str(event_data.get("tool_name", "")),
            },
            action={
                "name": str(event_data.get("operation", event_type_raw)),
                "result": result_str,
                "error": error_msg if not success else "",
            },
            data={
                "duration_ms": float(event_data.get("duration_ms", 0)),
                "metadata": dict(event_data.get("metadata", {})),
                "bridged_from": "llm/toolkit/audit.py",
            },
            context={"bridge_session": session_id},
            prev_hash="",
            signature="",
        )

    def replay_directory(self, audit_dir: Path, pattern: str = "*.json") -> dict[str, int]:
        """Replay all session files in a directory.

        Returns:
            Mapping of session file name -> bridged event count.
        """
        if not audit_dir.exists():
            logger.warning("Audit directory does not exist: %s", audit_dir)
            return {}

        results: dict[str, int] = {}
        for path in sorted(audit_dir.glob(pattern)):
            try:
                count = self.replay_session(path)
                results[path.name] = count
            except (RuntimeError, ValueError) as exc:
                logger.warning("Failed to replay session %s: %s", path.name, exc)
                results[path.name] = 0
        return results
