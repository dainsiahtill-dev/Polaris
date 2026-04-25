"""IProviderRegistryPort - Port for LLM provider management.

ACGA 2.0 Section 6.3: KernelOne defines interface contracts,
infrastructure provides implementations.

This port abstracts the infrastructure ``ProviderManager`` singleton so that
``polaris.kernelone.llm.providers.registry`` does not need to reverse-import
``polaris.infrastructure.llm.providers.provider_registry.ProviderManager``.

Infrastructure registers the concrete manager during bootstrap; KernelOne
consumes it through this stable Protocol.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class IProviderRegistryPort(Protocol):
    """Protocol for LLM provider registry and lifecycle management.

    Mirrors the public surface of the infrastructure ``ProviderManager``
    that KernelOne needs at runtime.  By programming against this Protocol,
    ``kernelone.llm.providers.registry`` avoids importing infrastructure
    modules directly.

    Dependency direction::

        KernelOne  ──defines──▸  IProviderRegistryPort (this port)
        Infrastructure  ──implements──▸  ProviderManager
        Bootstrap  ──wires──▸  set_provider_manager_port(manager)

    Example::

        from polaris.kernelone.ports.provider_registry import IProviderRegistryPort

        async def list_providers(mgr: IProviderRegistryPort) -> list[str]:
            return mgr.list_provider_types()
    """

    # ── Registration ──────────────────────────────────────────────

    def register_provider(
        self,
        provider_type: str,
        provider_class: type,
    ) -> None:
        """Register a concrete provider class by type key.

        Args:
            provider_type: Canonical provider type key (e.g. ``"ollama"``).
            provider_class: The provider class to register.
        """
        ...

    # ── Lookup ────────────────────────────────────────────────────

    def get_provider_class(self, provider_type: str) -> type | None:
        """Return the registered provider class, or ``None``.

        Args:
            provider_type: Provider type key.
        """
        ...

    def get_provider_instance(self, provider_type: str) -> Any | None:
        """Return a cached provider instance, creating lazily if needed.

        Args:
            provider_type: Provider type key.
        """
        ...

    async def get_provider_instance_async(
        self,
        provider_type: str,
    ) -> Any | None:
        """Async variant of :meth:`get_provider_instance`.

        Args:
            provider_type: Provider type key.
        """
        ...

    def list_provider_types(self) -> list[str]:
        """Return all registered provider type keys."""
        ...

    def list_provider_info(self) -> list[Any]:
        """Return metadata dicts for every registered provider."""
        ...

    def get_provider_info(self, provider_type: str) -> Any | None:
        """Return metadata for a single provider type, or ``None``.

        Args:
            provider_type: Provider type key.
        """
        ...

    # ── Configuration helpers ─────────────────────────────────────

    def validate_provider_config(
        self,
        provider_type: str,
        config: dict[str, Any],
    ) -> bool:
        """Validate *config* against the registered provider class.

        Args:
            provider_type: Provider type key.
            config: Configuration dict to validate.

        Returns:
            ``True`` if valid, ``False`` otherwise.
        """
        ...

    def supports_feature(self, provider_type: str, feature: str) -> bool:
        """Check whether a provider advertises *feature*.

        Args:
            provider_type: Provider type key.
            feature: Feature name string.
        """
        ...

    def get_provider_default_config(
        self,
        provider_type: str,
    ) -> dict[str, Any] | None:
        """Return the default config dict for *provider_type*, or ``None``.

        Args:
            provider_type: Provider type key.
        """
        ...

    def get_provider_for_config(
        self,
        config: dict[str, Any],
    ) -> str | None:
        """Infer the provider type key from a config dict.

        Args:
            config: Configuration dict (may contain ``"type"`` key).
        """
        ...

    def migrate_legacy_config(
        self,
        legacy_config: dict[str, Any],
    ) -> dict[str, Any]:
        """Migrate a legacy configuration dict to the current format.

        Args:
            legacy_config: Old-format configuration dict.

        Returns:
            Migrated configuration dict.
        """
        ...

    def health_check_all(
        self,
        configs: dict[str, dict[str, Any]],
    ) -> dict[str, dict[str, Any]]:
        """Run health checks on all configured providers.

        Args:
            configs: Mapping of provider-id to config dict.

        Returns:
            Mapping of provider-id to health-check result dict.
        """
        ...

    def clear_instances(self) -> None:
        """Drop all cached provider instances (for testing / hot-reload)."""
        ...

    def reset_for_testing(self) -> None:
        """Reset the registry to a clean state (for test isolation).

        Implementations should clear all registrations **and** cached
        instances so the next test starts fresh.
        """
        ...
