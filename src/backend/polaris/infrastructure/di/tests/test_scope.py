"""Tests for DIContainerScope - test isolation through scoped lifecycle management."""

from __future__ import annotations

import threading
from unittest.mock import MagicMock

import pytest
from polaris.infrastructure.di.scope import (
    DIContainerScope,
    ScopeContext,
    cleanup_all_scopes,
    get_current_scope,
    get_or_create_scope,
    register_resetter,
    reset_all_global_state,
)


class TestDIContainerScope:
    """Tests for DIContainerScope class."""

    def test_create_scope(self) -> None:
        """Test basic scope creation."""
        scope = DIContainerScope(name="test_scope")
        assert scope.name == "test_scope"
        assert not scope.is_cleaned_up
        scope.cleanup_scope()

    def test_register_singleton_sync(self) -> None:
        """Test registering a synchronous singleton."""
        scope = DIContainerScope()
        created_instances: list[int] = []

        def factory() -> MagicMock:
            mock = MagicMock()
            created_instances.append(id(mock))
            return mock

        scope.register_singleton(MagicMock, factory)
        instance1 = scope.resolve(MagicMock)
        instance2 = scope.resolve(MagicMock)

        # Should be same instance (singleton)
        assert instance1 is instance2
        # Factory called only once
        assert len(created_instances) == 1
        scope.cleanup_scope()

    def test_register_instance(self) -> None:
        """Test registering a pre-created instance."""
        scope = DIContainerScope()
        pre_created = MagicMock()

        scope.register_instance(MagicMock, pre_created)
        resolved = scope.resolve(MagicMock)

        assert resolved is pre_created
        scope.cleanup_scope()

    def test_resolve_unknown_raises_keyerror(self) -> None:
        """Test resolving unknown interface raises KeyError."""
        scope = DIContainerScope()

        with pytest.raises(KeyError):
            scope.resolve(MagicMock)

        scope.cleanup_scope()

    def test_cleanup_scope(self) -> None:
        """Test scope cleanup clears instances."""
        scope = DIContainerScope()
        mock_instance = MagicMock()

        scope.register_instance(MagicMock, mock_instance)
        scope.resolve(MagicMock)
        assert not scope.is_cleaned_up

        scope.cleanup_scope()
        assert scope.is_cleaned_up

        # After cleanup, resolving should raise
        with pytest.raises(RuntimeError, match="cleaned-up scope"):
            scope.resolve(MagicMock)

    def test_registration_after_cleanup_raises(self) -> None:
        """Test registering after cleanup raises RuntimeError."""
        scope = DIContainerScope()
        scope.cleanup_scope()

        with pytest.raises(RuntimeError, match="cleaned-up scope"):
            scope.register_singleton(MagicMock, MagicMock)

    def test_context_manager(self) -> None:
        """Test scope as context manager."""
        with DIContainerScope() as scope:
            assert not scope.is_cleaned_up
        assert scope.is_cleaned_up

    def test_concurrent_resolution(self) -> None:
        """Test concurrent singleton resolution is thread-safe."""
        scope = DIContainerScope()
        factory_call_count = 0
        lock = threading.Lock()

        def factory() -> MagicMock:
            nonlocal factory_call_count
            with lock:
                factory_call_count += 1
            return MagicMock()

        scope.register_singleton(MagicMock, factory)

        results: list[MagicMock | None] = [None, None, None]
        barrier = threading.Barrier(3)
        threads: list[threading.Thread] = []

        def resolve_in_thread(idx: int) -> None:
            barrier.wait()  # Synchronize threads
            results[idx] = scope.resolve(MagicMock)

        for i in range(3):
            t = threading.Thread(target=resolve_in_thread, args=(i,))
            threads.append(t)
            t.start()

        for t in threads:
            t.join()

        # All should get same instance
        assert results[0] is results[1] is results[2]
        # Factory should be called exactly once
        assert factory_call_count == 1
        scope.cleanup_scope()

    def test_scope_isolation(self) -> None:
        """Test two scopes have isolated singletons."""
        scope1 = DIContainerScope(name="scope1")
        scope2 = DIContainerScope(name="scope2")

        mock1 = MagicMock()
        mock2 = MagicMock()

        scope1.register_instance(MagicMock, mock1)
        scope2.register_instance(MagicMock, mock2)

        assert scope1.resolve(MagicMock) is mock1
        assert scope2.resolve(MagicMock) is mock2
        assert scope1.resolve(MagicMock) is not scope2.resolve(MagicMock)

        scope1.cleanup_scope()
        scope2.cleanup_scope()

    def test_has_registration(self) -> None:
        """Test has_registration returns correct status."""
        scope = DIContainerScope()
        scope.register_singleton(MagicMock, MagicMock)

        assert scope.has_registration(MagicMock)
        assert not scope.has_registration(int)
        scope.cleanup_scope()

    def test_get_registration_count(self) -> None:
        """Test registration count tracking."""
        scope = DIContainerScope()
        assert scope.get_registration_count() == 0

        scope.register_singleton(MagicMock, MagicMock)
        assert scope.get_registration_count() == 1

        scope.register_singleton(int, lambda: 42)
        assert scope.get_registration_count() == 2

        scope.cleanup_scope()

    def test_multiple_cleanup_calls_safe(self) -> None:
        """Test multiple cleanup calls don't raise."""
        scope = DIContainerScope()
        scope.cleanup_scope()
        scope.cleanup_scope()  # Should not raise
        scope.cleanup_scope()  # Should not raise


class TestScopeContext:
    """Tests for ScopeContext async context manager."""

    @pytest.mark.asyncio
    async def test_async_context(self) -> None:
        """Test async context manager creates and cleans up scope."""
        async with ScopeContext(name="async_test") as scope:
            assert isinstance(scope, DIContainerScope)
            assert not scope.is_cleaned_up
            assert get_current_scope() is scope
        assert scope.is_cleaned_up

    @pytest.mark.asyncio
    async def test_async_context_registers_singleton(self) -> None:
        """Test singleton registration in async context."""
        async with ScopeContext() as scope:
            scope.register_singleton(MagicMock, MagicMock)
            instance = await scope.resolve_async(MagicMock)
            assert isinstance(instance, MagicMock)
        assert scope.is_cleaned_up

    @pytest.mark.asyncio
    async def test_nested_scope_contexts(self) -> None:
        """Test nested scope contexts."""
        async with ScopeContext(name="outer") as outer_scope:
            outer_scope.register_instance(MagicMock, MagicMock())

            async with ScopeContext(name="inner") as inner_scope:
                inner_scope.register_instance(MagicMock, MagicMock())

                # Each scope should have its own instance
                outer_instance = outer_scope.resolve(MagicMock)
                inner_instance = inner_scope.resolve(MagicMock)
                assert outer_instance is not inner_instance


class TestGlobalScopeFunctions:
    """Tests for global scope management functions."""

    def test_cleanup_all_scopes(self) -> None:
        """Test cleanup_all_scopes clears all scopes."""
        scope1 = DIContainerScope(name="cleanup_1")
        scope2 = DIContainerScope(name="cleanup_2")

        scope1.register_instance(MagicMock, MagicMock())
        scope2.register_instance(MagicMock, MagicMock())

        count = cleanup_all_scopes()
        assert count == 2

        # Scopes should be cleaned up
        assert scope1.is_cleaned_up
        assert scope2.is_cleaned_up

    def test_get_or_create_scope_existing(self) -> None:
        """Test get_or_create_scope returns existing scope."""
        scope = DIContainerScope(name="existing")
        try:
            # Manually set in context for this test
            from polaris.infrastructure.di.scope import _current_scope

            token = _current_scope.set(scope)
            try:
                result = get_or_create_scope()
                assert result is scope
            finally:
                _current_scope.reset(token)
        finally:
            scope.cleanup_scope()

    def test_get_or_create_scope_creates_new(self) -> None:
        """Test get_or_create_scope creates new scope when none exists."""
        # Ensure no current scope
        from polaris.infrastructure.di.scope import _current_scope

        token = _current_scope.set(None)
        try:
            result = get_or_create_scope()
            assert isinstance(result, DIContainerScope)
            assert result.is_cleaned_up or not result.is_cleaned_up  # Just check it's a scope
            result.cleanup_scope()
        finally:
            _current_scope.reset(token)


class TestGlobalStateResetters:
    """Tests for global state resetter registration."""

    def test_register_resetter(self) -> None:
        """Test registering a resetter."""
        called = False

        def my_resetter() -> None:
            nonlocal called
            called = True

        register_resetter("test_resetter", my_resetter)
        results = reset_all_global_state()

        assert "test_resetter" in results
        assert results["test_resetter"] is True
        assert called

    def test_reset_all_handles_exceptions(self) -> None:
        """Test reset_all_global_state handles resetter exceptions."""

        def bad_resetter() -> None:
            raise RuntimeError("Reset failed")

        register_resetter("bad_resetter", bad_resetter)
        results = reset_all_global_state()

        assert "bad_resetter" in results
        assert results["bad_resetter"] is False
