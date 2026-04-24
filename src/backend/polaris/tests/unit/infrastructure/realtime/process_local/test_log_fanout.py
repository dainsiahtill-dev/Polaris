"""Tests for polaris.infrastructure.realtime.process_local.log_fanout."""

from __future__ import annotations

import asyncio

import pytest
from polaris.infrastructure.realtime.process_local.log_fanout import (
    LOG_REALTIME_FANOUT,
    RealtimeLogFanout,
    RealtimeLogSubscription,
    _normalize_runtime_root,
)


class TestNormalizeRuntimeRoot:
    def test_returns_absolute_path(self) -> None:
        result = _normalize_runtime_root("C:/test/path")
        # On Windows, os.path.abspath converts to backslashes
        assert result is not None
        assert len(result) > 0

    def test_empty_string_becomes_cwd(self) -> None:
        import os

        result = _normalize_runtime_root("")
        # Empty string becomes current working directory
        assert result == os.getcwd()

    def test_normalizes_whitespace(self) -> None:
        result = _normalize_runtime_root("  C:/test  ")
        # Result is normalized path (with Windows backslashes)
        assert "test" in result


class TestRealtimeLogSubscription:
    def test_matches_runtime_exact(self) -> None:
        loop = asyncio.new_event_loop()
        try:
            # Use normalized path to ensure consistent comparison
            import os

            runtime = os.path.abspath("C:/runtime")
            sub = RealtimeLogSubscription(
                connection_id="test-conn",
                runtime_root=runtime,
                queue=asyncio.Queue(),
                loop=loop,
            )
            # Both sides should normalize to the same path
            assert sub.matches_runtime(runtime) is True
            assert sub.matches_runtime(os.path.abspath("C:/other")) is False
        finally:
            loop.close()

    def test_dropped_counter(self) -> None:
        loop = asyncio.new_event_loop()
        try:
            sub = RealtimeLogSubscription(
                connection_id="test-conn",
                runtime_root="C:/runtime",
                queue=asyncio.Queue(),
                loop=loop,
            )
            sub._mark_dropped(3)
            assert sub._dropped == 3

            consumed = sub.consume_dropped()
            assert consumed == 3
            assert sub._dropped == 0
        finally:
            loop.close()


class TestRealtimeLogFanout:
    def test_initial_state(self) -> None:
        fanout = RealtimeLogFanout()
        assert fanout.list_connections() == []

    @pytest.mark.asyncio
    async def test_register_connection(self) -> None:
        fanout = RealtimeLogFanout()
        sub = await fanout.register_connection(
            connection_id="test-conn",
            runtime_root="C:/runtime",
        )
        assert sub.connection_id == "test-conn"
        assert sub.runtime_root is not None

    @pytest.mark.asyncio
    async def test_unregister_connection(self) -> None:
        fanout = RealtimeLogFanout()
        await fanout.register_connection(
            connection_id="test-conn",
            runtime_root="C:/runtime",
        )
        removed = await fanout.unregister_connection("test-conn")
        assert removed is True
        assert fanout.list_connections() == []

    @pytest.mark.asyncio
    async def test_unregister_nonexistent_returns_false(self) -> None:
        fanout = RealtimeLogFanout()
        removed = await fanout.unregister_connection("nonexistent")
        assert removed is False

    @pytest.mark.asyncio
    async def test_get_subscription(self) -> None:
        fanout = RealtimeLogFanout()
        await fanout.register_connection(
            connection_id="test-conn",
            runtime_root="C:/runtime",
        )
        sub = fanout.get_subscription("test-conn")
        assert sub is not None
        assert sub.connection_id == "test-conn"

    @pytest.mark.asyncio
    async def test_get_subscription_nonexistent(self) -> None:
        fanout = RealtimeLogFanout()
        sub = fanout.get_subscription("nonexistent")
        assert sub is None

    @pytest.mark.asyncio
    async def test_publish_no_subscribers(self) -> None:
        fanout = RealtimeLogFanout()
        # Should not raise
        fanout.publish(runtime_root="C:/runtime", event={"type": "test"})

    @pytest.mark.asyncio
    async def test_register_connection_with_custom_queue_size(self) -> None:
        fanout = RealtimeLogFanout()
        sub = await fanout.register_connection(
            connection_id="test-conn",
            runtime_root="C:/runtime",
            max_queue_size=512,
        )
        assert sub.max_queue_size == 512

    @pytest.mark.asyncio
    async def test_multiple_connections(self) -> None:
        fanout = RealtimeLogFanout()
        await fanout.register_connection(
            connection_id="conn-1",
            runtime_root="C:/runtime",
        )
        await fanout.register_connection(
            connection_id="conn-2",
            runtime_root="C:/runtime",
        )
        connections = fanout.list_connections()
        assert len(connections) == 2
        assert "conn-1" in connections
        assert "conn-2" in connections


class TestGlobalFanoutInstance:
    def test_global_instance_exists(self) -> None:
        assert LOG_REALTIME_FANOUT is not None
        assert isinstance(LOG_REALTIME_FANOUT, RealtimeLogFanout)
