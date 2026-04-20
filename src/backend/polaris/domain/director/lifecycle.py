"""Director 生命周期管理。

管理 Director 工作流程的状态和事件历史。

此模块从 polaris.kernelone.runtime.lifecycle 迁移而来，
将 Director 业务语义从 KernelOne 技术层分离。

迁移历史:
     - 2026-03-27: 从 polaris.kernelone.runtime.lifecycle 迁移

迁移指南:
    # 旧用法 (deprecated)
    from polaris.kernelone.runtime.lifecycle import update_director_lifecycle
    result = update_director_lifecycle(path, phase="planning", status="running")

    # 新用法
    from polaris.domain.director import DirectorLifecycleManager
    manager = DirectorLifecycleManager(workspace=".")
    state = manager.update(phase="planning", status="running")
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from polaris.domain.director.constants import (
    DEFAULT_DIRECTOR_LIFECYCLE,
    DirectorPhase,
)

# KernelOne fs utilities
from polaris.kernelone.fs.text_ops import ensure_parent_dir, write_json_atomic
from polaris.kernelone.utils import utc_now_iso

# ═══════════════════════════════════════════════════════════════════
# 数据类定义
# ═══════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class LifecycleEvent:
    """生命周期事件。

    Attributes:
        phase: 事件触发的阶段
        status: 事件状态
        timestamp: 事件时间戳 (ISO 8601)
        run_id: 运行 ID
        task_id: 任务 ID
        details: 额外详情
    """

    phase: str
    status: str
    timestamp: str
    run_id: str = ""
    task_id: str = ""
    details: dict[str, Any] | None = None


@dataclass(frozen=True)
class LifecycleState:
    """生命周期状态。

    Attributes:
        phase: 当前阶段
        status: 当前状态
        run_id: 运行 ID
        task_id: 任务 ID
        workspace: 工作区路径
        startup_completed: 启动是否完成
        execution_started: 执行是否开始
        terminal: 是否处于终态
        details: 额外详情
        error: 错误信息
        timestamp: 最后更新时间戳
        events: 事件历史
    """

    phase: str = DirectorPhase.INIT
    status: str = "unknown"
    run_id: str = ""
    task_id: str = ""
    workspace: str = ""
    startup_completed: bool = False
    execution_started: bool = False
    terminal: bool = False
    details: dict[str, Any] | None = None
    error: str | None = None
    timestamp: str = ""
    events: list[LifecycleEvent] = field(default_factory=list)


# ═══════════════════════════════════════════════════════════════════
# 生命周期管理器
# ═══════════════════════════════════════════════════════════════════


class DirectorLifecycleManager:
    """Director 生命周期管理器。

    提供线程安全的生命周期状态读写接口。

    Example:
        >>> manager = DirectorLifecycleManager(workspace=".")
        >>> manager.update(phase="planning", status="running", run_id="run-123")
        >>> state = manager.get_state()
        >>> print(state.phase, state.status)
        planning running
    """

    def __init__(self, workspace: str | Path | None = None) -> None:
        """初始化管理器。

        Args:
            workspace: 工作区路径，用于相对路径解析。
                     如果为 None，则使用当前工作目录。
        """
        self._workspace = Path(workspace) if workspace else Path.cwd()
        self._lock = threading.RLock()  # B-03: 改为实例级别，避免多实例阻塞

    def _resolve_path(self, path: str) -> Path:
        """解析生命周期文件路径。

        Args:
            path: 文件路径（相对或绝对）

        Returns:
            绝对路径
        """
        p = Path(path)
        if p.is_absolute():
            return p
        return self._workspace / p

    def _now_iso(self) -> str:
        """获取当前 UTC 时间戳 (ISO 8601 格式)。"""
        return utc_now_iso()

    def get_state(
        self,
        path: str = DEFAULT_DIRECTOR_LIFECYCLE,
    ) -> LifecycleState:
        """获取当前生命周期状态。

        Args:
            path: 生命周期文件路径

        Returns:
            生命周期状态，如果文件不存在则返回默认状态
        """
        file_path = self._resolve_path(path)

        # B-11: 添加锁保护，避免读写竞态条件
        with self._lock:
            if not file_path.exists():
                return LifecycleState()

            try:
                with open(file_path, encoding="utf-8") as handle:
                    data = json.load(handle)
            except (json.JSONDecodeError, OSError):
                return LifecycleState()

        # 数据解析在锁外进行，减少锁持有时间
        # 兼容旧格式
        if "lifecycle" in data:
            # 新格式
            lc = data.get("lifecycle", {})
            events = [
                LifecycleEvent(
                    phase=e["phase"],
                    status=e.get("status", ""),
                    timestamp=e["timestamp"],
                    run_id=e.get("run_id", ""),
                    task_id=e.get("task_id", ""),
                    details=e.get("details"),
                )
                for e in data.get("events", [])
            ]
            return LifecycleState(
                phase=lc.get("phase", DirectorPhase.INIT),
                status=lc.get("status", "unknown"),
                run_id=lc.get("run_id", ""),
                task_id=lc.get("task_id", ""),
                workspace=lc.get("workspace", ""),
                startup_completed=lc.get("startup_completed", False),
                execution_started=lc.get("execution_started", False),
                terminal=lc.get("terminal", False),
                details=lc.get("details"),
                error=lc.get("error"),
                timestamp=lc.get("timestamp", ""),
                events=events,
            )
        else:
            # 旧格式（直接字段）
            events = [
                LifecycleEvent(
                    phase=e.get("phase", ""),
                    status=e.get("status", ""),
                    timestamp=e.get("ts", ""),
                    run_id=e.get("run_id", ""),
                    task_id=e.get("task_id", ""),
                    details=e.get("details"),
                )
                for e in data.get("events", [])
            ]
            return LifecycleState(
                phase=data.get("phase", DirectorPhase.INIT),
                status=data.get("status", "unknown"),
                run_id=data.get("run_id", ""),
                task_id=data.get("task_id", ""),
                workspace=data.get("workspace", ""),
                startup_completed=data.get("startup_completed", False),
                execution_started=data.get("execution_started", False),
                terminal=data.get("terminal", False),
                details=data.get("details"),
                error=data.get("error"),
                timestamp=data.get("updated_at", ""),
                events=events,
            )

    def update(
        self,
        *,
        phase: str,
        path: str = DEFAULT_DIRECTOR_LIFECYCLE,
        status: str = "",
        run_id: str = "",
        task_id: str = "",
        startup_completed: bool | None = None,
        execution_started: bool | None = None,
        terminal: bool | None = None,
        details: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> LifecycleState:
        """更新生命周期状态。

        Args:
            phase: 当前阶段
            path: 生命周期文件路径
            status: 状态描述
            run_id: 运行 ID
            task_id: 任务 ID
            startup_completed: 启动是否完成
            execution_started: 执行是否开始
            terminal: 是否处于终态
            details: 额外详情字典
            error: 错误信息

        Returns:
            更新后的状态
        """
        with self._lock:
            file_path = self._resolve_path(path)
            now_iso = self._now_iso()

            # 读取当前状态
            if file_path.exists():
                try:
                    with open(file_path, encoding="utf-8") as handle:
                        payload = json.load(handle)
                except (json.JSONDecodeError, OSError):
                    payload = {}
            else:
                payload = {}

            # 初始化结构（兼容旧格式）
            if "lifecycle" not in payload:
                # 转换为新格式
                old_events = payload.get("events", [])
                new_events = []
                for e in old_events:
                    if isinstance(e, dict):
                        new_events.append(
                            {
                                "phase": e.get("phase", ""),
                                "status": e.get("status", ""),
                                "timestamp": e.get("ts", ""),
                                "run_id": e.get("run_id", ""),
                                "task_id": e.get("task_id", ""),
                                "details": e.get("details"),
                            }
                        )

                payload = {
                    "schema_version": 2,
                    "created_at": payload.get("created_at", now_iso),
                    "lifecycle": {
                        "phase": payload.get("phase", ""),
                        "status": payload.get("status", ""),
                        "run_id": payload.get("run_id", ""),
                        "task_id": payload.get("task_id", ""),
                        "startup_completed": payload.get("startup_completed", False),
                        "execution_started": payload.get("execution_started", False),
                        "terminal": payload.get("terminal", False),
                        "details": payload.get("details"),
                        "error": payload.get("error"),
                        "timestamp": payload.get("updated_at", ""),
                    },
                    "events": new_events,
                }

            lc = payload["lifecycle"]

            # 更新状态
            if phase:
                lc["phase"] = str(phase).strip().lower()
            if status:
                lc["status"] = str(status).strip().lower()
            if run_id:
                lc["run_id"] = str(run_id).strip()
            if task_id:
                lc["task_id"] = str(task_id).strip()
            if startup_completed is not None:
                lc["startup_completed"] = bool(startup_completed)
                if startup_completed and not lc.get("startup_at"):
                    lc["startup_at"] = now_iso
            if execution_started is not None:
                lc["execution_started"] = bool(execution_started)
                if execution_started and not lc.get("execution_started_at"):
                    lc["execution_started_at"] = now_iso
            if terminal is not None:
                lc["terminal"] = bool(terminal)
                if terminal and not lc.get("terminal_at"):
                    lc["terminal_at"] = now_iso
            if details is not None:
                existing_details = lc.get("details") if isinstance(lc.get("details"), dict) else {}
                existing_details.update(details)
                lc["details"] = existing_details
            if error is not None:
                lc["error"] = str(error)

            lc["timestamp"] = now_iso

            # 添加事件
            events = payload.setdefault("events", [])
            event_item: dict[str, Any] = {
                "phase": lc["phase"],
                "timestamp": now_iso,
            }
            if status:
                event_item["status"] = str(status).strip().lower()
            if run_id:
                event_item["run_id"] = str(run_id).strip()
            if task_id:
                event_item["task_id"] = str(task_id).strip()
            if details:
                event_item["details"] = details
            if error:
                event_item["error"] = str(error)

            events.append(event_item)

            # 限制事件数量
            if len(events) > 50:
                events[:] = events[-50:]

            # 原子写入
            ensure_parent_dir(str(file_path))
            write_json_atomic(str(file_path), payload)

            return self.get_state(path)


# ═══════════════════════════════════════════════════════════════════
# 兼容性别名函数（供外部调用）
# ═══════════════════════════════════════════════════════════════════


def update(
    *,
    path: str,
    phase: str,
    status: str = "",
    run_id: str = "",
    task_id: str = "",
    startup_completed: bool | None = None,
    execution_started: bool | None = None,
    terminal: bool | None = None,
    details: dict[str, Any] | None = None,
    error: str | None = None,
) -> dict[str, Any]:
    """更新 Director 生命周期状态。

    这是 DirectorLifecycleManager.update() 的函数式封装。

    Args:
        path: 生命周期文件路径
        phase: 当前阶段
        status: 状态描述
        run_id: 运行 ID
        task_id: 任务 ID
        startup_completed: 启动是否完成
        execution_started: 执行是否开始
        terminal: 是否处于终态
        details: 额外详情字典
        error: 错误信息

    Returns:
        原始格式的更新后状态（dict）
    """
    # B-04: 修复 workspace 解析 - 处理无目录的文件名
    p = Path(path)
    workspace: str | None
    if p.is_absolute():
        workspace = str(p.parent)
        filename = p.name
    else:
        # 对于相对路径，parent 可能是 "." 或空
        workspace = str(p.parent) if p.parent != Path(".") else None
        filename = p.name

    manager = DirectorLifecycleManager(workspace=workspace)
    state = manager.update(
        path=filename,
        phase=phase,
        status=status,
        run_id=run_id,
        task_id=task_id,
        startup_completed=startup_completed,
        execution_started=execution_started,
        terminal=terminal,
        details=details,
        error=error,
    )

    # 返回兼容的 dict 格式
    return {
        "schema_version": 2,
        "created_at": state.timestamp,
        "run_id": state.run_id,
        "task_id": state.task_id,
        "phase": state.phase,
        "startup_completed": state.startup_completed,
        "execution_started": state.execution_started,
        "terminal": state.terminal,
        "status": state.status,
        "details": state.details,
        "error": state.error,
        "events": [
            {
                "ts": e.timestamp,
                "phase": e.phase,
                "status": e.status,
                "run_id": e.run_id,
                "task_id": e.task_id,
                "details": e.details,
            }
            for e in state.events
        ],
        "updated_at": state.timestamp,
    }


def read(path: str) -> dict[str, Any]:
    """读取 Director 生命周期状态。

    Args:
        path: 生命周期文件路径

    Returns:
        原始格式的状态（dict）
    """
    # B-04: 修复 workspace 解析 - 处理无目录的文件名
    p = Path(path)
    workspace: str | None
    if p.is_absolute():
        workspace = str(p.parent)
        filename = p.name
    else:
        # 对于相对路径，parent 可能是 "." 或空
        workspace = str(p.parent) if p.parent != Path(".") else None
        filename = p.name

    manager = DirectorLifecycleManager(workspace=workspace)
    state = manager.get_state(filename)

    return {
        "schema_version": 2,
        "created_at": state.timestamp,
        "run_id": state.run_id,
        "task_id": state.task_id,
        "phase": state.phase,
        "startup_completed": state.startup_completed,
        "execution_started": state.execution_started,
        "terminal": state.terminal,
        "status": state.status,
        "details": state.details,
        "error": state.error,
        "events": [
            {
                "ts": e.timestamp,
                "phase": e.phase,
                "status": e.status,
                "run_id": e.run_id,
                "task_id": e.task_id,
                "details": e.details,
            }
            for e in state.events
        ],
        "updated_at": state.timestamp,
    }


__all__ = [
    "DirectorLifecycleManager",
    "LifecycleEvent",
    "LifecycleState",
    "read",
    "update",
]
