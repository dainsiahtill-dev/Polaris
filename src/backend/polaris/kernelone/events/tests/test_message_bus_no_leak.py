"""T7: message_bus asyncio task leak regression tests.

Verifies that:
1. When async handlers time out, all spawned tasks are cancelled (no leak).
2. After cancellation + gather, the bus returns cleanly (no exception propagation).
3. A warning is logged when handlers time out (observability).
"""

from __future__ import annotations

import asyncio
import logging
from unittest.mock import patch

import pytest


@pytest.mark.asyncio
async def test_message_bus_timeout_no_task_leak() -> None:
    """On handler timeout, all spawned asyncio tasks must be cancelled (no leak)."""
    from polaris.kernelone.events.message_bus import Message, MessageBus, MessageType

    bus = MessageBus()
    task_refs: list[asyncio.Task] = []
    completion_event = asyncio.Event()

    async def slow_handler(msg: Message) -> None:
        t = asyncio.current_task()
        if t is not None:
            task_refs.append(t)
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            completion_event.set()
            raise

    await bus.subscribe(MessageType.TASK_SUBMITTED, slow_handler)

    with patch(
        "polaris.kernelone.events.message_bus._ASYNC_HANDLER_TIMEOUT_SECONDS",
        0.05,
    ):
        msg = Message(type=MessageType.TASK_SUBMITTED, sender="test")
        await bus.publish(msg)

    # Allow event loop to process cancellations
    await asyncio.sleep(0.15)

    # All tasks spawned by the slow handler must be done (cancelled or otherwise)
    leaked = [t for t in task_refs if not t.done()]
    assert not leaked, (
        f"Leaked {len(leaked)} asyncio task(s) after timeout. "
        "message_bus must cancel + gather handler tasks on timeout."
    )


@pytest.mark.asyncio
async def test_message_bus_timeout_logs_warning(caplog) -> None:
    """Handler timeout must emit a WARNING-level log (observability guard)."""
    from polaris.kernelone.events.message_bus import Message, MessageBus, MessageType

    bus = MessageBus()

    async def never_finishes(msg: Message) -> None:
        await asyncio.sleep(60)

    await bus.subscribe(MessageType.TASK_SUBMITTED, never_finishes)

    with (
        patch(
            "polaris.kernelone.events.message_bus._ASYNC_HANDLER_TIMEOUT_SECONDS",
            0.05,
        ),
        caplog.at_level(logging.WARNING, logger="polaris.kernelone.events.message_bus"),
    ):
        msg = Message(type=MessageType.TASK_SUBMITTED, sender="test-warn")
        await bus.publish(msg)

    await asyncio.sleep(0.15)

    warning_records = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert warning_records, "Expected WARNING when async handlers time out, but no warning was logged"
    assert any("timed out" in r.message.lower() or "timeout" in r.message.lower() for r in warning_records), (
        "WARNING message must mention 'timed out' or 'timeout'"
    )


@pytest.mark.asyncio
async def test_message_bus_timeout_publish_does_not_raise() -> None:
    """publish() must return cleanly after handler timeout (no exception propagation)."""
    from polaris.kernelone.events.message_bus import Message, MessageBus, MessageType

    bus = MessageBus()

    async def blocks_forever(msg: Message) -> None:
        await asyncio.sleep(60)

    await bus.subscribe(MessageType.TASK_SUBMITTED, blocks_forever)

    with patch(
        "polaris.kernelone.events.message_bus._ASYNC_HANDLER_TIMEOUT_SECONDS",
        0.05,
    ):
        # Must not raise TimeoutError or any other exception
        msg = Message(type=MessageType.TASK_SUBMITTED, sender="test-noerr")
        await bus.publish(msg)  # This must return, not raise

    await asyncio.sleep(0.15)


@pytest.mark.asyncio
async def test_message_bus_normal_handler_completes_without_leak() -> None:
    """Fast handlers that complete within timeout must not leave leaked tasks."""
    from polaris.kernelone.events.message_bus import Message, MessageBus, MessageType

    bus = MessageBus()
    received: list[str] = []

    async def fast_handler(msg: Message) -> None:
        received.append(msg.sender)

    await bus.subscribe(MessageType.TASK_SUBMITTED, fast_handler)

    msg = Message(type=MessageType.TASK_SUBMITTED, sender="fast-sender")
    await bus.publish(msg)
    await asyncio.sleep(0.05)

    assert received == ["fast-sender"], "Fast handler must complete and record the message"


@pytest.mark.asyncio
async def test_message_bus_multiple_handlers_one_slow_no_leak() -> None:
    """With mixed fast/slow handlers, slow ones must be cancelled and fast ones still run."""
    from polaris.kernelone.events.message_bus import Message, MessageBus, MessageType

    bus = MessageBus()
    fast_received: list[str] = []
    slow_task_refs: list[asyncio.Task] = []

    async def fast_handler(msg: Message) -> None:
        fast_received.append("fast")

    async def slow_handler(msg: Message) -> None:
        t = asyncio.current_task()
        if t is not None:
            slow_task_refs.append(t)
        await asyncio.sleep(60)

    await bus.subscribe(MessageType.TASK_SUBMITTED, fast_handler)
    await bus.subscribe(MessageType.TASK_SUBMITTED, slow_handler)

    with patch(
        "polaris.kernelone.events.message_bus._ASYNC_HANDLER_TIMEOUT_SECONDS",
        0.05,
    ):
        msg = Message(type=MessageType.TASK_SUBMITTED, sender="mixed")
        await bus.publish(msg)

    await asyncio.sleep(0.15)

    leaked = [t for t in slow_task_refs if not t.done()]
    assert not leaked, f"Slow handler tasks leaked: {len(leaked)}"
