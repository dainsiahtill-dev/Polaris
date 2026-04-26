"""Integration tests for ProviderRegistry and ProviderManager.

Covers:
- ProviderRegistry: register, get, list, duplicate registration, unknown queries
- ProviderManager: register_provider, get_provider_class, get_provider_instance,
  list_provider_types, list_provider_info, config inference, health checks
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest
from polaris.infrastructure.llm.providers.provider_registry import (
    ProviderManager,
    get_token_tracking_provider,
    provider_manager,
)
from polaris.kernelone.llm.providers import BaseProvider, ProviderInfo, ProviderRegistry
from polaris.kernelone.llm.types import HealthResult, InvokeResult, ModelListResult


class DummyProvider(BaseProvider):
    """A minimal provider for registry testing."""

    @classmethod
    def get_provider_info(cls) -> ProviderInfo:
        return ProviderInfo(
            name="Dummy Provider",
            type="dummy",
            description="For testing",
            version="1.0.0",
            author="Test",
            documentation_url="https://example.com",
            supported_features=["health_check"],
            cost_class="LOCAL",
            provider_category="LLM",
            autonomous_file_access=False,
            requires_file_interfaces=False,
            model_listing_method="NONE",
        )

    @classmethod
    def get_default_config(cls) -> dict[str, Any]:
        return {"type": "dummy"}

    @classmethod
    def validate_config(cls, config: dict[str, Any]) -> Any:
        from polaris.kernelone.llm.providers import ValidationResult

        return ValidationResult(valid=True, errors=[], warnings=[])

    def health(self, config: dict[str, Any]) -> HealthResult:
        return HealthResult(ok=True, latency_ms=1)

    def list_models(self, config: dict[str, Any]) -> ModelListResult:
        return ModelListResult(ok=True, supported=True, models=[])

    def invoke(self, prompt: str, model: str, config: dict[str, Any]) -> InvokeResult:
        from polaris.kernelone.llm.shared_contracts import Usage

        return InvokeResult(ok=True, output="dummy", latency_ms=1, usage=Usage())


class AnotherDummyProvider(BaseProvider):
    """Another minimal provider for re-registration testing."""

    @classmethod
    def get_provider_info(cls) -> ProviderInfo:
        return ProviderInfo(
            name="Another Dummy",
            type="another_dummy",
            description="Another test provider",
            version="1.0.0",
            author="Test",
            documentation_url="https://example.com",
            supported_features=["health_check"],
            cost_class="LOCAL",
            provider_category="LLM",
            autonomous_file_access=False,
            requires_file_interfaces=False,
            model_listing_method="NONE",
        )

    @classmethod
    def get_default_config(cls) -> dict[str, Any]:
        return {"type": "another_dummy"}

    @classmethod
    def validate_config(cls, config: dict[str, Any]) -> Any:
        from polaris.kernelone.llm.providers import ValidationResult

        return ValidationResult(valid=True, errors=[], warnings=[])

    def health(self, config: dict[str, Any]) -> HealthResult:
        return HealthResult(ok=True, latency_ms=1)

    def list_models(self, config: dict[str, Any]) -> ModelListResult:
        return ModelListResult(ok=True, supported=True, models=[])

    def invoke(self, prompt: str, model: str, config: dict[str, Any]) -> InvokeResult:
        from polaris.kernelone.llm.shared_contracts import Usage

        return InvokeResult(ok=True, output="another_dummy", latency_ms=1, usage=Usage())


class TestProviderRegistry:
    """Tests for the KernelOne ProviderRegistry."""

    def test_register_and_get_provider(self) -> None:
        registry = ProviderRegistry()
        registry.register("dummy", DummyProvider)

        provider_class = registry.get_provider("dummy")
        assert provider_class is DummyProvider

    def test_register_is_case_insensitive(self) -> None:
        registry = ProviderRegistry()
        registry.register("DUMMY", DummyProvider)

        provider_class = registry.get_provider("dummy")
        assert provider_class is DummyProvider

        provider_class = registry.get_provider("DUMMY")
        assert provider_class is DummyProvider

    def test_get_unknown_provider_returns_none(self) -> None:
        registry = ProviderRegistry()
        assert registry.get_provider("nonexistent") is None

    def test_get_empty_provider_type_returns_none(self) -> None:
        registry = ProviderRegistry()
        assert registry.get_provider("") is None

    def test_list_provider_types(self) -> None:
        registry = ProviderRegistry()
        registry.register("dummy", DummyProvider)
        registry.register("another", AnotherDummyProvider)

        types = registry.list_provider_types()
        assert sorted(types) == ["another", "dummy"]

    def test_list_providers(self) -> None:
        registry = ProviderRegistry()
        registry.register("dummy", DummyProvider)

        infos = registry.list_providers()
        assert len(infos) == 1
        assert infos[0].type == "dummy"
        assert infos[0].name == "Dummy Provider"

    def test_get_provider_info(self) -> None:
        registry = ProviderRegistry()
        registry.register("dummy", DummyProvider)

        info = registry.get_provider_info("dummy")
        assert info is not None
        assert info.type == "dummy"

    def test_get_provider_info_unknown_returns_none(self) -> None:
        registry = ProviderRegistry()
        assert registry.get_provider_info("nonexistent") is None

    def test_unregister_provider(self) -> None:
        registry = ProviderRegistry()
        registry.register("dummy", DummyProvider)
        assert registry.get_provider("dummy") is DummyProvider

        result = registry.unregister("dummy")
        assert result is True
        assert registry.get_provider("dummy") is None

    def test_unregister_unknown_returns_false(self) -> None:
        registry = ProviderRegistry()
        assert registry.unregister("nonexistent") is False

    def test_clear_registry(self) -> None:
        registry = ProviderRegistry()
        registry.register("dummy", DummyProvider)
        registry.register("another", AnotherDummyProvider)

        registry.clear()
        assert registry.list_provider_types() == []

    def test_register_empty_type_raises(self) -> None:
        registry = ProviderRegistry()
        with pytest.raises(ValueError, match="provider_type is required"):
            registry.register("", DummyProvider)

    def test_register_whitespace_type_raises(self) -> None:
        registry = ProviderRegistry()
        with pytest.raises(ValueError, match="provider_type is required"):
            registry.register("   ", DummyProvider)

    def test_duplicate_registration_overwrites(self) -> None:
        registry = ProviderRegistry()
        registry.register("dummy", DummyProvider)
        registry.register("dummy", AnotherDummyProvider)

        provider_class = registry.get_provider("dummy")
        assert provider_class is AnotherDummyProvider


class TestProviderManager:
    """Tests for the infrastructure ProviderManager."""

    def test_register_provider(self) -> None:
        manager = ProviderManager()
        manager.register_provider("dummy", DummyProvider)

        provider_class = manager.get_provider_class("dummy")
        assert provider_class is DummyProvider

    def test_register_provider_invalid_base_class(self) -> None:
        manager = ProviderManager()
        with pytest.raises(ValueError, match="must inherit from BaseProvider"):
            manager.register_provider("invalid", str)  # type: ignore[arg-type]

    def test_get_provider_class_unknown_returns_none(self) -> None:
        manager = ProviderManager()
        assert manager.get_provider_class("nonexistent") is None

    def test_get_provider_instance_creates_and_caches(self) -> None:
        manager = ProviderManager()
        manager.register_provider("dummy", DummyProvider)

        instance1 = manager.get_provider_instance("dummy")
        instance2 = manager.get_provider_instance("dummy")

        assert isinstance(instance1, DummyProvider)
        assert instance1 is instance2  # Cached

    def test_get_provider_instance_unknown_returns_none(self) -> None:
        manager = ProviderManager()
        assert manager.get_provider_instance("nonexistent") is None

    def test_list_provider_types(self) -> None:
        manager = ProviderManager()
        manager.register_provider("dummy", DummyProvider)
        manager.register_provider("another_dummy", AnotherDummyProvider)

        types = manager.list_provider_types()
        assert "dummy" in types
        assert "another_dummy" in types

    def test_list_provider_info(self) -> None:
        manager = ProviderManager()
        manager.register_provider("dummy", DummyProvider)

        infos = manager.list_provider_info()
        assert len(infos) >= 1
        dummy_info = next((i for i in infos if i.type == "dummy"), None)
        assert dummy_info is not None
        assert dummy_info.name == "Dummy Provider"

    def test_get_provider_info(self) -> None:
        manager = ProviderManager()
        manager.register_provider("dummy", DummyProvider)

        info = manager.get_provider_info("dummy")
        assert info is not None
        assert info.type == "dummy"

    def test_get_provider_info_unknown_returns_none(self) -> None:
        manager = ProviderManager()
        assert manager.get_provider_info("nonexistent") is None

    def test_validate_provider_config(self) -> None:
        manager = ProviderManager()
        manager.register_provider("dummy", DummyProvider)

        assert manager.validate_provider_config("dummy", {"type": "dummy"}) is True

    def test_validate_provider_config_unknown_returns_false(self) -> None:
        manager = ProviderManager()
        assert manager.validate_provider_config("nonexistent", {}) is False

    def test_get_provider_default_config(self) -> None:
        manager = ProviderManager()
        manager.register_provider("dummy", DummyProvider)

        config = manager.get_provider_default_config("dummy")
        assert config is not None
        assert config["type"] == "dummy"

    def test_get_provider_default_config_unknown_returns_none(self) -> None:
        manager = ProviderManager()
        assert manager.get_provider_default_config("nonexistent") is None

    def test_supports_feature(self) -> None:
        manager = ProviderManager()
        manager.register_provider("dummy", DummyProvider)

        assert manager.supports_feature("dummy", "health_check") is True
        assert manager.supports_feature("dummy", "nonexistent_feature") is False

    def test_supports_feature_unknown_returns_false(self) -> None:
        manager = ProviderManager()
        assert manager.supports_feature("nonexistent", "health_check") is False

    def test_normalize_provider_type(self) -> None:
        manager = ProviderManager()
        assert manager._normalize_provider_type("cli") == "codex_cli"
        assert manager._normalize_provider_type("codex_cli") == "codex_cli"
        assert manager._normalize_provider_type("  openai_compat  ") == "openai_compat"

    def test_get_provider_for_config_by_type(self) -> None:
        manager = ProviderManager()
        assert manager.get_provider_for_config({"type": "minimax"}) == "minimax"
        assert manager.get_provider_for_config({"type": "openai_compat"}) == "openai_compat"

    def test_get_provider_for_config_cli_gemini(self) -> None:
        manager = ProviderManager()
        result = manager.get_provider_for_config({"type": "cli", "command": "gemini --help"})
        assert result == "gemini_cli"

    def test_get_provider_for_config_cli_codex(self) -> None:
        manager = ProviderManager()
        result = manager.get_provider_for_config({"type": "cli", "command": "codex --help"})
        assert result == "codex_cli"

    def test_get_provider_for_config_by_command(self) -> None:
        manager = ProviderManager()
        assert manager.get_provider_for_config({"command": "codex"}) == "codex_cli"
        assert manager.get_provider_for_config({"command": "gemini"}) == "gemini_cli"

    def test_get_provider_for_config_by_base_url(self) -> None:
        manager = ProviderManager()
        assert manager.get_provider_for_config({"base_url": "https://api.minimaxi.com/v1"}) == "minimax"
        assert (
            manager.get_provider_for_config({"base_url": "https://generativelanguage.googleapis.com"}) == "gemini_api"
        )
        assert manager.get_provider_for_config({"base_url": "https://api.openai.com"}) == "openai_compat"
        assert manager.get_provider_for_config({"base_url": "https://api.anthropic.com"}) == "anthropic_compat"

    def test_get_provider_for_config_unknown_returns_none(self) -> None:
        manager = ProviderManager()
        assert manager.get_provider_for_config({}) is None

    def test_migrate_legacy_config(self) -> None:
        manager = ProviderManager()
        manager.register_provider("dummy", DummyProvider)

        legacy = {
            "providers": {
                "my_dummy": {"type": "dummy", "custom_key": "value"},
            }
        }
        migrated = manager.migrate_legacy_config(legacy)

        assert "providers" in migrated
        assert migrated["providers"]["my_dummy"]["type"] == "dummy"
        assert migrated["providers"]["my_dummy"]["custom_key"] == "value"

    def test_migrate_legacy_config_unknown_type_preserved(self) -> None:
        manager = ProviderManager()
        legacy = {
            "providers": {
                "unknown": {"some_key": "value"},
            }
        }
        migrated = manager.migrate_legacy_config(legacy)

        assert migrated["providers"]["unknown"]["some_key"] == "value"

    def test_health_check_all(self) -> None:
        manager = ProviderManager()
        manager.register_provider("dummy", DummyProvider)

        configs = {
            "test_dummy": {"type": "dummy"},
        }
        results = manager.health_check_all(configs)

        assert "test_dummy" in results
        assert results["test_dummy"]["ok"] is True

    def test_health_check_all_unknown_provider(self) -> None:
        manager = ProviderManager()
        configs = {
            "test_unknown": {"type": "nonexistent"},
        }
        results = manager.health_check_all(configs)

        assert "test_unknown" in results
        assert results["test_unknown"]["ok"] is False

    def test_clear_instances(self) -> None:
        manager = ProviderManager()
        manager.register_provider("dummy", DummyProvider)

        instance1 = manager.get_provider_instance("dummy")
        manager.clear_instances()
        instance2 = manager.get_provider_instance("dummy")

        assert instance1 is not instance2  # Fresh instance after clear

    def test_record_provider_failure(self) -> None:
        manager = ProviderManager()
        manager.register_provider("dummy", DummyProvider)

        # Get instance to establish it
        instance1 = manager.get_provider_instance("dummy")
        assert instance1 is not None

        # Record failures up to threshold
        for _ in range(3):
            manager.record_provider_failure("dummy")

        # After threshold, instance should be evicted on next get
        instance2 = manager.get_provider_instance("dummy")
        assert instance2 is not instance1

    def test_reset_for_testing(self) -> None:
        manager = ProviderManager()
        manager.register_provider("dummy", DummyProvider)

        instance = manager.get_provider_instance("dummy")
        assert instance is not None

        ProviderManager.reset_for_testing()
        # After reset, a new instance may be created but cache is cleared
        assert True  # Just verify no exception

    def test_re_registration_updates_class(self) -> None:
        manager = ProviderManager()
        manager.register_provider("dummy", DummyProvider)

        instance1 = manager.get_provider_instance("dummy")
        assert isinstance(instance1, DummyProvider)

        # Re-register with different class
        manager.register_provider("dummy", AnotherDummyProvider)
        instance2 = manager.get_provider_instance("dummy")
        assert isinstance(instance2, AnotherDummyProvider)

    def test_get_provider_instance_async(self) -> None:
        import asyncio

        manager = ProviderManager()
        manager.register_provider("dummy", DummyProvider)

        async def _test() -> BaseProvider | None:
            return await manager.get_provider_instance_async("dummy")

        instance = asyncio.run(_test())
        assert isinstance(instance, DummyProvider)


class TestGetTokenTrackingProvider:
    """Tests for get_token_tracking_provider helper."""

    def test_returns_none_for_unknown_provider(self) -> None:
        result = get_token_tracking_provider("nonexistent")
        assert result is None

    def test_returns_raw_instance_without_budget(self, monkeypatch: pytest.MonkeyPatch) -> None:
        manager = ProviderManager()
        manager.register_provider("dummy", DummyProvider)

        monkeypatch.setattr(
            "polaris.infrastructure.llm.providers.provider_registry.provider_manager",
            manager,
        )

        result = get_token_tracking_provider("dummy")
        assert result is not None
        assert isinstance(result, DummyProvider)

    def test_returns_wrapped_instance_with_budget(self, monkeypatch: pytest.MonkeyPatch) -> None:
        manager = ProviderManager()
        manager.register_provider("dummy", DummyProvider)

        monkeypatch.setattr(
            "polaris.infrastructure.llm.providers.provider_registry.provider_manager",
            manager,
        )

        # Mock TokenTrackingWrapper to avoid import issues
        mock_wrapper_class = MagicMock()
        mock_wrapper_class.return_value = MagicMock()

        monkeypatch.setattr(
            "polaris.infrastructure.llm.token_tracking_wrapper.TokenTrackingWrapper",
            mock_wrapper_class,
        )

        result = get_token_tracking_provider("dummy", budget_limit=1000)
        assert result is not None
        mock_wrapper_class.assert_called_once()


class TestProviderManagerDefaultProviders:
    """Tests that the default provider manager has expected providers."""

    def test_default_providers_registered(self) -> None:
        types = provider_manager.list_provider_types()
        expected = [
            "codex_sdk",
            "codex_cli",
            "gemini_cli",
            "minimax",
            "kimi",
            "gemini_api",
            "ollama",
            "openai_compat",
            "anthropic_compat",
        ]
        for expected_type in expected:
            assert expected_type in types, f"Expected provider {expected_type} not registered"

    def test_get_openai_compat_provider_info(self) -> None:
        info = provider_manager.get_provider_info("openai_compat")
        assert info is not None
        assert info.type == "openai_compat"

    def test_get_anthropic_compat_provider_info(self) -> None:
        info = provider_manager.get_provider_info("anthropic_compat")
        assert info is not None
        assert info.type == "anthropic_compat"

    def test_get_codex_sdk_provider_info(self) -> None:
        info = provider_manager.get_provider_info("codex_sdk")
        assert info is not None
        assert info.type == "codex_sdk"

    def test_get_minimax_provider_info(self) -> None:
        info = provider_manager.get_provider_info("minimax")
        assert info is not None
        assert info.type == "minimax"

    def test_get_unknown_provider_info_returns_none(self) -> None:
        info = provider_manager.get_provider_info("totally_nonexistent")
        assert info is None

    def test_get_provider_instance_for_defaults(self) -> None:
        # Verify we can instantiate all default providers
        for provider_type in provider_manager.list_provider_types():
            instance = provider_manager.get_provider_instance(provider_type)
            assert instance is not None, f"Failed to instantiate {provider_type}"
            assert isinstance(instance, BaseProvider)
