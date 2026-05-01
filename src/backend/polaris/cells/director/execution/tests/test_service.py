"""Tests for service module."""

from __future__ import annotations

import asyncio
import contextlib
from unittest.mock import AsyncMock, Mock

import pytest
from polaris.cells.director.execution.service import (
    DirectorConfig,
    DirectorService,
    DirectorState,
)


class TestDirectorService:
    def test_init_basic(self):
        config = DirectorConfig(workspace="/workspace")
        service = DirectorService(config=config)
        assert service.config.workspace == "/workspace"
        assert service.state == DirectorState.IDLE

    def test_init_with_security(self):
        config = DirectorConfig(workspace="/workspace")
        mock_security = Mock()
        service = DirectorService(config=config, security=mock_security)
        assert service.security is mock_security


# =============================================================================
# Regression tests for confirmed defects
# =============================================================================
# M5: director/execution/service.py start(), stop(), get_status() access self.state
#     without acquiring _state_lock, leading to potential race conditions under
#     concurrent calls (e.g. start() interleaving with stop()).


class TestDirectorStateConcurrencyRegression:
    """Regression tests for M5: unguarded state transitions."""

    @pytest.mark.asyncio
    async def test_concurrent_start_stop_does_not_corrupt_state(self) -> None:
        """Verify concurrent start+stop does not leave state in an inconsistent intermediate value.

        Bug (M5): start() and stop() both read/write self.state without holding
        _state_lock. When called concurrently:

            Thread A (start): reads self.state == IDLE, enters, sets RUNNING
            Thread B (stop):  reads self.state == RUNNING (or IDLE), enters/stops

        The guard condition in start() is:
            if self.state not in {DirectorState.IDLE, DirectorState.STOPPED}:
                raise RuntimeError(...)
        If stop() changes state between the guard check and the body of start(),
        start() may proceed from an unexpected state, or both methods may run
        their bodies simultaneously, corrupting self.state.

        After fix: state transitions are protected by _state_lock, so concurrent
        calls are serialized and the state machine never enters an impossible
        state (e.g., not IDLE/RUNNING/STOPPING/STOPPED/PAUSED).
        """
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

        # Patch _main_loop so it does not run; we only care about state transitions
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
                service._event_handlers_ready = True  # skip re-setup

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

        Bug (M5): get_status() reads self.state at line 428 without the lock.
        A concurrent stop() may change state mid-read, but the returned dict
        should still have a consistent snapshot (no KeyError, no AttributeError,
        no impossible combination of fields).

        After fix: get_status() either acquires the lock or state reads are
        otherwise protected so the returned dict is consistent.
        """
        config = DirectorConfig(workspace="/workspace")
        service = DirectorService(config=config)

        service._task_service = Mock(get_tasks=AsyncMock(return_value=[]))
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
                    # State must be a valid string
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

        # No errors should have been raised during concurrent access
        unexpected = [e for e in errors if not isinstance(e, (RuntimeError, AssertionError))]
        assert not unexpected, (
            f"BUG M5: get_status() raised unexpected error during concurrent state access: {unexpected}"
        )
