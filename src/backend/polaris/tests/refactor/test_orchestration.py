"""Tests for RuntimeOrchestrator - unified process orchestration.

This module tests the orchestration functionality introduced
in Phase 3 of the "Thin CLI + Core OO" refactoring.
"""

import sys
from pathlib import Path

# Add src/backend to path
sys.path.insert(0, str(Path(__file__).parents[2] / "src" / "backend"))

import pytest
import asyncio

from core.orchestration import (
    RuntimeOrchestrator,
    ServiceDefinition,
    ProcessLauncher,
    EventStream,
    OrchestrationEvent,
    EventType,
    EventLevel,
)
from application.dto.process_launch import ProcessLaunchRequest, ProcessLaunchResult, RunMode


class TestServiceDefinition:
    """Test ServiceDefinition."""

    def test_basic_creation(self):
        """Test creating a service definition."""
        definition = ServiceDefinition(
            name="pm",
            command=["python", "-m", "pm"],
            working_dir=Path("."),
        )

        assert definition.name == "pm"
        assert definition.command == ["python", "-m", "pm"]
        assert definition.run_mode == RunMode.SINGLE

    def test_to_launch_request(self):
        """Test conversion to launch request."""
        definition = ServiceDefinition(
            name="pm",
            command=["python", "-m", "pm"],
            working_dir=Path("."),
            env_vars={"KEY": "value"},
        )

        request = definition.to_launch_request()
        assert request.name == "pm"
        assert request.command == ["python", "-m", "pm"]
        assert request.env_vars == {"KEY": "value"}


class TestProcessLauncher:
    """Test ProcessLauncher."""

    def test_initialization(self):
        """Test launcher initialization."""
        launcher = ProcessLauncher()
        assert launcher is not None

    def test_build_utf8_env(self):
        """Test UTF-8 environment setup."""
        launcher = ProcessLauncher()
        env = launcher._build_utf8_env({"CUSTOM": "value"})

        assert env["PYTHONUTF8"] == "1"
        assert env["PYTHONIOENCODING"] == "utf-8"
        assert env["CUSTOM"] == "value"

    def test_launch_pm_request(self):
        """Test PM launch request builder."""
        launcher = ProcessLauncher()
        request = launcher.launch_pm(Path("."), RunMode.SINGLE)

        assert request.name == "pm"
        assert request.role == "pm"
        assert "--workspace" in request.command

    def test_launch_director_request(self):
        """Test Director launch request builder."""
        launcher = ProcessLauncher()
        request = launcher.launch_director(Path("."), RunMode.ONE_SHOT, iterations=3)

        assert request.name == "director"
        assert request.role == "director"
        assert "--iterations" in request.command
        assert "3" in request.command


class TestEventStream:
    """Test EventStream."""

    def test_initialization(self):
        """Test event stream initialization."""
        stream = EventStream()
        assert stream is not None

    def test_subscribe_and_publish(self):
        """Test event subscription and publishing."""
        stream = EventStream()
        events = []

        def callback(event):
            events.append(event)

        stream.subscribe(callback)

        event = OrchestrationEvent(
            event_type=EventType.SPAWNED,
            source="test",
            payload={"test": True},
        )
        stream.publish(event)

        assert len(events) == 1
        assert events[0].source == "test"

    def test_get_events(self):
        """Test event retrieval."""
        stream = EventStream()

        stream.publish(OrchestrationEvent(source="pm"))
        stream.publish(OrchestrationEvent(source="director"))

        events = stream.get_events(source="pm")
        assert len(events) == 1
        assert events[0].source == "pm"

    def test_event_to_dict(self):
        """Test event serialization."""
        event = OrchestrationEvent(
            event_type=EventType.SPAWNED,
            source="pm",
            level=EventLevel.INFO,
            process_id="test123",
            payload={"pid": 12345},
        )

        data = event.to_dict()
        assert data["source"] == "pm"
        assert data["event_type"] == "spawned"
        assert data["payload"]["pid"] == 12345


class TestOrchestrationEvent:
    """Test OrchestrationEvent factory methods."""

    def test_spawned_event(self):
        """Test spawned event factory."""
        event = OrchestrationEvent.spawned(
            source="pm",
            process_id="test123",
            pid=12345,
            command=["python", "-m", "pm"],
        )

        assert event.event_type == EventType.SPAWNED
        assert event.source == "pm"
        assert event.pid == 12345

    def test_completed_event(self):
        """Test completed event factory."""
        event = OrchestrationEvent.completed(
            source="director",
            process_id="test456",
            pid=12346,
            exit_code=0,
            duration_ms=5000,
        )

        assert event.event_type == EventType.COMPLETED
        assert event.payload["exit_code"] == 0
        assert event.payload["duration_ms"] == 5000

    def test_failed_event(self):
        """Test failed event factory."""
        event = OrchestrationEvent.failed(
            source="pm",
            process_id="test789",
            error="Process crashed",
        )

        assert event.event_type == EventType.FAILED
        assert event.level == EventLevel.ERROR
        assert event.payload["error"] == "Process crashed"


class TestRuntimeOrchestratorBasics:
    """Test RuntimeOrchestrator basic functionality."""

    def test_initialization(self):
        """Test orchestrator initialization."""
        orchestrator = RuntimeOrchestrator()
        assert orchestrator is not None
        assert len(orchestrator.list_active()) == 0

    @pytest.mark.asyncio
    async def test_service_lifecycle(self):
        """Test full service lifecycle."""
        orchestrator = RuntimeOrchestrator()

        # Create simple service definition
        definition = ServiceDefinition(
            name="test_service",
            command=[sys.executable, "-c", "print('hello')"],
            working_dir=Path.cwd(),
            run_mode=RunMode.SINGLE,
        )

        # Submit service
        handle = await orchestrator.submit(definition)
        assert handle.id.startswith("test_service_")
        assert handle.definition.name == "test_service"

        # Wait for completion
        completed_handle = await orchestrator.wait_for_completion(handle, timeout=10.0)

        # Service should have completed
        assert completed_handle.is_completed

    def test_list_active_and_all(self):
        """Test listing services."""
        orchestrator = RuntimeOrchestrator()

        # Initially empty
        assert len(orchestrator.list_active()) == 0
        assert len(orchestrator.list_all()) == 0


class TestIntegration:
    """Integration tests."""

    @pytest.mark.asyncio
    async def test_pm_launch_request(self):
        """Test PM launch request generation."""
        orchestrator = RuntimeOrchestrator()

        # This just tests the request generation, not actual launch
        launcher = ProcessLauncher()
        request = launcher.launch_pm(Path("."), RunMode.SINGLE, iterations=1)

        assert request.name == "pm"
        assert request.role == "pm"
        assert "cli.py" in request.command[-1] or "pm" in request.command

    @pytest.mark.asyncio
    async def test_director_launch_request(self):
        """Test Director launch request generation."""
        launcher = ProcessLauncher()
        request = launcher.launch_director(Path("."), RunMode.ONE_SHOT, iterations=2)

        assert request.name == "director"
        assert request.role == "director"
        assert "--iterations" in request.command


if __name__ == "__main__":
    # Run tests without pytest
    print("Running Orchestration tests...")

    # ServiceDefinition tests
    test = TestServiceDefinition()
    test.test_basic_creation()
    test.test_to_launch_request()
    print("  ✓ ServiceDefinition tests passed")

    # ProcessLauncher tests
    test = TestProcessLauncher()
    test.test_initialization()
    test.test_build_utf8_env()
    test.test_launch_pm_request()
    test.test_launch_director_request()
    print("  ✓ ProcessLauncher tests passed")

    # EventStream tests
    test = TestEventStream()
    test.test_initialization()
    test.test_subscribe_and_publish()
    test.test_get_events()
    test.test_event_to_dict()
    print("  ✓ EventStream tests passed")

    # OrchestrationEvent tests
    test = TestOrchestrationEvent()
    test.test_spawned_event()
    test.test_completed_event()
    test.test_failed_event()
    print("  ✓ OrchestrationEvent tests passed")

    # RuntimeOrchestrator tests
    test = TestRuntimeOrchestratorBasics()
    test.test_initialization()
    print("  ✓ RuntimeOrchestrator basic tests passed")

    print("\n✅ All tests passed!")
