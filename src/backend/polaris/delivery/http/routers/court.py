"""宫廷投影 API 路由.

本模块提供宫廷化 3D UI 投影的后端接口，包括拓扑查询、状态获取等功能。
所有接口为只读，不修改现有执行闭环与写入路径。
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
    """获取宫廷拓扑结构.

    返回固定宫廷拓扑（天子/三省/六部/官员席位），包含角色层级关系和3D位置坐标。

    Returns:
        {
            "nodes": [...],  # 拓扑节点列表
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
    """获取当前宫廷状态.

    根据引擎实时状态返回每个宫廷角色的状态、任务摘要、风险级别、证据索引。
    这是纯投影层，读取现有 engine_status/snapshot 数据并映射为古制角色状态。

    Returns:
        {
            "phase": "draft",           # 当前阶段
            "current_scene": "...",     # 当前场景ID
            "actors": {...},            # 角色状态映射
            "recent_events": [...],     # 最近事件列表
            "updated_at": 1234567890    # 更新时间戳
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
    """获取单个角色的详细信息.

    Args:
        role_id: 角色唯一标识

    Returns:
        角色详细信息，包含状态、任务、证据链
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
    """获取单个场景的详细配置.

    Args:
        scene_id: 场景唯一标识

    Returns:
        场景配置，包含相机位置、焦点角色、可切换场景等
    """
    scenes = get_scene_configs()

    if scene_id not in scenes:
        raise HTTPException(status_code=404, detail=f"Scene '{scene_id}' not found")

    return scenes[scene_id]


@router.get("/mapping")
async def get_role_mapping() -> dict[str, Any]:
    """获取技术角色到宫廷角色的映射表.

    Returns:
        {
            "tech_to_court": {...},  # 技术角色 -> 宫廷角色
            "court_roles": [...],    # 所有宫廷角色列表
            "version": "1.0"         # 映射表版本
        }
    """
    return {
        "tech_to_court": TECH_TO_COURT_ROLE_MAPPING,
        "court_roles": [node.role_id for node in COURT_TOPOLOGY],
        "version": "1.0",
        "description": "技术角色到古制角色的唯一映射表",
    }
