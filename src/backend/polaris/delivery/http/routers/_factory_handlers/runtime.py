# ruff: noqa: E402, F403, F405
"""Factory HTTP router helpers — payload builders, cores, bench session utils.

Extracted from factory.py so route registration stays thin. External callers that
historically imported private helpers from factory.py continue to re-export them
from polaris.delivery.http.routers.factory.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from polaris.cells.audit.diagnosis.public import (
    QueryExactRunCausalAuditV1,
    query_exact_run_causal_audit,
)
from polaris.cells.factory.pipeline.internal.bench_service import (
    FactoryBenchService,
)
from polaris.cells.factory.pipeline.internal.factory_store import FileLockTimeoutError
from polaris.cells.factory.pipeline.public import (
    FactoryRun,
    FactoryRunService,
    RecoverStaleFactoryWorkspaceOwnerCommandV1,
    RecoverStaleFactoryWorkspaceOwnerResultV1,
)
from polaris.cells.factory.pipeline.public.types import (
    FactoryControlRequest,
    FactoryRunList,
    FactoryRunStatus as FactoryRunStatusContract,
    FactoryStartRequest,
)
from polaris.delivery.http.legacy_engine_retired import legacy_engine_retired
from polaris.delivery.http.routers._shared import (
    StructuredHTTPException,
)
from polaris.delivery.http.routers.jetstream_utils import (
    publish_to_jetstream,
)
from polaris.delivery.http.schemas import (
    FactoryRunArtifactsResponse,
    FactoryRunAuditBundleResponse,
    FactoryRunEventsResponse,
)
from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from polaris.cells.runtime.state_owner.public.service import AppState

logger = logging.getLogger("polaris.delivery.http.routers.factory")

from .mapping import *
from .stage_ops import *


async def _execute_run_with_service(
    service: FactoryRunService,
    run_id: str,
    payload: FactoryStartRequest,
    state: AppState,
) -> None:
    """Old runs require their frozen version; never execute them here."""
    raise legacy_engine_retired("Legacy Factory execution")


def _schedule_factory_run_task(
    service: FactoryRunService,
    run_id: str,
    payload: FactoryStartRequest,
    state: AppState,
) -> Any:
    """Never submit old pipeline work in the current process."""
    raise legacy_engine_retired("Legacy Factory scheduling")


# ---- Core implementations ----


async def _list_factory_runs_core(
    limit: int,
    offset: int,
    workspace: str | None,
    state: AppState,
) -> FactoryRunList:
    effective_workspace = _resolve_workspace(state, workspace)
    service = _get_service(effective_workspace, read_only=True)
    runs_data = await service.list_runs()
    runs_data.sort(key=lambda item: item.get("created_at", ""), reverse=True)

    items: list[FactoryRunStatusContract] = []
    for run_data in runs_data[offset : offset + limit]:
        run = await service.get_run(run_data["id"])
        if run is not None:
            items.append(_map_service_run_to_contract(run))

    return FactoryRunList(
        runs=items,
        total=len(runs_data),
        page=offset // limit + 1 if limit > 0 else 1,
        page_size=limit,
    )


async def _start_factory_run_core(
    payload: FactoryStartRequest,
    state: AppState,
) -> FactoryRunStatusContract:
    """Reject old Factory admission before workspace/settings/store effects."""
    raise legacy_engine_retired("Legacy Factory start")


async def _load_factory_run_for_http(service: FactoryRunService, run_id: str) -> FactoryRun | None:
    """Keep snapshot contention distinct from a genuinely absent run."""

    try:
        return await service.get_run(run_id)
    except FileLockTimeoutError as exc:
        raise StructuredHTTPException(
            status_code=503,
            code="FACTORY_RUN_SNAPSHOT_BUSY",
            message=f"Run {run_id} snapshot is temporarily busy",
        ) from exc


async def _get_factory_run_status_core(
    run_id: str,
    state: AppState,
    workspace: str | None = None,
) -> FactoryRunStatusContract:
    effective_workspace = _resolve_workspace(state, workspace)
    service = _get_service(effective_workspace, read_only=True)
    run = await _load_factory_run_for_http(service, run_id)
    if run is None:
        raise StructuredHTTPException(status_code=404, code="RUN_NOT_FOUND", message=f"Run {run_id} not found")
    return _map_service_run_to_contract(run)


async def _get_factory_run_events_core(
    run_id: str,
    limit: int,
    state: AppState,
    workspace: str | None = None,
) -> FactoryRunEventsResponse:
    service = _get_service(_resolve_workspace(state, workspace), read_only=True)
    run = await _load_factory_run_for_http(service, run_id)
    if run is None:
        raise StructuredHTTPException(status_code=404, code="RUN_NOT_FOUND", message=f"Run {run_id} not found")

    events = await service.get_run_events(run_id)
    return FactoryRunEventsResponse(events=events[-limit:])


async def _get_factory_run_audit_bundle_core(
    run_id: str,
    limit: int,
    state: AppState,
    workspace: str | None = None,
) -> FactoryRunAuditBundleResponse:
    effective_workspace = _resolve_workspace(state, workspace)
    service = _get_service(effective_workspace, read_only=True)
    run = await _load_factory_run_for_http(service, run_id)
    if run is None:
        raise StructuredHTTPException(status_code=404, code="RUN_NOT_FOUND", message=f"Run {run_id} not found")

    events = await service.get_run_events(run_id)
    artifacts = _merge_artifact_items(
        _list_run_artifacts(service=service, workspace=effective_workspace, run_id=run_id),
        _list_stage_artifacts(workspace=effective_workspace, events=events),
    )
    bundle = _build_factory_audit_bundle(
        run=run,
        events=events,
        artifacts=artifacts,
        events_tail_limit=limit,
    )
    run_ledger_projection = _attach_control_plane_projection(
        bundle=bundle,
        run=run,
        workspace=effective_workspace,
    )
    from polaris.cells.roles.kernel.public.provider_attempt_lifecycle_replay import (
        QUERY_FACTORY_PROVIDER_ATTEMPT_LIFECYCLE_REPLAY_SCHEMA,
        QueryFactoryProviderAttemptLifecycleReplayV1,
        query_factory_provider_route_qualification,
    )

    # Obligation comes from the loaded Factory state/events, never client phase
    # claims or the existence of provider attempts. Unknown state fails closed.
    stage_roles = {
        "pm_planning": "pm",
        "chief_engineer_review": "chief_engineer",
        "director_dispatch": "director",
        "quality_gate": "qa",
    }
    role_order = ("pm", "chief_engineer", "director", "qa")
    stage_results = run.metadata.get("stage_results")
    reached = {*run.stages_completed, *run.stages_failed}
    reached.update(stage_results if isinstance(stage_results, dict) else ())
    reached.add(str(run.metadata.get("current_stage") or ""))
    reached.update(
        str(event.get("stage") or "")
        for event in events
        if event.get("type") in {"stage_started", "stage_completed", "stage_failed"}
    )
    reached_roles = [stage_roles[stage] for stage in reached if stage in stage_roles]
    completed = str(getattr(run.status, "value", run.status)) == "completed"
    highest = max((role_order.index(role) for role in reached_roles), default=3)
    required_roles = role_order if completed else role_order[: highest + 1]
    routes = query_factory_provider_route_qualification(
        QueryFactoryProviderAttemptLifecycleReplayV1(
            schema_version=QUERY_FACTORY_PROVIDER_ATTEMPT_LIFECYCLE_REPLAY_SCHEMA,
            workspace=effective_workspace,
            factory_run_id=run.id,
        ),
        project_id=str(_factory_run_identity(run=run, workspace=effective_workspace).get("canonical_project_id") or ""),
        required_roles=required_roles,
    )
    routes["stage_obligations"] = {
        "source": "factory.server_run_state",
        "required_roles": list(required_roles),
        "reached_stages": sorted(reached),
        "completed": completed,
    }
    stage_results = run.metadata.get("stage_results")
    pm_result = stage_results.get("pm_planning") if isinstance(stage_results, dict) else None
    pm_metadata = pm_result.get("metadata") if isinstance(pm_result, dict) else None
    if (
        not isinstance(pm_metadata, dict)
        or pm_metadata.get("real_planning_qualified") is not True
        or pm_metadata.get("contract_origin") != "provider_planning"
    ):
        routes["ok"] = False
        routes["errors"].append("pm_provider_planning_evidence_missing")
    routes["project_id"] = _factory_run_identity(run=run, workspace=effective_workspace).get("canonical_project_id")
    bundle["role_route_qualification"] = routes
    # Compose the audit Cell at the HTTP delivery boundary rather than making
    # factory.pipeline depend back on audit.diagnosis (which already consumes
    # Factory/Run-Ledger/TaskRuntime projections).  A terminal audit bundle is
    # therefore self-contained: callers get the current root cause, owning
    # Cell, retry boundary, and evidence gaps without reconstructing causality
    # from the event tail.  Diagnosis failure remains explicit evidence and
    # never hides the underlying Factory bundle.
    identity = _factory_run_identity(run=run, workspace=effective_workspace)
    project_id = str(identity.get("canonical_project_id") or "")
    if run_ledger_projection is None:
        bundle["exact_run_causal_audit_error"] = {
            "error_code": "scoped_run_ledger_unavailable",
            "error_message": "Canonical scoped ledger unavailable; causal audit cannot use a fallback projection",
        }
        return FactoryRunAuditBundleResponse(**bundle)
    causal = await query_exact_run_causal_audit(
        QueryExactRunCausalAuditV1(
            workspace=effective_workspace,
            factory_run_id=run.id,
            project_id=project_id,
            audit_event_limit=max(300, min(int(limit or 100) * 4, 2000)),
            preloaded_run_ledger_projection=run_ledger_projection,
        )
    )
    if causal.status != "unavailable":
        bundle["exact_run_causal_audit"] = dict(causal.payload)
    else:
        bundle["exact_run_causal_audit_error"] = {
            "error_code": causal.error_code or "exact_run_causal_audit_failed",
            "error_message": causal.error_message or "Exact-run causal diagnosis unavailable",
        }
    return FactoryRunAuditBundleResponse(**bundle)


async def _control_factory_run_core(
    run_id: str,
    payload: FactoryControlRequest,
    state: AppState,
    workspace: str | None = None,
) -> FactoryRunStatusContract:
    """Frozen old owners must use their original process and authority."""
    raise legacy_engine_retired("Legacy Factory control")


async def _recover_stale_factory_workspace_owner_core(
    run_id: str,
    payload: RecoverStaleFactoryWorkspaceOwnerCommandV1,
    state: AppState,
) -> RecoverStaleFactoryWorkspaceOwnerResultV1:
    """Frozen old owners must use their original process and authority."""
    raise legacy_engine_retired("Legacy Factory control")


async def _get_factory_run_artifacts_core(
    run_id: str,
    state: AppState,
    workspace: str | None = None,
) -> FactoryRunArtifactsResponse:
    effective_workspace = _resolve_workspace(state, workspace)
    service = _get_service(effective_workspace, read_only=True)
    run = await _load_factory_run_for_http(service, run_id)
    if run is None:
        raise StructuredHTTPException(status_code=404, code="RUN_NOT_FOUND", message=f"Run {run_id} not found")

    events = await service.get_run_events(run_id)
    artifacts = _merge_artifact_items(
        _list_run_artifacts(service=service, workspace=effective_workspace, run_id=run_id),
        _list_stage_artifacts(workspace=effective_workspace, events=events),
    )
    response_data = _build_artifacts_response(run=run, artifacts=artifacts)
    return FactoryRunArtifactsResponse(**response_data)


# ---- factory-bench session helpers ----

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


class FactoryBenchStartRequest(BaseModel):
    work_dir: str = Field(..., description="factory-bench work dir (per-project subdirs)")
    project_ids: list[str] = Field(default_factory=list)
    total: int = Field(default=0, ge=0)
    metadata: dict[str, Any] = Field(default_factory=dict)
    session_id: str | None = Field(default=None, description="optional explicit id")


class FactoryBenchStartResponse(BaseModel):
    session_id: str
    status: str


class FactoryBenchEventRequest(BaseModel):
    type: str = Field(..., description="event semantic type, e.g. project.started")
    name: str | None = None
    actor: str | None = None
    summary: str | None = None
    ok: bool | None = None
    meta: dict[str, Any] = Field(default_factory=dict)


class FactoryBenchCompleteRequest(BaseModel):
    success: bool = True
    summary: dict[str, Any] = Field(default_factory=dict)


class FactoryBenchProgressRequest(BaseModel):
    completed: int | None = None
    failed: int | None = None


_bench_service = FactoryBenchService()


def _bench_session_event_meta(session_id: str, snapshot: dict[str, Any] | None = None) -> dict[str, Any]:
    """Build the bench session fields every realtime bench event should carry."""
    data = snapshot if snapshot is not None else (_bench_service.get_session(session_id) or {})
    meta = {
        "session_id": data.get("session_id") or session_id,
        "work_dir": data.get("work_dir"),
        "project_ids": data.get("project_ids"),
        "total": data.get("total"),
        "completed": data.get("completed"),
        "failed": data.get("failed"),
        "status": data.get("status"),
        "created_at": data.get("created_at"),
        "updated_at": data.get("updated_at"),
        "completed_at": data.get("completed_at"),
        "metadata": data.get("metadata"),
    }
    return {key: value for key, value in meta.items() if value is not None}


async def _publish_factory_bench_event_to_jetstream(session_id: str, event: dict[str, Any]) -> bool:
    """Fan out a persisted factory-bench event on the unified runtime stream."""
    try:
        from polaris.infrastructure.messaging.nats.nats_types import (
            create_runtime_event,
        )

        envelope = create_runtime_event(
            workspace_key="bench",
            run_id=session_id,
            # The front-end subscribes to the wildcard ``event.bench`` channel,
            # which the runtime.v2 subject builder maps to
            # ``hp.runtime.bench.>``. Keeping the envelope channel per-session
            # lets consumers still filter or pin a single bench run.
            channel=f"event.bench:{session_id}",
            kind=str(event.get("type") or "bench.event"),
            payload=event,
            meta={"source": "factory_bench_subprocess"},
        )
        # ``publish_to_jetstream`` -> ``client.publish_js`` JSON-serializes
        # the payload (nats-py has no dataclass hook). Hand it a dict.
        return await publish_to_jetstream(
            subject=f"hp.runtime.bench.{session_id}",
            payload=envelope.to_dict(),
        )
    except (RuntimeError, TimeoutError, ValueError, TypeError) as exc:
        # JetStream fanout is best-effort; JSONL/session write already succeeded.
        logger.debug("bench JetStream fanout failed for %s: %s", session_id, exc)
        return False


__all__ = [
    "FactoryBenchCompleteRequest",
    "FactoryBenchEventRequest",
    "FactoryBenchProgressRequest",
    "FactoryBenchStartRequest",
    "FactoryBenchStartResponse",
    "_bench_service",
    "_bench_session_event_meta",
    "_control_factory_run_core",
    "_execute_run_with_service",
    "_get_factory_run_artifacts_core",
    "_get_factory_run_audit_bundle_core",
    "_get_factory_run_events_core",
    "_get_factory_run_status_core",
    "_list_factory_runs_core",
    "_load_factory_run_for_http",
    "_publish_factory_bench_event_to_jetstream",
    "_recover_stale_factory_workspace_owner_core",
    "_schedule_factory_run_task",
    "_start_factory_run_core",
]
