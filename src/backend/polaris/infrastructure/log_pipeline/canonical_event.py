"""Canonical Log Event V2 - Unified Event Schema.

This module defines the single source of truth for all log events in Polaris.
All log sources (subprocess, emit_event, emit_llm_event, emit_dialogue) should
normalize to this schema before persistence.

The schema supports:
- Three fixed channels: system, process, llm
- Per-run sequence numbers
- Deduplication via fingerprints
- LLM enrichment fields
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timezone
from typing import Any, Literal

from polaris.kernelone.utils.time_utils import utc_now_str
from pydantic import BaseModel, Field

# Version guard for type checking
CANONICAL_LOG_EVENT_V2_GUARD = True


# Channel types - fixed three channels
LogChannel = Literal["system", "process", "llm"]

# Severity levels
LogSeverity = Literal["debug", "info", "warn", "error", "critical"]

# Event kinds
LogKind = Literal["state", "action", "observation", "output", "error"]

# Domain types
LogDomain = Literal["system", "process", "llm", "user"]


class LogEnrichmentV1(BaseModel):
    """LLM enhancement results for a log event."""

    signal_score: float = Field(default=0.0, ge=0.0, le=1.0)
    summary: str = ""
    normalized_fields: dict[str, Any] = Field(default_factory=dict)
    noise: bool = False
    status: Literal["pending", "success", "failed"] = "pending"
    error: str | None = None


class CanonicalLogEventV2(BaseModel):
    """Unified canonical log event schema.

    This is the single source of truth for all log events.
    All log producers should normalize to this schema.

    Storage layers:
    - journal.raw.jsonl: Original immutable facts (audit source)
    - journal.norm.jsonl: Normalized facts (unified schema)
    - journal.enriched.jsonl: LLM enhanced results (rebuildable)
    """

    # Schema versioning
    schema_version: Literal[2] = Field(default=2)

    # Core identifiers
    event_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    run_id: str = ""
    seq: int = 0

    # Timestamps
    ts: str = Field(default_factory=utc_now_str)
    ts_epoch: float = Field(default_factory=lambda: datetime.now(timezone.utc).timestamp())

    # Channel and domain - fixed three channels
    channel: LogChannel = "system"
    domain: LogDomain = "system"

    # Event classification
    severity: LogSeverity = "info"
    kind: LogKind = "observation"

    # Source information
    actor: str = "system"
    source: str = ""

    # Message content
    message: str = ""
    refs: dict[str, Any] = Field(default_factory=dict)
    tags: list[str] = Field(default_factory=list)

    # Raw original data (for audit and fallback)
    raw: dict[str, Any] | None = None

    # Deduplication
    fingerprint: str = ""
    dedupe_count: int = 0

    # LLM enrichment (async, populated by background worker)
    enrichment: LogEnrichmentV1 | None = None

    # Legacy compatibility fields (for backward compatibility with old channels)
    # These are populated by the adapter layer when reading
    legacy_name: str | None = None  # Old 'name' field
    legacy_output: dict[str, Any] | None = None  # Old 'output' field
    legacy_input: dict[str, Any] | None = None  # Old 'input' field

    def compute_fingerprint(self) -> str:
        """Compute deduplication fingerprint for this event."""
        content = f"{self.channel}:{self.kind}:{self.actor}:{self.message[:200]}"
        return hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]

    def to_legacy_projection(self, legacy_channel: str) -> dict[str, Any]:
        """Project this event to legacy format for backward compatibility.

        Args:
            legacy_channel: The old channel name (e.g., 'pm_log', 'runtime_events')

        Returns:
            Dict that mimics the old event format
        """
        projection: dict[str, Any] = {
            "ts": self.ts,
            "ts_epoch": self.ts_epoch,
            "seq": self.seq,
            "event_id": self.event_id,
            "run_id": self.run_id,
            # Legacy 'name' field maps to message
            "name": self.message[:100] if self.message else "",
            # Legacy 'data' or 'output' field
            "data": self.raw or self.enrichment.normalized_fields if self.enrichment else {},
            # Type-specific projections
        }

        if legacy_channel in {"pm_subprocess", "director_console", "runlog"}:
            # Process channel: preserve raw text
            projection["type"] = "line"
            projection["text"] = self.message
            if self.raw:
                projection["text"] = self.raw.get("text", self.message)

        elif legacy_channel in {"pm_llm", "director_llm", "ollama"}:
            # LLM channel: preserve role and event
            projection["role"] = self.actor
            projection["event"] = self.kind  # or specific LLM event type
            if self.raw:
                projection["data"] = self.raw

        elif legacy_channel in {"runtime_events", "engine_status"}:
            # System channel: state events
            projection["kind"] = self.kind
            projection["actor"] = self.actor
            projection["summary"] = self.message

        elif legacy_channel in {"pm_log", "pm_report", "planner", "qa", "dialogue"}:
            # System channel with structured data
            projection["type"] = self.kind
            projection["output"] = self.legacy_output or {}
            projection["input"] = self.legacy_input or {}

        return projection


# Channel mapping for legacy compatibility
# Maps old channel names to new channel + metadata
LEGACY_CHANNEL_MAPPING: dict[str, dict[str, Any]] = {
    # Process channels (subprocess stdout/stderr)
    "pm_subprocess": {"channel": "process", "domain": "process", "actor": "PM"},
    "director_console": {"channel": "process", "domain": "process", "actor": "Director"},
    "runlog": {"channel": "process", "domain": "process", "actor": "System"},
    # LLM channels
    "pm_llm": {"channel": "llm", "domain": "llm", "actor": "PM"},
    "director_llm": {"channel": "llm", "domain": "llm", "actor": "Director"},
    "ollama": {"channel": "llm", "domain": "llm", "actor": "Ollama"},
    # System channels
    "runtime_events": {"channel": "system", "domain": "system", "actor": "Runtime"},
    "engine_status": {"channel": "system", "domain": "system", "actor": "Engine"},
    "pm_log": {"channel": "system", "domain": "system", "actor": "PM"},
    "pm_report": {"channel": "system", "domain": "system", "actor": "PM"},
    "planner": {"channel": "system", "domain": "system", "actor": "Planner"},
    "qa": {"channel": "system", "domain": "system", "actor": "QA"},
    "dialogue": {"channel": "system", "domain": "system", "actor": "Dialogue"},
}


def normalize_legacy_event(
    raw: dict[str, Any],
    legacy_channel: str,
    run_id: str = "",
) -> CanonicalLogEventV2:
    """Normalize a legacy event to CanonicalLogEventV2.

    This function handles events from old channels and converts them
    to the unified schema.

    Args:
        raw: Raw event dict from old format
        legacy_channel: Source channel name (e.g., 'pm_log')
        run_id: Run identifier

    Returns:
        CanonicalLogEventV2 instance
    """
    mapping = LEGACY_CHANNEL_MAPPING.get(legacy_channel, {})
    channel = mapping.get("channel", "system")
    domain = mapping.get("domain", "system")
    actor = mapping.get("actor", "system")

    # Extract common fields
    ts = raw.get("ts", utc_now_str())
    ts_epoch = raw.get("ts_epoch", datetime.now(timezone.utc).timestamp())
    seq = raw.get("seq", 0)
    event_id = raw.get("event_id", str(uuid.uuid4()))

    # Extract message based on channel type
    message = ""
    if legacy_channel in {"pm_subprocess", "director_console", "runlog"}:
        message = raw.get("text", raw.get("message", ""))
    elif legacy_channel in {"pm_llm", "director_llm", "ollama"}:
        # LLM events have role/event/data structure
        role = raw.get("role", actor)
        event_type = raw.get("event", "")
        data = raw.get("data", {})
        message = f"[{role}] {event_type}: {str(data)[:100]}"
    elif legacy_channel in {"runtime_events", "engine_status"}:
        message = raw.get("summary", raw.get("message", raw.get("name", "")))
    else:
        message = raw.get("message", raw.get("summary", raw.get("name", "")))

    # Determine severity
    severity: LogSeverity = "info"
    if raw.get("error") or raw.get("kind") == "error":
        severity = "error"
    elif "warn" in str(raw.get("level", "")).lower():
        severity = "warn"

    # Determine kind
    kind: LogKind = "observation"
    if raw.get("kind"):
        kind = raw.get("kind")  # type: ignore[assignment]
    elif raw.get("type") == "action":
        kind = "action"

    return CanonicalLogEventV2(
        schema_version=2,
        event_id=event_id,
        run_id=run_id,
        seq=seq,
        ts=ts,
        ts_epoch=ts_epoch,
        channel=channel,
        domain=domain,
        severity=severity,
        kind=kind,
        actor=raw.get("actor", actor),
        source=legacy_channel,
        message=message,
        refs=raw.get("refs", {}),
        tags=raw.get("tags", []),
        raw=raw,
        legacy_name=raw.get("name"),
        legacy_output=raw.get("output", raw.get("data")),
        legacy_input=raw.get("input"),
    )
