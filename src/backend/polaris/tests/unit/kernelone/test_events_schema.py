"""Tests for polaris.kernelone.events.schema (Pydantic event models)."""

from __future__ import annotations

from polaris.kernelone.events.schema import (
    ActionEvent,
    Actor,
    EventBase,
    EventKind,
    EventRef,
    ObservationEvent,
    Phase,
    Truncation,
)


class TestEventRef:
    """Tests for EventRef model."""

    def test_defaults(self) -> None:
        ref = EventRef()
        assert ref.task_id is None
        assert ref.task_fingerprint is None
        assert ref.run_id is None
        assert ref.pm_iteration is None
        assert ref.director_iteration is None
        assert ref.phase is None
        assert ref.files is None
        assert ref.evidence_path is None
        assert ref.trajectory_path is None

    def test_with_values(self) -> None:
        ref = EventRef(
            task_id="task-1",
            run_id="run-1",
            phase="tool_exec",
        )
        assert ref.task_id == "task-1"
        assert ref.run_id == "run-1"
        assert ref.phase == "tool_exec"


class TestTruncation:
    """Tests for Truncation model."""

    def test_defaults(self) -> None:
        t = Truncation()
        assert t.truncated is False
        assert t.reason is None
        assert t.original_bytes is None
        assert t.kept_bytes is None
        assert t.original_lines is None
        assert t.kept_lines is None
        assert t.continuation_attempt == 0
        assert t.continuation_success is False
        assert t.blocked is False

    def test_continuation_fields(self) -> None:
        t = Truncation(
            continuation_attempt=3,
            continuation_success=True,
            blocked=True,
        )
        assert t.continuation_attempt == 3
        assert t.continuation_success is True
        assert t.blocked is True


class TestEventBase:
    """Tests for EventBase model."""

    def test_required_fields(self) -> None:
        event = EventBase(
            ts="2026-04-24T10:00:00Z",
            ts_epoch=1745491200.0,
            seq=1,
            event_id="evt-1",
            kind="action",
            actor="PM",
            name="test_event",
        )
        assert event.schema_version == 1
        assert event.ts == "2026-04-24T10:00:00Z"
        assert event.ts_epoch == 1745491200.0
        assert event.seq == 1
        assert event.event_id == "evt-1"
        assert event.kind == "action"
        assert event.actor == "PM"
        assert event.name == "test_event"
        assert event.summary == ""
        assert isinstance(event.meta, dict)

    def test_refs_field_default_factory(self) -> None:
        event = EventBase(
            ts="2026-04-24T10:00:00Z",
            ts_epoch=1745491200.0,
            seq=1,
            event_id="evt-1",
            kind="observation",
            actor="Director",
            name="obs",
        )
        assert isinstance(event.refs, EventRef)

    def test_meta_field(self) -> None:
        event = EventBase(
            ts="2026-04-24T10:00:00Z",
            ts_epoch=1745491200.0,
            seq=1,
            event_id="evt-1",
            kind="action",
            actor="Tooling",
            name="tool",
            meta={"key": "value", "count": 42},
        )
        assert event.meta["key"] == "value"
        assert event.meta["count"] == 42


class TestActionEvent:
    """Tests for ActionEvent model."""

    def test_kind_is_action(self) -> None:
        action = ActionEvent(
            ts="2026-04-24T10:00:00Z",
            ts_epoch=1745491200.0,
            seq=1,
            event_id="act-1",
            actor="PM",
            name="plan",
        )
        assert action.kind == "action"

    def test_input_default_empty_dict(self) -> None:
        action = ActionEvent(
            ts="2026-04-24T10:00:00Z",
            ts_epoch=1745491200.0,
            seq=1,
            event_id="act-1",
            actor="Director",
            name="execute",
        )
        assert action.input == {}


class TestObservationEvent:
    """Tests for ObservationEvent model."""

    def test_kind_is_observation(self) -> None:
        obs = ObservationEvent(
            ts="2026-04-24T10:00:00Z",
            ts_epoch=1745491200.0,
            seq=1,
            event_id="obs-1",
            actor="QA",
            name="review",
        )
        assert obs.kind == "observation"

    def test_defaults(self) -> None:
        obs = ObservationEvent(
            ts="2026-04-24T10:00:00Z",
            ts_epoch=1745491200.0,
            seq=1,
            event_id="obs-1",
            actor="System",
            name="check",
        )
        assert obs.ok is True
        assert obs.output == {}
        assert isinstance(obs.truncation, Truncation)
        assert obs.duration_ms is None
        assert obs.error is None

    def test_with_output_and_duration(self) -> None:
        obs = ObservationEvent(
            ts="2026-04-24T10:00:00Z",
            ts_epoch=1745491200.0,
            seq=1,
            event_id="obs-1",
            actor="Director",
            name="result",
            ok=True,
            output={"status": "success"},
            duration_ms=150,
        )
        assert obs.ok is True
        assert obs.output["status"] == "success"
        assert obs.duration_ms == 150

    def test_error_field(self) -> None:
        obs = ObservationEvent(
            ts="2026-04-24T10:00:00Z",
            ts_epoch=1745491200.0,
            seq=1,
            event_id="obs-1",
            actor="Reviewer",
            name="fail",
            ok=False,
            error="Timeout exceeded",
        )
        assert obs.ok is False
        assert obs.error == "Timeout exceeded"


class TestLiteralTypes:
    """Smoke tests for Literal type aliases."""

    def test_event_kind_is_literal(self) -> None:
        assert hasattr(EventKind, "__args__")
        assert "action" in EventKind.__args__
        assert "observation" in EventKind.__args__

    def test_actor_is_literal(self) -> None:
        assert hasattr(Actor, "__args__")
        assert "PM" in Actor.__args__
        assert "Director" in Actor.__args__

    def test_phase_is_literal(self) -> None:
        assert hasattr(Phase, "__args__")
        assert "tool_exec" in Phase.__args__
        assert "handoff" in Phase.__args__
