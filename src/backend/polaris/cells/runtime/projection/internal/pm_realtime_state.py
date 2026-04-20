"""PM Realtime Service - PM 状态实时聚合服务

本模块提供 PM 角色的实时状态聚合，用于统一 WebSocket 推送。
PM 作为独立进程运行，状态通过 Workflow 工作流和状态文件获取。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from polaris.cells.runtime.projection.internal.io_helpers import build_cache_root
from polaris.cells.runtime.projection.internal.runtime_v2 import (
    PMTaskNode,
    PMTaskState,
    RoleState,
    RoleType,
)
from polaris.cells.runtime.projection.internal.workflow_status import (
    get_workflow_runtime_status,
    summarize_workflow_tasks,
)

logger = logging.getLogger(__name__)


@dataclass
class PMRealtimeState:
    """PM 实时状态"""

    role: RoleType = RoleType.PM
    state: RoleState = RoleState.IDLE
    task_id: str | None = None
    task_title: str | None = None
    detail: str | None = None
    updated_at: datetime = field(default_factory=datetime.now)

    # PM 特定字段
    tasks: list[PMTaskNode] = field(default_factory=list)
    iteration: int = 0
    phase: str = "pending"


class PMRealtimeProvider:
    """PM 实时状态提供者

    从 Workflow 工作流和状态文件聚合 PM 状态。
    """

    def __init__(self, workspace: str, ramdisk_root: str = "") -> None:
        self.workspace = workspace
        self.ramdisk_root = ramdisk_root
        self.cache_root = build_cache_root(ramdisk_root, workspace)

    async def get_state(self) -> PMRealtimeState:
        """获取 PM 当前状态"""
        workflow_status = get_workflow_runtime_status(self.workspace, self.cache_root)

        state = PMRealtimeState()

        if isinstance(workflow_status, dict):
            running = bool(workflow_status.get("running"))
            phase = str(workflow_status.get("phase", "")).lower()

            if running:
                if phase in {"intake", "docs_check", "architect", "planning"}:
                    state.state = RoleState.ANALYZING
                elif phase in {"implementation", "dispatching"}:
                    state.state = RoleState.EXECUTING
                elif phase in {"verification", "qa_gate"}:
                    state.state = RoleState.VERIFICATION
                else:
                    state.state = RoleState.EXECUTING

                state.phase = phase
            else:
                state.state = RoleState.IDLE
                state.phase = "idle"

            # 获取任务摘要
            summary = summarize_workflow_tasks(
                workflow_status,
                workspace=self.workspace,
                cache_root=self.cache_root,
            )

            # 获取任务列表
            if summary.get("tasks"):
                state.tasks = self._convert_tasks(summary["tasks"])
                state.iteration = workflow_status.get("iteration", 0)

        return state

    def _convert_tasks(self, tasks: list[dict]) -> list[PMTaskNode]:
        """将 Workflow 任务转换为 PM 任务节点"""
        result = []
        for task in tasks:
            task_state = str(task.get("status", "")).upper()
            if task_state in {"COMPLETED", "DONE"}:
                pm_state = PMTaskState.COMPLETED
            elif task_state in {"FAILED", "ERROR"}:
                pm_state = PMTaskState.FAILED
            elif task_state in {"RUNNING", "IN_PROGRESS"}:
                pm_state = PMTaskState.GENERATING
            else:
                pm_state = PMTaskState.PENDING

            result.append(
                PMTaskNode(
                    id=str(task.get("id", "")),
                    title=str(task.get("title", task.get("subject", ""))),
                    description=str(task.get("description", "")),
                    state=pm_state,
                    priority=self._map_priority(task.get("priority", "")),
                    assignee=str(task.get("assignee", "")) or None,
                    acceptance=task.get("acceptance", []),
                    created_at=self._parse_datetime(task.get("created_at")),
                    updated_at=self._parse_datetime(task.get("updated_at")),
                )
            )

        return result

    def _map_priority(self, priority: Any) -> int:
        """映射优先级"""
        p = str(priority).upper()
        if p == "CRITICAL":
            return 3
        elif p == "HIGH":
            return 2
        elif p == "MEDIUM":
            return 1
        return 0

    def _parse_datetime(self, value: Any) -> datetime:
        """解析日期时间"""
        if isinstance(value, datetime):
            return value
        if isinstance(value, str):
            try:
                return datetime.fromisoformat(value.replace("Z", "+00:00"))
            except (RuntimeError, ValueError) as exc:
                logger.debug("pm_realtime_state.py:141 datetime.fromisoformat failed: %s", exc)
        return datetime.now()


async def build_pm_realtime_state(
    workspace: str,
    ramdisk_root: str = "",
) -> dict[str, Any]:
    """构建 PM 实时状态的字典格式

    用于 WebSocket 推送。
    """
    provider = PMRealtimeProvider(workspace, ramdisk_root)
    state = await provider.get_state()

    return {
        "role": state.role.value,
        "state": state.state.value,
        "task_id": state.task_id,
        "task_title": state.task_title,
        "detail": state.detail,
        "updated_at": state.updated_at.isoformat(),
        "tasks": [t.model_dump(mode="json") for t in state.tasks],
        "iteration": state.iteration,
        "phase": state.phase,
    }
