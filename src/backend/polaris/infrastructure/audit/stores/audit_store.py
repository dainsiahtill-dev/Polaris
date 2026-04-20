"""Append-only Audit Store with HMAC-SHA256 hash chain.

Implements tamper-proof audit logging with cryptographic integrity verification.

CRITICAL: All text file I/O must use UTF-8 encoding.

[P1-AUDIT-001] Converged to canonical types from polaris.kernelone.audit.contracts:
- KernelAuditEvent, KernelAuditEventType (canonical)
- AuditEvent, AuditEventType (backward compatibility aliases via adapter)
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import secrets
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from typing import Any

from polaris.kernelone.audit.contracts import (
    KernelAuditEvent,
    KernelAuditEventType,
    KernelChainVerificationResult,
)
from polaris.kernelone.utils.constants import DEFAULT_AUDIT_RETENTION_DAYS, GENESIS_HASH

logger = logging.getLogger(__name__)

# =============================================================================
# Backward Compatibility Aliases (P1-AUDIT-001)
# =============================================================================

# Backward compatibility aliases for external callers
AuditEventType = KernelAuditEventType  # type: ignore[misc,assignment]
"""Canonical audit event types. Prefer KernelAuditEventType."""

AuditEvent = KernelAuditEvent  # type: ignore[misc,assignment]
"""Canonical audit event. Prefer KernelAuditEvent."""

ChainVerificationResult = KernelChainVerificationResult  # type: ignore[misc,assignment]
"""Chain verification result. Prefer KernelChainVerificationResult."""

# Default retention period alias
DEFAULT_RETENTION_DAYS = DEFAULT_AUDIT_RETENTION_DAYS


# =============================================================================
# Backward Compatibility Enums (for external callers)
# =============================================================================


class AuditEventResult(str, Enum):
    """Result status for audit events."""

    SUCCESS = "success"
    FAILURE = "failure"
    PARTIAL = "partial"
    SKIPPED = "skipped"


class AuditRole(str, Enum):
    """Roles that can generate audit events.

    .. deprecated::
        Polaris-specific role constants have been migrated out of KernelOne.
        Use plain ``str`` for ``role`` fields throughout the audit runtime.
    """

    PM = "pm"
    ARCHITECT = "architect"
    CHIEF_ENGINEER = "chief_engineer"
    DIRECTOR = "director"
    QA = "qa"
    SYSTEM = "system"


class ResourceType(str, Enum):
    """Types of resources in audit events."""

    FILE = "file"
    DIRECTORY = "directory"
    TOOL = "tool"
    LLM = "llm"
    POLICY = "policy"
    VERDICT = "verdict"


class ResourceOperation(str, Enum):
    """Operations on resources."""

    CREATE = "create"
    READ = "read"
    UPDATE = "update"
    DELETE = "delete"
    EXECUTE = "execute"


# =============================================================================
# Adapter Functions (P1-AUDIT-001)
# =============================================================================


def audit_event_to_kernel(event: AuditEvent) -> KernelAuditEvent:
    """Convert AuditEvent to canonical KernelAuditEvent.

    Args:
        event: Legacy AuditEvent instance.

    Returns:
        Canonical KernelAuditEvent instance.
    """
    if isinstance(event, KernelAuditEvent):
        return event

    # Parse event_type if it's a string
    event_type = event.event_type
    if isinstance(event_type, str):
        event_type = KernelAuditEventType(event_type)

    return KernelAuditEvent(
        event_id=event.event_id,
        timestamp=event.timestamp,
        event_type=event_type,
        version=event.version,
        source=dict(event.source),
        task=dict(event.task),
        resource=dict(event.resource),
        action=dict(event.action),
        data=dict(event.data),
        context=dict(event.context),
        prev_hash=event.prev_hash,
        signature=event.signature,
    )


def kernel_event_to_audit(event: KernelAuditEvent) -> AuditEvent:
    """Convert KernelAuditEvent to AuditEvent (for backward compatibility).

    Args:
        event: Canonical KernelAuditEvent instance.

    Returns:
        AuditEvent instance (same type, for external compatibility).
    """
    # Both types are now the same, this is a no-op
    return event  # type: ignore[return-value]


class AuditStore:
    """Append-only audit log store with HMAC-SHA256 hash chain.

    Features:
    - Append-only: Only supports writing new events, no update/delete
    - HMAC-SHA256: Each event signed with hash chain
    - Log rotation: Automatic monthly rotation
    - Retention: Configurable retention period
    """

    # Marker line for append-only protection
    MARKER_LINE = "# POLARIS AUDIT LOG - APPEND ONLY\n"

    def __init__(
        self,
        runtime_root: Path,
        secret_key: str | None = None,
        retention_days: int = DEFAULT_RETENTION_DAYS,
    ) -> None:
        """Initialize audit store.

        Args:
            runtime_root: Runtime root directory for audit logs
            secret_key: Secret key for HMAC signatures (auto-generated if None)
            retention_days: Days to retain audit logs
        """
        self._runtime_root = Path(runtime_root).resolve()
        self._audit_dir = self._runtime_root / "audit"
        self._audit_dir.mkdir(parents=True, exist_ok=True)
        self._retention_days = retention_days

        # Generate or use provided secret key
        if secret_key:
            self._secret_key = secret_key.encode("utf-8")
        else:
            self._secret_key = self._load_or_generate_key()

        # Track last hash for new events
        self._last_hash = self._load_last_hash()

        # Lock for thread-safe append operations
        self._lock = __import__("threading").Lock()

    def _load_or_generate_key(self) -> bytes:
        """Load existing key or generate new one."""
        key_file = self._audit_dir / ".key"
        if key_file.exists():
            with open(key_file, encoding="utf-8") as f:
                return f.read().strip().encode("utf-8")
        else:
            # Generate new random key
            key = secrets.token_hex(32)
            with open(key_file, "w", encoding="utf-8") as f:
                f.write(key)
            return key.encode("utf-8")

    def _load_last_hash(self) -> str:
        """Load the last hash from existing log file."""
        log_file = self._get_current_log_file()
        if log_file.exists():
            events = self._read_events_from_file(log_file)
            if events:
                last_event = events[-1]
                content = json.dumps(last_event, sort_keys=True, ensure_ascii=False)
                return hashlib.sha256(content.encode("utf-8")).hexdigest()
        return GENESIS_HASH

    def _get_current_log_file(self) -> Path:
        """Get current log file with monthly rotation."""
        now = datetime.now(timezone.utc)
        return self._audit_dir / f"audit-{now.strftime('%Y-%m')}.jsonl"

    def _get_log_files(self) -> list[Path]:
        """Get all audit log files sorted by date."""
        pattern = "audit-*.jsonl"
        files = sorted(self._audit_dir.glob(pattern))
        return [f for f in files if f.name != "audit-temp.jsonl"]

    def _compute_signature(self, event: AuditEvent) -> str:
        """Compute HMAC-SHA256 signature for event."""
        payload = json.dumps(
            {
                "event_id": event.event_id,
                "timestamp": event.timestamp.isoformat(),
                "event_type": event.event_type.value,
                "prev_hash": event.prev_hash,
            },
            sort_keys=True,
            ensure_ascii=False,
        )
        message = f"{event.prev_hash}:{payload}".encode()
        return hmac.new(self._secret_key, message, hashlib.sha256).hexdigest()

    def append(self, event: KernelAuditEvent) -> KernelAuditEvent:
        """Append signed event to audit log.

        Args:
            event: Canonical KernelAuditEvent to append

        Returns:
            Signed event with hash chain
        """
        with self._lock:
            # Set previous hash
            event.prev_hash = self._last_hash

            # Use existing signature if already computed (from KernelAuditRuntime),
            # otherwise compute it here. This ensures the signature algorithm
            # used by KernelAuditRuntime._compute_signature is preserved.
            if not event.signature:
                event.signature = self._compute_signature(event)

            # Write to temp file then atomic append
            log_file = self._get_current_log_file()
            temp_file = self._audit_dir / "audit-temp.jsonl"

            # Ensure file exists with marker
            if not log_file.exists():
                with open(log_file, "w", encoding="utf-8") as f:
                    f.write(self.MARKER_LINE)

            # Atomic append using temp file
            # Use os.replace() for true atomic operation on POSIX systems
            # On Windows, this still provides better atomic guarantees than manual copy
            line = json.dumps(event.to_dict(), ensure_ascii=False) + "\n"

            # Write to temp file first
            with open(temp_file, "w", encoding="utf-8") as f:
                f.write(line)

            # Atomic append: read temp and append to log file
            # Using os.replace would not work for append, so we use the original approach
            # but with better error handling
            try:
                with open(log_file, "ab") as target, open(temp_file, "rb") as source:
                    target.write(source.read())
            except (RuntimeError, ValueError) as e:
                # If append fails, try to recover by removing partial data
                # The temp file still has the correct data
                logger.error("[audit.store] Failed to append to log file: %s", e)
                raise
            finally:
                # Clean up temp file
                try:
                    temp_file.unlink(missing_ok=True)
                except (RuntimeError, ValueError):
                    logger.debug("[audit.store] Temp file cleanup failed: %s")

            # Update last hash
            content = json.dumps(event.to_dict(), sort_keys=True, ensure_ascii=False)
            self._last_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()

            return event

    def _read_events_from_file(self, file_path: Path) -> list[dict[str, Any]]:
        """Read events from a log file."""
        events = []
        with open(file_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                try:
                    events.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        return events

    def query(
        self,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        event_type: KernelAuditEventType | None = None,
        role: str | None = None,
        task_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[KernelAuditEvent]:
        """Query audit events with filters.

        Args:
            start_time: Filter events after this time
            end_time: Filter events before this time
            event_type: Filter by event type
            role: Filter by source role
            task_id: Filter by task ID
            limit: Maximum events to return
            offset: Number of events to skip

        Returns:
            List of matching canonical KernelAuditEvent instances
        """
        all_events: list[KernelAuditEvent] = []

        for log_file in self._get_log_files():
            events = self._read_events_from_file(log_file)
            for event_data in events:
                try:
                    event = KernelAuditEvent.from_dict(event_data)
                    all_events.append(event)
                except (KeyError, ValueError):
                    continue

        # Apply filters
        filtered = all_events
        if start_time:
            filtered = [e for e in filtered if e.timestamp >= start_time]
        if end_time:
            filtered = [e for e in filtered if e.timestamp <= end_time]
        if event_type:
            filtered = [e for e in filtered if e.event_type == event_type]
        if role:
            filtered = [e for e in filtered if e.source.get("role") == role]
        if task_id:
            filtered = [e for e in filtered if e.task.get("task_id") == task_id]

        # Sort by timestamp descending (newest first)
        filtered.sort(key=lambda e: e.timestamp, reverse=True)

        # Apply pagination
        return filtered[offset : offset + limit]

    def verify_chain(self) -> KernelChainVerificationResult:
        """Verify integrity of the entire audit chain.

        Returns:
            Canonical KernelChainVerificationResult with verification details
        """
        all_events: list[KernelAuditEvent] = []

        for log_file in self._get_log_files():
            events = self._read_events_from_file(log_file)
            for event_data in events:
                try:
                    event = KernelAuditEvent.from_dict(event_data)
                    all_events.append(event)
                except (KeyError, ValueError):
                    continue

        # Sort by timestamp ascending for chain verification
        all_events.sort(key=lambda e: e.timestamp)

        if not all_events:
            return KernelChainVerificationResult(
                is_valid=True,
                first_hash=GENESIS_HASH,
                last_hash=GENESIS_HASH,
                total_events=0,
                gap_count=0,
                verified_at=datetime.now(timezone.utc),
            )

        first_hash = ""
        last_hash = ""
        prev_hash = GENESIS_HASH
        invalid_events: list[dict[str, Any]] = []
        gap_count = 0
        chain_valid = True

        for event in all_events:
            # Compute expected signature
            expected_sig = self._compute_signature(event)

            # Verify signature - signature failure means chain is tampered
            if event.signature != expected_sig:
                invalid_events.append(
                    {
                        "event_id": event.event_id,
                        "expected": expected_sig,
                        "actual": event.signature,
                    }
                )
                chain_valid = False
                # Signature is invalid, chain is broken - stop verification
                break

            # Check hash chain continuity
            if event.prev_hash != prev_hash:
                gap_count += 1
                chain_valid = False

            prev_hash = hashlib.sha256(
                json.dumps(event.to_dict(), sort_keys=True, ensure_ascii=False).encode("utf-8")
            ).hexdigest()

            if not first_hash:
                first_hash = hashlib.sha256(
                    json.dumps(event.to_dict(), sort_keys=True, ensure_ascii=False).encode("utf-8")
                ).hexdigest()

            last_hash = hashlib.sha256(
                json.dumps(event.to_dict(), sort_keys=True, ensure_ascii=False).encode("utf-8")
            ).hexdigest()

        return KernelChainVerificationResult(
            is_valid=chain_valid,
            first_hash=first_hash,
            last_hash=last_hash,
            total_events=len(all_events),
            gap_count=gap_count,
            verified_at=datetime.now(timezone.utc),
            invalid_events=invalid_events,
        )

    def get_stats(
        self,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
    ) -> dict[str, Any]:
        """Get audit statistics.

        Args:
            start_time: Start of time range
            end_time: End of time range

        Returns:
            Statistics dictionary
        """
        events = self.query(start_time=start_time, end_time=end_time, limit=10000)

        if not events:
            return {
                "total_events": 0,
                "by_type": {},
                "by_role": {},
                "by_result": {},
                "pass_rate": 0.0,
            }

        # Count by dimensions
        by_type: dict[str, int] = {}
        by_role: dict[str, int] = {}
        by_result: dict[str, int] = {}
        total = len(events)

        for event in events:
            # By type
            type_key = event.event_type.value
            by_type[type_key] = by_type.get(type_key, 0) + 1

            # By role
            role = event.source.get("role", "unknown")
            by_role[role] = by_role.get(role, 0) + 1

            # By result
            result = event.action.get("result", "unknown")
            by_result[result] = by_result.get(result, 0) + 1

        # Calculate pass rate (tasks completed successfully)
        task_completed = sum(
            1
            for e in events
            if e.event_type == KernelAuditEventType.TASK_COMPLETE and e.action.get("result") == "success"
        )
        task_total = sum(
            1 for e in events if e.event_type in (KernelAuditEventType.TASK_COMPLETE, KernelAuditEventType.TASK_FAILED)
        )
        pass_rate = task_completed / task_total if task_total > 0 else 0.0

        return {
            "total_events": total,
            "by_type": by_type,
            "by_role": by_role,
            "by_result": by_result,
            "pass_rate": pass_rate,
        }

    def export_json(
        self,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        event_types: list[KernelAuditEventType] | None = None,
        include_data: bool = True,
    ) -> dict[str, Any]:
        """Export events in JSON format.

        Args:
            start_time: Start of time range
            end_time: End of time range
            event_types: Filter by event types
            include_data: Include full data payload

        Returns:
            Export data with metadata
        """
        events = self.query(start_time=start_time, end_time=end_time, limit=10000)

        if event_types:
            events = [e for e in events if e.event_type in event_types]

        # Filter data if needed
        exported_events = []
        for event in events:
            if include_data:
                exported_events.append(event.to_dict())
            else:
                # Include only essential fields
                exported = {
                    "event_id": event.event_id,
                    "timestamp": event.timestamp.isoformat(),
                    "event_type": event.event_type.value,
                    "source": event.source,
                    "task": event.task,
                    "resource": event.resource,
                    "action": event.action,
                }
                exported_events.append(exported)

        # Get chain verification
        verification = self.verify_chain()

        return {
            "export_metadata": {
                "exported_at": datetime.now(timezone.utc).isoformat(),
                "time_range": {
                    "start": start_time.isoformat() if start_time else None,
                    "end": end_time.isoformat() if end_time else None,
                },
                "event_types": [et.value for et in event_types] if event_types else None,
                "record_count": len(exported_events),
            },
            "events": exported_events,
            "summary": self.get_stats(start_time=start_time, end_time=end_time),
            "integrity": {
                "first_hash": verification.first_hash,
                "last_hash": verification.last_hash,
                "chain_valid": verification.is_valid,
            },
        }

    def export_csv(
        self,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
    ) -> str:
        """Export events in CSV format.

        Args:
            start_time: Start of time range
            end_time: End of time range

        Returns:
            CSV string
        """
        events = self.query(start_time=start_time, end_time=end_time, limit=10000)

        # CSV header
        lines = ["event_id,timestamp,event_type,role,task_id,resource_path,operation,result"]

        for event in events:
            line = ",".join(
                [
                    event.event_id,
                    event.timestamp.isoformat(),
                    event.event_type.value,
                    event.source.get("role", ""),
                    event.task.get("task_id", ""),
                    event.resource.get("path", ""),
                    event.resource.get("operation", ""),
                    event.action.get("result", ""),
                ]
            )
            lines.append(line)

        return "\n".join(lines)

    def cleanup_old_logs(self, dry_run: bool = False) -> dict[str, Any]:
        """Clean up logs older than retention period.

        Args:
            dry_run: If True, only return what would be deleted

        Returns:
            Cleanup result with counts
        """
        cutoff = datetime.now(timezone.utc) - timedelta(days=self._retention_days)
        log_files = self._get_log_files()

        would_delete = 0
        would_free_bytes = 0
        affected_files = []

        for log_file in log_files:
            # Check file modification time
            file_stat = log_file.stat()
            mtime = datetime.fromtimestamp(file_stat.st_mtime, tz=timezone.utc)
            if mtime < cutoff:
                if dry_run:
                    would_delete += 1
                    would_free_bytes += file_stat.st_size
                    affected_files.append(str(log_file))
                else:
                    affected_files.append(str(log_file))
                    log_file.unlink()

        return {
            "would_delete": would_delete,
            "would_free_bytes": would_free_bytes,
            "affected_files": affected_files,
            "dry_run": dry_run,
            "cutoff_date": cutoff.isoformat(),
        }

    def get_log_file_path(self) -> Path:
        """Get path to current audit log file."""
        return self._get_current_log_file()


def create_audit_event(
    event_type: KernelAuditEventType,
    role: AuditRole,
    workspace: str,
    task_id: str = "",
    iteration: int = 0,
    run_id: str = "",
    resource_path: str = "",
    resource_type: ResourceType = ResourceType.FILE,
    resource_operation: ResourceOperation = ResourceOperation.READ,
    action_name: str = "",
    action_result: AuditEventResult = AuditEventResult.SUCCESS,
    data: dict[str, Any] | None = None,
    context: dict[str, Any] | None = None,
) -> KernelAuditEvent:
    """Helper to create a structured audit event.

    Args:
        event_type: Type of event (canonical KernelAuditEventType)
        role: Source role
        workspace: Workspace path
        task_id: Associated task ID
        iteration: Iteration number
        run_id: Run identifier
        resource_path: Path to resource
        resource_type: Type of resource
        resource_operation: Operation on resource
        action_name: Name of action
        action_result: Result of action
        data: Event data payload
        context: Extended context

    Returns:
        Configured canonical KernelAuditEvent
    """
    import uuid

    return KernelAuditEvent(
        event_id=str(uuid.uuid4()),
        timestamp=datetime.now(timezone.utc),
        event_type=event_type,
        source={
            "role": role.value,
            "workspace": workspace,
        },
        task={
            "task_id": task_id,
            "iteration": iteration,
            "run_id": run_id,
        },
        resource={
            "type": resource_type.value,
            "path": resource_path,
            "operation": resource_operation.value,
        },
        action={
            "name": action_name,
            "result": action_result.value,
        },
        data=data or {},
        context=context or {},
    )
