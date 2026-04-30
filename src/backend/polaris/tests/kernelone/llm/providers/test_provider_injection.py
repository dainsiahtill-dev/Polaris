"""Tests for provider_injection module."""

from __future__ import annotations

import threading
from typing import Any

from polaris.kernelone.llm.providers.provider_injection import (
    get_provider_manager_port,
    has_provider_manager_port,
    reset_provider_manager_port,
    set_provider_manager_port,
)
from polaris.kernelone.ports.provider_registry import IProviderRegistryPort


class MockProviderRegistry:
    """Mock implementation of IProviderRegistryPort for testing."""

    def register_provider(self, provider_type: str, provider_class: type) -> None:
        pass

    def get_provider_class(self, provider_type: str) -> type | None:
        return None

    def get_provider_instance(self, provider_type: str) -> Any | None:
        return None

    async def get_provider_instance_async(self, provider_type: str) -> Any | None:
        return None

    def list_provider_types(self) -> list[str]:
        return []

    def list_provider_info(self) -> list[Any]:
        return []

    def get_provider_info(self, provider_type: str) -> Any | None:
        return None

    def validate_provider_config(self, provider_type: str, config: dict[str, Any]) -> bool:
        return True

    def supports_feature(self, provider_type: str, feature: str) -> bool:
        return False

    def get_provider_default_config(self, provider_type: str) -> dict[str, Any] | None:
        return None

    def get_provider_for_config(self, config: dict[str, Any]) -> str | None:
        return None

    def migrate_legacy_config(self, legacy_config: dict[str, Any]) -> dict[str, Any]:
        return legacy_config

    def health_check_all(self, configs: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
        return {}

    def clear_instances(self) -> None:
        pass

    def reset_for_testing(self) -> None:
        pass


class TestProviderInjectionBasic:
    """Basic tests for provider injection functions."""

    def setup_method(self) -> None:
        reset_provider_manager_port()

    def teardown_method(self) -> None:
        reset_provider_manager_port()

    def test_get_returns_none_initially(self) -> None:
        assert get_provider_manager_port() is None

    def test_has_returns_false_initially(self) -> None:
        assert has_provider_manager_port() is False

    def test_set_and_get(self) -> None:
        mock = MockProviderRegistry()
        set_provider_manager_port(mock)
        assert get_provider_manager_port() is mock

    def test_set_and_has(self) -> None:
        mock = MockProviderRegistry()
        set_provider_manager_port(mock)
        assert has_provider_manager_port() is True

    def test_reset_clears_port(self) -> None:
        mock = MockProviderRegistry()
        set_provider_manager_port(mock)
        reset_provider_manager_port()
        assert get_provider_manager_port() is None
        assert has_provider_manager_port() is False

    def test_set_overwrites_previous(self) -> None:
        mock1 = MockProviderRegistry()
        mock2 = MockProviderRegistry()
        set_provider_manager_port(mock1)
        set_provider_manager_port(mock2)
        assert get_provider_manager_port() is mock2

    def test_reset_idempotent(self) -> None:
        reset_provider_manager_port()
        reset_provider_manager_port()
        assert get_provider_manager_port() is None


class TestProviderInjectionProtocol:
    """Tests for protocol compliance."""

    def setup_method(self) -> None:
        reset_provider_manager_port()

    def teardown_method(self) -> None:
        reset_provider_manager_port()

    def test_mock_implements_protocol(self) -> None:
        mock = MockProviderRegistry()
        assert isinstance(mock, IProviderRegistryPort)

    def test_set_accepts_protocol_instance(self) -> None:
        mock = MockProviderRegistry()
        set_provider_manager_port(mock)
        assert get_provider_manager_port() is mock

    def test_returns_same_protocol_instance(self) -> None:
        mock = MockProviderRegistry()
        set_provider_manager_port(mock)
        result = get_provider_manager_port()
        assert isinstance(result, IProviderRegistryPort)


class TestProviderInjectionThreadSafety:
    """Thread-safety tests for provider injection."""

    def teardown_method(self) -> None:
        reset_provider_manager_port()

    def test_concurrent_set_operations(self) -> None:
        """Multiple threads setting should not corrupt state."""
        reset_provider_manager_port()
        mocks = [MockProviderRegistry() for _ in range(10)]
        threads = []

        for mock in mocks:
            t = threading.Thread(target=set_provider_manager_port, args=(mock,))
            threads.append(t)

        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # After all threads complete, port should be one of the mocks
        result = get_provider_manager_port()
        assert result in mocks

    def test_concurrent_get_operations(self) -> None:
        """Multiple threads reading should not crash."""
        mock = MockProviderRegistry()
        set_provider_manager_port(mock)
        results = []

        def read_port() -> None:
            results.append(get_provider_manager_port())

        threads = [threading.Thread(target=read_port) for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert all(r is mock for r in results)

    def test_concurrent_set_and_reset(self) -> None:
        """Concurrent sets and resets should not crash."""
        mock = MockProviderRegistry()
        errors = []

        def set_port() -> None:
            try:
                set_provider_manager_port(mock)
            except RuntimeError as e:
                errors.append(e)

        def reset_port() -> None:
            try:
                reset_provider_manager_port()
            except RuntimeError as e:
                errors.append(e)

        threads = []
        for _ in range(10):
            threads.append(threading.Thread(target=set_port))
            threads.append(threading.Thread(target=reset_port))

        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors

    def test_has_is_thread_safe(self) -> None:
        mock = MockProviderRegistry()
        set_provider_manager_port(mock)
        results = []

        def check_has() -> None:
            results.append(has_provider_manager_port())

        threads = [threading.Thread(target=check_has) for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert all(r is True for r in results)


class TestProviderInjectionEdgeCases:
    """Edge case tests for provider injection."""

    def teardown_method(self) -> None:
        reset_provider_manager_port()

    def test_set_none_explicitly(self) -> None:
        set_provider_manager_port(None)
        assert get_provider_manager_port() is None

    def test_get_after_set_none(self) -> None:
        mock = MockProviderRegistry()
        set_provider_manager_port(mock)
        set_provider_manager_port(None)
        assert get_provider_manager_port() is None

    def test_has_after_set_none(self) -> None:
        mock = MockProviderRegistry()
        set_provider_manager_port(mock)
        set_provider_manager_port(None)
        assert has_provider_manager_port() is False

    def test_multiple_resets_no_error(self) -> None:
        for _ in range(100):
            reset_provider_manager_port()
        assert get_provider_manager_port() is None

    def test_set_same_instance_multiple_times(self) -> None:
        mock = MockProviderRegistry()
        for _ in range(10):
            set_provider_manager_port(mock)
        assert get_provider_manager_port() is mock

    def test_return_type_hint(self) -> None:
        mock = MockProviderRegistry()
        set_provider_manager_port(mock)
        result = get_provider_manager_port()
        assert result is not None
        # Verify it has expected protocol methods
        assert hasattr(result, "register_provider")
        assert hasattr(result, "get_provider_class")
        assert hasattr(result, "list_provider_types")

    def test_set_different_types_sequentially(self) -> None:
        class MinimalRegistry:
            def register_provider(self, provider_type: str, provider_class: type) -> None:
                pass

            def get_provider_class(self, provider_type: str) -> type | None:
                return None

            def get_provider_instance(self, provider_type: str) -> Any | None:
                return None

            async def get_provider_instance_async(self, provider_type: str) -> Any | None:
                return None

            def list_provider_types(self) -> list[str]:
                return []

            def list_provider_info(self) -> list[Any]:
                return []

            def get_provider_info(self, provider_type: str) -> Any | None:
                return None

            def validate_provider_config(self, provider_type: str, config: dict[str, Any]) -> bool:
                return True

            def supports_feature(self, provider_type: str, feature: str) -> bool:
                return False

            def get_provider_default_config(self, provider_type: str) -> dict[str, Any] | None:
                return None

            def get_provider_for_config(self, config: dict[str, Any]) -> str | None:
                return None

            def migrate_legacy_config(self, legacy_config: dict[str, Any]) -> dict[str, Any]:
                return legacy_config

            def health_check_all(self, configs: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
                return {}

            def clear_instances(self) -> None:
                pass

            def reset_for_testing(self) -> None:
                pass

        minimal = MinimalRegistry()
        set_provider_manager_port(minimal)
        assert get_provider_manager_port() is minimal
        mock = MockProviderRegistry()
        set_provider_manager_port(mock)
        assert get_provider_manager_port() is mock
