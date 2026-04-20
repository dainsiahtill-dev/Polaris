"""Unit tests for Hot-swap mechanism."""

from __future__ import annotations

import pytest

from polaris.kernelone.role.hotswap import (
    FallbackChain,
    HotSwapContext,
    HotSwapEngine,
    PromptModifier,
    SwapEvent,
    SwapReason,
    get_hot_swap_engine,
)


class TestSwapEvent:
    """Tests for SwapEvent dataclass."""

    def test_swap_event_creation(self) -> None:
        """Test creating a SwapEvent."""
        event = SwapEvent(
            from_profession="software_engineer",
            to_profession="python_principal_architect",
            reason=SwapReason.USER_REQUEST,
            context_snapshot={"session_id": "123"},
        )

        assert event.from_profession == "software_engineer"
        assert event.to_profession == "python_principal_architect"
        assert event.reason == SwapReason.USER_REQUEST


class TestFallbackChain:
    """Tests for FallbackChain dataclass."""

    def test_get_all(self) -> None:
        """Test getting all professions in fallback order."""
        chain = FallbackChain(
            primary="python_principal_architect",
            fallbacks=["software_engineer", "default"],
        )

        all_professions = chain.get_all()
        assert all_professions == [
            "python_principal_architect",
            "software_engineer",
            "default",
        ]

    def test_empty_fallbacks(self) -> None:
        """Test fallback chain with no fallbacks."""
        chain = FallbackChain(primary="software_engineer")
        assert chain.get_all() == ["software_engineer"]


class TestPromptModifier:
    """Tests for PromptModifier dataclass."""

    def test_priority_ordering(self) -> None:
        """Test that modifiers can be sorted by priority."""
        modifiers = [
            PromptModifier(modifier_type="format", content="a", priority=0),
            PromptModifier(modifier_type="format", content="b", priority=10),
            PromptModifier(modifier_type="format", content="c", priority=5),
        ]

        sorted_modifiers = sorted(modifiers, key=lambda m: m.priority)
        assert sorted_modifiers[0].priority == 0
        assert sorted_modifiers[1].priority == 5
        assert sorted_modifiers[2].priority == 10


class TestHotSwapContext:
    """Tests for HotSwapContext."""

    def test_record_swap(self) -> None:
        """Test recording a swap event."""
        context = HotSwapContext(active_profession="")

        event = SwapEvent(
            from_profession="engineer_a",
            to_profession="engineer_b",
            reason=SwapReason.USER_REQUEST,
            context_snapshot={},
        )
        context.record_swap(event)

        assert len(context.swap_history) == 1
        assert context.active_profession == "engineer_b"

    def test_can_swap_to_rate_limiting(self) -> None:
        """Test rate limiting for swaps."""
        context = HotSwapContext(active_profession="initial")

        # Record 5 swaps to the same profession
        for _ in range(5):
            event = SwapEvent(
                from_profession="other",
                to_profession="target",
                reason=SwapReason.USER_REQUEST,
                context_snapshot={},
            )
            context.record_swap(event)

        # Should be blocked now
        assert context.can_swap_to("target", max_swaps=5) is False
        # Different profession should still work
        assert context.can_swap_to("other", max_swaps=5) is True

    def test_add_and_get_modifiers(self) -> None:
        """Test adding and getting modifiers."""
        context = HotSwapContext(active_profession="test")

        context.add_modifier(
            PromptModifier(modifier_type="format", content="low", priority=1)
        )
        context.add_modifier(
            PromptModifier(modifier_type="format", content="high", priority=10)
        )

        modifiers = context.get_modifiers()
        assert len(modifiers) == 2
        assert modifiers[0].priority == 1
        assert modifiers[1].priority == 10

    def test_clear_modifiers(self) -> None:
        """Test clearing modifiers."""
        context = HotSwapContext(active_profession="test")

        context.add_modifier(
            PromptModifier(modifier_type="format", content="a", priority=0)
        )
        context.clear_modifiers()

        assert len(context.get_modifiers()) == 0


class TestHotSwapEngine:
    """Tests for HotSwapEngine."""

    def setup_method(self) -> None:
        """Setup fresh engine for each test."""
        self.engine = HotSwapEngine()

    def test_swap_success(self) -> None:
        """Test successful swap."""
        result = self.engine.swap(
            session_id="session_1",
            new_profession="python_principal_architect",
            reason=SwapReason.USER_REQUEST,
            context_snapshot={},
        )

        assert result is True
        history = self.engine.get_swap_history("session_1")
        assert len(history) == 1
        assert history[0].to_profession == "python_principal_architect"

    def test_swap_rate_limited(self) -> None:
        """Test swap is rate limited."""
        # First 5 swaps should succeed
        for i in range(5):
            result = self.engine.swap(
                session_id="session_1",
                new_profession="target",
                reason=SwapReason.USER_REQUEST,
                context_snapshot={},
            )
            assert result is True

        # 6th swap to same profession should fail
        result = self.engine.swap(
            session_id="session_1",
            new_profession="target",
            reason=SwapReason.USER_REQUEST,
            context_snapshot={},
        )
        assert result is False

    def test_swap_to_different_profession_allowed_after_rate_limit(self) -> None:
        """Test that swapping to different profession is allowed after rate limit."""
        # Hit rate limit on "target"
        for _ in range(5):
            self.engine.swap(
                session_id="session_1",
                new_profession="target",
                reason=SwapReason.USER_REQUEST,
                context_snapshot={},
            )

        # Different profession should still work
        result = self.engine.swap(
            session_id="session_1",
            new_profession="other",
            reason=SwapReason.USER_REQUEST,
            context_snapshot={},
        )
        assert result is True

    def test_swap_with_fallback(self) -> None:
        """Test swap with fallback chain."""
        # Register fallback chain
        chain = FallbackChain(
            primary="python_principal_architect",
            fallbacks=["software_engineer", "default"],
        )
        self.engine.register_fallback_chain("python_principal_architect", chain)

        # Primary should work if allowed
        result = self.engine.swap_with_fallback(
            session_id="session_1",
            primary_profession="python_principal_architect",
            reason=SwapReason.USER_REQUEST,
            context_snapshot={},
        )
        assert result == "python_principal_architect"

    def test_swap_with_fallback_primary_fails_uses_fallback(self) -> None:
        """Test fallback is used when primary is rate limited."""
        # Register fallback chain
        chain = FallbackChain(
            primary="target",
            fallbacks=["fallback_profession"],
        )
        self.engine.register_fallback_chain("target", chain)

        # Hit rate limit on primary
        for _ in range(5):
            self.engine.swap(
                session_id="session_1",
                new_profession="target",
                reason=SwapReason.USER_REQUEST,
                context_snapshot={},
            )

        # Should use fallback
        result = self.engine.swap_with_fallback(
            session_id="session_1",
            primary_profession="target",
            reason=SwapReason.USER_REQUEST,
            context_snapshot={},
        )
        assert result == "fallback_profession"

    def test_rollback(self) -> None:
        """Test rollback to previous profession."""
        # Do some swaps
        self.engine.swap(
            session_id="session_1",
            new_profession="a",
            reason=SwapReason.USER_REQUEST,
            context_snapshot={},
        )
        self.engine.swap(
            session_id="session_1",
            new_profession="b",
            reason=SwapReason.USER_REQUEST,
            context_snapshot={},
        )

        # Rollback should go back to "a"
        result = self.engine.rollback(session_id="session_1", context_snapshot={})
        assert result is True

        history = self.engine.get_swap_history("session_1")
        assert len(history) == 3  # 2 swaps + 1 rollback
        assert history[-1].to_profession == "a"

    def test_rollback_no_history(self) -> None:
        """Test rollback with no history fails."""
        result = self.engine.rollback(session_id="session_no_history", context_snapshot={})
        assert result is False

    def test_get_or_create_context(self) -> None:
        """Test context creation."""
        context = self.engine.get_or_create_context("session_new")
        assert context is not None
        assert context.swap_history == []

        # Same session should return same context
        context2 = self.engine.get_or_create_context("session_new")
        assert context is context2

    def test_clear_context(self) -> None:
        """Test clearing context."""
        self.engine.swap(
            session_id="session_1",
            new_profession="a",
            reason=SwapReason.USER_REQUEST,
            context_snapshot={},
        )

        self.engine.clear_context("session_1")

        history = self.engine.get_swap_history("session_1")
        assert len(history) == 0

    def test_add_modifier(self) -> None:
        """Test adding modifier through engine."""
        self.engine.add_modifier(
            session_id="session_1",
            modifier_type="format",
            content="override format",
            priority=5,
        )

        modifiers = self.engine.get_modifiers("session_1")
        assert len(modifiers) == 1
        assert modifiers[0].content == "override format"


class TestGetHotSwapEngine:
    """Tests for get_hot_swap_engine singleton."""

    def test_returns_singleton(self) -> None:
        """Test that get_hot_swap_engine returns singleton."""
        engine1 = get_hot_swap_engine()
        engine2 = get_hot_swap_engine()
        assert engine1 is engine2
