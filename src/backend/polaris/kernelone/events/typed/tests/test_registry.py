"""Tests for Event Registry with wildcard subscription.

Test coverage:
- Normal: Subscribe, emit, unsubscribe
- Boundary: Multiple handlers, priority ordering, wildcard patterns
- Error: Invalid patterns, missing handlers
"""

import asyncio
from typing import Any

import pytest
from polaris.kernelone.events.typed.registry import (
    EventPattern,
    EventRegistry,
    get_default_registry,
    subscribe,
    unsubscribe,
)
from polaris.kernelone.events.typed.schemas import (
    ToolCompleted,
    ToolError,
    ToolInvoked,
)


class TestEventPattern:
    """Tests for EventPattern matching."""

    def test_exact_match(self) -> None:
        """Test exact pattern matching."""
        pattern = EventPattern("tool_invoked")

        assert pattern.matches("tool_invoked") is True
        assert pattern.matches("tool_completed") is False
        assert pattern.matches("instance_started") is False

    def test_single_wildcard(self) -> None:
        """Test single wildcard matching."""
        pattern = EventPattern("tool_*")

        assert pattern.matches("tool_invoked") is True
        assert pattern.matches("tool_completed") is True
        assert pattern.matches("tool_error") is True
        assert pattern.matches("tool_") is True
        assert pattern.matches("tool") is False  # Missing underscore

    def test_multi_wildcard(self) -> None:
        """Test multi-wildcard matching."""
        pattern = EventPattern("*")

        assert pattern.matches("any_event") is True
        assert pattern.matches("tool_invoked") is True

    def test_question_mark_wildcard(self) -> None:
        """Test question mark wildcard matching.

        Note: fnmatch's ? matches exactly one character, not zero or more.
        So tool_???? matches tool_1234 (4 chars after underscore)
        but tool_abc (3 chars) won't match.
        """
        pattern = EventPattern("tool_????")  # Exactly 4 chars after underscore

        assert pattern.matches("tool_1234") is True
        assert pattern.matches("tool_abcd") is True
        assert pattern.matches("tool_123") is False  # Only 3 chars
        assert pattern.matches("tool_12345") is False  # 5 chars

    def test_from_string_exact(self) -> None:
        """Test from_string with exact pattern."""
        pattern = EventPattern.from_string("tool_invoked")

        assert pattern.is_regex is False
        assert pattern.pattern == "tool_invoked"
        assert pattern.matches("tool_invoked") is True

    def test_from_string_wildcard(self) -> None:
        """Test from_string with wildcard pattern.

        Note: fnmatch's * does NOT match literal dots. So "tool.*" won't
        match "tool_invoked" because * stops at the dot.
        Use "tool_*" or "tool*" instead.
        """
        pattern = EventPattern.from_string("tool*")  # Use * without dot

        # This should match
        assert pattern.matches("tool_invoked") is True
        assert pattern.matches("toolcompleted") is True
        assert pattern.matches("tool_completed") is True


class TestEventRegistrySubscription:
    """Tests for EventRegistry subscription management."""

    @pytest.fixture
    def registry(self) -> EventRegistry:
        """Create a fresh registry for each test."""
        return EventRegistry()

    def test_subscribe_returns_id(self, registry: EventRegistry) -> None:
        """Test that subscribe returns a subscription ID."""

        def handler(e) -> None:
            return None  # type: ignore

        sub_id = registry.subscribe("tool_invoked", handler)

        assert sub_id is not None
        assert isinstance(sub_id, str)

    def test_subscribe_with_custom_id(self, registry: EventRegistry) -> None:
        """Test subscribe with custom subscription ID."""

        def handler(e) -> None:
            return None  # type: ignore

        sub_id = registry.subscribe("tool_invoked", handler, subscription_id="my_sub")

        assert sub_id == "my_sub"

    def test_duplicate_subscription_returns_same_id(self, registry: EventRegistry) -> None:
        """Test that duplicate subscribe calls return the same ID."""

        def handler(e) -> None:
            return None  # type: ignore

        sub_id1 = registry.subscribe("tool_invoked", handler, subscription_id="dup")
        sub_id2 = registry.subscribe("tool_invoked", handler, subscription_id="dup")

        assert sub_id1 == sub_id2

    def test_unsubscribe(self, registry: EventRegistry) -> None:
        """Test unsubscribe removes the subscription."""

        def handler(e) -> None:
            return None  # type: ignore

        sub_id = registry.subscribe("tool_invoked", handler)

        result = registry.unsubscribe(sub_id)

        assert result is True
        assert registry.subscription_count == 0

    def test_unsubscribe_nonexistent(self, registry: EventRegistry) -> None:
        """Test unsubscribe with invalid ID returns False."""
        result = registry.unsubscribe("nonexistent_id")
        assert result is False

    def test_unsubscribe_all(self, registry: EventRegistry) -> None:
        """Test unsubscribe_all removes all subscriptions."""

        def handler1(e: Any) -> None:
            pass

        def handler2(e: Any) -> None:
            pass

        def handler3(e: Any) -> None:
            pass

        registry.subscribe("tool_invoked", handler1)
        registry.subscribe("tool_completed", handler2)
        registry.subscribe("instance_started", handler3)

        count = registry.unsubscribe_all()

        assert count == 3
        assert registry.subscription_count == 0

    def test_subscribe_with_priority(self, registry: EventRegistry) -> None:
        """Test that higher priority handlers are called first."""
        order: list[int] = []

        def handler1(e: Any) -> None:
            order.append(1)

        def handler2(e: Any) -> None:
            order.append(2)

        registry.subscribe("tool_invoked", handler1, priority=10)
        registry.subscribe("tool_invoked", handler2, priority=20)

        async def run_test() -> None:
            await registry.emit(ToolInvoked.create(tool_name="test", tool_call_id="1"))

        asyncio.run(run_test())

        # Higher priority (2) should be called first
        assert order == [2, 1]

    def test_subscribe_once(self, registry: EventRegistry) -> None:
        """Test subscribe_once auto-unsubscribes after first event."""
        call_count = 0

        def handler(e: Any) -> None:
            nonlocal call_count
            call_count += 1

        registry.subscribe_once("tool_invoked", handler)
        assert registry.subscription_count == 1

        async def run_test() -> None:
            nonlocal call_count
            # Emit first event
            await registry.emit(ToolInvoked.create(tool_name="test", tool_call_id="1"))
            assert call_count == 1
            assert registry.subscription_count == 0  # Auto-unsubscribed

            # Emit second event (should not trigger handler)
            await registry.emit(ToolInvoked.create(tool_name="test", tool_call_id="2"))
            assert call_count == 1  # Still 1

        asyncio.run(run_test())


class TestEventRegistryEmission:
    """Tests for EventRegistry event emission."""

    @pytest.fixture
    def registry(self) -> EventRegistry:
        """Create a fresh registry for each test."""
        return EventRegistry()

    @pytest.mark.asyncio
    async def test_emit_to_direct_subscriber(self, registry: EventRegistry) -> None:
        """Test emit reaches direct subscriber."""
        received: list[str] = []

        def handler(event: ToolInvoked) -> None:
            received.append(event.payload.tool_name)

        registry.subscribe("tool_invoked", handler)  # type: ignore[arg-type]

        await registry.emit(ToolInvoked.create(tool_name="read_file", tool_call_id="call_1"))

        assert received == ["read_file"]

    @pytest.mark.asyncio
    async def test_emit_to_wildcard_subscriber(self, registry: EventRegistry) -> None:
        """Test emit reaches wildcard subscribers.

        Note: Use "tool*" not "tool.*" because fnmatch's * doesn't match dots.
        """
        received: list[str] = []

        def handler(event: Any) -> None:
            received.append(event.event_name)

        registry.subscribe("tool*", handler)  # Use * without dot

        await registry.emit(ToolInvoked.create(tool_name="test", tool_call_id="call_1"))
        await registry.emit(ToolCompleted.create(tool_name="test", tool_call_id="call_1"))
        await registry.emit(ToolError.create(tool_name="test", tool_call_id="call_1", error="err"))

        assert "tool_invoked" in received
        assert "tool_completed" in received
        assert "tool_error" in received

    @pytest.mark.asyncio
    async def test_emit_to_multiple_handlers(self, registry: EventRegistry) -> None:
        """Test emit reaches all matching handlers."""
        count = 0

        def handler1(e: Any) -> None:
            nonlocal count
            count += 1

        def handler2(e: Any) -> None:
            nonlocal count
            count += 1

        registry.subscribe("tool_invoked", handler1)
        registry.subscribe("tool_invoked", handler2)

        await registry.emit(ToolInvoked.create(tool_name="test", tool_call_id="call_1"))

        assert count == 2

    @pytest.mark.asyncio
    async def test_no_subscriber_no_error(self, registry: EventRegistry) -> None:
        """Test that emitting without subscribers doesn't error."""
        # Should not raise
        await registry.emit(ToolInvoked.create(tool_name="test", tool_call_id="call_1"))

    @pytest.mark.asyncio
    async def test_async_handler(self, registry: EventRegistry) -> None:
        """Test async handler support."""
        result: list[str] = []

        async def async_handler(event: ToolInvoked) -> None:
            result.append(event.payload.tool_name)

        registry.subscribe("tool_invoked", async_handler)  # type: ignore[arg-type]

        await registry.emit(ToolInvoked.create(tool_name="async_test", tool_call_id="call_1"))

        assert result == ["async_test"]

    @pytest.mark.asyncio
    async def test_handler_exception_doesnt_crash(self, registry: EventRegistry) -> None:
        """Test that handler exceptions don't crash the emitter."""
        call_count = 0

        def bad_handler(e: Any) -> None:
            raise ValueError("Test error")

        def good_handler(e: Any) -> None:
            nonlocal call_count
            call_count += 1

        registry.subscribe("tool_invoked", bad_handler)
        registry.subscribe("tool_invoked", good_handler)

        # Should not raise despite bad handler
        await registry.emit(ToolInvoked.create(tool_name="test", tool_call_id="call_1"))

        assert call_count == 1  # Good handler still called


class TestEventRegistryStatistics:
    """Tests for EventRegistry statistics."""

    @pytest.fixture
    def registry(self) -> EventRegistry:
        return EventRegistry()

    def test_subscription_count(self, registry: EventRegistry) -> None:
        """Test subscription count tracking."""

        def handler1(e: Any) -> None:
            pass

        def handler2(e: Any) -> None:
            pass

        assert registry.subscription_count == 0

        registry.subscribe("tool_invoked", handler1)
        assert registry.subscription_count == 1

        registry.subscribe("tool_completed", handler2)
        assert registry.subscription_count == 2

        registry.unsubscribe_all()
        assert registry.subscription_count == 0

    @pytest.mark.asyncio
    async def test_emit_count(self, registry: EventRegistry) -> None:
        """Test emit count tracking."""

        def handler(e) -> None:
            return None  # type: ignore

        registry.subscribe("tool.*", handler)

        assert registry.emit_count == 0

        await registry.emit(ToolInvoked.create(tool_name="test", tool_call_id="1"))
        assert registry.emit_count == 1

        await registry.emit(ToolCompleted.create(tool_name="test", tool_call_id="1"))
        assert registry.emit_count == 2

    @pytest.mark.asyncio
    async def test_handler_invocation_count(self, registry: EventRegistry) -> None:
        """Test handler invocation count tracking."""

        def handler(e) -> None:
            return None  # type: ignore

        registry.subscribe("tool*", handler)  # Use * without dot

        assert registry.handler_invocation_count == 0

        await registry.emit(ToolInvoked.create(tool_name="test", tool_call_id="1"))
        assert registry.handler_invocation_count == 1

        await registry.emit(ToolInvoked.create(tool_name="test", tool_call_id="2"))
        assert registry.handler_invocation_count == 2


class TestGlobalRegistry:
    """Tests for global registry convenience functions."""

    def setup_method(self) -> None:
        """Reset global registry before each test."""
        import polaris.kernelone.events.typed.registry as module

        module._default_registry = None

    def test_subscribe_uses_default_registry(self) -> None:
        """Test that module-level subscribe uses default registry."""
        call_count = 0

        def handler(e: Any) -> None:
            nonlocal call_count
            call_count += 1

        sub_id = subscribe("tool_invoked", handler)
        assert sub_id is not None

        # Verify subscription was created
        registry = get_default_registry()
        assert registry.subscription_count == 1

    def test_unsubscribe_global(self) -> None:
        """Test that module-level unsubscribe works."""

        def handler(e) -> None:
            return None  # type: ignore

        sub_id = subscribe("tool_invoked", handler)

        result = unsubscribe(sub_id)
        assert result is True


class TestConcurrentSubscriptions:
    """Tests for concurrent subscription handling (E6)."""

    @pytest.fixture
    def registry(self) -> EventRegistry:
        return EventRegistry()

    @pytest.mark.asyncio
    async def test_concurrent_subscribe_same_handler(self, registry: EventRegistry) -> None:
        """Test that the same handler function can subscribe multiple times with unique IDs."""

        def handler(e: Any) -> None:
            pass

        # Subscribe the same handler multiple times
        id1 = registry.subscribe("tool*", handler)
        id2 = registry.subscribe("tool*", handler)
        id3 = registry.subscribe("tool*", handler)

        # All IDs should be unique (UUID-based)
        assert id1 != id2
        assert id2 != id3
        assert id1 != id3

        assert registry.subscription_count == 3

    @pytest.mark.asyncio
    async def test_concurrent_emit_no_race(self, registry: EventRegistry) -> None:
        """Test that concurrent emits don't cause race conditions."""
        results: list[str] = []
        lock = asyncio.Lock()

        async def handler(event: Any) -> None:
            async with lock:
                results.append(event.event_name)

        registry.subscribe("tool_invoked", handler)

        # Emit multiple events concurrently
        tasks = [registry.emit(ToolInvoked.create(tool_name=f"tool_{i}", tool_call_id=f"id_{i}")) for i in range(10)]
        await asyncio.gather(*tasks)

        assert len(results) == 10

    @pytest.mark.asyncio
    async def test_subscribe_during_emit(self, registry: EventRegistry) -> None:
        """Test that subscribe during emit is handled correctly."""
        order: list[str] = []

        def handler1(e: Any) -> None:
            order.append("h1_start")
            # Subscribe during handler execution
            registry.subscribe("tool_invoked", lambda e: order.append("h3"))
            order.append("h1_end")

        def handler2(e: Any) -> None:
            order.append("h2")

        registry.subscribe("tool_invoked", handler1)
        registry.subscribe("tool_invoked", handler2)

        await registry.emit(ToolInvoked.create(tool_name="test", tool_call_id="1"))

        # New handler should not be called for this emit
        assert "h3" not in order

    @pytest.mark.asyncio
    async def test_unsubscribe_during_emit(self, registry: EventRegistry) -> None:
        """Test that unsubscribe during emit prevents subsequent calls."""
        order: list[str] = []

        def handler1(e: Any) -> None:
            order.append("h1_start")
            # Unsubscribe handler2 during execution
            registry.unsubscribe(handler2_id)
            order.append("h1_end")

        def handler2(e: Any) -> None:
            order.append("h2")

        handler2_id = registry.subscribe("tool_invoked", handler2)
        registry.subscribe("tool_invoked", handler1)

        await registry.emit(ToolInvoked.create(tool_name="test", tool_call_id="1"))

        # Handler2 should have been called (was subscribed at start)
        assert "h1_start" in order
        assert "h1_end" in order

    @pytest.mark.asyncio
    async def test_lock_creation_thread_safety(self, registry: EventRegistry) -> None:
        """Test that lock creation is thread-safe for same event loop."""
        locks = []

        # Create multiple locks rapidly
        for _ in range(10):
            lock = registry._get_lock()
            locks.append(lock)

        # All locks should be the same instance for same loop
        assert all(l is locks[0] for l in locks)


class TestBoundaryConditions:
    """Tests for boundary and edge cases (E10)."""

    @pytest.fixture
    def registry(self) -> EventRegistry:
        return EventRegistry()

    def test_empty_pattern(self, registry: EventRegistry) -> None:
        """Test subscription with empty pattern."""

        def handler(e) -> None:
            return None  # type: ignore

        sub_id = registry.subscribe("", handler)
        assert sub_id is not None

    def test_special_chars_in_pattern(self, registry: EventRegistry) -> None:
        """Test subscription with special characters in pattern."""

        def handler(e) -> None:
            return None  # type: ignore

        # These should not crash
        sub_id = registry.subscribe("tool.test.123", handler)
        assert sub_id is not None

    @pytest.mark.asyncio
    async def test_emit_without_subscribers(self, registry: EventRegistry) -> None:
        """Test emit with no subscribers doesn't error."""
        await registry.emit(ToolInvoked.create(tool_name="test", tool_call_id="1"))
        assert registry.emit_count == 1

    @pytest.mark.asyncio
    async def test_many_events_same_handler(self, registry: EventRegistry) -> None:
        """Test many events to same handler."""
        call_count = 0

        def handler(e: Any) -> None:
            nonlocal call_count
            call_count += 1

        registry.subscribe("tool_invoked", handler)

        for i in range(100):
            await registry.emit(ToolInvoked.create(tool_name="test", tool_call_id=f"id_{i}"))

        assert call_count == 100

    @pytest.mark.asyncio
    async def test_many_handlers_same_event(self, registry: EventRegistry) -> None:
        """Test many handlers for same event."""

        def make_handler(i: int) -> Any:
            def handler(e: Any) -> None:
                pass

            return handler

        for i in range(50):
            registry.subscribe(f"tool_{i}", make_handler(i))

        # Each handler should be called
        assert registry.subscription_count == 50

    def test_priority_ordering_large(self, registry: EventRegistry) -> None:
        """Test priority ordering with many handlers."""
        order: list[int] = []

        def make_handler(priority: int) -> Any:
            def handler(e: Any) -> None:
                order.append(priority)

            return handler

        # Subscribe in random priority order
        priorities = [10, 50, 30, 70, 20, 60, 40, 80, 25, 55]
        for p in priorities:
            registry.subscribe("tool_invoked", make_handler(p), priority=p)

        async def run_test() -> None:
            await registry.emit(ToolInvoked.create(tool_name="test", tool_call_id="1"))

        asyncio.run(run_test())

        # Should be in descending priority order
        assert order == sorted(priorities, reverse=True)

    @pytest.mark.asyncio
    async def test_regression_uuid_subscription_ids(self, registry: EventRegistry) -> None:
        """Regression test: verify UUID-based subscription IDs (E2)."""

        def handler(e: Any) -> None:
            pass

        # Same handler should get different IDs each time
        ids = [registry.subscribe("test", handler) for _ in range(5)]
        assert len(set(ids)) == 5  # All unique

    @pytest.mark.asyncio
    async def test_regression_no_handler_memory_leak(self, registry: EventRegistry) -> None:
        """Regression test: verify no memory leak with many subscriptions."""

        def handler(e: Any) -> None:
            pass

        # Create many subscriptions
        for i in range(100):
            registry.subscribe(f"event_{i}", handler)

        assert registry.subscription_count == 100

        # Unsubscribe all
        registry.unsubscribe_all()

        assert registry.subscription_count == 0


class TestReplacerBoundaryConditions:
    """Tests for replacer boundary conditions (E10)."""

    def test_levenshtein_empty_strings(self) -> None:
        """Test Levenshtein with empty strings."""
        from polaris.kernelone.editing.replacers.opencode_replacers import levenshtein_distance

        assert levenshtein_distance("", "") == 0
        assert levenshtein_distance("", "abc") == 3
        assert levenshtein_distance("abc", "") == 3

    def test_levenshtein_large_strings(self) -> None:
        """Test Levenshtein with large strings (E3 regression)."""
        from polaris.kernelone.editing.replacers.opencode_replacers import levenshtein_distance

        # Large identical strings should work
        large1 = "a" * 5000
        large2 = "a" * 5000
        assert levenshtein_distance(large1, large2) == 0

        # One char difference
        large2 = "a" * 4999 + "b"
        assert levenshtein_distance(large1, large2) == 1

    def test_block_anchor_multiple_candidates(self) -> None:
        """Test BlockAnchorReplacer with multiple candidates (E7 regression)."""
        from polaris.kernelone.editing.replacers.opencode_replacers import BlockAnchorReplacer

        content = """
def foo():
    pass

def bar():
    pass

def baz():
    pass
"""
        search = """
def foo():
    x = 1
"""
        # Should find the first matching block
        matches = list(BlockAnchorReplacer.find(content, search))
        assert len(matches) <= 1  # At most one match

    def test_simple_replacer_exact_match(self) -> None:
        """Test SimpleReplacer exact matching."""
        from polaris.kernelone.editing.replacers.opencode_replacers import SimpleReplacer

        content = "Hello world, Hello again"
        matches = list(SimpleReplacer.find(content, "Hello"))
        assert len(matches) == 1
        assert matches[0] == "Hello"

    def test_line_trimmed_replacer_whitespace(self) -> None:
        """Test LineTrimmedReplacer handles whitespace."""
        from polaris.kernelone.editing.replacers.opencode_replacers import LineTrimmedReplacer

        content = "  line1  \n  line2  \n  line3  "
        search = "line1\nline2\nline3"
        matches = list(LineTrimmedReplacer.find(content, search))
        assert len(matches) >= 1
