"""Tests for polaris.infrastructure.log_pipeline.canonical_event."""

from __future__ import annotations

from polaris.infrastructure.log_pipeline.canonical_event import (
    CANONICAL_LOG_EVENT_V2_GUARD,
    LEGACY_CHANNEL_MAPPING,
    CanonicalLogEventV2,
    LogEnrichmentV1,
    normalize_legacy_event,
)


class TestCanonicalLogEventV2Guard:
    def test_guard_is_true(self) -> None:
        assert CANONICAL_LOG_EVENT_V2_GUARD is True


class TestLogEnrichmentV1:
    def test_defaults(self) -> None:
        e = LogEnrichmentV1()
        assert e.signal_score == 0.0
        assert e.summary == ""
        assert e.normalized_fields == {}
        assert e.noise is False
        assert e.status == "pending"
        assert e.error is None

    def test_with_values(self) -> None:
        e = LogEnrichmentV1(signal_score=0.8, summary="test", noise=True, status="success")
        assert e.signal_score == 0.8
        assert e.summary == "test"
        assert e.noise is True
        assert e.status == "success"


class TestCanonicalLogEventV2:
    def test_schema_version_default(self) -> None:
        e = CanonicalLogEventV2()
        assert e.schema_version == 2

    def test_default_fields(self) -> None:
        e = CanonicalLogEventV2()
        assert e.run_id == ""
        assert e.seq == 0
        assert e.channel == "system"
        assert e.domain == "system"
        assert e.severity == "info"
        assert e.kind == "observation"
        assert e.actor == "system"
        assert e.source == ""
        assert e.message == ""
        assert e.refs == {}
        assert e.tags == []

    def test_event_id_is_uuid(self) -> None:
        e = CanonicalLogEventV2()
        assert e.event_id  # non-empty
        assert len(e.event_id) > 10

    def test_fingerprint_length(self) -> None:
        e = CanonicalLogEventV2(
            channel="process",
            kind="action",
            actor="director",
            message="test message",
        )
        fp = e.compute_fingerprint()
        assert len(fp) == 16

    def test_fingerprint_consistency(self) -> None:
        e = CanonicalLogEventV2(
            channel="llm",
            kind="output",
            actor="pm",
            message="hello",
        )
        fp1 = e.compute_fingerprint()
        fp2 = e.compute_fingerprint()
        assert fp1 == fp2

    def test_fingerprint_different_for_different_content(self) -> None:
        e1 = CanonicalLogEventV2(channel="system", kind="state", actor="engine", message="start")
        e2 = CanonicalLogEventV2(channel="llm", kind="state", actor="engine", message="start")
        assert e1.compute_fingerprint() != e2.compute_fingerprint()

    def test_to_legacy_projection_pm_subprocess(self) -> None:
        e = CanonicalLogEventV2(
            ts="2026-04-24T12:00:00+00:00",
            ts_epoch=1745491200.0,
            seq=1,
            channel="process",
            message="subprocess output",
            raw={"text": "hello world"},
        )
        proj = e.to_legacy_projection("pm_subprocess")
        assert proj["type"] == "line"
        assert proj["text"] == "hello world"
        assert proj["ts"] == "2026-04-24T12:00:00+00:00"

    def test_to_legacy_projection_pm_llm(self) -> None:
        e = CanonicalLogEventV2(
            channel="llm",
            kind="observation",
            actor="pm",
            message="llm response",
            raw={"role": "assistant", "event": "response"},
        )
        proj = e.to_legacy_projection("pm_llm")
        assert proj["role"] == "pm"
        assert proj["event"] == "observation"

    def test_to_legacy_projection_runtime_events(self) -> None:
        e = CanonicalLogEventV2(
            channel="system",
            kind="state",
            actor="engine",
            message="engine started",
        )
        proj = e.to_legacy_projection("runtime_events")
        assert proj["kind"] == "state"
        assert proj["actor"] == "engine"
        assert proj["summary"] == "engine started"


class TestLegacyChannelMapping:
    def test_pm_subprocess_maps_to_process(self) -> None:
        assert LEGACY_CHANNEL_MAPPING["pm_subprocess"]["channel"] == "process"

    def test_runtime_events_maps_to_system(self) -> None:
        assert LEGACY_CHANNEL_MAPPING["runtime_events"]["channel"] == "system"

    def test_all_channels_have_actor(self) -> None:
        for _name, mapping in LEGACY_CHANNEL_MAPPING.items():
            assert "actor" in mapping
            assert mapping["actor"]


class TestNormalizeLegacyEvent:
    def test_pm_subprocess(self) -> None:
        raw = {"text": "hello from subprocess", "ts": "2026-04-24T12:00:00Z"}
        event = normalize_legacy_event(raw, "pm_subprocess", "run-123")
        assert event.channel == "process"
        assert event.domain == "process"
        assert event.actor == "PM"
        assert event.message == "hello from subprocess"

    def test_runtime_events(self) -> None:
        raw = {"summary": "task completed", "ts": "2026-04-24T12:00:00Z"}
        event = normalize_legacy_event(raw, "runtime_events", "run-456")
        assert event.channel == "system"
        assert event.domain == "system"
        assert event.message == "task completed"

    def test_with_error_severity(self) -> None:
        raw = {"error": "something failed", "ts": "2026-04-24T12:00:00Z"}
        event = normalize_legacy_event(raw, "pm_log", "run-789")
        assert event.severity == "error"

    def test_with_action_kind(self) -> None:
        raw = {"type": "action", "message": "doing something"}
        event = normalize_legacy_event(raw, "pm_log", "run-001")
        assert event.kind == "action"

    def test_unknown_channel_defaults_to_system(self) -> None:
        raw = {"message": "unknown source"}
        event = normalize_legacy_event(raw, "unknown_channel", "run-999")
        assert event.channel == "system"
        assert event.domain == "system"

    def test_extracts_seq_from_raw(self) -> None:
        raw = {"message": "test", "seq": 42}
        event = normalize_legacy_event(raw, "pm_log", "run-1")
        assert event.seq == 42

    def test_extracts_event_id_from_raw(self) -> None:
        raw = {"message": "test", "event_id": "evt-abc123"}
        event = normalize_legacy_event(raw, "pm_log", "run-1")
        assert event.event_id == "evt-abc123"
