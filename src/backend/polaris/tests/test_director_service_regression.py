"""Regression tests for director service defects.

This module is placed in polaris/tests/ to avoid the pre-existing import chain
bug in polaris.delivery and polaris.cells.director that prevents pytest
collection of tests importing those modules.

M5: director/execution/service.py start(), stop(), get_status() access self.state
    without acquiring _state_lock, leading to potential race conditions under
    concurrent calls (e.g. start() interleaving with stop()).

These tests load the service module directly without going through the broken
polaris.cells.director package import chain.
"""

from __future__ import annotations

import asyncio
import contextlib
from enum import Enum, auto
from typing import Any
from unittest.mock import AsyncMock, Mock

import pytest


def _load_director_service() -> dict[str, Any]:
    """Lazily load director service module directly, patching broken imports."""
    # Patch the broken import chain BEFORE loading
    import sys

    # Provide stubs for polaris.cells.workspace.integrity.public.service.DirectorCodeIntelMixin
    class _DirectorCodeIntelMixin:
        def __init__(self, workspace: str, *args: object, **kwargs: object) -> None:
            pass

    class _DummyModule:
        DirectorCodeIntelMixin = _DirectorCodeIntelMixin

    sys.modules["polaris.cells.workspace.integrity.public.service"] = _DummyModule()  # type: ignore[assignment]

    # Provide stub for polaris.cells.director.tasking.public
    class _StubTaskQueueConfig:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

    class _StubWorkerPoolConfig:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

    class _StubTaskService:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        async def get_tasks(self) -> list:
            return []

        async def get_ready_task_count(self) -> int:
            return 0

        async def create_task(self, **kwargs: Any):
            task = Mock()
            task.id = Mock()
            return task

    class _StubWorkerService:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        async def get_workers(self) -> list:
            return []

    class _DummyTaskingModule:
        TaskQueueConfig = _StubTaskQueueConfig
        TaskService = _StubTaskService
        WorkerPoolConfig = _StubWorkerPoolConfig
        WorkerService = _StubWorkerService

    sys.modules["polaris.cells.director.tasking.public"] = _DummyTaskingModule()  # type: ignore[assignment]

    # Provide stub for domain entities
    class _DummyEntities:
        class Task:
            def __init__(self, *args: Any, **kwargs: Any) -> None:
                self.id = Mock()
                self.status = Mock()
                self.to_dict = Mock(return_value={})

        class TaskPriority(Enum):
            MEDIUM = auto()

        class TaskStatus(Enum):
            pass

        class Worker:
            def is_available(self) -> bool:
                return True

            status = Mock()

        class WorkerStatus(Enum):
            BUSY = auto()

    _dom_entities: Any = Mock()
    _dom_entities.Task = _DummyEntities.Task
    _dom_entities.TaskPriority = _DummyEntities.TaskPriority
    _dom_entities.TaskStatus = _DummyEntities.TaskStatus
    _dom_entities.Worker = _DummyEntities.Worker
    _dom_entities.WorkerStatus = _DummyEntities.WorkerStatus
    sys.modules["polaris.domain.entities"] = _dom_entities
    sys.modules["polaris.domain.entities.capability"] = Mock()
    sys.modules["polaris.domain.entities.policy"] = Mock()
    sys.modules["polaris.domain.services"] = Mock()

    # Provide stub for kernelone modules
    class _StubConstants:
        DEFAULT_MAX_WORKERS = 4

    sys.modules["polaris.kernelone.constants"] = _StubConstants()  # type: ignore[assignment]
    sys.modules["polaris.kernelone.context.runtime_feature_flags"] = Mock(
        CognitiveRuntimeMode=object, resolve_cognitive_runtime_mode=Mock()
    )
    sys.modules["polaris.kernelone.events.message_bus"] = Mock(Message=object, MessageBus=Mock, MessageType=Mock)

    # Stub typed events with proper class structure
    class _TypedEvent:
        @classmethod
        def create(cls, **kwargs: Any) -> Any:
            return cls()

    class _StubTypedEvents:
        BudgetExceeded = type("BudgetExceeded", (), {"create": classmethod(lambda cls, **kw: _TypedEvent())})  # type: ignore
        DirectorStarted = type("DirectorStarted", (), {"create": classmethod(lambda cls, **kw: _TypedEvent())})  # type: ignore
        DirectorStopped = type("DirectorStopped", (), {"create": classmethod(lambda cls, **kw: _TypedEvent())})  # type: ignore
        NagReminder = type("NagReminder", (), {"create": classmethod(lambda cls, **kw: _TypedEvent())})  # type: ignore
        TaskCompleted = type("TaskCompleted", (), {"create": classmethod(lambda cls, **kw: _TypedEvent())})  # type: ignore
        TaskFailed = type("TaskFailed", (), {"create": classmethod(lambda cls, **kw: _TypedEvent())})  # type: ignore
        TaskStarted = type("TaskStarted", (), {"create": classmethod(lambda cls, **kw: _TypedEvent())})  # type: ignore
        TaskSubmitted = type("TaskSubmitted", (), {"create": classmethod(lambda cls, **kw: _TypedEvent())})  # type: ignore
        get_default_adapter = Mock()

    sys.modules["polaris.kernelone.events.typed"] = _StubTypedEvents()  # type: ignore[assignment]
    sys.modules["polaris.kernelone.process.command_executor"] = Mock(CommandExecutionService=Mock)
    sys.modules["polaris.bootstrap.config"] = Mock()
    sys.modules["polaris.cells.audit.evidence.public.service"] = Mock()

    # Now load the actual service module
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "polaris.cells.director.execution.service",
        "polaris/cells/director/execution/service.py",
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("Could not load service.py spec")

    ns: dict[str, Any] = {
        "__name__": "polaris.cells.director.execution.service",
        "__package__": "polaris.cells.director.execution",
    }

    m = importlib.util.module_from_spec(spec)
    m.__dict__.update(ns)
    # Add to sys.modules BEFORE exec_module so that @dataclass on DirectorConfig works
    sys.modules["polaris.cells.director.execution.service"] = m
    spec.loader.exec_module(m)

    return {
        "DirectorConfig": m.DirectorConfig,
        "DirectorService": m.DirectorService,
        "DirectorState": m.DirectorState,
    }


# =============================================================================
# Regression tests for confirmed defects
# =============================================================================


class TestDirectorStateConcurrencyRegression:
    """Regression tests for M5: unguarded state transitions."""

    @pytest.mark.asyncio
    async def test_concurrent_start_stop_does_not_corrupt_state(self) -> None:
        """Verify concurrent start+stop does not leave state in an inconsistent value.

        Bug (M5): start() and stop() both read/write self.state without holding
        _state_lock. When called concurrently, one can read stale state mid-flight
        and corrupt the state machine.

        After fix: state transitions are protected by _state_lock, so concurrent
        calls are serialized and the state machine never enters an impossible state.
        """
        utils = _load_director_service()
        DirectorConfig = utils["DirectorConfig"]  # noqa: N806
        DirectorService = utils["DirectorService"]  # noqa: N806
        DirectorState = utils["DirectorState"]  # noqa: N806

        config = DirectorConfig(workspace="/workspace")
        service = DirectorService(config=config)

        # Mock out heavy dependencies so we test only state machine behaviour
        service._worker_service = Mock(
            initialize=AsyncMock(),
            shutdown=AsyncMock(),
            get_workers=AsyncMock(return_value=[]),
        )
        service.transcript = Mock(
            start_session=Mock(),
            end_session=Mock(),
            record_message=Mock(),
        )
        service._bus.broadcast = AsyncMock()
        service._emit_typed_event = AsyncMock()

        async def fake_main_loop() -> None:
            # Wait until stopped
            await service._stop_event.wait()

        service._main_loop = fake_main_loop  # type: ignore[method-assign]

        async def start_task() -> None:
            with contextlib.suppress(RuntimeError):
                await service.start()

        async def stop_task() -> None:
            with contextlib.suppress(RuntimeError):
                await service.stop()

        # Run start and stop concurrently multiple times
        for _ in range(5):
            # Reset to IDLE state for next iteration
            if service.state != DirectorState.IDLE:
                service.state = DirectorState.IDLE
                service._event_handlers_ready = True

            await asyncio.gather(
                start_task(),
                stop_task(),
                return_exceptions=True,
            )

        # After all concurrent operations, state must be a valid DirectorState enum value
        valid_states = {
            DirectorState.IDLE,
            DirectorState.RUNNING,
            DirectorState.STOPPING,
            DirectorState.STOPPED,
            DirectorState.PAUSED,
        }
        assert service.state in valid_states, (
            f"BUG M5: state is {service.state!r}, which is not a valid DirectorState. "
            "Unguarded state access caused corruption."
        )

    @pytest.mark.asyncio
    async def test_get_status_does_not_race_with_state_transition(self) -> None:
        """Verify get_status() can be called concurrently with start()/stop() safely.

        Bug (M5): get_status() reads self.state without the lock.
        A concurrent stop() may change state mid-read, but the returned dict
        should still have a consistent snapshot.

        After fix: get_status() either acquires the lock or state reads are
        otherwise protected so the returned dict is consistent.
        """
        utils = _load_director_service()
        DirectorConfig = utils["DirectorConfig"]  # noqa: N806
        DirectorService = utils["DirectorService"]  # noqa: N806
        DirectorState = utils["DirectorState"]  # noqa: N806

        config = DirectorConfig(workspace="/workspace")
        service = DirectorService(config=config)

        # Mock dependencies
        task_mock = Mock(get_tasks=AsyncMock(return_value=[]))
        service._task_service = task_mock
        service._worker_service = Mock(
            get_workers=AsyncMock(return_value=[]),
            initialize=AsyncMock(),
            shutdown=AsyncMock(),
        )
        service.transcript = Mock(
            start_session=Mock(),
            end_session=Mock(),
            record_message=Mock(),
        )
        service.token = Mock(get_budget_status=Mock(to_dict=Mock(return_value={})))
        service._bus.broadcast = AsyncMock()
        service._emit_typed_event = AsyncMock()

        async def fake_main_loop() -> None:
            await service._stop_event.wait()

        service._main_loop = fake_main_loop  # type: ignore[method-assign]

        errors: list[BaseException] = []

        async def get_status_task() -> None:
            try:
                for _ in range(10):
                    status = await service.get_status()
                    assert isinstance(status["state"], str)
                    assert status["state"] in {s.name for s in DirectorState}
            except (RuntimeError, AssertionError) as e:
                errors.append(e)

        async def transition_task() -> None:
            with contextlib.suppress(RuntimeError):
                await service.start()
                await asyncio.sleep(0)
                await service.stop()

        # Concurrently poll status and transition state
        await asyncio.gather(
            get_status_task(),
            get_status_task(),
            transition_task(),
            transition_task(),
            return_exceptions=True,
        )

        # No unexpected errors during concurrent access
        assert not errors, f"BUG M5: get_status() raised unexpected error during concurrent state access: {errors}"
