"""Unit tests for polaris.cells.factory.pipeline.public.types."""

from __future__ import annotations

import pytest
from polaris.cells.factory.pipeline.public.types import (
    VALID_PHASE_TRANSITIONS,
    AgentTurnRequest,
    EventType,
    FactoryControlRequest,
    FactoryRun,
    FactoryRunList,
    FactoryRunStatus,
    FactoryStartRequest,
    FailureInfo,
    FailureType,
    GateResult,
    GateStatus,
    RunEvent,
    RunLevel,
    RunLifecycleStatus,
    RunPhase,
    get_next_phases,
    is_valid_transition,
)
from pydantic import ValidationError


class TestRunPhase:
    """Tests for RunPhase enum."""

    def test_phase_values(self) -> None:
        assert RunPhase.PENDING.value == "pending"
        assert RunPhase.INTAKE.value == "intake"
        assert RunPhase.COMPLETED.value == "completed"
        assert RunPhase.FAILED.value == "failed"

    def test_phase_count(self) -> None:
        assert len(RunPhase) == 13


class TestRunLifecycleStatus:
    """Tests for RunLifecycleStatus enum."""

    def test_status_values(self) -> None:
        assert RunLifecycleStatus.PENDING.value == "pending"
        assert RunLifecycleStatus.RUNNING.value == "running"
        assert RunLifecycleStatus.COMPLETED.value == "completed"
        assert RunLifecycleStatus.FAILED.value == "failed"


class TestFailureType:
    """Tests for FailureType enum."""

    def test_failure_type_values(self) -> None:
        assert FailureType.TRANSIENT.value == "transient"
        assert FailureType.DETERMINISTIC.value == "deterministic"
        assert FailureType.POLICY.value == "policy"


class TestRunLevel:
    """Tests for RunLevel enum."""

    def test_level_values(self) -> None:
        assert RunLevel.DEBUG.value == "debug"
        assert RunLevel.INFO.value == "info"
        assert RunLevel.WARNING.value == "warning"
        assert RunLevel.ERROR.value == "error"
        assert RunLevel.CRITICAL.value == "critical"


class TestGateStatus:
    """Tests for GateStatus enum."""

    def test_gate_status_values(self) -> None:
        assert GateStatus.PENDING.value == "pending"
        assert GateStatus.PASSED.value == "passed"
        assert GateStatus.FAILED.value == "failed"
        assert GateStatus.SKIPPED.value == "skipped"


class TestEventType:
    """Tests for EventType constants class."""

    def test_event_type_constants(self) -> None:
        assert EventType.PHASE_ENTER == "phase_enter"
        assert EventType.PHASE_EXIT == "phase_exit"
        assert EventType.TOOL_CALL == "tool_call"
        assert EventType.GATE_RESULT == "gate_result"
        assert EventType.ARTIFACT_CREATED == "artifact_created"
        assert EventType.ERROR == "error"
        assert EventType.CHECKPOINT_SAVED == "checkpoint_saved"


class TestFactoryStartRequest:
    """Tests for FactoryStartRequest model."""

    def test_valid_request(self) -> None:
        req = FactoryStartRequest(workspace="/tmp/ws")
        assert req.workspace == "/tmp/ws"
        assert req.start_from == "auto"
        assert req.run_director is True
        assert req.director_iterations == 1

    def test_directive_field(self) -> None:
        req = FactoryStartRequest(workspace="/tmp/ws", directive="Build a thing")
        assert req.directive == "Build a thing"

    def test_director_iterations_bounds(self) -> None:
        with pytest.raises(ValidationError):
            FactoryStartRequest(workspace="/tmp/ws", director_iterations=0)
        with pytest.raises(ValidationError):
            FactoryStartRequest(workspace="/tmp/ws", director_iterations=11)

    def test_start_from_values(self) -> None:
        for value in ("auto", "architect", "pm", "director"):
            req = FactoryStartRequest(workspace="/tmp/ws", start_from=value)  # type: ignore[arg-type]
            assert req.start_from == value


class TestFactoryControlRequest:
    """Tests for FactoryControlRequest model."""

    def test_valid_control(self) -> None:
        req = FactoryControlRequest(action="pause")
        assert req.action == "pause"
        assert req.target_phase is None

    def test_invalid_action(self) -> None:
        with pytest.raises(ValidationError):
            FactoryControlRequest(action="invalid")  # type: ignore[arg-type]


class TestAgentTurnRequest:
    """Tests for AgentTurnRequest model."""

    def test_valid_request(self) -> None:
        req = AgentTurnRequest(workspace="/tmp/ws", message="hello")
        assert req.workspace == "/tmp/ws"
        assert req.message == "hello"
        assert req.role == "assistant"
        assert req.mode == "chat"
        assert req.stream is True

    def test_invalid_role(self) -> None:
        with pytest.raises(ValidationError):
            AgentTurnRequest(workspace="/tmp/ws", message="hello", role="invalid")  # type: ignore[arg-type]


class TestRunEvent:
    """Tests for RunEvent model."""

    def test_default_event_id(self) -> None:
        event = RunEvent(run_id="r1", phase=RunPhase.PENDING, type="test", message="m")
        assert event.run_id == "r1"
        assert event.phase == RunPhase.PENDING
        assert event.type == "test"
        assert event.message == "m"
        assert event.level == RunLevel.INFO
        assert event.event_id
        assert event.payload == {}

    def test_custom_level(self) -> None:
        event = RunEvent(
            run_id="r1",
            phase=RunPhase.PENDING,
            type="test",
            message="m",
            level=RunLevel.ERROR,
        )
        assert event.level == RunLevel.ERROR


class TestGateResult:
    """Tests for GateResult model."""

    def test_gate_result(self) -> None:
        gate = GateResult(gate_name="quality", status=GateStatus.PASSED, passed=True, message="ok")
        assert gate.gate_name == "quality"
        assert gate.status == GateStatus.PASSED
        assert gate.passed is True
        assert gate.message == "ok"
        assert gate.score is None
        assert gate.details == {}
        assert gate.artifacts == []


class TestFailureInfo:
    """Tests for FailureInfo model."""

    def test_failure_info(self) -> None:
        info = FailureInfo(
            failure_type=FailureType.TRANSIENT,
            code="E001",
            detail="something broke",
            phase=RunPhase.IMPLEMENTATION,
            recoverable=True,
        )
        assert info.failure_type == FailureType.TRANSIENT
        assert info.code == "E001"
        assert info.recoverable is True
        assert info.hops == []


class TestFactoryRun:
    """Tests for FactoryRun model."""

    def test_default_values(self) -> None:
        run = FactoryRun(workspace="/tmp/ws")
        assert run.workspace == "/tmp/ws"
        assert run.phase == RunPhase.PENDING
        assert run.progress == 0.0
        assert run.start_from == "auto"
        assert run.input_source == "directive"
        assert run.run_director is True
        assert run.director_iterations == 1
        assert run.loop is False
        assert run.roles == {}
        assert run.gates == []
        assert run.artifacts == []
        assert run.events == []
        assert run.failure is None
        assert run.last_checkpoint is None

    def test_run_id_generated(self) -> None:
        run1 = FactoryRun(workspace="/tmp/ws")
        run2 = FactoryRun(workspace="/tmp/ws")
        assert run1.run_id != run2.run_id
        assert run1.run_id.startswith("run-")


class TestFactoryRunStatus:
    """Tests for FactoryRunStatus model."""

    def test_factory_run_status(self) -> None:
        import datetime

        now = datetime.datetime.now()
        status = FactoryRunStatus(
            run_id="r1",
            phase=RunPhase.PLANNING,
            status=RunLifecycleStatus.RUNNING,
            progress=50.0,
            roles={},
            gates=[],
            created_at=now,
            started_at=now,
            updated_at=now,
            completed_at=None,
        )
        assert status.run_id == "r1"
        assert status.progress == 50.0


class TestPhaseTransitions:
    """Tests for phase transition utilities."""

    def test_valid_transitions(self) -> None:
        assert is_valid_transition(RunPhase.PENDING, RunPhase.INTAKE) is True
        assert is_valid_transition(RunPhase.PENDING, RunPhase.DOCS_CHECK) is True
        assert is_valid_transition(RunPhase.ARCHITECT, RunPhase.PLANNING) is True
        assert is_valid_transition(RunPhase.HANDOVER, RunPhase.COMPLETED) is True

    def test_invalid_transitions(self) -> None:
        assert is_valid_transition(RunPhase.PENDING, RunPhase.COMPLETED) is False
        assert is_valid_transition(RunPhase.COMPLETED, RunPhase.PENDING) is False
        assert is_valid_transition(RunPhase.FAILED, RunPhase.COMPLETED) is False

    def test_terminal_phases(self) -> None:
        assert VALID_PHASE_TRANSITIONS[RunPhase.COMPLETED] == []
        assert VALID_PHASE_TRANSITIONS[RunPhase.CANCELLED] == []

    def test_get_next_phases(self) -> None:
        next_phases = get_next_phases(RunPhase.PENDING)
        assert RunPhase.INTAKE in next_phases
        assert RunPhase.DOCS_CHECK in next_phases

    def test_get_next_phases_terminal(self) -> None:
        assert get_next_phases(RunPhase.COMPLETED) == []


class TestFactoryRunList:
    """Tests for FactoryRunList model."""

    def test_factory_run_list(self) -> None:
        lst = FactoryRunList(runs=[], total=0, page=1, page_size=20)
        assert lst.total == 0
        assert lst.page == 1
        assert lst.page_size == 20
