"""Public boundary for `runtime.projection` cell.

Keep package import side effects light. Neighboring cells import small public
facades from this package during bootstrap, and eager service imports can create
cycles before their modules finish initializing.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

_CONTRACT_EXPORTS = {
    "RuntimeObserverEventTypeV1",
    "RuntimeObserverEventV1",
    "RuntimeProjectedEventV1",
    "RuntimeProjectionError",
    "RuntimeProjectionQueryV1",
    "RuntimeProjectionResultV1",
}

_ROLE_CONTRACT_EXPORTS = {
    "ChiefEngineerBlueprintDetailV1",
    "ChiefEngineerBlueprintListV1",
    "ChiefEngineerBlueprintSummaryV1",
    "RoleTaskContractV1",
}

_SERVICE_EXPORTS = {
    "CodeChangeState",
    "CodeChangeStatus",
    "DiffHunk",
    "EventSeverity",
    "PMTaskNode",
    "PMTaskState",
    "QATaskNode",
    "QATaskState",
    "ReviewComment",
    "ReviewState",
    "ReviewVerdict",
    "RoleState",
    "RoleType",
    "RuntimeEventV2",
    "RuntimeProjection",
    "RuntimeProjectionService",
    "RuntimeRoleState",
    "RuntimeSnapshotV2",
    "RuntimeSummary",
    "RuntimeTaskNode",
    "RuntimeWorkerState",
    "TaskSource",
    "TaskState",
    "WorkerState",
    "WorkspaceRuntimeContext",
    "build_anthro_state",
    "build_cache_root",
    "build_director_runtime_status",
    "build_director_status",
    "build_engine_status",
    "build_llm_status",
    "build_pm_status",
    "build_pm_status_async",
    "build_resident_state",
    "build_runtime_projection",
    "build_runtime_projection_sync",
    "build_snapshot_payload_from_projection",
    "build_status_payload_sync",
    "build_workflow_status_payload",
    "build_workflow_task_rows",
    "canonicalize_workflow_task_state",
    "decode_bytes",
    "format_mtime",
    "get_runtime_event_v2_schema",
    "get_runtime_snapshot_v2_schema",
    "get_workflow_director_status_sync",
    "get_workflow_runtime_status",
    "get_workflow_stage",
    "invalidate_projection_cache",
    "list_memos",
    "load_runtime_task_rows",
    "merge_director_status",
    "read_file_head",
    "read_file_tail",
    "read_incremental",
    "read_json",
    "read_readme_title",
    "resolve_artifact_path",
    "resolve_workspace_runtime_context",
    "select_latest_artifact",
    "select_task_rows",
    "select_task_rows_from_projection",
    "summarize_workflow_tasks",
    "write_text_atomic",
    "write_workflow_state",
}

if TYPE_CHECKING:
    from polaris.cells.runtime.projection.public.contracts import (
        RuntimeObserverEventTypeV1,
        RuntimeObserverEventV1,
        RuntimeProjectedEventV1,
        RuntimeProjectionError,
        RuntimeProjectionQueryV1,
        RuntimeProjectionResultV1,
    )
    from polaris.cells.runtime.projection.public.role_contracts import (
        ChiefEngineerBlueprintDetailV1,
        ChiefEngineerBlueprintListV1,
        ChiefEngineerBlueprintSummaryV1,
        RoleTaskContractV1,
    )
    from polaris.cells.runtime.projection.public.service import (
        CodeChangeState,
        CodeChangeStatus,
        DiffHunk,
        EventSeverity,
        PMTaskNode,
        PMTaskState,
        QATaskNode,
        QATaskState,
        ReviewComment,
        ReviewState,
        ReviewVerdict,
        RoleState,
        RoleType,
        RuntimeEventV2,
        RuntimeProjection,
        RuntimeProjectionService,
        RuntimeRoleState,
        RuntimeSnapshotV2,
        RuntimeSummary,
        RuntimeTaskNode,
        RuntimeWorkerState,
        TaskSource,
        TaskState,
        WorkerState,
        WorkspaceRuntimeContext,
        build_anthro_state,
        build_cache_root,
        build_director_runtime_status,
        build_director_status,
        build_engine_status,
        build_llm_status,
        build_pm_status,
        build_pm_status_async,
        build_resident_state,
        build_runtime_projection,
        build_runtime_projection_sync,
        build_snapshot_payload_from_projection,
        build_status_payload_sync,
        build_workflow_status_payload,
        build_workflow_task_rows,
        canonicalize_workflow_task_state,
        decode_bytes,
        format_mtime,
        get_runtime_event_v2_schema,
        get_runtime_snapshot_v2_schema,
        get_workflow_director_status_sync,
        get_workflow_runtime_status,
        get_workflow_stage,
        invalidate_projection_cache,
        list_memos,
        load_runtime_task_rows,
        merge_director_status,
        read_file_head,
        read_file_tail,
        read_incremental,
        read_json,
        read_readme_title,
        resolve_artifact_path,
        resolve_workspace_runtime_context,
        select_latest_artifact,
        select_task_rows,
        select_task_rows_from_projection,
        summarize_workflow_tasks,
        write_text_atomic,
        write_workflow_state,
    )

__all__ = [
    "ChiefEngineerBlueprintDetailV1",
    "ChiefEngineerBlueprintListV1",
    "ChiefEngineerBlueprintSummaryV1",
    "CodeChangeState",
    "CodeChangeStatus",
    "DiffHunk",
    "EventSeverity",
    "PMTaskNode",
    "PMTaskState",
    "QATaskNode",
    "QATaskState",
    "RoleTaskContractV1",
    "build_cache_root",
    "build_director_status",
    "build_llm_status",
    "build_pm_status",
    "build_pm_status_async",
    "build_resident_state",
    "build_runtime_projection",
    "build_status_payload_sync",
    "build_workflow_status_payload",
    "build_workflow_task_rows",
    "canonicalize_workflow_task_state",
    "get_workflow_runtime_status",
    "get_workflow_stage",
    "list_memos",
    "load_runtime_task_rows",
    "merge_director_status",
    "resolve_artifact_path",
    "select_latest_artifact",
    "select_task_rows",
    "select_task_rows_from_projection",
    "summarize_workflow_tasks",
    "write_workflow_state",
]


def __getattr__(name: str) -> Any:
    if name in _CONTRACT_EXPORTS:
        from polaris.cells.runtime.projection.public import contracts

        value = getattr(contracts, name)
    elif name in _ROLE_CONTRACT_EXPORTS:
        from polaris.cells.runtime.projection.public import role_contracts

        value = getattr(role_contracts, name)
    elif name in _SERVICE_EXPORTS:
        from polaris.cells.runtime.projection.public import service

        value = getattr(service, name)
    else:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

    globals()[name] = value
    return value
