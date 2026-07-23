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
from polaris.infrastructure.llm.providers.provider_registry import (
    ProviderManager as InfrastructureProviderManager,
)
from polaris.kernelone.llm.engine.contracts import bind_physical_provider_dispatch_port
from polaris.kernelone.llm.providers import (
    BaseProvider,
    ProviderConfigValidationResult,
    ProviderInfo,
)
from polaris.kernelone.llm.toolkit.contracts import AIRequest, ServiceLocator, TaskType
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
    def validate_config(cls, config: dict[str, object]) -> ProviderConfigValidationResult:
        del config
        return ProviderConfigValidationResult(valid=True, errors=[], warnings=[], normalized_config={})

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


@pytest.mark.asyncio
async def test_provider_adapter_blocks_opaque_factory_route_before_instance_resolution(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from polaris.kernelone.llm import config_store as llm_config

    manager = InfrastructureProviderManager()
    instance_lookups: list[str] = []
    monkeypatch.setattr(
        manager,
        "get_provider_instance",
        lambda provider_type: instance_lookups.append(provider_type),
    )
    monkeypatch.setattr(
        llm_config,
        "resolve_workspace_cache_root_for_workspace",
        lambda _workspace: tmp_path / "cache",
    )
    monkeypatch.setattr(
        llm_config,
        "load_llm_config",
        lambda *_args, **_kwargs: {
            "providers": {
                "opaque-provider": {
                    "type": "codex_cli",
                }
            }
        },
    )
    adapter = ProviderAdapter(manager)
    request = AIRequest(
        task_type=TaskType.DIALOGUE,
        role="director",
        provider_id="opaque-provider",
        model="gpt-codex",
        input="must-not-reach-sdk",
        context={"workspace": str(tmp_path)},
    )

    with (
        bind_physical_provider_dispatch_port(object()),  # type: ignore[arg-type]
        pytest.raises(
            RuntimeError,
            match="factory_provider_route_disabled_opaque:codex_cli:invoke",
        ),
    ):
        await adapter.generate(request)

    assert instance_lookups == []


@pytest.mark.asyncio
@pytest.mark.parametrize("stream", [False, True])
async def test_provider_adapter_rejects_same_name_replacement_before_provider_call(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    stream: bool,
) -> None:
    from polaris.kernelone.llm import config_store as llm_config

    manager = InfrastructureProviderManager()
    constructor_calls: list[str] = []
    monkeypatch.setattr(
        _BootstrapTestProvider,
        "__init__",
        lambda _self: constructor_calls.append("constructed"),
    )
    manager.register_provider("openai_compat", _BootstrapTestProvider)
    monkeypatch.setattr(
        _BootstrapTestProvider,
        "invoke",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("replacement must not be invoked")),
    )
    monkeypatch.setattr(
        llm_config,
        "resolve_workspace_cache_root_for_workspace",
        lambda _workspace: tmp_path / "cache",
    )
    monkeypatch.setattr(
        llm_config,
        "load_llm_config",
        lambda *_args, **_kwargs: {
            "providers": {
                "replacement-provider": {
                    "type": "openai_compat",
                }
            }
        },
    )
    adapter = ProviderAdapter(manager)
    request = AIRequest(
        task_type=TaskType.DIALOGUE,
        role="director",
        provider_id="replacement-provider",
        model="replacement-model",
        input="must-not-reach-replacement",
        context={"workspace": str(tmp_path)},
    )

    with (
        bind_physical_provider_dispatch_port(object()),  # type: ignore[arg-type]
        pytest.raises(
            RuntimeError,
            match=f"factory_provider_route_implementation_untrusted:openai_compat:{'stream' if stream else 'invoke'}",
        ),
    ):
        if stream:
            async for _chunk in adapter.generate_stream(request):
                pass
        else:
            await adapter.generate(request)

    assert constructor_calls == []


def test_assemble_core_services_injects_provider_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    assembly_module = _import_assembly_module()
    _, _, di_container = _import_backend_bootstrapper()
    injected: list[str] = []

    def _record_injection() -> None:
        injected.append("called")

    monkeypatch.setattr(assembly_module, "inject_kernelone_provider_runtime", _record_injection)
    monkeypatch.setattr(assembly_module, "_inject_embedding_port", lambda settings: None)

    container = di_container()
    settings = Settings()

    assembly_module.assemble_core_services(container, settings=settings)

    assert injected == ["called"]


def test_assemble_core_services_registers_business_storage_resolver(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assembly_module = _import_assembly_module()
    _, _, di_container = _import_backend_bootstrapper()
    registered: list[object] = []

    monkeypatch.setattr(assembly_module, "inject_kernelone_provider_runtime", lambda: None)
    monkeypatch.setattr(assembly_module, "_inject_embedding_port", lambda settings: None)
    monkeypatch.setattr(assembly_module, "register_business_roots_resolver", registered.append)

    container = di_container()
    settings = Settings()

    assembly_module.assemble_core_services(container, settings=settings)

    assert registered == [assembly_module.resolve_polaris_roots]


def test_assemble_core_services_rebinds_explicit_settings_and_storage(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    assembly_module = _import_assembly_module()
    _, _, di_container = _import_backend_bootstrapper()

    monkeypatch.setattr(assembly_module, "inject_kernelone_provider_runtime", lambda: None)
    monkeypatch.setattr(assembly_module, "_inject_embedding_port", lambda settings: None)

    old_workspace = tmp_path / "old-workspace"
    new_workspace = tmp_path / "new-workspace"
    old_workspace.mkdir()
    new_workspace.mkdir()

    container = di_container()
    old_settings = Settings(workspace=str(old_workspace))
    new_settings = Settings(workspace=str(new_workspace))

    assembly_module.assemble_core_services(container, settings=old_settings)
    assembly_module.assemble_core_services(container, settings=new_settings)

    assert container.resolve(Settings) is new_settings
    storage = container.resolve(assembly_module.StorageLayout)
    assert Path(str(storage.workspace)).resolve() == new_workspace.resolve()


def test_assemble_core_services_initializes_typed_event_bridge(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assembly_module = _import_assembly_module()
    _, _, di_container = _import_backend_bootstrapper()

    from polaris.kernelone.events import typed as typed_events
    from polaris.kernelone.events.typed import bus_adapter as typed_bus_adapter, registry as typed_registry

    monkeypatch.setattr(assembly_module, "inject_kernelone_provider_runtime", lambda: None)
    monkeypatch.setattr(assembly_module, "_inject_embedding_port", lambda settings: None)
    monkeypatch.setattr(typed_bus_adapter, "_default_adapter", None)
    monkeypatch.setattr(typed_registry, "_default_registry", None)

    container = di_container()
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
    backend_bootstrapper_cls, config_snapshot_cls, _ = _import_backend_bootstrapper()
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

    snapshot = config_snapshot_cls.merge_sources(
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

    bootstrapper = backend_bootstrapper_cls()
    app = await bootstrapper._create_application(snapshot)

    assert injected == ["called"]
    assert Path(str(app.state.settings.workspace)).resolve() == tmp_path.resolve()
