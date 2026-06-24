"""Platform instance management endpoints."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from polaris.cells.instances.public.service import get_instance_supervisor
from polaris.delivery.http.dependencies import require_auth
from pydantic import BaseModel, Field

router = APIRouter(prefix="/instances", tags=["Instances"])


class StartInstanceRequest(BaseModel):
    instance_id: str = ""
    name: str = ""
    kind: str = "project"
    polaris_root: str = ""
    workspace: str
    runtime_root: str = ""
    backend_port: int | None = None
    frontend_port: int | None = None
    token: str = ""
    backend_reload: bool = True
    frontend_vite: bool = True
    start_frontend: bool = True
    bench: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


@router.get("", dependencies=[Depends(require_auth)])
async def list_instances() -> dict[str, Any]:
    supervisor = get_instance_supervisor()
    return {"instances": supervisor.list_instances()}


@router.post("/start", dependencies=[Depends(require_auth)])
async def start_instance(request: StartInstanceRequest) -> dict[str, Any]:
    supervisor = get_instance_supervisor()
    try:
        instance = supervisor.start_instance(request.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {"instance": instance}


@router.post("/{instance_id}/stop", dependencies=[Depends(require_auth)])
async def stop_instance(instance_id: str) -> dict[str, Any]:
    supervisor = get_instance_supervisor()
    try:
        return {"instance": supervisor.stop_instance(instance_id)}
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/{instance_id}/restart", dependencies=[Depends(require_auth)])
async def restart_instance(instance_id: str) -> dict[str, Any]:
    supervisor = get_instance_supervisor()
    try:
        return {"instance": supervisor.restart_instance(instance_id)}
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/{instance_id}", dependencies=[Depends(require_auth)])
async def delete_instance(instance_id: str) -> dict[str, Any]:
    try:
        deleted = get_instance_supervisor().delete_instance(instance_id)
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if not deleted:
        raise HTTPException(status_code=404, detail="instance not found")
    return {"ok": True}


@router.get("/{instance_id}/health", dependencies=[Depends(require_auth)])
async def instance_health(instance_id: str) -> dict[str, Any]:
    supervisor = get_instance_supervisor()
    try:
        return {"instance": supervisor.health(instance_id)}
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/{instance_id}/logs", dependencies=[Depends(require_auth)])
async def instance_logs(
    instance_id: str,
    stream: str = Query("backend", pattern="^(backend|frontend)$"),
    tail_lines: int = Query(200, ge=1, le=5000),
) -> dict[str, str]:
    supervisor = get_instance_supervisor()
    try:
        return {"stream": stream, "content": supervisor.get_logs(instance_id, stream, tail_lines)}
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
