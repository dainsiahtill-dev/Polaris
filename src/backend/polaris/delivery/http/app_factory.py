"""FastAPI application factory for Polaris backend.

Canonical delivery-layer API gateway implementation.
Legacy `api.main` delegates to this module.
"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from polaris.bootstrap.config import Settings, get_settings
from polaris.cells.runtime.state_owner.public.service import AppState, Auth, ConnectionState
from polaris.cells.storage.layout.public.service import sync_process_settings_environment
from polaris.delivery.http.error_handlers import setup_exception_handlers

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager."""
    from polaris.bootstrap.assembly import assemble_core_services
    from polaris.cells.resident.autonomy.public.service import reset_resident_services
    from polaris.infrastructure.di.container import get_container, reset_container
    from polaris.infrastructure.log_pipeline.jetstream_publisher import (
        shutdown_log_jetstream_publisher,
    )
    from polaris.infrastructure.messaging import close_default_client
    from polaris.infrastructure.messaging.nats.server_runtime import (
        ensure_local_nats_runtime,
        shutdown_local_nats_runtime,
    )
    from polaris.kernelone.process import terminate_external_loop_pm_processes

    reset_container()
    reset_resident_services()
    container = await get_container()

    try:
        await ensure_local_nats_runtime(str(getattr(app.state.settings.nats, "url", "") or ""))
    except (RuntimeError, ValueError) as exc:
        logger.critical(
            "[startup] Managed NATS bootstrap failed – application cannot start safely: %s",
            exc,
            exc_info=True,
        )
        raise

    # Delegate assembly to bootstrap layer.
    # Wrap in an explicit error boundary: a failed bootstrap must never leave
    # the application in a half-initialised state silently accepting requests.
    try:
        assemble_core_services(container, settings=app.state.settings)
    except (RuntimeError, ValueError) as exc:
        logger.critical(
            "[startup] Bootstrap assembly failed – application cannot start safely: %s",
            exc,
            exc_info=True,
        )
        raise

    app.state.container = container

    # Refresh Auth from the environment at startup time so that tokens
    # injected after create_app() (e.g. in tests or subprocesses) are picked up.
    app.state.auth = Auth(os.environ.get("KERNELONE_TOKEN", ""))

    workspace = str(getattr(app.state.settings, "workspace", "") or "").strip()
    if workspace:
        stale_pids = terminate_external_loop_pm_processes(workspace)
        if stale_pids:
            logger.info(
                f"[startup] terminated stale PM loop processes for workspace={workspace}: {stale_pids}",
            )

    yield

    with suppress(Exception):
        await close_default_client()
    with suppress(Exception):
        shutdown_log_jetstream_publisher()
    with suppress(Exception):
        await shutdown_local_nats_runtime()
    reset_resident_services()


def create_app(settings: Settings | None = None) -> FastAPI:
    """Create and configure FastAPI application."""
    if settings is None:
        settings = get_settings()

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
    app.state.auth = Auth(os.environ.get("KERNELONE_TOKEN", ""))
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
    Router module locations still include legacy paths while behavior is
    incrementally migrated into Polaris.
    """

    from polaris.delivery.http.middleware.metrics import metrics_router
    from polaris.delivery.http.routers import (
        agents,
        arsenal,
        cognitive_runtime,
        conversations,
        court,
        docs,
        factory,
        files,
        history,
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
        stream_router,
        system,
        tests,
    )
    from polaris.delivery.http.v2 import router as v2_router

    app.include_router(primary_router)
    app.include_router(v2_router)
    app.include_router(factory.router)

    app.include_router(role_chat.router)
    app.include_router(role_session.router)
    app.include_router(pm_chat.router)
    app.include_router(llm.router)
    app.include_router(interview.router)
    app.include_router(providers.router)
    app.include_router(tests.router)
    app.include_router(conversations.router)
    app.include_router(runtime.router)
    app.include_router(system.router)
    app.include_router(stream_router.router)
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
