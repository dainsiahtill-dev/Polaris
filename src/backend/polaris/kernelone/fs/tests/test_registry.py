"""Concurrency tests for the KernelOne filesystem adapter registry."""

from __future__ import annotations

import threading
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import MagicMock

import pytest
from polaris.kernelone.fs import registry
from polaris.kernelone.fs.registry import (
    DefaultAdapterInitializationError,
    get_default_adapter,
    reset_default_adapter,
    set_adapter_factory,
)


@pytest.fixture(autouse=True)
def isolated_registry(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Start every test with no configured singleton or lazy-init outcome."""
    reset_default_adapter()
    monkeypatch.setattr(registry, "_adapter_factory", None)
    yield
    reset_default_adapter()


def test_concurrent_getters_publish_one_complete_adapter() -> None:
    """Fifty-six concurrent readers share one factory result after publication."""
    adapter = MagicMock()
    factory_started = threading.Event()
    release_factory = threading.Event()
    factory_calls = 0
    factory_calls_lock = threading.Lock()

    def factory() -> MagicMock:
        nonlocal factory_calls
        with factory_calls_lock:
            factory_calls += 1
        factory_started.set()
        assert release_factory.wait(timeout=5)
        return adapter

    set_adapter_factory(factory)
    with ThreadPoolExecutor(max_workers=56) as executor:
        futures = [executor.submit(get_default_adapter) for _ in range(56)]
        assert factory_started.wait(timeout=5)
        assert not any(future.done() for future in futures)
        release_factory.set()
        results = [future.result(timeout=5) for future in futures]

    assert factory_calls == 1
    assert all(result is adapter for result in results)


def test_reader_waits_until_initializer_publishes_adapter() -> None:
    """A reader never returns while the initializer owns an unpublished adapter."""
    adapter = MagicMock()
    factory_started = threading.Event()
    release_factory = threading.Event()
    reader_finished = threading.Event()

    def factory() -> MagicMock:
        factory_started.set()
        assert release_factory.wait(timeout=5)
        return adapter

    set_adapter_factory(factory)
    with ThreadPoolExecutor(max_workers=2) as executor:
        initializer = executor.submit(get_default_adapter)
        assert factory_started.wait(timeout=5)

        def read_adapter() -> object:
            result = get_default_adapter()
            reader_finished.set()
            return result

        reader = executor.submit(read_adapter)
        assert not reader_finished.wait(timeout=0.1)
        release_factory.set()
        assert initializer.result(timeout=5) is adapter
        assert reader.result(timeout=5) is adapter


def test_failed_initialization_is_stable_until_explicit_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Failures expose their cause and do not publish a partial singleton."""
    adapter = MagicMock()
    factory_calls = 0
    fallback_calls = 0

    def factory() -> MagicMock:
        nonlocal factory_calls
        factory_calls += 1
        if factory_calls == 1:
            raise ValueError("factory unavailable")
        return adapter

    def unavailable_fallback() -> MagicMock:
        nonlocal fallback_calls
        fallback_calls += 1
        raise OSError("fallback unavailable")

    monkeypatch.setattr(registry, "_create_fallback_adapter", unavailable_fallback)
    set_adapter_factory(factory)

    with pytest.raises(DefaultAdapterInitializationError) as first_error:
        get_default_adapter()
    assert isinstance(first_error.value.__cause__, OSError)
    assert registry._default_adapter is None

    with pytest.raises(DefaultAdapterInitializationError) as repeated_error:
        get_default_adapter()
    assert isinstance(repeated_error.value.__cause__, OSError)
    assert factory_calls == 1
    assert fallback_calls == 1

    reset_default_adapter()
    assert get_default_adapter() is adapter
    assert factory_calls == 2


def test_repeated_calls_after_lazy_initialization_reuse_published_adapter() -> None:
    """A successful lazy initialization is idempotent for subsequent callers."""
    adapter = MagicMock()
    factory = MagicMock(return_value=adapter)
    set_adapter_factory(factory)

    assert get_default_adapter() is adapter
    assert get_default_adapter() is adapter
    factory.assert_called_once_with()
