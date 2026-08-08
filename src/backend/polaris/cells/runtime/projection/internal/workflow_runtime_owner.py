"""Bootstrap-bound read port for workflow-runtime projection facts."""

from __future__ import annotations

from threading import Lock
from typing import Any, Protocol


class WorkflowRuntimeProjectionOwnerPortV1(Protocol):
    def workflow_config(self) -> Any: ...

    def describe_workflow(self, workflow_id: str, config: Any) -> dict[str, Any]: ...

    def query_workflow(self, workflow_id: str, query_name: str, config: Any) -> dict[str, Any]: ...

    def director_workflow_id(self, run_id: str) -> str: ...

    def qa_workflow_id(self, run_id: str) -> str: ...

    async def list_runs(self, *, workspace: str, limit: int) -> list[Any]: ...


_bound_port: WorkflowRuntimeProjectionOwnerPortV1 | None = None
_lock = Lock()


def bind_workflow_runtime_projection_owner_port(port: WorkflowRuntimeProjectionOwnerPortV1) -> None:
    global _bound_port
    with _lock:
        if _bound_port is not None and _bound_port is not port:
            raise RuntimeError("workflow_runtime_projection_owner_conflicting_rebind")
        _bound_port = port


def workflow_runtime_projection_owner_port() -> WorkflowRuntimeProjectionOwnerPortV1:
    with _lock:
        port = _bound_port
    if port is None:
        raise RuntimeError("workflow_runtime_projection_owner_unbound")
    return port


__all__ = [
    "WorkflowRuntimeProjectionOwnerPortV1",
    "bind_workflow_runtime_projection_owner_port",
    "workflow_runtime_projection_owner_port",
]
