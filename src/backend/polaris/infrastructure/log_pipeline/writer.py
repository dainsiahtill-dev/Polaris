"""LogEventWriter - Unified Event Writer.

This module provides the single writer interface for all log events.
All code should use this writer instead of directly appending to JSONL files.

Key features:
- Per-run sequence numbers (monotonic, no seq=0 issues)
- Automatic channel mapping from legacy channels
- Three-layer persistence (raw, norm, enriched)
- Fingerprint-based deduplication
- Thread-safe operation
- JetStream publishing for cross-process event streaming
"""

from __future__ import annotations

import json
import logging
import os
import threading
import uuid
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

from polaris.infrastructure.realtime.process_local.log_fanout import LOG_REALTIME_FANOUT
from polaris.kernelone.fs.fsync_mode import is_fsync_enabled
from polaris.kernelone.storage import resolve_storage_roots
from polaris.kernelone.utils.time_utils import utc_now_str

from .canonical_event import (
    CanonicalLogEventV2,
    LogChannel,
    LogDomain,
    LogKind,
    LogSeverity,
)

logger = logging.getLogger(__name__)

# JetStream imports (truly lazy to avoid cold-starting NATS/aiohttp on local-only
# journal paths).
_jetstream_available = False
_jetstream_import_attempted = False
_jetstream_publisher_factory: Callable[[], Any] | None = None
_jetstream_constants: Any = None


def _ensure_jetstream_support() -> bool:
    global _jetstream_available
    global _jetstream_import_attempted
    global _jetstream_publisher_factory
    global _jetstream_constants

    if _jetstream_import_attempted:
        return _jetstream_available

    _jetstream_import_attempted = True
    try:
        from polaris.infrastructure.log_pipeline.jetstream_publisher import (
            get_log_jetstream_publisher,
        )
        from polaris.infrastructure.messaging.nats.nats_types import JetStreamConstants
    except ImportError:
        logger.debug("JetStream not available, using fallback mode")
        return False

    _jetstream_publisher_factory = get_log_jetstream_publisher
    _jetstream_constants = JetStreamConstants
    _jetstream_available = True
    return True


# =============================================================================
# Configuration
# =============================================================================

# Publish retry configuration
PUBLISH_ENABLED = (
    os.environ.get("KERNELONE_JETSTREAM_PUBLISH") or os.environ.get("KERNELONE_JETSTREAM_PUBLISH", "0")
) not in ("0", "false", "no")


# =============================================================================
# Channel to JetStream Subject Mapping
# =============================================================================


def _channel_to_subject(channel: str, workspace_key: str = "") -> str:
    """Map canonical channel to JetStream subject.

    Args:
        channel: Canonical channel name (system, process, llm)
        workspace_key: Workspace identifier

    Returns:
        JetStream subject path
    """
    base = _jetstream_constants.SUBJECT_PREFIX if _jetstream_constants is not None else "hp.runtime"

    # Map canonical channels to subjects
    channel_map = {
        "system": "system",
        "process": "process",
        "llm": "llm",
        "user": "user",
    }

    subject_channel = channel_map.get(channel, "system")

    if workspace_key:
        return f"{base}.{workspace_key}.{subject_channel}"
    return f"{base}.{subject_channel}"


# Sequence locks per run_id
_seq_locks: dict[str, threading.Lock] = {}
_seq_counters: dict[str, int] = {}
_global_lock = threading.Lock()


def _report_publish_failure(event: dict[str, Any]) -> None:
    """Report publish failure for metrics collection."""
    # This would integrate with metrics system in production
    logger.error(
        f"P0 METRIC: jetstream_publish_failure - "
        f"event_id={event.get('event_id', 'unknown')}, "
        f"channel={event.get('channel', 'unknown')}"
    )


def get_log_jetstream_publisher() -> Any:
    """Return the shared JetStream publisher when support is available."""
    if _jetstream_publisher_factory is None and not _ensure_jetstream_support():
        raise RuntimeError("JetStream publisher unavailable")
    if _jetstream_publisher_factory is None:
        raise RuntimeError("JetStream publisher factory unavailable")
    return _jetstream_publisher_factory()


def _fsync_enabled() -> bool:
    """Check if fsync is enabled via shared fsync-mode helper."""
    return is_fsync_enabled()


def _get_run_lock(run_id: str) -> threading.Lock:
    """Get or create a lock for the given run_id."""
    with _global_lock:
        if run_id not in _seq_locks:
            _seq_locks[run_id] = threading.Lock()
            _seq_counters[run_id] = 0
        return _seq_locks[run_id]


def _next_seq_for_run(run_id: str) -> int:
    """Get next sequence number for a run (thread-safe)."""
    lock = _get_run_lock(run_id)
    with lock:
        _seq_counters[run_id] += 1
        return _seq_counters[run_id]


class LogEventWriter:
    """Unified writer for all log events.

    This is the single interface for writing events to the log pipeline.
    All code should use this instead of direct JSONL appends.

    Usage:
        writer = LogEventWriter(workspace=".", run_id="run-123")
        writer.write_event(channel="system", message="Task started", actor="PM")
    """

    def __init__(
        self,
        workspace: str,
        run_id: str = "",
        runtime_root: str | None = None,
    ) -> None:
        """Initialize the writer.

        Args:
            workspace: Workspace directory path
            run_id: Run identifier (required for sequence numbers)
            runtime_root: Optional runtime root (defaults to unified storage layout)
        """
        self.workspace = os.path.abspath(workspace)
        self.run_id = run_id or ""
        self.workspace_key = "unknown"

        # Resolve runtime root
        roots = None
        if runtime_root:
            self.runtime_root = os.path.abspath(runtime_root)
        else:
            roots = resolve_storage_roots(self.workspace)
            self.runtime_root = os.path.abspath(roots.runtime_root)
        if roots is None:
            try:
                roots = resolve_storage_roots(self.workspace)
            except (RuntimeError, ValueError):
                roots = None
        if roots is not None:
            self.workspace_key = str(roots.workspace_key or "").strip() or "unknown"

        # Ensure runtime directory exists
        os.makedirs(self.runtime_root, exist_ok=True)

        # Get or create run directory
        if self.run_id:
            self.run_dir = os.path.join(self.runtime_root, "runs", self.run_id, "logs")
        else:
            self.run_dir = os.path.join(self.runtime_root, "logs")

        os.makedirs(self.run_dir, exist_ok=True)

        # File paths
        self.raw_path = os.path.join(self.run_dir, "journal.raw.jsonl")
        self.norm_path = os.path.join(self.run_dir, "journal.norm.jsonl")
        self.enriched_path = os.path.join(self.run_dir, "journal.enriched.jsonl")

        # Initialize sequence counter from existing files
        self._init_seq_from_files()

    def _init_seq_from_files(self) -> None:
        """Initialize sequence counter from existing files."""
        if not self.run_id:
            return

        # Find max seq in existing files
        max_seq = 0
        for path in [self.raw_path, self.norm_path]:
            if os.path.exists(path):
                try:
                    with open(path, encoding="utf-8") as f:
                        for line in f:
                            line = line.strip()
                            if not line:
                                continue
                            try:
                                data = json.loads(line)
                                seq = data.get("seq", 0)
                                max_seq = max(max_seq, seq)
                            except json.JSONDecodeError:
                                continue
                except OSError as exc:
                    logger.debug("seq file read failed (non-critical): path=%s: %s", path, exc)

        # Set the counter
        lock = _get_run_lock(self.run_id)
        with lock:
            _seq_counters[self.run_id] = max_seq

    def _write_jsonl(self, path: str, event: CanonicalLogEventV2) -> None:
        """Write event to a JSONL file (thread-safe, append-only)."""
        # Ensure directory exists
        os.makedirs(os.path.dirname(path), exist_ok=True)

        # Serialize with UTF-8
        line = json.dumps(event.model_dump(), ensure_ascii=False) + "\n"

        # Phase B: Use append-only mode to prevent overwriting existing content
        # Lock + append + fsync pattern for durability
        lock = _get_run_lock(self.run_id or "default")
        with lock:
            try:
                with open(path, "a", encoding="utf-8") as f:
                    f.write(line)
                    f.flush()
                    if _fsync_enabled():
                        os.fsync(f.fileno())
            except (RuntimeError, ValueError) as e:
                # Log error but don't crash the process
                logger.warning("Failed to write event to %s: %s", path, e)

    def _publish_realtime_event(self, event: CanonicalLogEventV2) -> None:
        """Publish canonical event to in-process realtime fanout and JetStream."""
        try:
            # Step 1: In-process realtime fanout (best-effort)
            LOG_REALTIME_FANOUT.publish(
                runtime_root=self.runtime_root,
                event=event.model_dump(),
            )
        except (RuntimeError, ValueError) as exc:
            # Realtime push is best-effort and must not break writer durability.
            logger.debug("realtime fanout failed (best-effort): %s", exc)

        # Step 2: JetStream publishing (async, best-effort after disk write succeeds)
        if PUBLISH_ENABLED and (_jetstream_available or _ensure_jetstream_support()):
            self._publish_to_jetstream(event)

    def _publish_to_jetstream(self, event: CanonicalLogEventV2) -> None:
        """Publish event to JetStream with retry logic.

        This method publishes after the disk write succeeds, maintaining
        the "disk-first then publish" semantics. Publication retries are
        handled by the dedicated background JetStream publisher.
        """
        if not _jetstream_available and not _ensure_jetstream_support():
            return
        try:
            workspace_key = str(self.workspace_key or "").strip()
            if not workspace_key or workspace_key == "unknown":
                workspace_key = self._extract_workspace_key()

            # Map channel to subject
            subject = _channel_to_subject(event.channel, workspace_key)

            # Build RuntimeEventEnvelope
            envelope_dict = {
                "schema_version": "runtime.v2",
                "event_id": event.event_id,
                "workspace_key": workspace_key,
                "run_id": event.run_id or "",
                "channel": event.channel,
                "kind": f"{event.domain}.{event.kind}",
                "ts": event.ts,
                "cursor": event.seq,
                "trace_id": None,
                "payload": {
                    "message": event.message,
                    "actor": event.actor,
                    "severity": event.severity,
                    "domain": event.domain,
                    "kind": event.kind,
                    "refs": event.refs,
                    "tags": event.tags,
                    "raw": event.raw if event.raw else None,
                },
                "meta": {
                    "source": "log_pipeline_writer",
                    "writer_run_id": self.run_id,
                },
            }

            publisher = get_log_jetstream_publisher()
            accepted = publisher.publish(subject=subject, payload=envelope_dict)
            if not accepted:
                _report_publish_failure(envelope_dict)

        except (RuntimeError, ValueError) as e:
            logger.debug(f"JetStream publish preparation failed: {e}")
            # Don't crash - disk write already succeeded
            pass

    def _extract_workspace_key(self) -> str:
        """Extract workspace key from runtime_root path.

        Returns:
            Workspace identifier string
        """
        try:
            roots = resolve_storage_roots(self.workspace)
            workspace_key = str(roots.workspace_key or "").strip()
            if workspace_key:
                return workspace_key
        except (RuntimeError, ValueError):
            logger.debug("Failed to resolve workspace_key from storage roots", exc_info=True)

        runtime_match = os.path.normpath(self.runtime_root).replace("\\", "/")
        for marker in ("/.polaris/projects/", "/.polaris/projects/"):
            if marker in runtime_match:
                try:
                    suffix = runtime_match.split(marker, 1)[1]
                    candidate = suffix.split("/", 1)[0].strip()
                    if candidate:
                        return candidate
                except (RuntimeError, ValueError):
                    logger.debug("Failed to derive workspace_key from runtime_root", exc_info=True)

        return "unknown"

    def write_event(
        self,
        message: str,
        channel: LogChannel = "system",
        domain: LogDomain = "system",
        severity: LogSeverity = "info",
        kind: LogKind = "observation",
        actor: str = "System",
        source: str = "",
        run_id: str | None = None,
        refs: dict[str, Any] | None = None,
        tags: list[str] | None = None,
        raw: dict[str, Any] | None = None,
        input_data: dict[str, Any] | None = None,
        output_data: dict[str, Any] | None = None,
        error: str | None = None,
        duration_ms: int | None = None,
    ) -> CanonicalLogEventV2:
        """Write a log event to the pipeline.

        This is the main entry point for writing events.

        Args:
            message: Event message/content
            channel: Target channel (system|process|llm)
            domain: Domain (system|process|llm|user)
            severity: Severity level
            kind: Event kind
            actor: Actor name (PM, Director, QA, etc.)
            source: Source identifier
            run_id: Run identifier (defaults to self.run_id)
            refs: Reference data (task_id, file paths, etc.)
            tags: Event tags for filtering
            raw: Original raw data (for audit)
            input_data: Input data (for action events)
            output_data: Output data (for observation events)
            error: Error message (for error events)
            duration_ms: Duration in milliseconds (for action events)

        Returns:
            The written CanonicalLogEventV2 event
        """
        # Use provided run_id or fall back to self.run_id
        effective_run_id = run_id or self.run_id

        # Get sequence number
        seq = _next_seq_for_run(effective_run_id)

        # Create the event
        event = CanonicalLogEventV2(
            schema_version=2,
            event_id=str(uuid.uuid4()),
            run_id=effective_run_id,
            seq=seq,
            ts=utc_now_str(),
            ts_epoch=datetime.now(timezone.utc).timestamp(),
            channel=channel,
            domain=domain,
            severity=severity,
            kind=kind,
            actor=actor,
            source=source,
            message=message[:5000],  # Limit message length
            refs=refs or {},
            tags=tags or [],
            raw=raw,
            legacy_input=input_data,
            legacy_output=output_data,
        )

        # Compute fingerprint for deduplication
        event.fingerprint = event.compute_fingerprint()

        # Write to all three layers
        # Layer 1: Raw (immutable, audit source)
        self._write_jsonl(self.raw_path, event)

        # Layer 2: Normalized (unified schema)
        self._write_jsonl(self.norm_path, event)

        # Layer 3: Enriched (initially empty, populated by LLM worker)
        # Write with enrichment: null to indicate pending
        enriched_event = event.model_copy()
        enriched_event.enrichment = None  # Mark as pending
        self._write_jsonl(self.enriched_path, enriched_event)

        self._publish_realtime_event(event)

        return event

    def write_from_legacy(
        self,
        legacy_channel: str,
        raw_event: dict[str, Any],
        run_id: str | None = None,
    ) -> CanonicalLogEventV2:
        """Write an event from legacy format to the pipeline.

        This handles events from old emit_event, emit_llm_event, emit_dialogue
        functions and normalizes them to the canonical format.

        Args:
            legacy_channel: Old channel name (e.g., 'pm_log', 'runtime_events')
            raw_event: Raw event dict
            run_id: Run identifier

        Returns:
            The written CanonicalLogEventV2 event
        """
        from .canonical_event import normalize_legacy_event

        # Normalize to canonical format
        effective_run_id = run_id or self.run_id
        canonical = normalize_legacy_event(raw_event, legacy_channel, effective_run_id)

        # Get sequence number
        canonical.seq = _next_seq_for_run(effective_run_id)
        canonical.run_id = effective_run_id

        # Compute fingerprint
        canonical.fingerprint = canonical.compute_fingerprint()

        # Write to all three layers
        self._write_jsonl(self.raw_path, canonical)
        self._write_jsonl(self.norm_path, canonical)

        # Enriched layer
        enriched = canonical.model_copy()
        enriched.enrichment = None
        self._write_jsonl(self.enriched_path, enriched)

        self._publish_realtime_event(canonical)

        return canonical


# Factory function for getting a writer
def get_writer(workspace: str, run_id: str = "") -> LogEventWriter:
    """Get a LogEventWriter instance.

    Args:
        workspace: Workspace directory
        run_id: Run identifier

    Returns:
        LogEventWriter instance
    """
    return LogEventWriter(workspace=workspace, run_id=run_id)


# =============================================================================
# Module Initialization
# =============================================================================


def _init_module() -> None:
    """Module init is intentionally a no-op.

    JetStream publishing is started lazily on first publish attempt so imports
    that only need local journaling do not cold-start the NATS stack.
    """
    return None


# Auto-initialize on module load
try:
    _init_module()
except (RuntimeError, ValueError) as exc:
    logger.debug("Module-level JetStream init failed (best-effort): %s", exc)
