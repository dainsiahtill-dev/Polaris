"""Regression tests for SSE utils defects.

This module is placed in polaris/tests/ to avoid the pre-existing import chain
bug in polaris.delivery that prevents pytest collection of router tests.

M4: sse_jetstream_generator finally block shadowing original exception
    The disconnect() call in the finally block can raise its own exception,
    which in Python's async generator cleanup replaces the original one,
    making debugging harder and masking root-cause errors.

These tests load sse_utils.py directly without going through the polaris.delivery
package import chain.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncGenerator
from typing import Any

import pytest

# Load sse_utils directly from file without going through polaris.delivery import chain
_imported: dict[str, Any] = {}


def _load_sse_utils() -> dict[str, Any]:
    """Lazily load sse_utils module directly from source file."""
    if _imported:
        return _imported
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "polaris.delivery.http.routers.sse_utils",
        "polaris/delivery/http/routers/sse_utils.py",
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("Could not load sse_utils.py spec")

    # Inject required stdlib modules into the module's globals
    ns: dict[str, Any] = {
        "__name__": "polaris.delivery.http.routers.sse_utils",
        "__package__": "polaris.delivery.http.routers",
        "asyncio": asyncio,
        "json": json,
        "logging": logging,
        "contextlib": __import__("contextlib"),
    }

    # Copy annotations/type hints support
    ns["TYPE_CHECKING"] = False
    ns["Any"] = Any

    m = importlib.util.module_from_spec(spec)
    m.__dict__.update(ns)
    spec.loader.exec_module(m)

    _imported["SSEJetStreamConsumer"] = m.SSEJetStreamConsumer
    _imported["sse_jetstream_generator"] = m.sse_jetstream_generator
    _imported["sse_event_generator"] = m.sse_event_generator
    _imported["create_sse_response"] = m.create_sse_response
    _imported["create_sse_jetstream_consumer"] = m.create_sse_jetstream_consumer
    return _imported


# =============================================================================
# Regression tests for confirmed defects
# =============================================================================


class TestJetstreamGeneratorExceptionPreservation:
    """Regression tests for M4: exception shadowing in sse_jetstream_generator."""

    @pytest.mark.asyncio
    async def test_jetstream_stream_exception_not_shadowed_by_disconnect_error(self) -> None:
        """Verify the original stream exception is preserved when disconnect() also fails.

        Bug (M4): The finally block in sse_jetstream_generator is:
            finally:
                await consumer.disconnect()
        If consumer.disconnect() raises an exception (e.g. cleanup failure),
        it can shadow the original RuntimeError from the stream in Python's
        async generator cleanup, making debugging harder.

        After fix: disconnect errors should be caught and logged, preserving
        the original stream exception as the propagated error.
        """
        utils = _load_sse_utils()
        SSEJetStreamConsumer = utils["SSEJetStreamConsumer"]  # noqa: N806
        sse_jetstream_generator = utils["sse_jetstream_generator"]

        consumer = SSEJetStreamConsumer(workspace_key="test", subject="events")

        async def mock_stream() -> AsyncGenerator[dict[str, Any], None]:
            yield {"type": "message", "payload": {"text": "hello"}, "cursor": 1, "ts": None}
            raise RuntimeError("stream_error_original")  # original exception

        async def mock_disconnect() -> None:
            raise RuntimeError("disconnect_error_secondary")  # shadowing exception

        consumer.stream = mock_stream  # type: ignore[method-assign]
        consumer.disconnect = mock_disconnect  # type: ignore[method-assign]

        gen = sse_jetstream_generator(consumer)
        collected: list[str] = []

        with pytest.raises(RuntimeError) as exc_info:
            async for event in gen:
                collected.append(event)

        # The original stream exception must be preserved, not the disconnect error
        assert "stream_error_original" in str(exc_info.value), (
            f"BUG M4: Expected 'stream_error_original' in raised exception, "
            f"got: {exc_info.value!s}. The disconnect error is shadowing the root cause."
        )
        # Disconnect error must NOT be the primary exception
        assert "disconnect_error_secondary" not in str(exc_info.value), (
            "BUG M4: disconnect_error_secondary should not be the raised exception; it masks the original stream error."
        )

    @pytest.mark.asyncio
    async def test_jetstream_disconnect_error_on_normal_exit_should_not_raise(self) -> None:
        """Verify disconnect error during normal completion is handled gracefully.

        Even when the stream completes normally, a failing disconnect()
        should be caught and logged, not raised as an exception.
        """
        utils = _load_sse_utils()
        SSEJetStreamConsumer = utils["SSEJetStreamConsumer"]  # noqa: N806
        sse_jetstream_generator = utils["sse_jetstream_generator"]

        consumer = SSEJetStreamConsumer(workspace_key="test", subject="events")

        disconnect_called = False

        async def mock_stream() -> AsyncGenerator[dict[str, Any], None]:
            # Stream has only one message event, then completes
            yield {"type": "message", "payload": {"text": "done"}, "cursor": 1, "ts": None}

        async def mock_disconnect() -> None:
            nonlocal disconnect_called
            disconnect_called = True
            raise RuntimeError("disconnect_cleanup_error")

        consumer.stream = mock_stream  # type: ignore[method-assign]
        consumer.disconnect = mock_disconnect  # type: ignore[method-assign]

        gen = sse_jetstream_generator(consumer)
        collected: list[str] = []

        # After fix: should not raise; disconnect error is caught and logged
        try:
            async for event in gen:
                collected.append(event)
        except RuntimeError as e:
            pytest.fail(
                f"BUG M4: sse_jetstream_generator leaked disconnect error as: {e!s}. "
                "Disconnect errors during cleanup should be caught and logged."
            )

        assert disconnect_called, "disconnect() should still be called even on normal exit"
        assert len(collected) == 1, "Should have collected exactly one event"
