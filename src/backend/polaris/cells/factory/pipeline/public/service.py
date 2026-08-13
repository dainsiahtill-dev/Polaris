"""Stable public service surface for factory.pipeline.

Error Handling Contract:
    All factory.pipeline operations raise ``FactoryPipelineError`` on
    expected domain failures (e.g., invalid workspace, missing run_id).
    ``FactoryPipelineError.code`` contains a machine-readable error code;
    ``FactoryPipelineError.details`` contains structured context.

    Unexpected infrastructure failures propagate as standard exceptions and
    should NOT be caught here — let them surface to the application layer.

    Callers should only catch ``FactoryPipelineError``, not the base
    ``RuntimeError``, to avoid masking infrastructure failures.

Example::

    from polaris.cells.factory.pipeline.public.service import (
        IFactoryPipeline,
        FactoryPipelineError,
    )

    try:
        result = await pipeline.run_pipeline(project_path, config)
    except FactoryPipelineError as exc:
        logger.error("Pipeline failed [%s]: %s", exc.code, exc)
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from polaris.cells.storage.layout.public import (
    ResolveExistingRuntimeRootReadOnlyQueryV1,
    resolve_existing_runtime_root_read_only,
)

from ..internal.bench_gates import build_real_run_gate
from ..internal.factory_run_admission import FactoryWorkspaceRunAdmission
from ..internal.factory_run_service import (
    TERMINAL_RUN_STATUSES,
    FactoryConfig,
    FactoryRun,
    FactoryRunService,
    FactoryRunStatus,
)
from ..internal.factory_settlement_runtime import (
    FactorySettlementRuntime,
    FactorySettlementRuntimeError,
    create_factory_settlement_runtime,
    start_factory_settlement_runtime,
    stop_all_factory_settlement_runtimes,
    stop_factory_settlement_runtime,
    wake_factory_settlement_runtime,
)
from ..internal.factory_store import FactoryStore
from ..internal.projection_change_analysis import ProjectionChangeAnalysisService
from ..internal.projection_lab import FactoryProjectionLabService
from ..internal.run_ledger import (
    JobToken,
    RunLedger,
    build_gate_ledger_event,
    build_job_token_from_record,
    build_run_ledger_projection,
    load_run_ledger_projection,
    persist_real_run_gate_ledger,
    summarize_run_ledger_meta,
    summarize_run_ledger_projection,
)
from .contracts import (
    CancelFactoryRunCommandV1,
    FactoryChainProjectionV1,
    FactoryLifecycleOperationClaimV1,
    FactoryPipelineError,
    FactoryRunCompletedEventV1,
    FactoryRunResultV1,
    FactoryRunStartedEventV1,
    FactoryStageExecutionClaimV1,
    FactoryTerminalTaskRuntimeProjectionV1,
    FactoryWorkspaceReleaseEvidenceV1,
    FactoryWorkspaceRunLeaseConflictError,
    FactoryWorkspaceRunLeaseStateV1,
    FactoryWorkspaceRunLeaseStorageError,
    FactoryWorkspaceRunLeaseV1,
    GetFactoryChainProjectionQueryV1,
    GetFactoryRunStatusQueryV1,
    GetFactoryTerminalTaskRuntimeProjectionQueryV1,
    IFactoryPipeline,
    IFactoryProjectionLab,
    ListFactoryRunsQueryV1,
    ProjectionBackMappingRefreshResultV1,
    ProjectionExperimentResultV1,
    ProjectionReprojectionResultV1,
    RecoverStaleFactoryWorkspaceOwnerCommandV1,
    RecoverStaleFactoryWorkspaceOwnerResultV1,
    RefreshProjectionBackMappingCommandV1,
    ReprojectProjectionExperimentCommandV1,
    RunProjectionExperimentCommandV1,
    StartFactoryRunCommandV1,
    compute_factory_chain_completed,
    compute_factory_chain_projection_hash,
    stable_factory_event_ref,
)

FactoryRunServiceFactory = Callable[[str], FactoryRunService]


def _create_factory_run_service(workspace: str) -> FactoryRunService:
    return FactoryRunService(workspace=Path(workspace))


class _FactoryChainProjectionReader:
    """Factory-owned zero-write reader for strict run and event snapshots."""

    def __init__(self, workspace: str) -> None:
        self.workspace = Path(workspace).resolve()
        runtime = resolve_existing_runtime_root_read_only(
            ResolveExistingRuntimeRootReadOnlyQueryV1(workspace=str(self.workspace)),
        )
        self._store = (
            FactoryStore(Path(runtime.runtime_root) / "factory", create_root=False) if runtime is not None else None
        )

    async def get_run(self, run_id: str) -> FactoryRun | None:
        if self._store is None:
            return None
        logical_ref = self._store.run_snapshot_ref(run_id)
        snapshot_path = self._store.base_dir / logical_ref.removeprefix("runtime/")
        try:
            snapshot_path.lstat()
        except FileNotFoundError:
            return None
        payload = await self._store.read_strict_run_snapshot(run_id)
        return FactoryRun.from_dict(payload)

    def get_run_sync(self, run_id: str) -> FactoryRun | None:
        """Descriptor-safe strict read for synchronous owner adapters."""

        if self._store is None:
            return None
        logical_ref = self._store.run_snapshot_ref(run_id)
        snapshot_path = self._store.base_dir / logical_ref.removeprefix("runtime/")
        try:
            snapshot_path.lstat()
        except FileNotFoundError:
            return None
        payload = self._store._read_strict_snapshot_sync(logical_ref)
        return FactoryRun.from_dict(payload)

    async def get_authoritative_run_events(self, run_id: str) -> Sequence[Mapping[str, Any]]:
        if self._store is None:
            return ()
        return await self._store.get_authoritative_events_read_only(run_id)


def _create_factory_chain_projection_reader(workspace: str) -> _FactoryChainProjectionReader:
    return _FactoryChainProjectionReader(workspace)


def get_factory_workspace_run_lease(workspace: str) -> FactoryWorkspaceRunLeaseV1 | None:
    """Return the durable Factory admission projection for one workspace."""

    return FactoryWorkspaceRunAdmission(workspace).current()


def get_factory_terminal_task_runtime_projection(
    query: GetFactoryTerminalTaskRuntimeProjectionQueryV1,
) -> FactoryTerminalTaskRuntimeProjectionV1 | None:
    """Read Factory's exact frozen TaskRuntime authority without mutating state."""

    if type(query) is not GetFactoryTerminalTaskRuntimeProjectionQueryV1:
        raise TypeError("query must be an exact GetFactoryTerminalTaskRuntimeProjectionQueryV1 instance")
    workspace = str(Path(query.workspace).expanduser().resolve())
    reader = _create_factory_chain_projection_reader(workspace)
    if str(reader.workspace) != workspace:
        raise FactoryPipelineError(
            "Factory terminal projection reader is bound to another workspace",
            code="factory_workspace_binding_mismatch",
            details={"requested_workspace": workspace, "factory_run_id": query.factory_run_id},
        )
    run = reader.get_run_sync(query.factory_run_id)
    if run is None:
        return None
    if run.id != query.factory_run_id:
        raise FactoryPipelineError(
            "Factory run identity does not match terminal projection query",
            code="factory_terminal_task_runtime_projection_run_identity_mismatch",
            details={"requested_run_id": query.factory_run_id, "owner_run_id": run.id},
        )
    payload = run.metadata.get("factory_terminal_task_runtime_projection")
    if payload is None:
        return None
    if not isinstance(payload, Mapping):
        raise FactoryPipelineError(
            "Factory terminal TaskRuntime projection is not a mapping",
            code="factory_terminal_task_runtime_projection_invalid",
            details={"factory_run_id": query.factory_run_id},
        )
    try:
        projection = FactoryTerminalTaskRuntimeProjectionV1.from_dict(payload)
    except (TypeError, ValueError) as exc:
        raise FactoryPipelineError(
            "Factory terminal TaskRuntime projection is invalid",
            code="factory_terminal_task_runtime_projection_invalid",
            details={"factory_run_id": query.factory_run_id, "error": str(exc)[:300]},
        ) from exc
    if Path(projection.workspace).expanduser().resolve() != Path(workspace):
        raise FactoryPipelineError(
            "Factory terminal TaskRuntime projection workspace does not match query",
            code="factory_terminal_task_runtime_projection_workspace_mismatch",
            details={"factory_run_id": query.factory_run_id},
        )
    return projection


def _normalize_owner_stage_tuple(values: object, field_name: str) -> tuple[str, ...]:
    """Normalize typed owner stages while rejecting malformed or duplicated facts."""
    if not isinstance(values, Sequence) or isinstance(values, (str, bytes)):
        raise FactoryPipelineError(
            "Factory chain stage facts are not a sequence",
            code="factory_chain_projection_owner_facts_invalid",
            details={"field": field_name},
        )
    seen: set[str] = set()
    out: list[str] = []
    for item in values:
        if type(item) is not str or not item.strip():
            raise FactoryPipelineError(
                "Factory chain stage facts contain a non-string or empty item",
                code="factory_chain_projection_owner_facts_invalid",
                details={"field": field_name},
            )
        token = item.strip()
        if token in seen:
            raise FactoryPipelineError(
                "Factory chain stage facts contain a duplicate item",
                code="factory_chain_projection_owner_facts_invalid",
                details={"field": field_name, "stage": token},
            )
        seen.add(token)
        out.append(token)
    return tuple(out)


def _normalize_owner_event_projection(events: object) -> tuple[tuple[str, ...], str | None]:
    """Derive stable refs plus the sole successful terminal completion event."""
    if not isinstance(events, Sequence) or isinstance(events, (str, bytes)):
        raise FactoryPipelineError(
            "Factory chain events are not a sequence",
            code="factory_chain_projection_owner_events_invalid",
        )
    seen: set[str] = set()
    out: list[str] = []
    completion_refs: list[str] = []
    for event in events:
        if not isinstance(event, Mapping):
            raise FactoryPipelineError(
                "Factory chain contains a non-object event",
                code="factory_chain_projection_owner_events_invalid",
            )
        try:
            ref = stable_factory_event_ref(event)
        except (TypeError, ValueError) as exc:
            raise FactoryPipelineError(
                "Factory chain event identity is invalid",
                code="factory_chain_projection_owner_events_invalid",
            ) from exc
        if ref in seen:
            raise FactoryPipelineError(
                "Factory chain contains a duplicate event identity",
                code="factory_chain_projection_owner_events_invalid",
                details={"event_ref": ref},
            )
        seen.add(ref)
        out.append(ref)
        if event.get("type") == "completed":
            if event.get("success") is not True:
                raise FactoryPipelineError(
                    "Factory completion event does not carry success=true",
                    code="factory_chain_projection_terminal_event_invalid",
                    details={"event_ref": ref},
                )
            completion_refs.append(ref)
    if len(completion_refs) > 1:
        raise FactoryPipelineError(
            "Factory chain contains multiple completion events",
            code="factory_chain_projection_terminal_event_invalid",
            details={"completion_event_refs": completion_refs},
        )
    return tuple(out), completion_refs[0] if completion_refs else None


def _build_factory_chain_projection(
    *,
    workspace: str,
    run_id: str,
    available: bool,
    status: str,
    configured_stages: Sequence[str],
    completed_stages: Sequence[str],
    failed_stages: Sequence[str],
    event_refs: Sequence[str],
    completion_event_ref: str | None,
) -> FactoryChainProjectionV1:
    configured = tuple(configured_stages)
    completed = tuple(completed_stages)
    failed = tuple(failed_stages)
    refs = tuple(event_refs)
    missing = tuple(stage for stage in configured if stage not in set(completed))
    chain_completed = compute_factory_chain_completed(
        available=available,
        status=status,
        configured_stages=configured,
        completed_stages=completed,
        failed_stages=failed,
        event_refs=refs,
        completion_event_ref=completion_event_ref,
    )
    event_count = len(refs)
    projection_hash = compute_factory_chain_projection_hash(
        workspace=workspace,
        run_id=run_id,
        available=available,
        status=status,
        configured_stages=configured,
        completed_stages=completed,
        failed_stages=failed,
        missing_stages=missing,
        chain_completed=chain_completed,
        event_count=event_count,
        event_refs=refs,
        completion_event_ref=completion_event_ref,
    )
    return FactoryChainProjectionV1(
        workspace=workspace,
        run_id=run_id,
        available=available,
        status=status,
        configured_stages=configured,
        completed_stages=completed,
        failed_stages=failed,
        missing_stages=missing,
        chain_completed=chain_completed,
        event_count=event_count,
        event_refs=refs,
        completion_event_ref=completion_event_ref,
        projection_hash=projection_hash,
    )


async def get_factory_chain_projection(
    query: GetFactoryChainProjectionQueryV1,
) -> FactoryChainProjectionV1:
    """Return the typed, read-only Factory chain owner projection for one run.

    Reads exact Factory-owned run/event snapshots through a zero-write reader.
    No caller dependency injection, retries, Provider calls, Bench work, or
    caller-supplied evidence refs are accepted. Missing runs return
    ``available=False`` and never complete.
    """
    if type(query) is not GetFactoryChainProjectionQueryV1:
        raise TypeError("query must be an exact GetFactoryChainProjectionQueryV1 instance")

    workspace = str(Path(query.workspace).expanduser().resolve())
    run_id = query.run_id
    service = _create_factory_chain_projection_reader(workspace)
    service_workspace = str(Path(service.workspace).expanduser().resolve())
    if service_workspace != workspace:
        raise FactoryPipelineError(
            "Factory chain projection service is bound to another workspace",
            code="factory_workspace_binding_mismatch",
            details={
                "requested_workspace": workspace,
                "service_workspace": service_workspace,
                "run_id": run_id,
            },
        )

    run = await service.get_run(run_id)
    if run is None:
        return _build_factory_chain_projection(
            workspace=workspace,
            run_id=run_id,
            available=False,
            status="",
            configured_stages=(),
            completed_stages=(),
            failed_stages=(),
            event_refs=(),
            completion_event_ref=None,
        )

    owner_run_id_raw = getattr(run, "id", None)
    if type(owner_run_id_raw) is not str:
        raise FactoryPipelineError(
            "Factory run identity is not an exact string",
            code="factory_chain_projection_run_identity_invalid",
            details={"requested_run_id": run_id, "workspace": workspace},
        )
    owner_run_id = owner_run_id_raw.strip()
    if owner_run_id != run_id:
        raise FactoryPipelineError(
            "Factory run identity does not match the requested run_id",
            code="factory_chain_projection_run_identity_mismatch",
            details={
                "requested_run_id": run_id,
                "owner_run_id": owner_run_id,
                "workspace": workspace,
            },
        )

    if isinstance(run.status, FactoryRunStatus):
        status_value = run.status.value
    elif type(run.status) is str:
        status_value = run.status.strip()
    else:
        status_value = ""
    if not status_value:
        raise FactoryPipelineError(
            "Factory run status is missing",
            code="factory_chain_projection_status_missing",
            details={"run_id": run_id, "workspace": workspace},
        )

    config = getattr(run, "config", None)
    configured_raw = getattr(config, "stages", None) if config is not None else None
    configured = _normalize_owner_stage_tuple(configured_raw, "configured_stages")
    completed_raw = getattr(run, "stages_completed", None)
    failed_raw = getattr(run, "stages_failed", None)
    completed = _normalize_owner_stage_tuple(completed_raw, "completed_stages")
    failed = _normalize_owner_stage_tuple(failed_raw, "failed_stages")

    events = await service.get_authoritative_run_events(run_id)
    event_refs, completion_event_ref = _normalize_owner_event_projection(events)

    return _build_factory_chain_projection(
        workspace=workspace,
        run_id=run_id,
        available=True,
        status=status_value,
        configured_stages=configured,
        completed_stages=completed,
        failed_stages=failed,
        event_refs=event_refs,
        completion_event_ref=completion_event_ref,
    )


async def recover_stale_factory_workspace_owner(
    command: RecoverStaleFactoryWorkspaceOwnerCommandV1,
    *,
    service_factory: FactoryRunServiceFactory | None = None,
) -> RecoverStaleFactoryWorkspaceOwnerResultV1:
    """Fence child sessions and release one explicitly identified stale owner.

    The command workspace is canonicalized before service construction. The
    supplied service factory is checked against that binding so a delivery
    adapter cannot accidentally recover authority in another workspace.
    Domain errors from the admission owner pass through unchanged.
    """

    workspace = str(Path(command.workspace).expanduser().resolve())
    factory = service_factory or _create_factory_run_service
    service = factory(workspace)
    service_workspace = str(Path(service.workspace).expanduser().resolve())
    if service_workspace != workspace:
        raise FactoryPipelineError(
            "Factory stale-owner recovery service is bound to another workspace",
            code="factory_workspace_binding_mismatch",
            details={
                "requested_workspace": workspace,
                "service_workspace": service_workspace,
                "run_id": command.run_id,
            },
        )

    released = await service.recover_stale_workspace_owner(
        command.run_id,
        expected_fencing_token=command.expected_fencing_token,
        reason=command.reason,
    )
    return RecoverStaleFactoryWorkspaceOwnerResultV1(
        workspace=released.workspace,
        run_id=command.run_id,
        expected_fencing_token=command.expected_fencing_token,
        reason=command.reason,
        lease=released,
    )


__all__ = [
    "TERMINAL_RUN_STATUSES",
    "CancelFactoryRunCommandV1",
    "FactoryChainProjectionV1",
    "FactoryConfig",
    "FactoryLifecycleOperationClaimV1",
    "FactoryPipelineError",
    "FactoryProjectionLabService",
    "FactoryRun",
    "FactoryRunCompletedEventV1",
    "FactoryRunResultV1",
    "FactoryRunService",
    "FactoryRunStartedEventV1",
    "FactoryRunStatus",
    "FactorySettlementRuntime",
    "FactorySettlementRuntimeError",
    "FactoryStageExecutionClaimV1",
    "FactoryTerminalTaskRuntimeProjectionV1",
    "FactoryWorkspaceReleaseEvidenceV1",
    "FactoryWorkspaceRunLeaseConflictError",
    "FactoryWorkspaceRunLeaseStateV1",
    "FactoryWorkspaceRunLeaseStorageError",
    "FactoryWorkspaceRunLeaseV1",
    "GetFactoryChainProjectionQueryV1",
    "GetFactoryRunStatusQueryV1",
    "GetFactoryTerminalTaskRuntimeProjectionQueryV1",
    "IFactoryPipeline",
    "IFactoryProjectionLab",
    "JobToken",
    "ListFactoryRunsQueryV1",
    "ProjectionBackMappingRefreshResultV1",
    "ProjectionChangeAnalysisService",
    "ProjectionExperimentResultV1",
    "ProjectionReprojectionResultV1",
    "RecoverStaleFactoryWorkspaceOwnerCommandV1",
    "RecoverStaleFactoryWorkspaceOwnerResultV1",
    "RefreshProjectionBackMappingCommandV1",
    "ReprojectProjectionExperimentCommandV1",
    "RunLedger",
    "RunProjectionExperimentCommandV1",
    "StartFactoryRunCommandV1",
    "build_gate_ledger_event",
    "build_job_token_from_record",
    "build_real_run_gate",
    "build_run_ledger_projection",
    "create_factory_settlement_runtime",
    "get_factory_chain_projection",
    "get_factory_terminal_task_runtime_projection",
    "get_factory_workspace_run_lease",
    "load_run_ledger_projection",
    "persist_real_run_gate_ledger",
    "recover_stale_factory_workspace_owner",
    "start_factory_settlement_runtime",
    "stop_all_factory_settlement_runtimes",
    "stop_factory_settlement_runtime",
    "summarize_run_ledger_meta",
    "summarize_run_ledger_projection",
    "wake_factory_settlement_runtime",
]
