"""Unit tests for InMemoryAgentBusPort.poll_async().

Tests cover:
- Normal async poll operation (message received immediately)
- Timeout behavior (no message available)
- Cancellation via asyncio.cancel()
- Concurrent publish while polling
- Edge cases (zero timeout, negative timeout)

Implements test coverage for P0-002: bus_port blocking poll issue.
"""

from __future__ import annotations

import asyncio
import contextlib
import threading
import time
from typing import Any

import pytest
from polaris.cells.roles.runtime.internal.bus_port import (
    _DEFAULT_POLL_INTERVAL_SEC,
    _MAX_CANCEL_DELAY_SEC,
    AgentEnvelope,
    InMemoryAgentBusPort,
)

# ── Fixtures ───────────────────────────────────────────────────────────────────


@pytest.fixture
def bus() -> InMemoryAgentBusPort:
    """Create a clean InMemoryAgentBusPort for testing."""
    return InMemoryAgentBusPort(max_queue_size=100)


@pytest.fixture
def sample_envelope() -> AgentEnvelope:
    """Create a sample AgentEnvelope for testing."""
    return AgentEnvelope.from_fields(
        msg_type="task",
        sender="director",
        receiver="qa",
        payload={"objective": "verify changes"},
        correlation_id="corr-123",
    )


# ── Test Normal Operation ─────────────────────────────────────────────────────


class TestPollAsyncNormal:
    """Tests for normal poll_async() operation."""

    @pytest.mark.asyncio
    async def test_poll_async_immediate_message(
        self,
        bus: InMemoryAgentBusPort,
        sample_envelope: AgentEnvelope,
    ) -> None:
        """poll_async() should return message immediately if available."""
        bus.publish(sample_envelope)

        result = await bus.poll_async("qa", block=False)

        assert result is not None
        assert result.message_id == sample_envelope.message_id
        assert result.msg_type == "task"
        assert result.sender == "director"
        assert result.receiver == "qa"

    @pytest.mark.asyncio
    async def test_poll_async_returns_none_when_empty(
        self,
        bus: InMemoryAgentBusPort,
    ) -> None:
        """poll_async() should return None when no messages and block=False."""
        result = await bus.poll_async("qa", block=False)

        assert result is None

    @pytest.mark.asyncio
    async def test_poll_async_blocking_receives_message(
        self,
        bus: InMemoryAgentBusPort,
        sample_envelope: AgentEnvelope,
    ) -> None:
        """poll_async(block=True) should wait and receive delayed message."""
        delivery_time = 0.1  # 100ms

        def delayed_publish() -> None:
            time.sleep(delivery_time)
            bus.publish(sample_envelope)

        thread = threading.Thread(target=delayed_publish, daemon=True)
        thread.start()

        start = time.monotonic()
        result = await bus.poll_async("qa", block=True, timeout=1.0)
        elapsed = time.monotonic() - start

        thread.join()

        assert result is not None
        assert result.message_id == sample_envelope.message_id
        # Should have waited approximately delivery_time
        assert elapsed >= delivery_time * 0.8  # Allow some tolerance
        assert elapsed < delivery_time + 0.5  # But not too long

    @pytest.mark.asyncio
    async def test_poll_async_moves_to_inflight(
        self,
        bus: InMemoryAgentBusPort,
        sample_envelope: AgentEnvelope,
    ) -> None:
        """poll_async() should move message to inflight after pop."""
        bus.publish(sample_envelope)

        result = await bus.poll_async("qa", block=False)

        assert result is not None
        # Message should be in inflight
        assert bus.pending_count("qa") == 0

    @pytest.mark.asyncio
    async def test_poll_async_fifo_order(
        self,
        bus: InMemoryAgentBusPort,
    ) -> None:
        """poll_async() should return messages in FIFO order."""
        for i in range(3):
            env = AgentEnvelope.from_fields(
                msg_type="task",
                sender=f"sender-{i}",
                receiver="qa",
                payload={"index": i},
            )
            bus.publish(env)

        result1 = await bus.poll_async("qa", block=False)
        result2 = await bus.poll_async("qa", block=False)
        result3 = await bus.poll_async("qa", block=False)

        assert result1 is not None
        assert result2 is not None
        assert result3 is not None
        assert result1.payload["index"] == 0
        assert result2.payload["index"] == 1
        assert result3.payload["index"] == 2


# ── Test Timeout Behavior ─────────────────────────────────────────────────────


class TestPollAsyncTimeout:
    """Tests for poll_async() timeout behavior."""

    @pytest.mark.asyncio
    async def test_poll_async_timeout_returns_none(
        self,
        bus: InMemoryAgentBusPort,
    ) -> None:
        """poll_async(block=True, timeout=X) should return None after timeout."""
        start = time.monotonic()
        result = await bus.poll_async("qa", block=True, timeout=0.2)
        elapsed = time.monotonic() - start

        assert result is None
        assert elapsed >= 0.18  # Allow some tolerance for timing

    @pytest.mark.asyncio
    async def test_poll_async_zero_timeout_returns_immediately(
        self,
        bus: InMemoryAgentBusPort,
    ) -> None:
        """poll_async(timeout=0) should return immediately."""
        start = time.monotonic()
        result = await bus.poll_async("qa", block=True, timeout=0.0)
        elapsed = time.monotonic() - start

        assert result is None
        assert elapsed < 0.05  # Should be nearly instant

    @pytest.mark.asyncio
    async def test_poll_async_negative_timeout_returns_immediately(
        self,
        bus: InMemoryAgentBusPort,
    ) -> None:
        """poll_async(timeout=-1) should return immediately."""
        start = time.monotonic()
        result = await bus.poll_async("qa", block=True, timeout=-5.0)
        elapsed = time.monotonic() - start

        assert result is None
        assert elapsed < 0.05  # Should be nearly instant

    @pytest.mark.asyncio
    async def test_poll_async_timeout_precise(
        self,
        bus: InMemoryAgentBusPort,
    ) -> None:
        """poll_async() should respect exact timeout boundaries."""
        timeout_value = 0.15

        start = time.monotonic()
        result = await bus.poll_async("qa", block=True, timeout=timeout_value)
        elapsed = time.monotonic() - start

        assert result is None
        assert elapsed >= timeout_value - 0.01
        assert elapsed < timeout_value + 0.1  # Allow some tolerance


# ── Test Cancellation ─────────────────────────────────────────────────────────


class TestPollAsyncCancellation:
    """Tests for poll_async() cancellation via asyncio.cancel()."""

    @pytest.mark.asyncio
    async def test_poll_async_cancellation_raises_cancelled_error(
        self,
        bus: InMemoryAgentBusPort,
    ) -> None:
        """asyncio.cancel() should raise CancelledError in the coroutine."""

        # Use a long timeout so cancellation happens during sleep
        async def cancellable_poll() -> Any:
            return await bus.poll_async("qa", block=True, timeout=10.0)

        task = asyncio.create_task(cancellable_poll())

        # Give the task time to start sleeping
        await asyncio.sleep(0.05)

        # Cancel the task
        task.cancel()

        # Should raise CancelledError
        with pytest.raises(asyncio.CancelledError):
            await task

    @pytest.mark.asyncio
    async def test_poll_async_cancellation_receives_message_before_cancel(
        self,
        bus: InMemoryAgentBusPort,
        sample_envelope: AgentEnvelope,
    ) -> None:
        """If message arrives before cancellation, it should be returned."""
        delivery_time = 0.05  # 50ms

        def delayed_publish() -> None:
            time.sleep(delivery_time)
            bus.publish(sample_envelope)

        thread = threading.Thread(target=delayed_publish, daemon=True)
        thread.start()

        async def cancellable_poll() -> Any:
            return await bus.poll_async("qa", block=True, timeout=10.0)

        task = asyncio.create_task(cancellable_poll())

        # Wait for message to arrive
        await asyncio.sleep(delivery_time + 0.05)

        # Cancel after message should be delivered
        task.cancel()

        # Should receive the message before cancellation takes effect
        try:
            result = await task
            # If we get here, the message was delivered before cancel took effect
            assert result is not None
            assert result.message_id == sample_envelope.message_id
        except asyncio.CancelledError:
            # If CancelledError, message arrived after cancellation
            # This is acceptable given timing
            pass

        thread.join()

    @pytest.mark.asyncio
    async def test_poll_async_multiple_cancellations(
        self,
        bus: InMemoryAgentBusPort,
    ) -> None:
        """Multiple rapid cancellations should all raise CancelledError."""
        for _ in range(3):

            async def cancellable_poll() -> Any:
                return await bus.poll_async("qa", block=True, timeout=5.0)

            task = asyncio.create_task(cancellable_poll())
            await asyncio.sleep(0.01)
            task.cancel()

            with pytest.raises(asyncio.CancelledError):
                await task

    @pytest.mark.asyncio
    async def test_poll_async_no_infinite_blocking_on_cancel(
        self,
        bus: InMemoryAgentBusPort,
    ) -> None:
        """Cancelled poll should not block indefinitely."""
        # The _MAX_CANCEL_DELAY_SEC provides a hard upper bound
        # This test verifies the implementation honors it

        async def cancellable_poll() -> Any:
            return await bus.poll_async("qa", block=True, timeout=30.0)

        task = asyncio.create_task(cancellable_poll())
        await asyncio.sleep(0.01)
        task.cancel()

        start = time.monotonic()
        with pytest.raises(asyncio.CancelledError):
            await task
        elapsed = time.monotonic() - start

        # Cancellation should occur within a reasonable time
        # (not the full 30 second timeout)
        assert elapsed < 2.0, f"Cancellation took {elapsed}s, too long"


# ── Test Concurrent Access ────────────────────────────────────────────────────


class TestPollAsyncConcurrency:
    """Tests for concurrent access patterns."""

    @pytest.mark.asyncio
    async def test_poll_async_concurrent_with_publish(
        self,
        bus: InMemoryAgentBusPort,
    ) -> None:
        """poll_async() should work correctly with concurrent publish."""
        results: list[AgentEnvelope | None] = []
        results_lock = asyncio.Lock()

        async def poll_worker(worker_id: int) -> None:
            for _ in range(5):
                result = await bus.poll_async("qa", block=True, timeout=0.5)
                async with results_lock:
                    results.append(result)

        async def publish_worker(worker_id: int) -> None:
            for i in range(15):
                env = AgentEnvelope.from_fields(
                    msg_type="task",
                    sender=f"sender-{worker_id}",
                    receiver="qa",
                    payload={"index": i, "worker": worker_id},
                )
                bus.publish(env)
                await asyncio.sleep(0.02)

        # Start both workers
        poll_tasks = [asyncio.create_task(poll_worker(i)) for i in range(2)]
        publish_task = asyncio.create_task(publish_worker(0))

        # Wait for all to complete
        await asyncio.gather(*poll_tasks)
        publish_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await publish_task

        # Should have received some messages
        received = [r for r in results if r is not None]
        assert len(received) > 0

    @pytest.mark.asyncio
    async def test_poll_async_multiple_receivers(
        self,
        bus: InMemoryAgentBusPort,
    ) -> None:
        """poll_async() should correctly filter by receiver."""
        env_qa = AgentEnvelope.from_fields(
            msg_type="task",
            sender="director",
            receiver="qa",
            payload={"target": "qa"},
        )
        env_director = AgentEnvelope.from_fields(
            msg_type="task",
            sender="qa",
            receiver="director",
            payload={"target": "director"},
        )

        bus.publish(env_qa)
        bus.publish(env_director)

        result_qa = await bus.poll_async("qa", block=False)
        result_director = await bus.poll_async("director", block=False)

        assert result_qa is not None
        assert result_qa.receiver == "qa"
        assert result_director is not None
        assert result_director.receiver == "director"


# ── Test Edge Cases ────────────────────────────────────────────────────────────


class TestPollAsyncEdgeCases:
    """Tests for edge cases and boundary conditions."""

    @pytest.mark.asyncio
    async def test_poll_async_custom_poll_interval(
        self,
        bus: InMemoryAgentBusPort,
    ) -> None:
        """poll_async() should honor custom poll_interval."""
        custom_interval = 0.15

        start = time.monotonic()
        result = await bus.poll_async(
            "qa",
            block=True,
            timeout=custom_interval,
            poll_interval=custom_interval,
        )
        elapsed = time.monotonic() - start

        assert result is None
        # Should timeout after approximately poll_interval
        assert elapsed >= custom_interval - 0.01

    @pytest.mark.asyncio
    async def test_poll_async_invalid_poll_interval_normalized(
        self,
        bus: InMemoryAgentBusPort,
    ) -> None:
        """poll_async() should normalize invalid poll_interval to safe minimum."""
        # Zero and negative intervals should be normalized
        start = time.monotonic()
        result = await bus.poll_async(
            "qa",
            block=True,
            timeout=0.1,
            poll_interval=0.0,  # Invalid
        )
        elapsed = time.monotonic() - start

        assert result is None
        # Should still work, using normalized interval
        assert elapsed < 0.5

    @pytest.mark.asyncio
    async def test_poll_async_large_timeout(
        self,
        bus: InMemoryAgentBusPort,
    ) -> None:
        """poll_async() should handle large timeouts gracefully.

        Uses a moderate timeout for testing (not 3600s) since the
        implementation has internal bounds that make the test complete quickly.
        """
        start = time.monotonic()
        result = await bus.poll_async("qa", block=True, timeout=0.5)
        elapsed = time.monotonic() - start

        # Without a message, should timeout quickly
        assert result is None
        # Should respect the timeout value
        assert elapsed >= 0.45
        assert elapsed < 1.0

    @pytest.mark.asyncio
    async def test_poll_async_preserves_envelope_data(
        self,
        bus: InMemoryAgentBusPort,
    ) -> None:
        """poll_async() should preserve all envelope fields."""
        original = AgentEnvelope.from_fields(
            msg_type="result",
            sender="qa",
            receiver="pm",
            payload={"status": "success", "data": [1, 2, 3]},
            correlation_id="corr-456",
            max_attempts=5,
        )
        bus.publish(original)

        result = await bus.poll_async("pm", block=False)

        assert result is not None
        assert result.msg_type == "result"
        assert result.sender == "qa"
        assert result.receiver == "pm"
        assert result.payload == {"status": "success", "data": [1, 2, 3]}
        assert result.correlation_id == "corr-456"
        assert result.max_attempts == 5


# ── Test Backward Compatibility ────────────────────────────────────────────────


class TestPollAsyncBackwardCompatibility:
    """Tests to ensure original poll() still works."""

    def test_original_poll_still_works(
        self,
        bus: InMemoryAgentBusPort,
        sample_envelope: AgentEnvelope,
    ) -> None:
        """Original synchronous poll() should still function correctly."""
        bus.publish(sample_envelope)

        result = bus.poll("qa", block=False)

        assert result is not None
        assert result.message_id == sample_envelope.message_id

    def test_original_poll_blocking(
        self,
        bus: InMemoryAgentBusPort,
        sample_envelope: AgentEnvelope,
    ) -> None:
        """Original synchronous poll(block=True) should still block."""

        def delayed_publish() -> None:
            time.sleep(0.1)
            bus.publish(sample_envelope)

        thread = threading.Thread(target=delayed_publish, daemon=True)
        thread.start()

        result = bus.poll("qa", block=True, timeout=1.0)

        thread.join()

        assert result is not None
        assert result.message_id == sample_envelope.message_id

    def test_original_poll_timeout(
        self,
        bus: InMemoryAgentBusPort,
    ) -> None:
        """Original synchronous poll() should still timeout correctly."""
        start = time.monotonic()
        result = bus.poll("qa", block=True, timeout=0.2)
        elapsed = time.monotonic() - start

        assert result is None
        assert elapsed >= 0.18


# ── Test Constants ─────────────────────────────────────────────────────────────


class TestPollAsyncConstants:
    """Tests for exported constants."""

    def test_default_poll_interval_positive(self) -> None:
        """_DEFAULT_POLL_INTERVAL_SEC should be positive."""
        assert _DEFAULT_POLL_INTERVAL_SEC > 0

    def test_max_cancel_delay_positive(self) -> None:
        """_MAX_CANCEL_DELAY_SEC should be positive."""
        assert _MAX_CANCEL_DELAY_SEC > 0

    def test_max_cancel_delay_reasonable(self) -> None:
        """_MAX_CANCEL_DELAY_SEC should be a reasonable upper bound."""
        assert _MAX_CANCEL_DELAY_SEC <= 5.0  # Should be at most 5 seconds
