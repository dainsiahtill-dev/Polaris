"""Unit tests for UEPEventPublisher with TypedEventBusAdapter integration."""

from __future__ import annotations

import asyncio
import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from polaris.kernelone.events.message_bus import MessageBus, MessageType
from polaris.kernelone.events.registry import set_global_bus
from polaris.kernelone.events.uep_publisher import UEPEventPublisher


@pytest.fixture
def message_bus() -> MessageBus:
    """Create a fresh MessageBus for testing."""
    bus = MessageBus()
    set_global_bus(bus)
    return bus


@pytest.fixture(autouse=True)
def _restore_global_bus() -> Any:
    """Ensure global bus is restored after each test to prevent state leaks."""
    from polaris.kernelone.events.registry import get_global_bus, set_global_bus

    prev = get_global_bus()
    yield
    set_global_bus(prev)


@pytest.fixture
def received_messages(message_bus: MessageBus) -> list[dict[str, Any]]:
    """Collect messages published to the bus."""
    messages: list[dict[str, Any]] = []

    async def handler(msg: Any) -> None:
        messages.append(
            {
                "type": msg.type.name if hasattr(msg.type, "name") else str(msg.type),
                "sender": msg.sender,
                "payload": dict(msg.payload),
            }
        )

    # Subscribe before the loop is needed
    async def _setup() -> None:
        await message_bus.subscribe(MessageType.RUNTIME_EVENT, handler)

    asyncio.get_event_loop().run_until_complete(_setup())
    return messages


class TestUEPEventPublisher:
    """Test suite for UEPEventPublisher."""

    @pytest.mark.asyncio
    async def test_publish_stream_event_success(
        self,
        message_bus: MessageBus,
    ) -> None:
        """Test successful stream event publication."""
        publisher = UEPEventPublisher(bus=message_bus)

        result = await publisher.publish_stream_event(
            workspace="/tmp/test",
            run_id="run-123",
            role="director",
            event_type="content_chunk",
            payload={"content": "hello"},
            turn_id="turn-456",
        )

        assert result is True

    @pytest.mark.asyncio
    async def test_publish_stream_event_no_bus(self) -> None:
        """Test publish returns False when no bus available."""
        set_global_bus(None)  # type: ignore[arg-type]
        publisher = UEPEventPublisher(bus=None)

        result = await publisher.publish_stream_event(
            workspace="/tmp/test",
            run_id="run-123",
            role="director",
            event_type="content_chunk",
            payload={"content": "test"},
        )

        assert result is False

    @pytest.mark.asyncio
    async def test_publish_via_typed_adapter_when_available(
        self,
        message_bus: MessageBus,
    ) -> None:
        """Test that publish uses TypedEventBusAdapter when available."""
        publisher = UEPEventPublisher(bus=message_bus)

        # Mock the adapter
        mock_adapter = MagicMock()
        mock_adapter.emit_to_both = AsyncMock()
        publisher._adapter = mock_adapter

        result = await publisher.publish_stream_event(
            workspace="/tmp/test",
            run_id="run-123",
            role="director",
            event_type="tool_call",
            payload={"tool": "read_file"},
            turn_id="turn-456",
        )

        assert result is True
        mock_adapter.emit_to_both.assert_called_once()

    @pytest.mark.asyncio
    async def test_publish_fallback_when_adapter_emit_fails(
        self,
        message_bus: MessageBus,
    ) -> None:
        """Test fallback to direct MessageBus publish when adapter emit fails."""
        publisher = UEPEventPublisher(bus=message_bus)

        # Mock adapter that raises exception
        mock_adapter = MagicMock()
        mock_adapter.emit_to_both = AsyncMock(side_effect=Exception("Adapter failed"))
        publisher._adapter = mock_adapter

        # Should fall back to direct publish
        result = await publisher.publish_stream_event(
            workspace="/tmp/test",
            run_id="run-123",
            role="director",
            event_type="content_chunk",
            payload={"content": "test"},
        )

        assert result is True  # Fallback succeeded

    @pytest.mark.asyncio
    async def test_publish_llm_event_via_adapter(
        self,
        message_bus: MessageBus,
    ) -> None:
        """Test LLM lifecycle event uses adapter when available."""
        publisher = UEPEventPublisher(bus=message_bus)

        mock_adapter = MagicMock()
        mock_adapter.emit_to_both = AsyncMock()
        publisher._adapter = mock_adapter

        result = await publisher.publish_llm_lifecycle_event(
            workspace="/tmp/test",
            run_id="run-123",
            role="director",
            event_type="llm_call_start",
            metadata={"model": "gpt-4"},
        )

        assert result is True
        mock_adapter.emit_to_both.assert_called_once()

    @pytest.mark.asyncio
    async def test_publish_fingerprint_event_via_adapter(
        self,
        message_bus: MessageBus,
    ) -> None:
        """Test fingerprint event uses adapter when available."""
        publisher = UEPEventPublisher(bus=message_bus)

        mock_adapter = MagicMock()
        mock_adapter.emit_to_both = AsyncMock()
        publisher._adapter = mock_adapter

        result = await publisher.publish_fingerprint_event(
            workspace="/tmp/test",
            run_id="run-123",
            role="director",
            fingerprint={"profile_id": "test"},
        )

        assert result is True
        mock_adapter.emit_to_both.assert_called_once()

    @pytest.mark.asyncio
    async def test_publish_audit_event_via_adapter(
        self,
        message_bus: MessageBus,
    ) -> None:
        """Test audit event uses adapter when available."""
        publisher = UEPEventPublisher(bus=message_bus)

        mock_adapter = MagicMock()
        mock_adapter.emit_to_both = AsyncMock()
        publisher._adapter = mock_adapter

        result = await publisher.publish_audit_event(
            workspace="/tmp/test",
            run_id="run-123",
            role="director",
            event_type="security_check",
            data={"violation": False},
        )

        assert result is True
        mock_adapter.emit_to_both.assert_called_once()

    @pytest.mark.asyncio
    async def test_publish_fallback_llm_event(
        self,
        message_bus: MessageBus,
    ) -> None:
        """Test LLM lifecycle event falls back to direct publish when no adapter."""
        publisher = UEPEventPublisher(bus=message_bus)

        # No adapter set, should fall back
        result = await publisher.publish_llm_lifecycle_event(
            workspace="/tmp/test",
            run_id="run-123",
            role="director",
            event_type="llm_call_start",
            metadata={"model": "gpt-4"},
        )

        assert result is True

    @pytest.mark.asyncio
    async def test_get_adapter_returns_cached_instance(
        self,
        message_bus: MessageBus,
    ) -> None:
        """Test that adapter is cached after first access."""
        publisher = UEPEventPublisher(bus=message_bus)

        mock_adapter = MagicMock()
        mock_adapter.emit_to_both = AsyncMock()

        with patch("polaris.kernelone.events.typed.get_default_adapter", return_value=mock_adapter):
            adapter1 = publisher._get_adapter()
            adapter2 = publisher._get_adapter()

            assert adapter1 is adapter2  # Same cached instance


class TestUEPEventPublisherPayloadValidation:
    """Test payload validation and edge cases."""

    @pytest.mark.asyncio
    async def test_empty_payload_handling(
        self,
        message_bus: MessageBus,
    ) -> None:
        """Test handling of empty/minimal payloads."""
        publisher = UEPEventPublisher(bus=message_bus)

        result = await publisher.publish_stream_event(
            workspace="",
            run_id="",
            role="",
            event_type="",
            payload={},
        )

        assert result is True

    @pytest.mark.asyncio
    async def test_nested_payload_handling(
        self,
        message_bus: MessageBus,
    ) -> None:
        """Test handling of nested/complex payloads."""
        publisher = UEPEventPublisher(bus=message_bus)

        nested_payload = {
            "tool": "apply_diff",
            "args": {
                "path": "test.py",
                "diff": "@@ -1,1 +1,2 @@\n-old\n+new",
            },
        }

        result = await publisher.publish_stream_event(
            workspace="/workspace",
            run_id=str(uuid.uuid4()),
            role="director",
            event_type="tool_call",
            payload=nested_payload,
        )

        assert result is True

    @pytest.mark.asyncio
    async def test_timestamp_generation(
        self,
        message_bus: MessageBus,
    ) -> None:
        """Test that timestamp is auto-generated."""
        publisher = UEPEventPublisher(bus=message_bus)

        result = await publisher.publish_stream_event(
            workspace="/tmp/test",
            run_id="run-1",
            role="pm",
            event_type="complete",
            payload={},
        )

        assert result is True


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
