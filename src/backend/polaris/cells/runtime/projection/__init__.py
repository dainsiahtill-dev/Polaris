"""Entry for `runtime.projection` cell."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from polaris.cells.runtime.projection.public import (
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
        RuntimeObserverEventTypeV1,
        RuntimeObserverEventV1,
        RuntimeProjectedEventV1,
        RuntimeProjection,
        RuntimeProjectionError,
        RuntimeProjectionQueryV1,
        RuntimeProjectionResultV1,
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
        build_director_runtime_status,
        build_engine_status,
        build_resident_state,
        build_runtime_projection_sync,
        build_snapshot_payload_from_projection,
        get_runtime_event_v2_schema,
        get_runtime_snapshot_v2_schema,
        get_workflow_director_status_sync,
        invalidate_projection_cache,
        load_runtime_task_rows,
        merge_director_status,
        resolve_workspace_runtime_context,
    )

__all__ = [
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
    "RuntimeObserverEventTypeV1",
    "RuntimeObserverEventV1",
    "RuntimeProjectedEventV1",
    "RuntimeProjection",
    "RuntimeProjectionError",
    "RuntimeProjectionQueryV1",
    "RuntimeProjectionResultV1",
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
    "build_director_runtime_status",
    "build_engine_status",
    "build_resident_state",
    "build_runtime_projection_sync",
    "build_snapshot_payload_from_projection",
    "get_runtime_event_v2_schema",
    "get_runtime_snapshot_v2_schema",
    "get_workflow_director_status_sync",
    "invalidate_projection_cache",
    "load_runtime_task_rows",
    "merge_director_status",
    "resolve_workspace_runtime_context",
]


def __getattr__(name: str) -> Any:
    if name not in __all__:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

    from polaris.cells.runtime.projection import public

    value = getattr(public, name)
    globals()[name] = value
    return value
