"""Thin authenticated HTTP facade for the workspace's engineering owner."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from polaris.bootstrap.config import Settings
from polaris.bootstrap.engineering_runtime import (
    engineering_error_status,
    get_engineering_service,
    observe_engineering_run,
    start_engineering_run,
)
from polaris.cells.roles.runtime.public.engineering_service import EngineeringRunService
from polaris.delivery.http.dependencies import require_auth
from pydantic import BaseModel, Field

router = APIRouter(prefix="/v2/engineering/runs", tags=["engineering"], dependencies=[Depends(require_auth)])


class EngineeringRunRequest(BaseModel):
    """User intent and profile selection; raw grants are never accepted."""

    model_config = {"extra": "forbid"}
    instruction: str = Field(min_length=1)
    workspace: str = Field(min_length=1)
    workflow: Literal["direct", "collaborative"] = "direct"
    role: Literal["pm", "chief_engineer", "director", "qa"] = "director"
    permission_profile_id: str | None = None
    required_checks: tuple[str, ...] = ()
    request_key: str | None = None


class EngineeringRunActionRequest(BaseModel):
    """Optional workspace assertion; action bodies cannot replace saved policy."""

    model_config = {"extra": "forbid"}
    workspace: str | None = None


def _bound(request: Request, body_workspace: str | None = None) -> tuple[EngineeringRunService, Settings, Path]:
    service = get_engineering_service(request.app.state)
    state = getattr(request.app.state, "app_state", None)
    settings = getattr(state, "settings", None) or request.app.state.settings
    workspace = Path(request.query_params.get("workspace", str(service.workspace))).expanduser().resolve()
    if workspace != service.workspace:
        raise PermissionError("engineering service workspace mismatch")
    if body_workspace is not None and Path(body_workspace).expanduser().resolve() != service.workspace:
        raise PermissionError("engineering service workspace mismatch")
    return service, settings, workspace


def _error(error: Exception) -> HTTPException:
    return HTTPException(status_code=engineering_error_status(error), detail=str(error))


@router.post("")
async def create_run(payload: EngineeringRunRequest, request: Request) -> dict[str, Any]:
    try:
        service, settings, _workspace = _bound(request)
        return await start_engineering_run(service, settings, **payload.model_dump())
    except (KeyError, OSError, ValueError, RuntimeError) as error:
        raise _error(error) from error


@router.get("/{session_id}")
async def get_run(session_id: str, request: Request) -> dict[str, Any]:
    try:
        service, _settings, workspace = _bound(request)
        return observe_engineering_run(service, session_id, workspace)
    except (KeyError, OSError, ValueError, RuntimeError) as error:
        raise _error(error) from error


@router.post("/{session_id}/cancel")
async def cancel_run(
    session_id: str, request: Request, payload: EngineeringRunActionRequest | None = None
) -> dict[str, Any]:
    try:
        service, _settings, workspace = _bound(request, payload.workspace if payload is not None else None)
        await service.cancel(session_id, workspace=workspace)
        return observe_engineering_run(service, session_id, workspace)
    except (KeyError, OSError, ValueError, RuntimeError) as error:
        raise _error(error) from error


@router.post("/{session_id}/resume")
async def resume_run(
    session_id: str, request: Request, payload: EngineeringRunActionRequest | None = None
) -> dict[str, Any]:
    try:
        service, _settings, workspace = _bound(request, payload.workspace if payload is not None else None)
        await service.resume(session_id, workspace=workspace)
        return observe_engineering_run(service, session_id, workspace)
    except (KeyError, OSError, ValueError, RuntimeError) as error:
        raise _error(error) from error
