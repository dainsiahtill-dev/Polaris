from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from polaris.bootstrap.config import Settings
from polaris.infrastructure.llm.provider_bootstrap import (
    ProviderAdapter,
    inject_kernelone_provider_runtime,
)
from polaris.kernelone.llm.providers import (
    BaseProvider,
    ProviderInfo,
    ValidationResult,
)
from polaris.infrastructure.llm.providers.provider_registry import (
    ProviderManager as InfrastructureProviderManager,
)
from polaris.kernelone.llm.providers import get_provider_manager
from polaris.kernelone.llm.toolkit.contracts import ServiceLocator
from polaris.kernelone.llm.types import HealthResult, InvokeResult, ModelListResult, Usage


def _import_assembly_module():
    try:
        from polaris.bootstrap import assembly as assembly_module
    except (ImportError, OSError, RuntimeError, ValueError) as exc:  # pragma: no cover
        pytest.skip(f"bootstrap assembly import unavailable outside KernelOne scope: {exc}")
    return assembly_module


def _import_backend_bootstrapper():
    try:
        from polaris.bootstrap.backend_bootstrap import BackendBootstrapper
        from polaris.domain.models.config_snapshot import ConfigSnapshot
        from polaris.infrastructure.di.container import DIContainer
    except (ImportError, OSError, RuntimeError, ValueError) as exc:  # pragma: no cover
        pytest.skip(f"backend bootstrap import unavailable outside KernelOne scope: {exc}")
    return BackendBootstrapper, ConfigSnapshot, DIContainer


class _BootstrapTestProvider(BaseProvider):
    @classmethod
    def get_provider_info(cls) -> ProviderInfo:
        return ProviderInfo(
            name="Bootstrap Test Provider",
            type="bootstrap_test",
            description="bootstrap test provider",
            version="1.0",
            author="tests",
            documentation_url="",
            supported_features=[],
            cost_class="LOCAL",
            provider_category="LLM",
            autonomous_file_access=False,
            requires_file_interfaces=False,
            model_listing_method="NONE",
        )

    @classmethod
    def get_default_config(cls) -> dict[str, object]:
        return {}

    @classmethod
    def validate_config(cls, config: dict[str, object]) -> ValidationResult:
        del config
        return ValidationResult(valid=True, errors=[], warnings=[], normalized_config={})

    def health(self, config: dict[str, object]) -> HealthResult:
        del config
        return HealthResult(ok=True, latency_ms=1)

    def list_models(self, config: dict[str, object]) -> ModelListResult:
        del config
        return ModelListResult(ok=True, models=[])

    def invoke(self, prompt: str, model: str, config: dict[str, object]) -> InvokeResult:
        del prompt, model, config
        return InvokeResult(ok=True, output="ok", latency_ms=1, usage=Usage())


def test_inject_kernelone_provider_runtime_syncs_registry_and_service_locator() -> None:
    """Test that inject_kernelone_provider_runtime publishes adapter to ServiceLocator.

    After Phase 3 convergence, get_provider_manager() returns the infrastructure
    singleton. Custom managers passed to inject_kernelone_provider_runtime() are
    used directly for ServiceLocator injection - they are NOT synced to the
    singleton (the singleton is the single source of truth).
    """
    previous_provider = ServiceLocator.get_provider()
    manager = InfrastructureProviderManager()
    provider_type = f"bootstrap_test_{uuid4().hex}"
    manager.register_provider(provider_type, _BootstrapTestProvider)

    try:
        inject_kernelone_provider_runtime(manager)

        # ServiceLocator should have the adapter using the passed custom manager
        runtime_provider = ServiceLocator.get_provider()
        assert isinstance(runtime_provider, ProviderAdapter)
        assert runtime_provider.manager is manager

        # The passed manager should have the test provider registered
        provider_class = manager.get_provider_class(provider_type)
        assert provider_class is _BootstrapTestProvider
    finally:
        ServiceLocator._provider = previous_provider  # type: ignore[attr-defined]


def test_assemble_core_services_injects_provider_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    assembly_module = _import_assembly_module()
    _, _, DIContainer = _import_backend_bootstrapper()
    injected: list[str] = []

    def _record_injection() -> None:
        injected.append("called")

    monkeypatch.setattr(assembly_module, "inject_kernelone_provider_runtime", _record_injection)
    monkeypatch.setattr(assembly_module, "_inject_embedding_port", lambda settings: None)

    container = DIContainer()
    settings = Settings()

    assembly_module.assemble_core_services(container, settings=settings)

    assert injected == ["called"]


def test_assemble_core_services_registers_business_storage_resolver(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assembly_module = _import_assembly_module()
    _, _, DIContainer = _import_backend_bootstrapper()
    registered: list[object] = []

    monkeypatch.setattr(assembly_module, "inject_kernelone_provider_runtime", lambda: None)
    monkeypatch.setattr(assembly_module, "_inject_embedding_port", lambda settings: None)
    monkeypatch.setattr(assembly_module, "register_business_roots_resolver", registered.append)

    container = DIContainer()
    settings = Settings()

    assembly_module.assemble_core_services(container, settings=settings)

    assert registered == [assembly_module.resolve_polaris_roots]


def test_assemble_core_services_initializes_typed_event_bridge(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assembly_module = _import_assembly_module()
    _, _, DIContainer = _import_backend_bootstrapper()

    from polaris.kernelone.events import typed as typed_events
    from polaris.kernelone.events.typed import bus_adapter as typed_bus_adapter
    from polaris.kernelone.events.typed import registry as typed_registry

    monkeypatch.setattr(assembly_module, "inject_kernelone_provider_runtime", lambda: None)
    monkeypatch.setattr(assembly_module, "_inject_embedding_port", lambda settings: None)
    monkeypatch.setattr(typed_bus_adapter, "_default_adapter", None)
    monkeypatch.setattr(typed_registry, "_default_registry", None)

    container = DIContainer()
    settings = Settings()
    assembly_module.assemble_core_services(container, settings=settings)

    adapter = typed_events.get_default_adapter()
    assert adapter is not None
    assert getattr(adapter, "_bus", None) is container.resolve(assembly_module.MessageBus)


@pytest.mark.asyncio
async def test_backend_bootstrap_create_application_injects_provider_runtime(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    BackendBootstrapper, ConfigSnapshot, _ = _import_backend_bootstrapper()
    injected: list[str] = []

    def _record_injection() -> None:
        injected.append("called")

    import polaris.delivery.http.app_factory as app_factory

    monkeypatch.setattr(
        "polaris.bootstrap.backend_bootstrap.inject_kernelone_provider_runtime",
        _record_injection,
    )
    monkeypatch.setattr(
        app_factory,
        "create_app",
        lambda settings: SimpleNamespace(state=SimpleNamespace(settings=settings)),
    )

    snapshot = ConfigSnapshot.merge_sources(
        default={
            "workspace": str(tmp_path),
            "server.host": "127.0.0.1",
            "server.port": 49977,
            "logging.level": "INFO",
            "llm.model": "test-model",
            "llm.provider": "ollama",
            "pm.backend": "auto",
        }
    )

    bootstrapper = BackendBootstrapper()
    app = await bootstrapper._create_application(snapshot)

    assert injected == ["called"]
    assert Path(str(app.state.settings.workspace)).resolve() == tmp_path.resolve()
