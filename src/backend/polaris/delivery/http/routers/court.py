"""Court projection API router.

This module provides backend interfaces for the court-style 3D UI projection, including topology queries and state retrieval.
All interfaces are read-only and do not modify existing execution loops or write paths.
"""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from polaris.cells.docs.court_workflow.public.service import (
    COURT_TOPOLOGY,
    TECH_TO_COURT_ROLE_MAPPING,
    get_court_topology,
    get_scene_configs,
    map_engine_to_court_state,
)
from polaris.cells.runtime.projection.public.service import (
    build_director_status,
    build_pm_status_async,
    read_json,
)
from polaris.cells.runtime.state_owner.public.service import AppState
from polaris.delivery.http.routers._shared import get_state as get_runtime_state, require_auth
from polaris.kernelone.runtime.defaults import DEFAULT_WORKSPACE
from polaris.kernelone.storage.io_paths import build_cache_root, resolve_artifact_path

router = APIRouter(prefix="/court", tags=["court"], dependencies=[Depends(require_auth)])


def _get_engine_status(app_state: AppState) -> dict[str, Any] | None:
    """读取引擎状态文件."""
    workspace = app_state.settings.workspace or DEFAULT_WORKSPACE
    cache_root = build_cache_root("", str(workspace))
    path = resolve_artifact_path(str(workspace), str(cache_root), "runtime/status/engine.status.json")
    if not path or not isinstance(path, str):
        return None
    import os

    if not os.path.isfile(path):
        return None
    payload = read_json(path)
    if not isinstance(payload, dict):
        return None
    payload.setdefault("path", path)
    return payload


async def _get_pm_status(app_state: AppState) -> dict[str, Any]:
    """构建 PM 状态."""
    return await build_pm_status_async(app_state)


async def _get_director_status(app_state: AppState) -> dict[str, Any]:
    """构建 Director 状态."""
    workspace = app_state.settings.workspace or DEFAULT_WORKSPACE
    cache_root = build_cache_root("", str(workspace))
    return await build_director_status(app_state, str(workspace), str(cache_root))


@router.get("/topology")
async def get_topology() -> dict[str, Any]:
    """Get court topology structure.

    Returns fixed court topology (User/Top departments/Departments/Officer seats), including role hierarchy and 3D position coordinates.

    Returns:
        {
            "nodes": [...],  # Topology node list
            "count": 22,     # 可交互角色数量
            "scenes": {...}  # 场景配置
        }
    """
    topology = get_court_topology()
    scenes = get_scene_configs()

    # 统计可交互角色
    interactive_count = sum(1 for node in topology if node.get("is_interactive", True))

    return {
        "nodes": topology,
        "count": interactive_count,
        "total": len(topology),
        "scenes": scenes,
    }


@router.get("/state")
async def get_state(
    request: Request,
) -> dict[str, Any]:
    """Get current court state.

    Returns the status, task summary, risk level, and evidence index for each court role based on real-time engine status.
    This is a pure projection layer that reads existing engine_status/snapshot data and maps it to display role states.

    Returns:
        {
            "phase": "draft",           # Current phase
            "current_scene": "...",     # Current scene ID
            "actors": {...},            # Role state mapping
            "recent_events": [...],     # Recent event list
            "updated_at": 1234567890    # Update timestamp
        }
    """
    request_state = get_runtime_state(request)
    engine_status = _get_engine_status(request_state)
    pm_status = await _get_pm_status(request_state)
    director_status = await _get_director_status(request_state)

    court_state = map_engine_to_court_state(
        engine_status=engine_status,
        pm_status=pm_status,
        director_status=director_status,
    )

    return court_state


@router.get("/actors/{role_id}")
async def get_actor_detail(
    role_id: str,
    request: Request,
) -> dict[str, Any]:
    """Get detailed information for a single role.

    Args:
        role_id: Unique role identifier

    Returns:
        Detailed role information, including status, tasks, and evidence chain
    """
    request_state = get_runtime_state(request)
    # 获取完整状态
    engine_status = _get_engine_status(request_state)
    pm_status = await _get_pm_status(request_state)
    director_status = await _get_director_status(request_state)

    court_state = map_engine_to_court_state(
        engine_status=engine_status,
        pm_status=pm_status,
        director_status=director_status,
    )

    actors = court_state.get("actors", {})
    if role_id not in actors:
        raise HTTPException(status_code=404, detail=f"Role '{role_id}' not found")

    actor = actors[role_id]

    # 添加拓扑信息
    topology_nodes = {n["role_id"]: n for n in get_court_topology()}
    if role_id in topology_nodes:
        actor["topology"] = topology_nodes[role_id]

    return actor


@router.get("/scenes/{scene_id}")
async def get_scene_detail(scene_id: str) -> dict[str, Any]:
    """Get detailed configuration for a single scene.

    Args:
        scene_id: Unique scene identifier

    Returns:
        Scene configuration, including camera position, focus roles, switchable scenes, etc.
    """
    scenes = get_scene_configs()

    if scene_id not in scenes:
        raise HTTPException(status_code=404, detail=f"Scene '{scene_id}' not found")

    return scenes[scene_id]


@router.get("/mapping")
async def get_role_mapping() -> dict[str, Any]:
    """Get technical role to court role mapping table.

    Returns:
        {
            "tech_to_court": {...},  # Technical role -> Court role
            "court_roles": [...],    # All court role list
            "version": "1.0"         # Mapping version
        }
    """
    return {
        "tech_to_court": TECH_TO_COURT_ROLE_MAPPING,
        "court_roles": [node.role_id for node in COURT_TOPOLOGY],
        "version": "1.0",
        "description": "Unique mapping table from technical roles to display roles",
    }
