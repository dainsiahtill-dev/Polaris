"""FastAPI application factory for Polaris backend.

Canonical delivery-layer API gateway implementation.
Bootstrap and ASGI entrypoints delegate to this module.
"""

from __future__ import annotations

import asyncio
import logging
import os
import secrets as _secrets
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from polaris.bootstrap.config import Settings, get_settings
from polaris.cells.runtime.state_owner.public.service import AppState, Auth, ConnectionState
from polaris.cells.storage.layout.public.service import sync_process_settings_environment
from polaris.delivery.http.error_handlers import setup_exception_handlers

logger = logging.getLogger(__name__)

# Module-level store for the effective runtime token actually enforced by
# Auth().  Public discovery is disabled by default; see
# ``is_auth_token_discovery_enabled``.
_effective_token: str = ""
_token_was_auto_generated: bool = False

SyncCleanupCallback = Callable[[], None]
AsyncCleanupCallback = Callable[[], Awaitable[None]]


def _run_sync_cleanup_callbacks(
    callbacks: tuple[tuple[str, SyncCleanupCallback], ...],
) -> None:
    """Run every synchronous cleanup callback without masking another failure."""
    for name, callback in callbacks:
        try:
            callback()
        except (OSError, RuntimeError, ValueError) as exc:
            logger.error("Lifespan cleanup failed for %s: %s", name, exc, exc_info=True)


async def _run_async_cleanup_callbacks(
    callbacks: tuple[tuple[str, AsyncCleanupCallback], ...],
) -> None:
    """Run every awaitable cleanup callback without masking another failure."""
    for name, callback in callbacks:
        try:
            await callback()
        except (OSError, RuntimeError, ValueError) as exc:
            logger.error("Lifespan cleanup failed for %s: %s", name, exc, exc_info=True)


def get_effective_token() -> str:
    """Return the token that Auth() is enforcing at runtime."""
    return _effective_token


def is_auth_token_discovery_enabled() -> bool:
    """Return whether the public auth-token discovery endpoint may expose a token."""
    return str(os.environ.get("KERNELONE_AUTH_TOKEN_DISCOVERY_ENABLED", "") or "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Application lifespan manager."""
    from polaris.bootstrap.assembly import assemble_core_services
    from polaris.cells.events.fact_stream.public import (
        BootstrapFactStreamWorkspaceCommandV1,
        bootstrap_fact_stream_workspace,
        fact_stream_bootstrap_streams,
    )
    from polaris.cells.factory.pipeline.public import (
        start_factory_settlement_runtime,
        stop_factory_settlement_runtime,
    )
    from polaris.cells.instances.public.service import maybe_start_instance_watchdog
    from polaris.cells.resident.autonomy.public.service import reset_resident_services
    from polaris.delivery.http.resident_autotick import maybe_start_resident_autotick
    from polaris.infrastructure.db.repositories.accel_session_receipt_store import (
        clear_context_retrieve_receipt_lookup,
        install_context_retrieve_receipt_lookup,
    )
    from polaris.infrastructure.di.container import get_container, reset_container
    from polaris.infrastructure.log_pipeline.jetstream_publisher import (
        clear_file_event_broadcaster_publisher,
        install_file_event_broadcaster_publisher,
        shutdown_log_jetstream_publisher,
    )
    from polaris.infrastructure.messaging import close_default_client
    from polaris.infrastructure.messaging.nats.server_runtime import (
        ensure_local_nats_runtime,
        shutdown_local_nats_runtime,
    )
    from polaris.kernelone.process import terminate_external_loop_pm_processes

    async def cleanup_startup_resources() -> None:
        """Release idempotent process resources after a failed startup stage."""

        _run_sync_cleanup_callbacks(
            (
                ("context receipt lookup", clear_context_retrieve_receipt_lookup),
                ("file event publisher", clear_file_event_broadcaster_publisher),
                ("log JetStream publisher", shutdown_log_jetstream_publisher),
            )
        )
        await _run_async_cleanup_callbacks(
            (
                ("default NATS client", close_default_client),
                ("managed NATS runtime", shutdown_local_nats_runtime),
            )
        )

    reset_container()
    reset_resident_services()
    container = await get_container()
    os.environ.setdefault("KERNELONE_JETSTREAM_PUBLISH", "1")
    install_file_event_broadcaster_publisher()
    install_context_retrieve_receipt_lookup()

    nats_settings = getattr(app.state.settings, "nats", None)
    nats_enabled = bool(getattr(nats_settings, "enabled", True))
    nats_required = bool(getattr(nats_settings, "required", True))
    if nats_enabled:
        try:
            await ensure_local_nats_runtime(str(getattr(nats_settings, "url", "") or ""))
        except (OSError, RuntimeError, ValueError) as exc:
            log_method = logger.critical if nats_required else logger.warning
            log_method(
                "[startup] Managed NATS bootstrap failed%s: %s",
                " – application cannot start safely" if nats_required else " but NATS is not required",
                exc,
                exc_info=True,
            )
            if nats_required:
                await cleanup_startup_resources()
                reset_resident_services()
                raise
    else:
        logger.info("[startup] NATS bootstrap skipped because settings.nats.enabled is false.")

    # Delegate assembly to bootstrap layer.
    # Wrap in an explicit error boundary: a failed bootstrap must never leave
    # the application in a half-initialised state silently accepting requests.
    try:
        assemble_core_services(container, settings=app.state.settings)
    except (OSError, RuntimeError, ValueError) as exc:
        logger.critical(
            "[startup] Bootstrap assembly failed – application cannot start safely: %s",
            exc,
            exc_info=True,
        )
        await cleanup_startup_resources()
        reset_resident_services()
        raise

    app.state.container = container

    # Refresh Auth from the environment at startup time so that tokens
    # injected after create_app() (e.g. in tests or subprocesses) are picked up.
    # The default token "polaris-local-dev" is set by get_effective_token() in
    # BackendLaunchRequest when no explicit token is provided.  If it is still
    # empty at this point (should not happen), fall back to a random token so
    # that Auth() never rejects all requests with an empty token.
    global _effective_token
    token = os.environ.get("KERNELONE_TOKEN", "").strip()
    if not token:
        token = _secrets.token_urlsafe(32)
        os.environ["KERNELONE_TOKEN"] = token
        logger.warning("[startup] Auto-generated random KERNELONE_TOKEN (was empty)")
    _effective_token = token
    app.state.auth = Auth(token, allow_dev_fallback=_token_was_auto_generated)

    workspace = str(getattr(app.state.settings, "workspace", "") or "").strip()
    if workspace:
        stale_pids = terminate_external_loop_pm_processes(workspace)
        if stale_pids:
            logger.info(
                f"[startup] terminated stale PM loop processes for workspace={workspace}: {stale_pids}",
            )

    resident_autotick_task = None
    instance_watchdog_task = None
    settlement_runtime_started = False
    project_completion_runtime = None
    factory_run_driver_runtime = None
    try:
        if workspace:
            bootstrap_fact_stream_workspace(
                BootstrapFactStreamWorkspaceCommandV1(
                    workspace=workspace,
                    streams=fact_stream_bootstrap_streams(),
                    maintenance_reason="http_app_lifespan_startup",
                )
            )
        # Opt-in unattended ignition for the Resident autonomy loop (default off).
        resident_autotick_task = maybe_start_resident_autotick(workspace)
        instance_watchdog_task = maybe_start_instance_watchdog()
        if workspace:
            from polaris.bootstrap.factory_run_driver_runtime import (
                FactoryRunDriverRuntimeV1,
                bind_factory_run_driver_runtime,
                recover_committed_factory_run_ids,
            )
            from polaris.bootstrap.managed_process_receipt_owner import (
                configure_managed_process_receipt_owner,
            )
            from polaris.bootstrap.project_completion_convergence_runtime import (
                configure_project_completion_convergence_runtime,
            )
            from polaris.bootstrap.project_verification_clients import (
                configure_project_verification_clients,
            )

            configure_managed_process_receipt_owner()
            configure_project_verification_clients()
            project_completion_runtime = configure_project_completion_convergence_runtime(workspace)
            await project_completion_runtime.start()
            await start_factory_settlement_runtime(
                workspace,
                enable_wake_bridge=nats_enabled,
                wake_bridge_required=nats_enabled and nats_required,
            )
            settlement_runtime_started = True
            from polaris.delivery.http.routers.factory import (
                _build_retry_start_request,
                _execute_run_with_service,
                _get_service,
            )

            factory_run_driver_runtime = FactoryRunDriverRuntimeV1(
                workspace=workspace,
                service=_get_service(workspace),
                state=app.state.app_state,
                execute=_execute_run_with_service,
                build_recovery_payload=_build_retry_start_request,
                recover_committed_run_ids=lambda: recover_committed_factory_run_ids(workspace),
            )
            bind_factory_run_driver_runtime(factory_run_driver_runtime)
            await factory_run_driver_runtime.start()

        yield
    finally:
        if factory_run_driver_runtime is not None:
            from polaris.bootstrap.factory_run_driver_runtime import clear_factory_run_driver_runtime

            try:
                await factory_run_driver_runtime.stop()
            except (OSError, RuntimeError, ValueError) as exc:
                logger.error(
                    "Factory run driver shutdown failed for workspace=%s: %s",
                    workspace,
                    exc,
                    exc_info=True,
                )
            finally:
                clear_factory_run_driver_runtime(factory_run_driver_runtime)
        if project_completion_runtime is not None:
            try:
                await project_completion_runtime.stop()
            except (OSError, RuntimeError, ValueError) as exc:
                logger.error(
                    "Project completion convergence shutdown failed for workspace=%s: %s",
                    workspace,
                    exc,
                    exc_info=True,
                )
        if instance_watchdog_task is not None:
            instance_watchdog_task.cancel()
            try:
                await instance_watchdog_task
            except asyncio.CancelledError:
                logger.debug("Instance watchdog cancelled during lifespan shutdown")
            except (OSError, RuntimeError, ValueError) as exc:
                logger.error("Instance watchdog shutdown failed: %s", exc, exc_info=True)
        if resident_autotick_task is not None:
            resident_autotick_task.cancel()
            try:
                await resident_autotick_task
            except asyncio.CancelledError:
                logger.debug("Resident autotick cancelled during lifespan shutdown")
            except (OSError, RuntimeError, ValueError) as exc:
                logger.error("Resident autotick shutdown failed: %s", exc, exc_info=True)
        if settlement_runtime_started:
            try:
                await stop_factory_settlement_runtime(workspace)
            except (OSError, RuntimeError, ValueError) as exc:
                logger.error(
                    "Factory settlement runtime shutdown failed for workspace=%s: %s",
                    workspace,
                    exc,
                    exc_info=True,
                )
        await cleanup_startup_resources()
        try:
            reset_resident_services()
        except (OSError, RuntimeError, ValueError) as exc:
            logger.error("Resident service reset failed during lifespan shutdown: %s", exc, exc_info=True)


def create_app(settings: Settings | None = None) -> FastAPI:
    """Create and configure FastAPI application."""
    if settings is None:
        settings = get_settings()

    from polaris.bootstrap.project_completion_diagnostics_owner import (
        configure_project_completion_diagnostics_owner,
    )
    from polaris.bootstrap.runtime_projection_director_status import (
        configure_runtime_projection_director_status,
    )
    from polaris.bootstrap.runtime_projection_factory_owner import (
        configure_runtime_projection_factory_owner,
    )
    from polaris.bootstrap.runtime_projection_project_outcome_owner import (
        configure_runtime_projection_project_outcome_owner,
    )
    from polaris.bootstrap.runtime_projection_workflow_runtime_owner import (
        configure_runtime_projection_workflow_runtime_owner,
    )
    from polaris.bootstrap.workflow_runtime_model_ceiling_owner import (
        configure_workflow_runtime_model_ceiling_owner,
    )

    configure_runtime_projection_factory_owner()
    configure_runtime_projection_director_status()
    configure_runtime_projection_project_outcome_owner()
    configure_runtime_projection_workflow_runtime_owner()
    configure_project_completion_diagnostics_owner()
    configure_workflow_runtime_model_ceiling_owner()

    # Ensure KERNELONE_TOKEN is always set. When started via bootstrap the
    # token is auto-generated, but direct uvicorn invocation may skip that
    # step, leaving Auth("") which rejects *every* request with 401.
    global _token_was_auto_generated
    if not os.environ.get("KERNELONE_TOKEN", "").strip():
        os.environ["KERNELONE_TOKEN"] = _secrets.token_urlsafe(32)
        _token_was_auto_generated = True

    sync_process_settings_environment(settings)
    from polaris.cells.events.fact_stream.public.service import configure_debug_tracing

    configure_debug_tracing(
        bool(
            getattr(getattr(settings, "logging", None), "enable_debug_tracing", False)
            or getattr(settings, "debug_tracing", False)
        )
    )

    app = FastAPI(
        title="Polaris Desktop Backend",
        description="Clean Architecture API for Polaris",
        version="2.0.0",
        lifespan=lifespan,
    )

    app.state.settings = settings
    app.state.app_state = AppState(settings=settings)
    # Auto-generate token if KERNELONE_TOKEN is not set.  The lifespan
    # handler will also refresh this, but we need a valid token at
    # create_app time so that pre-lifespan test scenarios work.
    global _effective_token
    token = os.environ.get("KERNELONE_TOKEN", "").strip()
    if not token:
        token = _secrets.token_urlsafe(32)
        os.environ["KERNELONE_TOKEN"] = token
    _effective_token = token
    app.state.auth = Auth(token, allow_dev_fallback=_token_was_auto_generated)
    app.state.connection_state = ConnectionState()

    _setup_observability(app)

    # CORS must be added LAST so it becomes the outermost middleware layer
    # (Starlette/FastAPI uses LIFO: last added = first to execute).
    # Preflight OPTIONS requests must receive CORS headers before they reach
    # rate limiting, metrics, or any other inner middleware.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.server.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    setup_exception_handlers(app)
    _register_routers(app)
    return app


def _register_routers(app: FastAPI) -> None:
    """Register all API routers.

    Transitional note:
    Some router module names reflect historical package layout, but route
    registration and behavior are owned by this canonical gateway.
    """

    from polaris.delivery.http.middleware.metrics import metrics_router
    from polaris.delivery.http.routers import (
        agents,
        aggregate_chat,
        arsenal,
        cognitive_runtime,
        control_plane,
        conversations,
        court,
        docs,
        factory,
        files,
        history,
        interventions,
        interview,
        lancedb,
        llm,
        logs,
        memory,
        memos,
        ollama,
        permissions,
        pm_chat,
        pm_management,
        primary_router,
        providers,
        role_chat,
        role_session,
        runtime,
        system,
        tests,
    )
    from polaris.delivery.http.v2 import context as v2_context, router as v2_router

    app.include_router(primary_router)
    app.include_router(v2_router)
    app.include_router(v2_context.router)
    app.include_router(factory.router)
    app.include_router(control_plane.router)

    app.include_router(aggregate_chat.router)
    app.include_router(role_chat.router)
    app.include_router(role_session.router)
    app.include_router(pm_chat.router)
    app.include_router(llm.router)
    app.include_router(interview.router)
    app.include_router(interventions.router)
    app.include_router(providers.router)
    app.include_router(tests.router)
    app.include_router(conversations.router)
    app.include_router(runtime.router)
    app.include_router(system.router)
    app.include_router(history.router)
    app.include_router(files.router)
    app.include_router(docs.router)
    app.include_router(cognitive_runtime.router)
    app.include_router(agents.router)
    app.include_router(ollama.router)
    app.include_router(lancedb.router)
    app.include_router(memos.router)
    app.include_router(memory.router)
    app.include_router(logs.router)
    app.include_router(permissions.router)
    app.include_router(pm_management.v2_router)
    app.include_router(pm_management.router)
    app.include_router(arsenal.router)
    app.include_router(court.router)

    app.include_router(metrics_router)


def _setup_observability(app: FastAPI) -> None:
    """Setup observability middleware.

    FastAPI/Starlette uses a LIFO stack for middleware: the *last* call to
    add_middleware() produces the *outermost* layer (first to receive a
    request, last to send a response).

    Desired runtime onion (outer → inner):
        CORS  →  logging  →  metrics  →  rate_limit  →  audit_context  →  route handler

    Therefore the registration order here must be the reverse:
        audit_context (registered first  → innermost - sets trace context)
        rate_limit    (registered second → second innermost)
        metrics       (registered third  → middle)
        logging       (registered last   → outermost observability layer)

    CORS is registered AFTER _setup_observability() returns (in create_app),
    making it the absolute outermost layer – which is correct: preflight
    OPTIONS requests are handled before they reach rate_limit or any other
    middleware.
    """
    from polaris.delivery.http.middleware.audit_context import get_audit_context_middleware
    from polaris.delivery.http.middleware.logging import get_logging_middleware
    from polaris.delivery.http.middleware.metrics import get_metrics_middleware
    from polaris.delivery.http.middleware.rate_limit import get_rate_limit_middleware

    # Innermost observability layer – applied first to inbound requests.
    # Sets UnifiedAuditContext that propagates to all downstream handlers.
    app.add_middleware(get_audit_context_middleware)
    # Second innermost layer.
    app.add_middleware(get_rate_limit_middleware)
    # Middle layer.
    app.add_middleware(get_metrics_middleware)
    # Outermost observability layer – sees every request including those later
    # rejected by rate_limit, providing a complete audit trail.
    app.add_middleware(get_logging_middleware)


# Global app singleton - commented out to prevent import-time side effects.
# To run via uvicorn CLI, use: uvicorn polaris.delivery.http.app_factory:create_app --factory
# app = create_app()

__all__ = ["create_app", "lifespan"]
