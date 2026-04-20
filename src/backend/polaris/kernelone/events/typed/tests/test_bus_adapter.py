"""Tests for TypedEventBusAdapter.

Test coverage:
- Normal: Event conversion, dual-write, subscription mapping
- Boundary: Missing mappings, conversion errors
- Error: Invalid event types, adapter errors
"""

from typing import Any
from unittest.mock import MagicMock

import pytest
from polaris.kernelone.events.typed.bus_adapter import (
    _EVENT_NAME_TO_MESSAGE_TYPE,
    _MESSAGE_TYPE_TO_EVENT_NAME,
    TypedEventBusAdapter,
    init_default_adapter,
)
from polaris.kernelone.events.typed.schemas import (
    ToolInvoked,
)


class MockMessageBus:
    """Mock MessageBus for testing."""

    def __init__(self) -> None:
        self.subscribers: dict[str, list[Any]] = {}
        self.published_messages: list[Any] = []
        self.Message = MagicMock()

    async def subscribe(self, message_type: Any, handler: Any) -> bool:
        type_name = message_type.name if hasattr(message_type, "name") else str(message_type)
        if type_name not in self.subscribers:
            self.subscribers[type_name] = []
        self.subscribers[type_name].append(handler)
        return True

    async def publish(self, message: Any) -> None:
        self.published_messages.append(message)


class MockEventRegistry:
    """Mock EventRegistry for testing."""

    def __init__(self) -> None:
        self.emitted_events: list[Any] = []
        self.subscriptions: list[tuple[str, Any]] = []

    async def emit(self, event: Any) -> None:
        self.emitted_events.append(event)

    def subscribe(self, pattern: Any, handler: Any) -> str:
        self.subscriptions.append((str(pattern), handler))
        return f"sub_{len(self.subscriptions)}"


class TestEventMapping:
    """Tests for event type mappings."""

    def test_event_to_message_type_mapping(self) -> None:
        """Test that core events have message type mappings."""
        assert "tool_invoked" in _EVENT_NAME_TO_MESSAGE_TYPE
        assert _EVENT_NAME_TO_MESSAGE_TYPE["tool_invoked"] == "TASK_STARTED"

        assert "tool_completed" in _EVENT_NAME_TO_MESSAGE_TYPE
        assert _EVENT_NAME_TO_MESSAGE_TYPE["tool_completed"] == "TASK_COMPLETED"

    def test_message_type_to_event_mapping(self) -> None:
        """Test reverse mapping exists for tool events.

        Note: Some MessageTypes map to multiple TypedEvents (e.g., TASK_STARTED
        maps to both tool_invoked and task_started). The reverse mapping dict
        stores the last registered mapping.
        """
        assert "TASK_STARTED" in _MESSAGE_TYPE_TO_EVENT_NAME
        # TASK_STARTED now maps to task_started (Director event) due to dual-write
        assert _MESSAGE_TYPE_TO_EVENT_NAME["TASK_STARTED"] == "task_started"


class TestTypedEventBusAdapter:
    """Tests for TypedEventBusAdapter."""

    @pytest.fixture
    def adapter(self) -> TypedEventBusAdapter:
        """Create adapter with mock dependencies."""
        bus = MockMessageBus()
        registry = MockEventRegistry()
        return TypedEventBusAdapter(
            message_bus=bus,  # type: ignore
            event_registry=registry,  # type: ignore
            dual_write=True,
        )

    def test_adapter_initialization(self, adapter: TypedEventBusAdapter) -> None:
        """Test adapter initializes correctly."""
        assert adapter._dual_write is True
        assert adapter.events_converted == 0
        assert adapter.conversion_errors == 0

    def test_register_default_mappings(self, adapter: TypedEventBusAdapter) -> None:
        """Test registering default event mappings."""
        adapter.register_default_mappings()

        # Check some known mappings
        assert "tool_invoked" in adapter._event_to_message_type
        assert "TASK_STARTED" in adapter._message_type_to_event

    def test_register_custom_mapping(self, adapter: TypedEventBusAdapter) -> None:
        """Test registering custom event mappings."""
        from polaris.kernelone.events.message_bus import MessageType

        adapter.register_event_type("custom_event", MessageType.TASK_STARTED)

        assert "custom_event" in adapter._event_to_message_type

    @pytest.mark.asyncio
    async def test_emit_to_both_systems(self, adapter: TypedEventBusAdapter) -> None:
        """Test dual-write emits to both registry and bus."""
        adapter.register_default_mappings()

        event = ToolInvoked.create(
            tool_name="read_file",
            tool_call_id="call_123",
        )

        await adapter.emit_to_both(event)

        # Should be emitted to registry
        assert len(adapter._registry.emitted_events) == 1  # type: ignore[attr-defined]
        assert adapter._registry.emitted_events[0].payload.tool_name == "read_file"  # type: ignore[attr-defined]

        # Should be emitted to bus
        assert len(adapter._bus.published_messages) == 1  # type: ignore[attr-defined]

        # Statistics should be updated
        assert adapter.events_converted == 1
        assert adapter.conversion_errors == 0

    @pytest.mark.asyncio
    async def test_emit_to_registry_only(self, adapter: TypedEventBusAdapter) -> None:
        """Test emit_to_registry only emits to registry."""
        adapter._dual_write = False
        adapter.register_default_mappings()

        event = ToolInvoked.create(
            tool_name="read_file",
            tool_call_id="call_123",
        )

        await adapter.emit_to_registry(event)

        assert len(adapter._registry.emitted_events) == 1  # type: ignore[attr-defined]
        assert len(adapter._bus.published_messages) == 0  # type: ignore[attr-defined]

    @pytest.mark.asyncio
    async def test_emit_without_mapping_logs_warning(self, adapter: TypedEventBusAdapter) -> None:
        """Test that unmapped events log a warning but don't crash."""
        # Don't register any mappings
        adapter._dual_write = True

        event = ToolInvoked.create(
            tool_name="read_file",
            tool_call_id="call_123",
        )

        # Should not raise
        await adapter.emit_to_both(event)

        # Event should still go to registry
        assert len(adapter._registry.emitted_events) == 1  # type: ignore[attr-defined]


class TestAdapterSubscription:
    """Tests for adapter subscription functionality."""

    @pytest.fixture
    def adapter(self) -> TypedEventBusAdapter:
        """Create adapter with mock dependencies."""
        bus = MockMessageBus()
        registry = MockEventRegistry()
        adapter = TypedEventBusAdapter(
            message_bus=bus,  # type: ignore
            event_registry=registry,  # type: ignore
            dual_write=True,
        )
        adapter.register_default_mappings()
        return adapter

    def test_subscribe_to_registry(self, adapter: TypedEventBusAdapter) -> None:
        """Test subscribing to registry with pattern."""

        def handler(e) -> None:
            return None  # type: ignore

        sub_id = adapter.subscribe_to_registry("tool.*", handler)

        assert sub_id is not None
        assert len(adapter._registry.subscriptions) == 1  # type: ignore[attr-defined]


class TestAdapterGlobalInstance:
    """Tests for global adapter instance."""

    def setup_method(self) -> None:
        """Reset global adapter before each test."""
        import polaris.kernelone.events.typed.bus_adapter as module

        module._default_adapter = None

    def test_init_default_adapter(self) -> None:
        """Test initializing default adapter."""
        bus = MockMessageBus()
        registry = MockEventRegistry()

        adapter = init_default_adapter(
            message_bus=bus,  # type: ignore
            event_registry=registry,  # type: ignore
            dual_write=True,
        )

        assert adapter is not None
        assert adapter._dual_write is True

        # Default mappings should be registered
        assert "tool_invoked" in adapter._event_to_message_type

    def test_get_default_adapter_not_initialized(self) -> None:
        """Test get_default_adapter returns None when not initialized."""
        from polaris.kernelone.events.typed.bus_adapter import get_default_adapter

        assert get_default_adapter() is None

    def test_get_default_adapter_after_init(self) -> None:
        """Test get_default_adapter returns instance after init."""
        from polaris.kernelone.events.typed.bus_adapter import get_default_adapter

        bus = MockMessageBus()
        registry = MockEventRegistry()

        adapter = init_default_adapter(
            message_bus=bus,  # type: ignore
            event_registry=registry,  # type: ignore
        )

        assert get_default_adapter() is adapter


class TestAdapterConversionErrors:
    """Tests for adapter error handling."""

    @pytest.fixture
    def adapter(self) -> TypedEventBusAdapter:
        """Create adapter with mock dependencies."""
        bus = MockMessageBus()
        registry = MockEventRegistry()
        return TypedEventBusAdapter(
            message_bus=bus,  # type: ignore
            event_registry=registry,  # type: ignore
            dual_write=True,
        )

    @pytest.mark.asyncio
    async def test_registry_error_continues(self, adapter: TypedEventBusAdapter) -> None:
        """Test that registry errors don't prevent bus emission."""
        adapter.register_default_mappings()

        # Make registry emit raise

        async def failing_emit(e: Any) -> None:
            raise RuntimeError("Registry error")

        adapter._registry.emit = failing_emit  # type: ignore

        event = ToolInvoked.create(
            tool_name="read_file",
            tool_call_id="call_123",
        )

        # Should not raise
        await adapter.emit_to_both(event)

        # Error count should be incremented
        assert adapter.conversion_errors >= 1
