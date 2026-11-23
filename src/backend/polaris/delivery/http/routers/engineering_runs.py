"""Thin authenticated HTTP facade for the workspace's engineering owner."""

from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from polaris.bootstrap.config import Settings
from polaris.bootstrap.engineering_runtime import (
    engineering_error_status,
    get_engineering_service,
    observe_engineering_run,
    start_engineering_run,
)
from polaris.cells.roles.runtime.internal.engineering_session import EngineeringSessionStore
from polaris.cells.roles.runtime.public.engineering_service import EngineeringRunService
from polaris.delivery.http.dependencies import require_auth
from polaris.kernelone.concurrency import get_concurrency_manager
from pydantic import BaseModel, Field

router = APIRouter(prefix="/v2/engineering", tags=["engineering"], dependencies=[Depends(require_auth)])


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


async def _read_response(request: Request, read: Callable[[EngineeringRunService, Path], object]) -> JSONResponse:
    """Keep workspace resolution, KFS/SQL reads and JSON encoding off ASGI.

    Reuse K1's bounded IO pool. A cancelled request may finish its read there;
    the worker owns no execution/cancellation handles or persistent cache.
    """

    def response() -> JSONResponse:
        service, _settings, workspace = _bound(request)
        return JSONResponse(read(service, workspace))

    return await asyncio.get_running_loop().run_in_executor(get_concurrency_manager().get_io_pool(), response)


@router.post("/runs")
async def create_run(payload: EngineeringRunRequest, request: Request) -> dict[str, Any]:
    try:
        service, settings, _workspace = _bound(request)
        return await start_engineering_run(service, settings, **payload.model_dump())
    except (KeyError, OSError, ValueError, RuntimeError) as error:
        raise _error(error) from error


@router.get("/requests/{request_key}")
async def find_request(request_key: str, request: Request) -> JSONResponse:
    """Find a committed request; absence cannot resolve an in-flight effect."""

    def read(service: EngineeringRunService, workspace: Path) -> dict[str, Any]:
        identity = service.request_identity(request_key, workspace=workspace)
        if identity is None:
            raise KeyError(f"no committed engineering request found: {request_key}")
        return {
            **observe_engineering_run(service, identity["session_id"], workspace),
            "request_identity": {key: value for key, value in identity.items() if key != "session_id"},
        }

    try:
        return await _read_response(request, read)
    except (KeyError, OSError, ValueError, RuntimeError) as error:
        raise _error(error) from error


@router.get("/runs/current")
async def get_current_run(request: Request) -> JSONResponse:
    """Read current workspace facts through the injected engineering owner."""

    def read(service: EngineeringRunService, workspace: Path) -> dict[str, Any] | None:
        session_id = service.current_session_id(workspace=workspace)
        return observe_engineering_run(service, session_id, workspace) if session_id is not None else None

    try:
        return await _read_response(request, read)
    except (KeyError, OSError, ValueError, RuntimeError) as error:
        raise _error(error) from error


def observation_image(store: EngineeringSessionStore, session_id: str, call_id: str, index: int) -> tuple[bytes, str]:
    """Read one stored frame and verify it against the recorded digest."""

    if index < 0:
        raise ValueError("image index must be nonnegative")
    for event in store.load_events(session_id):
        payload = event["payload"]
        if payload.get("tool") != "browser_observe" or payload.get("call_id") != call_id:
            continue
        if payload.get("ok") is not True:
            raise ValueError("observation did not succeed")
        images = payload.get("images")
        if not isinstance(images, list) or index >= len(images):
            raise ValueError("image index out of range")
        image = images[index]
        if not isinstance(image, dict):
            raise ValueError("image entry is malformed")
        try:
            raw = base64.b64decode(str(image.get("data_base64") or ""), validate=True)
        except (binascii.Error, ValueError) as error:
            raise ValueError("stored image bytes are not valid base64") from error
        if hashlib.sha256(raw).hexdigest() != image.get("sha256"):
            raise ValueError("stored image bytes failed integrity verification")
        return raw, str(image.get("media_type") or "image/jpeg")
    raise KeyError(f"no such browser observation: {call_id}")


@router.get("/runs/{session_id}/observations/{call_id}/images/{index}")
async def get_observation_image(
    session_id: str, call_id: str, index: int, request: Request
) -> Response:
    """Serve one captured frame's bytes.

    The live event stream deliberately carries image metadata only, so the
    transport stays bounded and SQLite stays the single source of truth. The
    console fetches the frame here instead of receiving an unusable placeholder.
    """

    def read() -> tuple[bytes, str]:
        service, _settings, _workspace = _bound(request)
        return observation_image(service.store, session_id, call_id, index)

    try:
        raw, media_type = await asyncio.get_running_loop().run_in_executor(
            get_concurrency_manager().get_io_pool(), read
        )
    except (KeyError, OSError, ValueError, RuntimeError) as error:
        raise _error(error) from error
    return Response(content=raw, media_type=media_type)


@router.get("/runs/{session_id}")
async def get_run(session_id: str, request: Request) -> JSONResponse:
    try:
        return await _read_response(
            request, lambda service, workspace: observe_engineering_run(service, session_id, workspace)
        )
    except (KeyError, OSError, ValueError, RuntimeError) as error:
        raise _error(error) from error


@router.post("/runs/{session_id}/cancel")
async def cancel_run(
    session_id: str, request: Request, payload: EngineeringRunActionRequest | None = None
) -> JSONResponse:
    try:
        service, _settings, workspace = _bound(request, payload.workspace if payload is not None else None)
        await service.cancel(session_id, workspace=workspace)
        return await _read_response(request, lambda owner, bound: observe_engineering_run(owner, session_id, bound))
    except (KeyError, OSError, ValueError, RuntimeError) as error:
        raise _error(error) from error


@router.post("/runs/{session_id}/resume")
async def resume_run(
    session_id: str, request: Request, payload: EngineeringRunActionRequest | None = None
) -> JSONResponse:
    try:
        service, _settings, workspace = _bound(request, payload.workspace if payload is not None else None)
        await service.resume(session_id, workspace=workspace)
        return await _read_response(request, lambda owner, bound: observe_engineering_run(owner, session_id, bound))
    except (KeyError, OSError, ValueError, RuntimeError) as error:
        raise _error(error) from error
