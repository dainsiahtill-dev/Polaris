"""Tests for polaris.infrastructure.log_pipeline.canonical_event."""

from __future__ import annotations

import pytest
from polaris.infrastructure.log_pipeline.canonical_event import (
    LEGACY_CHANNEL_MAPPING,
    CanonicalLogEventV2,
    LogEnrichmentV1,
    normalize_legacy_event,
)
from pydantic import ValidationError


class TestLogEnrichmentV1:
    """Tests for LogEnrichmentV1 model."""

    def test_default_creation(self) -> None:
        """Happy path: create with all defaults."""
        enrichment = LogEnrichmentV1()

        assert enrichment.signal_score == 0.0
        assert enrichment.summary == ""
        assert enrichment.normalized_fields == {}
        assert enrichment.noise is False
        assert enrichment.status == "pending"
        assert enrichment.error is None

    def test_custom_values(self) -> None:
        """Create with custom values."""
        enrichment = LogEnrichmentV1(
            signal_score=0.75,
            summary="test summary",
            normalized_fields={"key": "value"},
            noise=True,
            status="success",
            error=None,
        )

        assert enrichment.signal_score == 0.75
        assert enrichment.summary == "test summary"

    def test_signal_score_boundary_min(self) -> None:
        """signal_score at minimum boundary (0.0)."""
        enrichment = LogEnrichmentV1(signal_score=0.0)
        assert enrichment.signal_score == 0.0

    def test_signal_score_boundary_max(self) -> None:
        """signal_score at maximum boundary (1.0)."""
        enrichment = LogEnrichmentV1(signal_score=1.0)
        assert enrichment.signal_score == 1.0

    def test_signal_score_below_min_raises(self) -> None:
        """signal_score below 0 raises validation error."""
        with pytest.raises(ValidationError):
            LogEnrichmentV1(signal_score=-0.1)

    def test_signal_score_above_max_raises(self) -> None:
        """signal_score above 1 raises validation error."""
        with pytest.raises(ValidationError):
            LogEnrichmentV1(signal_score=1.1)

    def test_invalid_status_raises(self) -> None:
        """Invalid status value raises validation error."""
        with pytest.raises(ValidationError):
            LogEnrichmentV1(status="invalid_status")


class TestCanonicalLogEventV2:
    """Tests for CanonicalLogEventV2 model."""

    def test_default_creation(self) -> None:
        """Happy path: create with all defaults."""
        event = CanonicalLogEventV2()

        assert event.schema_version == 2
        assert event.channel == "system"
        assert event.domain == "system"
        assert event.severity == "info"
        assert event.kind == "observation"
        assert event.actor == "system"
        assert event.message == ""
        assert event.run_id == ""
        assert event.seq == 0
        assert event.refs == {}
        assert event.tags == []
        assert event.raw is None
        assert event.fingerprint == ""
        assert event.dedupe_count == 0
        assert event.enrichment is None

    def test_custom_creation(self) -> None:
        """Create with custom values."""
        event = CanonicalLogEventV2(
            channel="process",
            domain="process",
            severity="error",
            kind="action",
            actor="test_actor",
            message="test message",
            run_id="run-123",
            seq=42,
        )

        assert event.channel == "process"
        assert event.severity == "error"
        assert event.actor == "test_actor"
        assert event.message == "test message"
        assert event.run_id == "run-123"
        assert event.seq == 42

    def test_invalid_channel_raises(self) -> None:
        """Invalid channel raises validation error."""
        with pytest.raises(ValidationError):
            CanonicalLogEventV2(channel="invalid_channel")

    def test_invalid_severity_raises(self) -> None:
        """Invalid severity raises validation error."""
        with pytest.raises(ValidationError):
            CanonicalLogEventV2(severity="invalid")

    def test_invalid_kind_raises(self) -> None:
        """Invalid kind raises validation error."""
        with pytest.raises(ValidationError):
            CanonicalLogEventV2(kind="invalid")

    def test_invalid_domain_raises(self) -> None:
        """Invalid domain raises validation error."""
        with pytest.raises(ValidationError):
            CanonicalLogEventV2(domain="invalid")

    def test_schema_version_fixed(self) -> None:
        """schema_version is fixed at 2."""
        event = CanonicalLogEventV2()
        assert event.schema_version == 2

        with pytest.raises(ValidationError):
            CanonicalLogEventV2(schema_version=1)

    def test_event_id_auto_generated(self) -> None:
        """event_id is auto-generated UUID."""
        event = CanonicalLogEventV2()
        assert len(event.event_id) == 36  # UUID4 format

    def test_ts_auto_generated(self) -> None:
        """ts is auto-generated."""
        event = CanonicalLogEventV2()
        assert event.ts != ""
        assert "T" in event.ts

    def test_ts_epoch_auto_generated(self) -> None:
        """ts_epoch is auto-generated."""
        event = CanonicalLogEventV2()
        assert event.ts_epoch > 0


class TestComputeFingerprint:
    """Tests for compute_fingerprint method."""

    def test_fingerprint_deterministic(self) -> None:
        """Same event produces same fingerprint."""
        event = CanonicalLogEventV2(
            channel="system",
            kind="observation",
            actor="test",
            message="test message",
        )
        fp1 = event.compute_fingerprint()
        fp2 = event.compute_fingerprint()

        assert fp1 == fp2
        assert len(fp1) == 16

    def test_different_events_different_fingerprints(self) -> None:
        """Different events produce different fingerprints."""
        event1 = CanonicalLogEventV2(message="message 1")
        event2 = CanonicalLogEventV2(message="message 2")

        assert event1.compute_fingerprint() != event2.compute_fingerprint()

    def test_fingerprint_truncates_long_message(self) -> None:
        """Fingerprint uses only first 200 chars of message."""
        event1 = CanonicalLogEventV2(message="x" * 200 + "suffix")
        event2 = CanonicalLogEventV2(message="x" * 200 + "different")

        assert event1.compute_fingerprint() == event2.compute_fingerprint()

    def test_fingerprint_includes_channel_kind_actor(self) -> None:
        """Fingerprint changes with channel, kind, or actor."""
        base = CanonicalLogEventV2(message="test")
        different_channel = CanonicalLogEventV2(channel="process", message="test")
        different_kind = CanonicalLogEventV2(kind="action", message="test")
        different_actor = CanonicalLogEventV2(actor="other", message="test")

        assert base.compute_fingerprint() != different_channel.compute_fingerprint()
        assert base.compute_fingerprint() != different_kind.compute_fingerprint()
        assert base.compute_fingerprint() != different_actor.compute_fingerprint()

    def test_fingerprint_hex_only(self) -> None:
        """Fingerprint contains only hex characters."""
        event = CanonicalLogEventV2(message="test")
        fp = event.compute_fingerprint()

        assert all(c in "0123456789abcdef" for c in fp)


class TestToLegacyProjection:
    """Tests for to_legacy_projection method."""

    def test_pm_subprocess_projection(self) -> None:
        """Project to pm_subprocess legacy format."""
        event = CanonicalLogEventV2(
            message="subprocess output",
            raw={"text": "raw text"},
        )
        projection = event.to_legacy_projection("pm_subprocess")

        assert projection["type"] == "line"
        assert projection["text"] == "raw text"

    def test_director_console_projection(self) -> None:
        """Project to director_console legacy format."""
        event = CanonicalLogEventV2(message="console output")
        projection = event.to_legacy_projection("director_console")

        assert projection["type"] == "line"

    def test_pm_llm_projection(self) -> None:
        """Project to pm_llm legacy format."""
        event = CanonicalLogEventV2(
            actor="assistant",
            kind="output",
            raw={"data": "llm data"},
        )
        projection = event.to_legacy_projection("pm_llm")

        assert projection["role"] == "assistant"
        assert projection["event"] == "output"
        assert projection["data"] == {"data": "llm data"}

    def test_runtime_events_projection(self) -> None:
        """Project to runtime_events legacy format."""
        event = CanonicalLogEventV2(
            kind="state",
            actor="runtime",
            message="state changed",
        )
        projection = event.to_legacy_projection("runtime_events")

        assert projection["kind"] == "state"
        assert projection["actor"] == "runtime"
        assert projection["summary"] == "state changed"

    def test_pm_log_projection(self) -> None:
        """Project to pm_log legacy format."""
        event = CanonicalLogEventV2(
            kind="action",
            legacy_output={"result": "ok"},
            legacy_input={"command": "test"},
        )
        projection = event.to_legacy_projection("pm_log")

        assert projection["type"] == "action"
        assert projection["output"] == {"result": "ok"}
        assert projection["input"] == {"command": "test"}

    def test_unknown_legacy_channel(self) -> None:
        """Unknown legacy channel produces minimal projection."""
        event = CanonicalLogEventV2(message="test")
        projection = event.to_legacy_projection("unknown_channel")

        assert projection["ts"] == event.ts
        assert "name" in projection

    def test_projection_preserves_core_fields(self) -> None:
        """Projection preserves core identifier fields."""
        event = CanonicalLogEventV2(
            event_id="test-id",
            run_id="run-123",
            seq=5,
        )
        projection = event.to_legacy_projection("pm_log")

        assert projection["event_id"] == "test-id"
        assert projection["run_id"] == "run-123"
        assert projection["seq"] == 5

    def test_empty_message_in_projection(self) -> None:
        """Empty message results in empty name field."""
        event = CanonicalLogEventV2(message="")
        projection = event.to_legacy_projection("pm_log")

        assert projection["name"] == ""

    def test_long_message_truncated_in_projection(self) -> None:
        """Message is truncated to 100 chars in projection name."""
        long_msg = "x" * 150
        event = CanonicalLogEventV2(message=long_msg)
        projection = event.to_legacy_projection("pm_log")

        assert len(projection["name"]) == 100
        assert projection["name"] == "x" * 100


class TestLegacyChannelMapping:
    """Tests for LEGACY_CHANNEL_MAPPING."""

    def test_contains_expected_channels(self) -> None:
        """Mapping contains all expected legacy channels."""
        expected = {
            "pm_subprocess",
            "director_console",
            "runlog",
            "pm_llm",
            "director_llm",
            "ollama",
            "runtime_events",
            "engine_status",
            "pm_log",
            "pm_report",
            "planner",
            "qa",
            "dialogue",
        }
        assert set(LEGACY_CHANNEL_MAPPING.keys()) == expected

    def test_process_channels(self) -> None:
        """Process channels map correctly."""
        for channel in ["pm_subprocess", "director_console", "runlog"]:
            mapping = LEGACY_CHANNEL_MAPPING[channel]
            assert mapping["channel"] == "process"
            assert mapping["domain"] == "process"

    def test_llm_channels(self) -> None:
        """LLM channels map correctly."""
        for channel in ["pm_llm", "director_llm", "ollama"]:
            mapping = LEGACY_CHANNEL_MAPPING[channel]
            assert mapping["channel"] == "llm"
            assert mapping["domain"] == "llm"

    def test_system_channels(self) -> None:
        """System channels map correctly."""
        for channel in ["runtime_events", "engine_status", "pm_log", "planner", "qa", "dialogue"]:
            mapping = LEGACY_CHANNEL_MAPPING[channel]
            assert mapping["channel"] == "system"
            assert mapping["domain"] == "system"


class TestNormalizeLegacyEvent:
    """Tests for normalize_legacy_event function."""

    def test_normalize_pm_subprocess(self) -> None:
        """Normalize pm_subprocess event."""
        raw = {"text": "output line", "seq": 1}
        event = normalize_legacy_event(raw, "pm_subprocess", run_id="run-1")

        assert event.channel == "process"
        assert event.message == "output line"
        assert event.run_id == "run-1"
        assert event.seq == 1

    def test_normalize_pm_llm(self) -> None:
        """Normalize pm_llm event."""
        raw = {"role": "user", "event": "message", "data": {"content": "hello"}}
        event = normalize_legacy_event(raw, "pm_llm")

        assert event.channel == "llm"
        assert event.actor == "PM"  # from LEGACY_CHANNEL_MAPPING, raw has no "actor"
        assert "user" in event.message
        assert "message" in event.message

    def test_normalize_runtime_events(self) -> None:
        """Normalize runtime_events event."""
        raw = {"summary": "system started", "seq": 5}
        event = normalize_legacy_event(raw, "runtime_events")

        assert event.channel == "system"
        assert event.message == "system started"
        assert event.seq == 5

    def test_normalize_unknown_channel_defaults(self) -> None:
        """Unknown channel defaults to system."""
        raw = {"message": "generic event"}
        event = normalize_legacy_event(raw, "unknown_channel")

        assert event.channel == "system"
        assert event.domain == "system"
        assert event.actor == "system"

    def test_normalize_extracts_message_from_various_fields(self) -> None:
        """Message extracted from various field names."""
        raw = {"summary": "from summary"}
        event = normalize_legacy_event(raw, "pm_log")
        assert event.message == "from summary"

        raw = {"name": "from name"}
        event = normalize_legacy_event(raw, "pm_log")
        assert event.message == "from name"

    def test_normalize_determines_severity_error(self) -> None:
        """Severity determined as error from error field."""
        raw = {"error": "something went wrong"}
        event = normalize_legacy_event(raw, "pm_log")

        assert event.severity == "error"

    def test_normalize_determines_severity_warn(self) -> None:
        """Severity determined as warn from level field."""
        raw = {"level": "WARNING"}
        event = normalize_legacy_event(raw, "pm_log")

        assert event.severity == "warn"

    def test_normalize_determines_kind_action(self) -> None:
        """Kind determined as action from type field."""
        raw = {"type": "action"}
        event = normalize_legacy_event(raw, "pm_log")

        assert event.kind == "action"

    def test_normalize_preserves_raw(self) -> None:
        """Raw data is preserved."""
        raw = {"custom": "data", "tags": ["a", "b"]}
        event = normalize_legacy_event(raw, "pm_log")

        assert event.raw == raw
        assert event.tags == ["a", "b"]
        assert event.refs == {}

    def test_normalize_preserves_legacy_fields(self) -> None:
        """Legacy fields are populated."""
        raw = {"name": "legacy_name", "output": {"result": "ok"}, "input": {"cmd": "test"}}
        event = normalize_legacy_event(raw, "pm_log")

        assert event.legacy_name == "legacy_name"
        assert event.legacy_output == {"result": "ok"}
        assert event.legacy_input == {"cmd": "test"}

    def test_normalize_empty_raw(self) -> None:
        """Empty raw dict handled gracefully."""
        event = normalize_legacy_event({}, "pm_log")

        assert event.message == ""
        assert event.severity == "info"
        assert event.kind == "observation"

    def test_normalize_with_run_id(self) -> None:
        """run_id is set from parameter."""
        event = normalize_legacy_event({}, "pm_log", run_id="run-abc")

        assert event.run_id == "run-abc"

    def test_normalize_preserves_event_id(self) -> None:
        """Existing event_id is preserved."""
        raw = {"event_id": "existing-id"}
        event = normalize_legacy_event(raw, "pm_log")

        assert event.event_id == "existing-id"

    def test_normalize_generates_event_id_if_missing(self) -> None:
        """New event_id generated if missing."""
        event = normalize_legacy_event({}, "pm_log")

        assert len(event.event_id) == 36
