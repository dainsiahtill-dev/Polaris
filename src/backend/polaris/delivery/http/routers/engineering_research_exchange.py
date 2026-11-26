"""Authenticated, explicit research publication exchange between workspace-bound local instances."""

from __future__ import annotations

import asyncio
import hashlib
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import TypeVar
from urllib.parse import quote

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from polaris.bootstrap.engineering_runtime import resolve_engineering_profile
from polaris.cells.roles.runtime.public.engineering_research import (
    DESCRIPTOR_LIMIT,
    FILE_LIMIT,
    decode_research_json,
    valid_digest,
    validate_descriptor,
)
from polaris.delivery.http.dependencies import require_auth
from polaris.delivery.research_peer import registered_research_instance
from polaris.kernelone.concurrency import get_concurrency_manager
from pydantic import BaseModel, Field

router = APIRouter(dependencies=[Depends(require_auth)])
T = TypeVar("T")


class ResearchPublicationRequest(BaseModel):
    model_config = {"extra": "forbid"}

    workspace: str | None = None
    experiment: str = Field(min_length=1, max_length=100)
    files: list[str] = Field(min_length=1, max_length=256)
    verification_seq: int | None = Field(default=None, gt=0, strict=True)


class ResearchImportRequest(BaseModel):
    model_config = {"extra": "forbid"}

    workspace: str | None = None
    source_instance: str = Field(min_length=1, max_length=128)
    source_run: str = Field(min_length=1, max_length=256)
    publication_sha256: str = Field(pattern="^[0-9a-f]{64}$")
    permission_profile_id: str | None = None


def _source_token(request: Request) -> str:
    values = request.headers.getlist("x-polaris-research-source-token")
    if len(values) != 1 or not values[0] or len(values[0]) > 4096 or any(not 32 < ord(ch) < 127 for ch in values[0]):
        raise PermissionError("one explicit source instance credential is required")
    return values[0]


def _run_segment(session_id: str) -> str:
    if (
        session_id in {".", ".."}
        or any(char in session_id for char in "/\\")
        or any(ord(char) < 32 for char in session_id)
    ):
        raise ValueError("invalid source run identity")
    return quote(session_id, safe="")


def _remaining(deadline: float) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise HTTPException(504, "research transfer deadline exceeded")
    return remaining


def _read_bytes(client: httpx.Client, path: str, workspace: str, limit: int, deadline: float) -> bytes:
    try:
        remaining = _remaining(deadline)
        timeout = httpx.Timeout(min(15.0, remaining), connect=min(3.0, remaining))
        with client.stream("GET", path, params={"workspace": workspace}, timeout=timeout) as response:
            if response.status_code in (401, 403):
                raise PermissionError("source instance refused publication access")
            if response.status_code == 404:
                raise KeyError("source publication or file was not found")
            if response.status_code != 200:
                raise HTTPException(502, f"source publication returned HTTP {response.status_code}")
            if response.headers.get("content-encoding", "identity").lower() != "identity":
                raise ValueError("source publication requires an identity byte stream")
            declared = response.headers.get("content-length")
            if declared is not None and (
                not declared.isascii() or not declared.isdecimal() or len(declared) > 20 or int(declared) > limit
            ):
                raise ValueError("source publication exceeds its byte bound")
            data = bytearray()
            for chunk in response.iter_raw():
                _remaining(deadline)
                if len(chunk) > limit - len(data):
                    raise ValueError("source publication exceeds its byte bound")
                data.extend(chunk)
            if declared is not None and int(declared) != len(data):
                raise ValueError("source publication length mismatch")
            return bytes(data)
    except httpx.HTTPError:
        raise HTTPException(502, "source publication transport unavailable") from None


@contextmanager
def _source_client(origin: str, token: str) -> Iterator[httpx.Client]:
    with httpx.Client(
        base_url=origin,
        headers={"Authorization": "Bearer " + token, "Accept-Encoding": "identity"},
        follow_redirects=False,
        trust_env=False,
        timeout=httpx.Timeout(15.0, connect=3.0),
    ) as client:
        yield client


async def _off_loop(action: Callable[[], T]) -> T:
    from .engineering_runs import _error

    try:
        return await asyncio.get_running_loop().run_in_executor(get_concurrency_manager().get_io_pool(), action)
    except (KeyError, OSError, ValueError, RuntimeError) as error:
        raise _error(error) from error


@router.post("/runs/{session_id}/research-publications")
async def publish_research(session_id: str, payload: ResearchPublicationRequest, request: Request) -> JSONResponse:
    from .engineering_runs import _bound

    def publish() -> JSONResponse:
        service, _, workspace = _bound(request, payload.workspace)
        return JSONResponse(
            service.publish_research(
                session_id,
                workspace=workspace,
                experiment=payload.experiment,
                files=payload.files,
                verification_seq=payload.verification_seq,
            )
        )

    return await _off_loop(publish)


@router.get("/runs/{session_id}/research-publications/{digest}")
async def read_research_publication(session_id: str, digest: str, request: Request) -> JSONResponse:
    from .engineering_runs import _bound

    def read() -> JSONResponse:
        service, _, workspace = _bound(request)
        if not valid_digest(digest):
            raise ValueError("invalid research publication digest")
        return JSONResponse(service.research_publication(session_id, digest, workspace=workspace))

    return await _off_loop(read)


@router.get("/runs/{session_id}/research-publications/{digest}/files/{index}")
async def read_research_publication_file(session_id: str, digest: str, index: int, request: Request) -> Response:
    from .engineering_runs import _bound

    def read() -> Response:
        service, _, workspace = _bound(request)
        if not valid_digest(digest) or index < 0:
            raise ValueError("invalid research publication file identity")
        return Response(
            service.research_publication_file(session_id, digest, index, workspace=workspace),
            media_type="application/octet-stream",
        )

    return await _off_loop(read)


@router.post("/research/imports")
async def import_research_publication(payload: ResearchImportRequest, request: Request) -> JSONResponse:
    from .engineering_runs import _bound

    def transfer() -> JSONResponse:
        service, settings, workspace = _bound(request, payload.workspace)
        selected = (
            payload.permission_profile_id
            if payload.permission_profile_id is not None
            else settings.server.engineering_default_permission_profile_id
        )
        scope = resolve_engineering_profile(settings, workspace, selected)
        token = _source_token(request)
        remote = registered_research_instance(payload.source_instance)
        if Path(remote["workspace"]) == workspace:
            raise ValueError("research exchange requires distinct workspaces")
        path = f"/v2/engineering/runs/{_run_segment(payload.source_run)}/research-publications/{payload.publication_sha256}"
        deadline = time.monotonic() + 120.0
        with _source_client(remote["backend_url"], token) as client:
            descriptor = decode_research_json(
                _read_bytes(client, path, remote["workspace"], DESCRIPTOR_LIMIT, deadline)
            )
            if not isinstance(descriptor, dict):
                raise ValueError("invalid publication descriptor")
            validate_descriptor(descriptor, payload.publication_sha256)
            if (
                descriptor.get("producer_workspace") != remote["workspace"]
                or descriptor.get("run_id") != payload.source_run
            ):
                raise PermissionError("source publication does not match the registered workspace and run")

            def fetch(index: int) -> bytes:
                item = descriptor["files"][index]
                data = _read_bytes(
                    client, f"{path}/files/{index}", remote["workspace"], min(FILE_LIMIT, item["bytes"]), deadline
                )
                if len(data) != item["bytes"] or hashlib.sha256(data).hexdigest() != item["sha256"]:
                    raise ValueError("source publication file integrity mismatch")
                return data

            return JSONResponse(
                service.import_research_publication(
                    workspace=workspace,
                    digest=payload.publication_sha256,
                    descriptor=descriptor,
                    fetch=fetch,
                    scope=scope,
                )
            )

    return await _off_loop(transfer)
