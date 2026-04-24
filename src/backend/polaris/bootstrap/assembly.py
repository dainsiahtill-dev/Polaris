"""Application assembly and DI wiring."""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from polaris.bootstrap.config import Settings, get_settings

# Lazy import to avoid cross-cell internal import at module level.
# ArchiveSink will be imported inside _register_uep_sinks() where it's needed.
from polaris.cells.audit.evidence.public.service import bind_audit_llm_to_task_service
from polaris.cells.director.execution.public.service import (
    DirectorConfig,
    DirectorService,
    TaskQueueConfig,
    TaskService,
    WorkerPoolConfig,
    WorkerService,
)
from polaris.cells.orchestration.pm_planning.public.service import PMService
from polaris.cells.storage.layout import PolarisStorageLayout as StorageLayout
from polaris.cells.storage.layout.public.service import resolve_polaris_roots
from polaris.domain.services.background_task import BackgroundTaskService
from polaris.infrastructure.audit.adapters.store_adapter import AuditStoreAdapter
from polaris.infrastructure.audit.sinks.audit_hash_sink import AuditHashSink
from polaris.infrastructure.di.container import DIContainer, get_container
from polaris.infrastructure.di.factories import register_all_factories
from polaris.infrastructure.llm.adapters.local_embedding_adapter import LocalTransformerEmbeddingAdapter
from polaris.infrastructure.llm.adapters.ollama_embedding_adapter import OllamaEmbeddingAdapter
from polaris.infrastructure.llm.adapters.ollama_runtime_adapter import OllamaRuntimeAdapter
from polaris.infrastructure.llm.adapters.stub_embedding_adapter import StubEmbeddingAdapter
from polaris.infrastructure.llm.provider_bootstrap import inject_kernelone_provider_runtime
from polaris.infrastructure.log_pipeline.journal_sink import JournalSink
from polaris.infrastructure.log_pipeline.llm_realtime_bridge import (
    LogPipelineLLMRealtimeBridge,
)
from polaris.infrastructure.storage import LocalFileSystemAdapter, get_storage_adapter
from polaris.kernelone.audit.registry import set_audit_store_factory
from polaris.kernelone.events.message_bus import MessageBus
from polaris.kernelone.events.realtime_bridge import set_llm_realtime_bridge
from polaris.kernelone.events.registry import set_global_bus
from polaris.kernelone.events.typed import (
    get_default_adapter as get_default_typed_event_adapter,
    get_default_registry,
    init_default_adapter as init_typed_event_adapter,
)
from polaris.kernelone.fs.registry import set_default_adapter
from polaris.kernelone.llm.embedding import set_default_embedding_port
from polaris.kernelone.process.ollama_utils import set_default_ollama_adapter
from polaris.kernelone.storage import register_business_roots_resolver

logger = logging.getLogger(__name__)


def ensure_minimal_kernelone_bindings() -> None:
    """Install the minimal Polaris runtime bindings for standalone tooling.

    This keeps CLI/offline tooling aligned with Polaris's `.polaris`
    storage contract without requiring full backend assembly.
    """
    from polaris.kernelone._runtime_config import set_workspace_metadata_dir_name
    from polaris.kernelone.audit.registry import has_audit_store_factory
    from polaris.kernelone.events.realtime_bridge import get_llm_realtime_bridge

    set_workspace_metadata_dir_name(".polaris")

    try:
        from polaris.kernelone.fs import get_default_adapter

        get_default_adapter()
    except RuntimeError:
        set_default_adapter(LocalFileSystemAdapter())

    if not has_audit_store_factory():
        set_audit_store_factory(AuditStoreAdapter)

    register_business_roots_resolver(resolve_polaris_roots)  # type: ignore[arg-type]

    # Ensure LLM realtime event bridge is initialized for runtime artifact persistence.
    # Without this, events are silently dropped and no runtime artifacts are produced.
    if get_llm_realtime_bridge() is None:
        set_llm_realtime_bridge(LogPipelineLLMRealtimeBridge())


def _inject_embedding_port(settings: Settings) -> None:
    """Inject the best available embedding port."""
    del settings
    set_default_ollama_adapter(None)

    # 1. Try Local GPU Transformer
    try:
        import torch
    except ImportError:
        torch = None
    except OSError as exc:
        logger.debug("PyTorch import unavailable during embedding injection: %s", exc)
        torch = None
    if torch is not None:
        try:
            if torch.cuda.is_available():
                port = LocalTransformerEmbeddingAdapter()
                set_default_embedding_port(port)
                logger.info(
                    "Injected LocalTransformerEmbeddingPort (GPU): %s",
                    port.get_fingerprint(),
                )
                return
        except (AttributeError, OSError, RuntimeError) as exc:
            logger.debug("Local transformer embedding injection unavailable: %s", exc)

    # 2. Try Ollama
    try:
        import requests
    except ImportError:
        requests = None
    if requests is not None:
        try:
            host = os.environ.get("OLLAMA_HOST", "http://120.24.117.59:11434")
            resp = requests.get(f"{host}/api/tags", timeout=2)
            if resp.status_code == 200:
                set_default_ollama_adapter(OllamaRuntimeAdapter())
                port = OllamaEmbeddingAdapter()  # type: ignore[assignment]
                set_default_embedding_port(port)
                logger.info("Injected OllamaEmbeddingPort: %s", port.get_fingerprint())
                return
        except (OSError, RuntimeError, ValueError, requests.RequestException) as exc:
            logger.debug("Ollama embedding injection unavailable: %s", exc)

    # 3. Fallback to Stub
    port = StubEmbeddingAdapter()  # type: ignore[assignment]
    set_default_embedding_port(port)
    logger.warning("No real embedding infrastructure found. Injected StubEmbeddingPort.")


def _ensure_typed_event_bridge(message_bus: MessageBus) -> None:
    """Ensure typed-events bridge is globally initialized in dual-write mode."""
    if get_default_typed_event_adapter() is not None:
        return
    init_typed_event_adapter(
        message_bus=message_bus,
        event_registry=get_default_registry(),
        dual_write=True,
    )


def _create_director_service_for_workspace(
    settings: Settings,
    workspace: str | Path,
    message_bus: MessageBus,
) -> DirectorService:
    workspace_value = str(Path(str(workspace)).resolve())
    max_workers = max(1, int(settings.director.max_parallel_tasks or 3))

    task_service = TaskService(
        config=TaskQueueConfig(default_timeout_seconds=settings.pm.director_timeout),
        workspace=workspace_value,
    )
    try:
        bind_audit_llm_to_task_service(
            task_service=task_service,
            settings=settings,
            workspace=workspace_value,
        )
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        logger.warning("Failed to bind audit LLM caller for workspace %s: %s", workspace_value, exc)
    worker_pool_config = WorkerPoolConfig(
        min_workers=1,
        max_workers=max_workers,
    )
    worker_service = WorkerService(
        config=worker_pool_config,
        workspace=workspace_value,
        task_service=task_service,
    )
    director_config = DirectorConfig(
        workspace=workspace_value,
        max_workers=worker_pool_config.max_workers,
        token_budget=None,
    )
    return DirectorService(
        config=director_config,
        message_bus=message_bus,
        task_service=task_service,
        worker_service=worker_service,
    )


async def rebind_director_service(workspace: str | Path) -> DirectorService:
    """Recreate DirectorService singleton for the target workspace."""
    container = await get_container()
    settings = container.resolve(Settings)
    target_workspace = str(Path(str(workspace)).resolve())

    existing = await container.resolve_async(DirectorService)
    current_workspace = str(Path(str(existing.config.workspace)).resolve())
    if current_workspace == target_workspace:
        return existing

    try:
        state_name = str(getattr(existing, "state", "")).upper()
        if "RUNNING" in state_name:
            await existing.stop()
    except (AttributeError, OSError, RuntimeError, TypeError, ValueError) as exc:
        logger.debug("Failed to stop existing Director service: %s", exc)

    message_bus = await container.resolve_async(MessageBus)
    service = _create_director_service_for_workspace(settings, target_workspace, message_bus)
    container.register_instance(DirectorService, service)
    return service


def assemble_core_services(container: DIContainer | None = None, settings: Settings | None = None) -> None:
    """Wire up core infrastructure and application services.

    This function sets up DI registrations and performs infrastructure-to-kernelone port injections.
    """
    if settings is None:
        settings = get_settings()

    # 0. Register singleton factories for DI pattern
    if container is not None:
        register_all_factories(container)

    # 1. Kernel Ports injection
    ensure_minimal_kernelone_bindings()

    # Inject message bus to kernelone
    bus = (
        container.resolve(MessageBus)
        if (container is not None and container.has_registration(MessageBus))
        else MessageBus()
    )
    set_global_bus(bus)
    _ensure_typed_event_bridge(bus)

    # Inject LLM realtime bridge for runtime.v2 / JetStream streaming.
    set_llm_realtime_bridge(LogPipelineLLMRealtimeBridge())

    # Inject infrastructure provider implementations into KernelOne explicitly.
    inject_kernelone_provider_runtime()
    try:
        set_default_ollama_adapter(OllamaRuntimeAdapter())
    except (OSError, RuntimeError, ValueError) as exc:
        logger.debug("Failed to inject Ollama runtime adapter: %s", exc)

    # 2. Base configurations
    if container is not None and not container.has_registration(Settings):
        container.register_instance(Settings, settings)

    if container is not None and not container.has_registration(StorageLayout):
        storage_layout = StorageLayout(
            workspace=settings.workspace,
            runtime_base=settings.runtime_base,
        )
        container.register_instance(StorageLayout, storage_layout)

    # MessageBus injection
    if container is not None and not container.has_registration(MessageBus):
        container.register_instance(MessageBus, bus)

    # Inject embedding port after core config registration so container always
    # exposes Settings/Storage even when embedding initialization is slow/fails.
    try:
        _inject_embedding_port(settings)
    except (OSError, RuntimeError, ValueError) as exc:
        logger.warning("Embedding port injection failed, continuing with existing port: %s", exc)

    # 3. Application Services
    if container is not None and not container.has_registration(PMService):
        container.register_singleton(
            PMService,
            lambda c: PMService(
                settings=c.resolve(Settings),
                storage_layout=c.resolve(StorageLayout),
            ),
        )

    if container is not None and not container.has_registration(DirectorService):

        def _build_director_service(c: DIContainer) -> DirectorService:
            s = c.resolve(Settings)
            bus = c.resolve(MessageBus)
            return _create_director_service_for_workspace(s, s.workspace, bus)

        container.register_singleton(DirectorService, _build_director_service)

    if container is not None and not container.has_registration(BackgroundTaskService):
        container.register_singleton(
            BackgroundTaskService,
            lambda c: BackgroundTaskService.with_defaults(get_storage_adapter(str(c.resolve(Settings).workspace))),
        )

    # 4. UEP v2.0 Sinks registration
    _register_uep_sinks(bus)


_uep_sinks: list[Any] = []


def _register_uep_sinks(bus: MessageBus) -> None:
    """Register Unified Event Pipeline v2.0 consumers on the MessageBus.

    This wires JournalSink, ArchiveSink, and AuditHashSink so that
    all runtime event producers produce consistent output regardless
    of entry point (benchmark, CLI, API).
    """
    global _uep_sinks
    if _uep_sinks:
        return

    import asyncio

    from polaris.cells.archive.run_archive.public.service import create_archive_sink

    journal_sink = JournalSink(bus)
    archive_sink = create_archive_sink(bus)
    audit_hash_sink = AuditHashSink(bus)

    _uep_sinks = [journal_sink, archive_sink, audit_hash_sink]

    async def _start_sinks() -> None:
        for sink in _uep_sinks:
            await sink.start()

    try:
        loop = asyncio.get_running_loop()
        _sink_task = loop.create_task(_start_sinks())  # noqa: RUF006
        logger.info("UEP v2.0 sinks registered and starting")
    except RuntimeError:
        # No running loop; sink start will be triggered later by the caller
        logger.debug("UEP v2.0 sinks registered (no event loop yet)")
