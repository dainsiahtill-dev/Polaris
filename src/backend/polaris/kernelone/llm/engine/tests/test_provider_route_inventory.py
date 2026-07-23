from __future__ import annotations

from polaris.infrastructure.llm.providers.provider_registry import ProviderManager
from polaris.kernelone.llm.engine.provider_route_inventory import (
    classify_factory_provider_route,
    factory_provider_route_inventory,
    factory_provider_route_policy_error,
)
from polaris.kernelone.llm.providers import BaseProvider


def test_static_inventory_classifies_every_registered_provider_mode_exactly_once() -> None:
    inventory = factory_provider_route_inventory()
    provider_types = [entry.provider_type for entry in inventory]

    assert len(provider_types) == len(set(provider_types))
    assert set(ProviderManager().list_provider_types()) <= set(provider_types)
    for entry in inventory:
        assert classify_factory_provider_route(entry.provider_type, mode="invoke") == entry.invoke
        assert classify_factory_provider_route(entry.provider_type, mode="stream") == entry.stream


def test_opaque_cli_sdk_and_unprojected_native_routes_are_factory_disabled() -> None:
    for entry in factory_provider_route_inventory():
        for mode, classification in (("invoke", entry.invoke), ("stream", entry.stream)):
            if classification != "factory_disabled_opaque":
                continue
            assert (
                factory_provider_route_policy_error(
                    entry.provider_type,
                    mode=mode,
                    physical_dispatch_port=object(),  # type: ignore[arg-type]
                )
                == f"factory_provider_route_disabled_opaque:{entry.provider_type}:{mode}"
            )


def test_unprojected_gemini_codex_sdk_and_ollama_modes_fail_closed() -> None:
    for provider_type in ("gemini_api", "codex_sdk", "ollama"):
        assert classify_factory_provider_route(provider_type, mode="invoke") == "factory_disabled_opaque"
        assert classify_factory_provider_route(provider_type, mode="stream") == "factory_disabled_opaque"


def test_dynamic_registration_cannot_promote_uninventoried_factory_route() -> None:
    assert (
        factory_provider_route_policy_error(
            "plugin_transport",
            mode="invoke",
            physical_dispatch_port=object(),  # type: ignore[arg-type]
        )
        == "factory_provider_route_uninventoried:plugin_transport:invoke"
    )


def test_same_name_registration_cannot_replace_factory_trusted_implementation() -> None:
    manager = ProviderManager()
    trusted = manager.get_provider_instance("openai_compat")
    replacement = manager.get_provider_class("anthropic_compat")
    assert trusted is not None
    assert replacement is not None
    assert manager.is_factory_default_provider_implementation("openai_compat", trusted) is True

    manager.register_provider("openai_compat", replacement)
    replaced = manager.get_provider_instance("openai_compat")

    assert replaced is not None
    assert manager.is_factory_default_provider_implementation("openai_compat", replaced) is False


def test_virtual_default_registration_cannot_self_seal_replacement() -> None:
    class _PresealedHijackManager(ProviderManager):
        override_called = False

        def _register_default_providers(self) -> dict[str, type[BaseProvider]]:
            type(self).override_called = True
            replacement = ProviderManager().get_provider_class("anthropic_compat")
            assert replacement is not None
            self.register_provider("openai_compat", replacement)
            return {"openai_compat": replacement}

    manager = _PresealedHijackManager()
    provider = manager.get_factory_default_provider_instance("openai_compat")

    assert _PresealedHijackManager.override_called is False
    assert provider is not None
    assert manager.is_factory_default_provider_implementation("openai_compat", provider) is True


def test_non_factory_admin_or_ordinary_route_is_not_restricted() -> None:
    assert (
        factory_provider_route_policy_error(
            "codex_cli",
            mode="invoke",
            physical_dispatch_port=None,
        )
        == ""
    )
