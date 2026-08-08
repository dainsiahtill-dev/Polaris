"""Composition adapter: workflow runtime owner facts -> runtime projection."""

from __future__ import annotations

from typing import Any

from polaris.cells.orchestration.workflow_runtime.public.service import (
    WorkflowConfig,
    describe_workflow_sync,
    director_workflow_id,
    get_orchestration_service,
    qa_workflow_id,
    query_workflow_sync,
)
from polaris.cells.runtime.projection.public.bootstrap import (
    bind_workflow_runtime_projection_owner_port,
)


class WorkflowRuntimeProjectionOwnerAdapterV1:
    def workflow_config(self) -> WorkflowConfig:
        return WorkflowConfig.from_env(force_enable=True)

    def describe_workflow(self, workflow_id: str, config: Any) -> dict[str, Any]:
        return describe_workflow_sync(workflow_id, config)

    def query_workflow(self, workflow_id: str, query_name: str, config: Any) -> dict[str, Any]:
        return query_workflow_sync(workflow_id, query_name, config=config)

    def director_workflow_id(self, run_id: str) -> str:
        return director_workflow_id(run_id)

    def qa_workflow_id(self, run_id: str) -> str:
        return qa_workflow_id(run_id)

    async def list_runs(self, *, workspace: str, limit: int) -> list[Any]:
        service = await get_orchestration_service()
        scoped = list(await service.list_runs(workspace=workspace, limit=limit))
        return scoped or list(await service.list_runs(limit=limit))


WORKFLOW_RUNTIME_PROJECTION_OWNER_ADAPTER = WorkflowRuntimeProjectionOwnerAdapterV1()


def configure_runtime_projection_workflow_runtime_owner() -> None:
    bind_workflow_runtime_projection_owner_port(WORKFLOW_RUNTIME_PROJECTION_OWNER_ADAPTER)


__all__ = [
    "WORKFLOW_RUNTIME_PROJECTION_OWNER_ADAPTER",
    "WorkflowRuntimeProjectionOwnerAdapterV1",
    "configure_runtime_projection_workflow_runtime_owner",
]
