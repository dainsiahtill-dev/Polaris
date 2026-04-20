from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response
from polaris.cells.llm.control_plane.public import get_vision_service
from polaris.cells.runtime.artifact_store.public import get_arrow_service
from polaris.delivery.http.routers._shared import get_state, require_auth
from polaris.kernelone.constants import MAX_FILE_SIZE_BYTES
from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from collections.abc import Generator

logger = logging.getLogger(__name__)


def read_file_chunked(filepath: str, chunk_size: int = 8192) -> Generator[str, None, None]:
    """流式读取文件内容，避免大文件一次性加载到内存。

    Truncation is performed at the byte boundary so that the total bytes
    yielded never exceed MAX_FILE_SIZE_BYTES, even for multi-byte (e.g.
    UTF-8 CJK/emoji) content.
    """
    total_read = 0
    with open(filepath, encoding="utf-8", errors="ignore") as f:
        while True:
            chunk = f.read(chunk_size)
            if not chunk:
                break
            encoded = chunk.encode("utf-8", errors="ignore")
            if total_read + len(encoded) > MAX_FILE_SIZE_BYTES:
                # Byte-accurate truncation: slice the encoded bytes then decode.
                remaining = MAX_FILE_SIZE_BYTES - total_read
                if remaining > 0:
                    yield encoded[:remaining].decode("utf-8", errors="ignore")
                break
            total_read += len(encoded)
            yield chunk


router = APIRouter(prefix="/arsenal", tags=["arsenal"])


class VisionRequest(BaseModel):
    image: str  # Base64
    task: str = "<OD>"


class CodeSearchRequest(BaseModel):
    query: str
    limit: int = Field(default=10, ge=1, le=100)


def _turbo_disabled_status() -> dict[str, Any]:
    return {
        "available": False,
        "active": False,
        "reason": "turbo_disabled",
    }


def _build_basic_project_map(file_contents: dict[str, str]) -> list[dict[str, Any]]:
    points: list[dict[str, Any]] = []
    for idx, rel_path in enumerate(sorted(file_contents.keys())):
        text = file_contents.get(rel_path) or ""
        lines = text.count("\n") + 1 if text else 0
        size_bytes = len(text.encode("utf-8", errors="ignore"))
        points.append(
            {
                "id": rel_path,
                "path": rel_path,
                "x": float(idx % 20) * 1.6,
                "y": float(idx // 20) * 1.2,
                "z": max(0.1, min(4.0, size_bytes / 4096.0)),
                "lines": lines,
                "size_bytes": size_bytes,
            }
        )
    return points


@router.get("/vision/status", dependencies=[Depends(require_auth)])
def vision_status(request: Request) -> dict[str, Any]:
    """Get vision service status."""
    try:
        service = get_vision_service()
        return service.get_status()
    except (RuntimeError, ValueError) as exc:
        logger.debug("vision_status: service unavailable: %s", exc)
        return {"pil_available": False, "advanced_available": False, "model_loaded": False}


@router.post("/vision/analyze", dependencies=[Depends(require_auth)])
def analyze_ui(request: Request, payload: VisionRequest) -> dict[str, Any]:
    service = get_vision_service()
    # Auto-load for testing if not loaded
    if not service.is_loaded:
        service.load_model()  # This will likely just enable the mock if dependencies missing

    return service.analyze_image(payload.image, payload.task)


@router.get("/scheduler/status", dependencies=[Depends(require_auth)])
def get_scheduler_status(request: Request) -> dict[str, Any]:
    return _turbo_disabled_status()


@router.post("/scheduler/start", dependencies=[Depends(require_auth)])
async def start_scheduler(request: Request) -> dict[str, Any]:
    payload = _turbo_disabled_status()
    payload["message"] = "turbo feature is disabled"
    return payload


@router.post("/scheduler/stop", dependencies=[Depends(require_auth)])
async def stop_scheduler(request: Request) -> dict[str, Any]:
    payload = _turbo_disabled_status()
    payload["message"] = "turbo feature is disabled"
    return payload


@router.get("/code_map", dependencies=[Depends(require_auth)])
def get_code_map(request: Request) -> dict[str, Any] | Response:
    state = get_state(request)
    # 1. Gather all "code" files from workspace
    workspace = state.settings.workspace
    if not workspace or not os.path.isdir(workspace):
        return {"points": [], "mode": "error", "message": "Invalid workspace"}

    file_contents: dict[str, str] = {}
    _SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv"}
    _CODE_EXTS = {".py", ".ts", ".tsx", ".js", ".jsx", ".md", ".json"}
    MAX_FILES = 200

    try:
        count = 0
        for root, dirs, files in os.walk(workspace):
            # Prune hidden / dependency directories in-place (modifies the walk).
            dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]

            for file in files:
                if count >= MAX_FILES:
                    break
                ext = os.path.splitext(file)[1].lower()
                if ext not in _CODE_EXTS:
                    continue

                full_path = os.path.join(root, file)
                rel_path = os.path.relpath(full_path, workspace)

                try:
                    file_size = os.path.getsize(full_path)
                    if file_size > MAX_FILE_SIZE_BYTES:
                        # Large file: stream with byte-accurate truncation.
                        content = "".join(read_file_chunked(full_path))
                    else:
                        with open(full_path, encoding="utf-8", errors="ignore") as f:
                            content = f.read()
                    if content.strip():
                        file_contents[rel_path] = content
                        count += 1
                except OSError as e:
                    logger.debug("Failed to read file %s: %s", rel_path, e)

            if count >= MAX_FILES:
                break
    except (RuntimeError, ValueError) as e:
        logger.error("Error scanning files for Code Map: %s", e)

    # 2. Generate Map
    points = _build_basic_project_map(file_contents)

    # Check for Arrow format request
    output_format = request.query_params.get("format", "json")
    if output_format == "arrow":
        arrow_svc = get_arrow_service()
        if arrow_svc.available:
            ipc_bytes = arrow_svc.to_arrow_ipc(points)
            if ipc_bytes:
                return Response(content=ipc_bytes, media_type="application/vnd.apache.arrow.stream")

    return {
        "points": points,
        "mode": "cpu",
        "engine_active": False,
    }


# --- Code Search endpoints ---


@router.post("/code/index", dependencies=[Depends(require_auth)])
def code_index(request: Request) -> dict[str, Any]:
    """Index workspace code for semantic search."""
    state = get_state(request)
    workspace = state.settings.workspace
    try:
        from polaris.infrastructure.db.repositories.lancedb_code_search import index_workspace

        result = index_workspace(str(workspace))
        return {"result": result, "ok": True}
    except (RuntimeError, ValueError) as exc:
        return {"result": [], "ok": False, "error": str(exc)}


@router.post("/code/search", dependencies=[Depends(require_auth)])
async def code_search(request: Request, body: CodeSearchRequest) -> dict[str, Any]:
    """Search indexed code."""
    state = get_state(request)
    workspace = state.settings.workspace
    try:
        from polaris.infrastructure.db.repositories.lancedb_code_search import search_code

        results = search_code(body.query, str(workspace), limit=body.limit)
        return {"results": results, "ok": True}
    except (RuntimeError, ValueError) as exc:
        return {"results": [], "ok": False, "error": str(exc)}


# --- MCP status endpoint ---


async def _check_mcp_server_health_async(server_path: str, timeout: float = 5.0) -> dict[str, Any]:
    """Async health check for the MCP server using asyncio subprocess.

    Runs the MCP server process without blocking the event loop.
    """
    if not os.path.isfile(server_path):
        return {"healthy": False, "error": "Server file not found"}

    requests_text = (
        json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "clientInfo": {"name": "polaris-api", "version": "1.0.0"},
                },
            }
        )
        + "\n"
        + json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": "health", "arguments": {}},
            }
        )
        + "\n"
    )

    try:
        proc = await asyncio.create_subprocess_exec(
            sys.executable,
            server_path,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(input=requests_text.encode("utf-8")),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            return {"healthy": False, "error": f"Health check timed out after {timeout}s"}

        stdout = stdout_bytes.decode("utf-8", errors="replace")
        stderr = stderr_bytes.decode("utf-8", errors="replace")

        for line in stdout.strip().split("\n"):
            line = line.strip()
            if not line:
                continue
            try:
                response = json.loads(line)
                if response.get("id") == 2 and "result" in response:
                    result = response["result"]
                    content_list = result.get("content", [])
                    if isinstance(content_list, list) and content_list:
                        content = content_list[0]
                        if isinstance(content, dict) and "text" in content:
                            health_data = json.loads(content["text"])
                            return {
                                "healthy": health_data.get("status") == "healthy",
                                "server_version": health_data.get("version"),
                                "tools_available": health_data.get("tools_available", []),
                                "uptime_seconds": health_data.get("uptime_seconds"),
                                "workspace": health_data.get("workspace"),
                            }
            except (json.JSONDecodeError, KeyError):
                continue

        if stderr:
            return {"healthy": False, "error": f"Server error: {stderr[:200]}"}

        return {"healthy": False, "error": "Invalid health check response"}

    except (RuntimeError, ValueError) as exc:
        return {"healthy": False, "error": str(exc)}


@router.get("/mcp/status", dependencies=[Depends(require_auth)])
async def mcp_status(request: Request) -> dict[str, Any]:
    """Get MCP server availability status with dynamic health check."""
    mcp_server_path = os.path.normpath(
        os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            "..",
            "..",
            "tools",
            "policy_mcp_server.py",
        )
    )

    all_tools = ["health", "policy_check", "finops_check", "invariant_check", "get_policy_config"]
    file_exists = os.path.isfile(mcp_server_path)

    if not file_exists:
        return {
            "available": False,
            "healthy": False,
            "server_path": mcp_server_path,
            "tools": [],
            "protocol": "JSON-RPC 2.0 / stdio",
            "error": "Server file not found",
        }

    # Async health check – does not block the event loop.
    health = await _check_mcp_server_health_async(mcp_server_path)

    return {
        "available": True,
        "healthy": health.get("healthy", False),
        "server_path": mcp_server_path,
        "server_version": health.get("server_version"),
        "tools": health.get("tools_available", all_tools) if health.get("healthy") else all_tools,
        "protocol": "JSON-RPC 2.0 / stdio",
        "health_check": health,
    }


# --- Director Capabilities endpoint ---


@router.get("/director/capabilities", dependencies=[Depends(require_auth)])
def director_capabilities(request: Request) -> dict[str, Any]:
    """Get Director capability matrix."""
    try:
        from polaris.domain.entities.capability import get_role_capabilities

        return {
            "role": "director",
            "capabilities": get_role_capabilities("director"),
        }
    except (RuntimeError, ValueError) as exc:
        return {"error": str(exc), "capabilities": []}
