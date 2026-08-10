# ruff: noqa: F403, F405
"""Factory Router - unattended factory HTTP + Nats-JetStream adapter."""

from __future__ import annotations

import logging
import sys
import types
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Depends
from polaris.cells.factory.pipeline.public import (
    RecoverStaleFactoryWorkspaceOwnerCommandV1,
    RecoverStaleFactoryWorkspaceOwnerResultV1,
)
from polaris.cells.factory.pipeline.public.types import (
    FactoryControlRequest,
    FactoryRunList,
    FactoryRunStatus as FactoryRunStatusContract,
    FactoryStartRequest,
)
from polaris.delivery.http.routers._shared import (
    StructuredHTTPException,
    require_internal_bench_surface,
)
from polaris.delivery.http.schemas import (
    FactoryRunArtifactsResponse,
    FactoryRunAuditBundleResponse,
    FactoryRunEventsResponse,
)
from polaris.kernelone.trace import create_task_with_context  # noqa: F401 — patch target

from ._factory_handlers import mapping as _factory_mapping, runtime as _factory_runtime, stage_ops as _factory_stage_ops
from ._factory_handlers.mapping import *
from ._factory_handlers.runtime import *
from ._factory_handlers.stage_ops import *
from ._shared import get_state, require_auth

if TYPE_CHECKING:
    from polaris.cells.runtime.state_owner.public.service import AppState

logger = logging.getLogger(__name__)

router = APIRouter(tags=["factory"], dependencies=[Depends(require_auth)])


def _rebind_helper_module(source_module: types.ModuleType) -> None:
    """Install helper callables/constants into this module's globals.

    Unit tests patch ``polaris.delivery.http.routers.factory.<name>``. Helper
    bodies must therefore resolve free names (and each other) through *this*
    module's namespace, not the extracted helper module's globals.

    Later helper modules may re-export earlier helpers via ``import *``. Those
    re-exports must not overwrite functions already rebound into the host.
    """

    host = sys.modules[__name__]
    host_globals = host.__dict__
    source_name = source_module.__name__
    for name, obj in vars(source_module).items():
        if name.startswith("__"):
            continue
        # Functions defined in *this* helper module: rebind to host globals.
        if isinstance(obj, types.FunctionType) and getattr(obj, "__module__", None) == source_name:
            rebound = types.FunctionType(
                obj.__code__,
                host_globals,
                name=obj.__name__,
                argdefs=obj.__defaults__,
                closure=obj.__closure__,
            )
            rebound.__kwdefaults__ = obj.__kwdefaults__
            rebound.__annotations__ = dict(getattr(obj, "__annotations__", {}) or {})
            rebound.__doc__ = obj.__doc__
            setattr(host, name, rebound)
            continue
        # Imported functions from sibling helpers — keep already-rebound host fn.
        if isinstance(obj, types.FunctionType):
            existing = host_globals.get(name)
            if isinstance(existing, types.FunctionType):
                continue
            setattr(host, name, obj)
            continue
        # Classes defined in this helper module (e.g. bench request models).
        if isinstance(obj, type) and getattr(obj, "__module__", None) == source_name:
            setattr(host, name, obj)
            continue
        # Imports, stdlib modules, TypeAliases, constants, singletons.
        existing = host_globals.get(name)
        if isinstance(existing, types.FunctionType):
            continue
        setattr(host, name, obj)


# Rebind extracted helpers so free-name lookups hit this module (test patch targets).
# Order: mapping -> stage_ops -> runtime (linear dependency).
_rebind_helper_module(_factory_mapping)
_rebind_helper_module(_factory_stage_ops)
_rebind_helper_module(_factory_runtime)

del _factory_mapping, _factory_stage_ops, _factory_runtime, _rebind_helper_module


# ---- v2 routes (canonical) ----


@router.get("/v2/factory/runs", response_model=FactoryRunList)
async def list_factory_runs_v2(
    limit: int = 50,
    offset: int = 0,
    workspace: str | None = None,
    state: AppState = Depends(get_state),
) -> FactoryRunList:
    """List factory runs for the current workspace."""
    return await _list_factory_runs_core(limit=limit, offset=offset, workspace=workspace, state=state)


@router.post("/v2/factory/runs", response_model=FactoryRunStatusContract)
async def start_factory_run_v2(
    payload: FactoryStartRequest,
    state: AppState = Depends(get_state),
) -> FactoryRunStatusContract:
    """Create and start an unattended factory run."""
    return await _start_factory_run_core(payload=payload, state=state)


@router.get("/v2/factory/runs/{run_id}", response_model=FactoryRunStatusContract)
async def get_factory_run_status_v2(
    run_id: str,
    workspace: str | None = None,
    state: AppState = Depends(get_state),
) -> FactoryRunStatusContract:
    """Query run status."""
    return await _get_factory_run_status_core(run_id=run_id, workspace=workspace, state=state)


@router.get("/v2/factory/runs/{run_id}/events", response_model=FactoryRunEventsResponse)
async def get_factory_run_events_v2(
    run_id: str,
    limit: int = 100,
    workspace: str | None = None,
    state: AppState = Depends(get_state),
) -> FactoryRunEventsResponse:
    """Get append-only audit events for a run."""
    return await _get_factory_run_events_core(run_id=run_id, limit=limit, workspace=workspace, state=state)


@router.get("/v2/factory/runs/{run_id}/audit-bundle", response_model=FactoryRunAuditBundleResponse)
async def get_factory_run_audit_bundle_v2(
    run_id: str,
    limit: int = 100,
    workspace: str | None = None,
    state: AppState = Depends(get_state),
) -> FactoryRunAuditBundleResponse:
    """Get a machine-readable audit bundle for a factory run."""
    return await _get_factory_run_audit_bundle_core(run_id=run_id, limit=limit, workspace=workspace, state=state)


@router.post("/v2/factory/runs/{run_id}/control", response_model=FactoryRunStatusContract)
async def control_factory_run_v2(
    run_id: str,
    payload: FactoryControlRequest,
    workspace: str | None = None,
    state: AppState = Depends(get_state),
) -> FactoryRunStatusContract:
    """Control a run. This phase only supports cancel."""
    return await _control_factory_run_core(run_id=run_id, payload=payload, workspace=workspace, state=state)


@router.post(
    "/v2/factory/runs/{run_id}/actions/recover-stale-workspace-owner",
    response_model=RecoverStaleFactoryWorkspaceOwnerResultV1,
)
async def recover_stale_factory_workspace_owner_v2(
    run_id: str,
    payload: RecoverStaleFactoryWorkspaceOwnerCommandV1,
    state: AppState = Depends(get_state),
) -> RecoverStaleFactoryWorkspaceOwnerResultV1:
    """Explicitly fence and release one stale workspace owner."""

    return await _recover_stale_factory_workspace_owner_core(
        run_id=run_id,
        payload=payload,
        state=state,
    )


@router.get("/v2/factory/runs/{run_id}/artifacts", response_model=FactoryRunArtifactsResponse)
async def get_factory_run_artifacts_v2(
    run_id: str,
    workspace: str | None = None,
    state: AppState = Depends(get_state),
) -> FactoryRunArtifactsResponse:
    """List artifact files for a run."""
    return await _get_factory_run_artifacts_core(run_id=run_id, workspace=workspace, state=state)


# =============================================================================
# Factory-bench session endpoints (workspace-agnostic).
#
# These endpoints expose the ``FactoryBenchService`` so the
# ``scripts/factory_bench/run_factory_bench.py`` subprocess (which runs in
# a terminal, not in the backend process) can publish its lifecycle to the
# Factory front-end panel in real time. The bench subprocess posts over HTTP
# (urllib in the bench, FastAPI here) and the front-end subscribes via the
# unified Nats-JetStream WebSocket runtime transport. Failures on either side
# are soft: missing session dir / dropped events are logged, never raised into
# the HTTP response, so a misconfigured bench can never crash the panel.
# =============================================================================


@router.post("/v2/factory/bench/sessions", response_model=FactoryBenchStartResponse)
async def start_factory_bench_session_v2(
    payload: FactoryBenchStartRequest,
) -> FactoryBenchStartResponse:
    """Register a new bench session (typically called by the bench subprocess)."""
    require_internal_bench_surface()
    sid = _bench_service.register_session(
        work_dir=payload.work_dir,
        project_ids=payload.project_ids,
        total=payload.total or len(payload.project_ids),
        metadata=payload.metadata,
        session_id=payload.session_id,
    )
    snapshot = _bench_service.get_session(sid) or {}
    event = {
        "type": "factory_bench.session.started",
        "actor": "factory-bench",
        "summary": f"Factory bench session started: {sid}",
        "ok": True,
        "meta": _bench_session_event_meta(sid, snapshot),
    }
    if _bench_service.append_event(sid, event):
        await _publish_factory_bench_event_to_jetstream(sid, event)
    return FactoryBenchStartResponse(session_id=sid, status="running")


@router.get("/v2/factory/bench/sessions")
async def list_factory_bench_sessions_v2(
    limit: int = 50,
) -> dict[str, Any]:
    """List recent bench sessions for the Factory panel UI."""
    require_internal_bench_surface()
    sessions = _bench_service.list_sessions(limit=limit)
    return {"total": len(sessions), "sessions": sessions}


@router.get("/v2/factory/bench/sessions/{session_id}")
async def get_factory_bench_session_v2(session_id: str) -> dict[str, Any]:
    """Read a bench session's status + a tail of its recent events."""
    require_internal_bench_surface()
    snapshot = _bench_service.get_session(session_id)
    if snapshot is None:
        raise StructuredHTTPException(
            status_code=404,
            code="BENCH_SESSION_NOT_FOUND",
            message=f"bench session {session_id!r} not found",
        )
    return snapshot


@router.post("/v2/factory/bench/sessions/{session_id}/events")
async def append_factory_bench_event_v2(
    session_id: str,
    payload: FactoryBenchEventRequest,
) -> dict[str, Any]:
    """Append an event to a bench session's event log + fanout via NAT JetStream.

    Two-write path mirroring the platform's runtime event subsystem:
      1. **JSONL** (durable on disk) via ``FactoryBenchService.append_event``,
         so the audit trail survives JetStream outages and the front-end can
         replay the full event history via the standard get-session
         endpoint (``GET /v2/factory/bench/sessions/{id}``).
      2. **NAT JetStream** (best-effort fanout) via ``publish_to_jetstream``,
         with the canonical subject ``hp.runtime.bench.<session_id>``. This
         is the **only** real-time push path — the platform's existing
         ``JetStreamConsumerManager`` / WebSocket pipeline subscribes to
         ``event.bench`` and forwards every envelope to the
         client, the same way it already carries ``log.llm`` /
         ``log.process`` / etc.
    """
    require_internal_bench_surface()
    event: dict[str, Any] = {
        "type": payload.type,
        "name": payload.name,
        "actor": payload.actor,
        "summary": payload.summary,
        "ok": payload.ok,
        "meta": dict(payload.meta),
    }
    # Drop None fields so the JSONL stays compact.
    event = {k: v for k, v in event.items() if v is not None}
    ok = _bench_service.append_event(session_id, event)
    if not ok:
        # The bench may POST events before register, or against a stale id.
        return {"session_id": session_id, "appended": False, "published": False}

    # Best-effort JetStream fanout. The platform's RuntimeEventEnvelope is
    # what every existing consumer already knows how to filter on (channel,
    # kind, workspace_key, run_id); wrapping the bench event in that shape
    # means a front-end subscribing to ``event.bench`` gets the same shape
    # it already gets for ``log.llm`` etc.
    published = await _publish_factory_bench_event_to_jetstream(session_id, event)
    return {"session_id": session_id, "appended": True, "published": published}


@router.post("/v2/factory/bench/sessions/{session_id}/complete")
async def complete_factory_bench_session_v2(
    session_id: str,
    payload: FactoryBenchCompleteRequest,
) -> dict[str, Any]:
    """Mark a bench session complete (or failed)."""
    require_internal_bench_surface()
    ok = _bench_service.complete_session(
        session_id,
        success=payload.success,
        summary=payload.summary,
    )
    published = False
    if ok:
        snapshot = _bench_service.get_session(session_id) or {}
        status = str(snapshot.get("status") or ("completed" if payload.success else "failed"))
        meta: dict[str, Any] = {
            **_bench_session_event_meta(session_id, snapshot),
            "status": status,
            "total": snapshot.get("total"),
            "completed": snapshot.get("completed"),
            "failed": snapshot.get("failed"),
            "completed_at": snapshot.get("completed_at"),
            **dict(payload.summary),
        }
        event = {
            "type": f"factory_bench.run.{status}",
            "actor": "factory-bench",
            "summary": "Factory bench run completed" if payload.success else "Factory bench run failed",
            "ok": payload.success,
            "meta": {k: v for k, v in meta.items() if v is not None},
        }
        _bench_service.append_event(session_id, event)
        published = await _publish_factory_bench_event_to_jetstream(session_id, event)
    return {"session_id": session_id, "updated": ok, "published": published}


@router.post("/v2/factory/bench/sessions/{session_id}/progress")
async def update_factory_bench_progress_v2(
    session_id: str,
    payload: FactoryBenchProgressRequest,
) -> dict[str, Any]:
    """Update per-project counters so the front-end sees live ``X/Y 通过``."""
    require_internal_bench_surface()
    ok = _bench_service.update_progress(
        session_id,
        completed=payload.completed,
        failed=payload.failed,
    )
    published = False
    if ok:
        snapshot = _bench_service.get_session(session_id) or {}
        event = {
            "type": "factory_bench.progress.updated",
            "actor": "factory-bench",
            "summary": "Factory bench progress updated",
            "ok": True,
            "meta": _bench_session_event_meta(session_id, snapshot),
        }
        _bench_service.append_event(session_id, event)
        published = await _publish_factory_bench_event_to_jetstream(session_id, event)
    return {"session_id": session_id, "updated": ok, "published": published}
