"""Tests for polaris.infrastructure.di.container module."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from polaris.infrastructure.di.container import DIContainer, get_container, reset_container


class TestDIContainerRegistration:
    def test_register_instance(self):
        container = DIContainer()
        instance = {"test": "data"}
        container.register_instance(dict, instance)
        assert container.has_registration(dict)

    def test_register_factory(self):
        container = DIContainer()
        container.register_factory(str, lambda c: "hello", is_singleton=False)
        assert container.has_registration(str)

    def test_register_singleton(self):
        container = DIContainer()
        container.register_singleton(int, lambda c: 42)
        assert container.has_registration(int)

    def test_register_transient(self):
        container = DIContainer()
        container.register_transient(float, lambda c: 3.14)
        assert container.has_registration(float)

    def test_has_registration_false(self):
        container = DIContainer()
        assert not container.has_registration(list)

    def test_clear_removes_all(self):
        container = DIContainer()
        container.register_instance(str, "hello")
        container.clear()
        assert not container.has_registration(str)


class TestDIContainerResolve:
    def test_resolve_singleton_instance(self):
        container = DIContainer()
        instance = {"key": "value"}
        container.register_instance(dict, instance)
        result = container.resolve(dict)
        assert result is instance

    def test_resolve_missing_raises(self):
        container = DIContainer()
        with pytest.raises(KeyError, match="No registration"):
            container.resolve(list)

    def test_resolve_async_factory(self):
        container = DIContainer()

        async def factory(c):
            return "async_result"

        container.register_factory(str, factory, is_singleton=False)
        result = asyncio.run(container.resolve_async(str))
        assert result == "async_result"

    def test_resolve_async_singleton_caches(self):
        container = DIContainer()
        call_count = 0

        async def factory(c):
            nonlocal call_count
            call_count += 1
            return f"instance_{call_count}"

        container.register_singleton(str, factory)
        result1 = asyncio.run(container.resolve_async(str))
        result2 = asyncio.run(container.resolve_async(str))
        assert result1 == result2
        assert call_count == 1

    def test_resolve_async_transient_new_each_time(self):
        container = DIContainer()
        call_count = 0

        async def factory(c):
            nonlocal call_count
            call_count += 1
            return f"instance_{call_count}"

        container.register_transient(str, factory)
        result1 = asyncio.run(container.resolve_async(str))
        result2 = asyncio.run(container.resolve_async(str))
        assert result1 != result2
        assert call_count == 2

    def test_resolve_sync_factory_raises_for_async(self):
        container = DIContainer()

        async def factory(c):
            return "async"

        container.register_factory(str, factory, is_singleton=False)
        with pytest.raises(RuntimeError, match="Use resolve_async"):
            container.resolve(str)

    def test_resolve_async_awaitable_factory(self):
        container = DIContainer()
        container.register_factory(str, lambda c: asyncio.sleep(0, result="done"), is_singleton=False)
        result = asyncio.run(container.resolve_async(str))
        assert result == "done"


class TestDIContainerFindRegistration:
    def test_find_by_identity(self):
        container = DIContainer()
        container.register_instance(str, "hello")
        reg = container._find_registration(str)
        assert reg is not None

    def test_find_by_module_qualname_fallback(self):
        container = DIContainer()
        # Register with one class object
        container.register_instance(str, "hello")
        # Create a different str class reference (simulating reload)
        # In practice, module reload creates a new type object
        # Here we test the fallback path with a mock
        mock_type = MagicMock()
        mock_type.__module__ = str.__module__
        mock_type.__qualname__ = str.__qualname__
        reg = container._find_registration(mock_type)
        assert reg is not None

    def test_find_no_module_raises(self):
        container = DIContainer()
        mock_type = MagicMock()
        mock_type.__module__ = ""
        mock_type.__qualname__ = "test"
        reg = container._find_registration(mock_type)
        assert reg is None


class TestDIContainerConcurrency:
    def test_singleton_thread_safe(self):
        container = DIContainer()
        call_count = 0

        async def slow_factory(c):
            nonlocal call_count
            call_count += 1
            await asyncio.sleep(0.01)
            return f"instance_{call_count}"

        container.register_singleton(str, slow_factory)

        async def resolve_multiple():
            return await asyncio.gather(
                container.resolve_async(str),
                container.resolve_async(str),
                container.resolve_async(str),
            )

        results = asyncio.run(resolve_multiple())
        assert all(r == results[0] for r in results)
        assert call_count == 1


class TestGlobalContainer:
    @pytest.mark.asyncio
    async def test_get_container_returns_same_instance(self):
        reset_container()
        c1 = await get_container()
        c2 = await get_container()
        assert c1 is c2

    def test_reset_container_creates_new_next_time(self):
        reset_container()
        c1 = asyncio.run(get_container())
        reset_container()
        c2 = asyncio.run(get_container())
        assert c1 is not c2

    def test_get_container_is_di_container(self):
        reset_container()
        container = asyncio.run(get_container())
        assert isinstance(container, DIContainer)


class TestDIContainerEdgeCases:
    def test_register_instance_with_none(self):
        container = DIContainer()
        container.register_instance(str, None)
        result = container.resolve(str)
        assert result is None

    def test_resolve_singleton_with_none_instance(self):
        container = DIContainer()
        container.register_factory(str, lambda c: None, is_singleton=True)
        result1 = asyncio.run(container.resolve_async(str))
        result2 = asyncio.run(container.resolve_async(str))
        assert result1 is None
        assert result2 is None

    def test_multiple_registrations_last_wins(self):
        container = DIContainer()
        container.register_instance(str, "first")
        container.register_instance(str, "second")
        result = container.resolve(str)
        assert result == "second"

    def test_factory_receives_container(self):
        container = DIContainer()
        received = None

        def factory(c):
            nonlocal received
            received = c
            return "hello"

        container.register_transient(str, factory)
        asyncio.run(container.resolve_async(str))
        assert received is container

    def test_clear_then_register_again(self):
        container = DIContainer()
        container.register_instance(str, "first")
        container.clear()
        container.register_instance(str, "second")
        result = container.resolve(str)
        assert result == "second"
