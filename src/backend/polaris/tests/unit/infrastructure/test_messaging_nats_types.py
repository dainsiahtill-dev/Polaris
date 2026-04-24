"""Tests for polaris.infrastructure.messaging.nats.nats_types."""

from __future__ import annotations

from polaris.infrastructure.messaging.nats.nats_types import (
    JetStreamConstants,
    RuntimeEventEnvelope,
    create_runtime_event,
)


class TestJetStreamConstants:
    def test_stream_name(self) -> None:
        assert JetStreamConstants.STREAM_NAME == "HP_RUNTIME"

    def test_consumer_prefix(self) -> None:
        assert JetStreamConstants.CONSUMER_DURABLE_PREFIX == "hp_consumer_"

    def test_subject_prefix(self) -> None:
        assert JetStreamConstants.SUBJECT_PREFIX == "hp.runtime"

    def test_channels(self) -> None:
        assert JetStreamConstants.CHANNEL_PM == "pm"
        assert JetStreamConstants.CHANNEL_DIRECTOR == "director"
        assert JetStreamConstants.CHANNEL_QA == "qa"
        assert JetStreamConstants.CHANNEL_SYSTEM == "system"

    def test_event_kinds(self) -> None:
        assert JetStreamConstants.EVENT_KIND_TASK_CREATED == "task.created"
        assert JetStreamConstants.EVENT_KIND_MESSAGE == "message"


class TestRuntimeEventEnvelope:
    def test_defaults(self) -> None:
        evt = RuntimeEventEnvelope()
        assert evt.schema_version == "runtime.v2"
        assert evt.event_id != ""
        assert evt.ts != ""

    def test_to_dict(self) -> None:
        evt = RuntimeEventEnvelope(workspace_key="ws", run_id="run_1", channel="pm", kind="task.created")
        d = evt.to_dict()
        assert d["schema_version"] == "runtime.v2"
        assert d["workspace_key"] == "ws"
        assert d["run_id"] == "run_1"
        assert d["channel"] == "pm"
        assert d["kind"] == "task.created"

    def test_from_dict(self) -> None:
        data = {
            "schema_version": "runtime.v2",
            "event_id": "evt-1",
            "workspace_key": "ws",
            "run_id": "run_1",
            "channel": "pm",
            "kind": "task.created",
            "ts": "2024-01-01T00:00:00+00:00",
            "cursor": 0,
        }
        evt = RuntimeEventEnvelope.from_dict(data)
        assert evt.event_id == "evt-1"
        assert evt.workspace_key == "ws"

    def test_with_cursor(self) -> None:
        evt = RuntimeEventEnvelope()
        evt2 = evt.with_cursor(5)
        assert evt2.cursor == 5
        assert evt.cursor == 0

    def test_with_trace_id(self) -> None:
        evt = RuntimeEventEnvelope()
        evt2 = evt.with_trace_id("trace-1")
        assert evt2.trace_id == "trace-1"


class TestCreateRuntimeEvent:
    def test_factory(self) -> None:
        evt = create_runtime_event("ws", "run_1", "pm", "task.created", {"task_id": "t1"})
        assert evt.workspace_key == "ws"
        assert evt.run_id == "run_1"
        assert evt.channel == "pm"
        assert evt.kind == "task.created"
        assert evt.payload == {"task_id": "t1"}
