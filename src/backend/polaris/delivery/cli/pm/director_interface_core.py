"""Director Interface compatibility layer for PM delivery code.

The public dataclasses and factory remain for older PM callers, but code
materialization is routed through the ``director.execution`` public Cell
contract. This module must not spawn Director scripts or own a second execution
protocol.
"""

import os
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from polaris.kernelone.shared.path_utils import normalize_path_list


@dataclass
class DirectorTask:
    """Director 任务定义"""

    task_id: str
    goal: str
    target_files: list[str]
    acceptance_criteria: list[str]
    constraints: list[str]
    context: dict[str, Any]
    scope_paths: list[str] | None = None
    scope_mode: str = "module"

    def __post_init__(self) -> None:
        self.target_files = normalize_path_list(self.target_files or [])
        self.scope_paths = normalize_path_list(self.scope_paths or [])
        if not isinstance(self.acceptance_criteria, list):
            self.acceptance_criteria = []
        if not isinstance(self.constraints, list):
            self.constraints = []


@dataclass
class DirectorResult:
    """Director 执行结果"""

    success: bool
    task_id: str
    changed_files: list[str]
    patches: list[dict[str, Any]]
    error: str | None = None
    metadata: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        self.changed_files = normalize_path_list(self.changed_files or [])
        if self.metadata is None:
            self.metadata = {}


class DirectorInterface(ABC):
    """
    Director 抽象接口

    Delivery compatibility interface. Concrete implementations must delegate
    to canonical services instead of launching standalone scripts.
    """

    def __init__(self, workspace: Path, config: dict | None = None) -> None:
        self.workspace = Path(workspace)
        self.config = config or {}

    @abstractmethod
    def execute(self, task: DirectorTask) -> DirectorResult:
        """
        执行单个任务

        Args:
            task: 任务定义

        Returns:
            DirectorResult: 执行结果
        """
        pass

    @abstractmethod
    def is_available(self) -> bool:
        """检查 Director 是否可用"""
        pass

    @abstractmethod
    def get_info(self) -> dict[str, str]:
        """获取 Director 信息"""
        pass


class CanonicalDirectorAdapter(DirectorInterface):
    """PM compatibility adapter backed by the Director execution public Cell."""

    def is_available(self) -> bool:
        """The canonical adapter is the only materialization path."""

        return True

    def execute(self, task: DirectorTask) -> DirectorResult:
        """Execute a task through ``ExecuteDirectorTaskCommandV1``."""

        try:
            from polaris.cells.director.execution.public import (
                ExecuteDirectorTaskCommandV1,
                execute_director_task,
            )

            payload = self._to_execution_payload(task)
            metadata = dict(payload["metadata"])
            metadata.update(
                {
                    "source": "pm.director_interface",
                    "target_files": normalize_path_list(task.target_files),
                    "scope_paths": normalize_path_list(task.scope_paths or []),
                    "acceptance_criteria": list(task.acceptance_criteria or []),
                    "constraints": list(task.constraints or []),
                    "timeout_seconds": int(self.config.get("timeout") or 3600),
                    "model": str(self.config.get("model") or ""),
                    "max_workers": self._resolve_max_workers(),
                    "execution_mode": self._resolve_execution_mode(),
                }
            )
            result = execute_director_task(
                ExecuteDirectorTaskCommandV1(
                    task_id=task.task_id,
                    workspace=str(self.workspace),
                    instruction=str(payload.get("description") or task.goal or task.task_id),
                    metadata=metadata,
                )
            )
        except (ImportError, RuntimeError, TypeError, ValueError) as exc:
            return DirectorResult(
                success=False,
                task_id=task.task_id,
                changed_files=[],
                patches=[],
                error=f"Director execution public contract failed: {exc}",
                metadata={"adapter": "director.execution.public"},
            )

        metadata = dict(result.metadata)
        changed_files = normalize_path_list(metadata.get("changed_files") or list(result.evidence_paths or ()))
        adapter_payload = metadata.get("adapter_result")
        adapter_result = adapter_payload if isinstance(adapter_payload, dict) else {}
        raw_patches = adapter_result.get("patches")
        patches = (
            [dict(item) for item in raw_patches if isinstance(item, dict)] if isinstance(raw_patches, list) else []
        )
        return DirectorResult(
            success=bool(result.ok),
            task_id=task.task_id,
            changed_files=changed_files,
            patches=patches,
            error=result.error_message or result.error_code or None,
            metadata={
                **metadata,
                "adapter": "director.execution.public",
                "canonical_execution_contract": "ExecuteDirectorTaskCommandV1",
                "status": result.status,
            },
        )

    @staticmethod
    def _to_execution_payload(task: DirectorTask) -> dict[str, Any]:
        context = dict(task.context) if isinstance(task.context, dict) else {}
        embedded_task_raw = context.get("task")
        embedded_task = dict(embedded_task_raw) if isinstance(embedded_task_raw, dict) else {}
        description = (
            str(embedded_task.get("description") or "").strip()
            or str(embedded_task.get("goal") or "").strip()
            or str(task.goal or "").strip()
        )
        embedded_metadata_raw = embedded_task.get("metadata")
        metadata = dict(embedded_metadata_raw) if isinstance(embedded_metadata_raw, dict) else {}
        metadata.update(
            {
                "pm_task_id": task.task_id,
                "source": "pm.director_interface",
            }
        )
        return {
            "id": task.task_id,
            "task_id": task.task_id,
            "subject": task.goal,
            "title": task.goal,
            "goal": description,
            "description": description,
            "target_files": normalize_path_list(task.target_files),
            "scope_paths": normalize_path_list(task.scope_paths or []),
            "scope_mode": str(task.scope_mode or "module"),
            "acceptance_criteria": list(task.acceptance_criteria or []),
            "constraints": list(task.constraints or []),
            "context": context,
            "metadata": metadata,
        }

    def _resolve_max_workers(self) -> int:
        raw_workers = (
            self.config.get("max_directors")
            or self.config.get("max_parallel_tasks")
            or self.config.get("director_max_parallel_tasks")
            or self.config.get("max_workers")
        )
        if raw_workers is None:
            return 1
        try:
            parsed = int(raw_workers)
        except (RuntimeError, ValueError, TypeError):
            return 1
        return max(1, parsed)

    def _resolve_execution_mode(self) -> str:
        raw_mode = (
            self.config.get("director_execution_mode")
            or self.config.get("director_workflow_execution_mode")
            or self.config.get("execution_mode")
        )
        mode_token = str(raw_mode or "").strip().lower()
        if mode_token in ("parallel", "concurrent", "multi"):
            return "parallel"
        return "serial"

    def get_info(self) -> dict[str, str]:
        return {
            "type": "canonical",
            "name": "Canonical Director Adapter",
            "adapter": "roles.adapters.director",
        }


class NoDirectorAdapter(DirectorInterface):
    """
    No Director Adapter - 无导演模式

    PM独立运行时使用，不调用任何Director。
    用于任务分解、规划等纯PM功能。
    """

    def __init__(self, workspace: Path, config: dict | None = None) -> None:
        super().__init__(workspace, config)
        self.config = config or {}

    def is_available(self) -> bool:
        """No Director总是可用"""
        return True

    def execute(self, task: DirectorTask) -> DirectorResult:
        """
        不执行任何操作，直接返回成功

        PM独立运行时，任务由PM自己分解规划，
        不需要Director执行代码生成。
        """
        from datetime import datetime

        return DirectorResult(
            success=True,
            task_id=task.task_id,
            changed_files=[],
            patches=[],
            error=None,
            metadata={
                "note": "PM running in standalone mode - no Director executed",
                "timestamp": datetime.now().isoformat(),
                "mode": "no_director",
            },
        )

    def get_info(self) -> dict[str, str]:
        return {
            "type": "none",
            "name": "No Director (Standalone PM)",
            "description": "PM runs independently without Director",
        }


class DirectorFactory:
    """
    Director 工厂

    根据配置创建对应的 Director 实例。
    """

    _aliases: dict[str, str] = {
        "": "canonical",
        "auto": "canonical",
        "adapter": "canonical",
        "script": "canonical",
    }
    _registry: dict[str, type[DirectorInterface]] = {
        "canonical": CanonicalDirectorAdapter,
        "none": NoDirectorAdapter,
    }

    @classmethod
    def create(
        cls,
        director_type: str,
        workspace: Path,
        config: dict | None = None,
    ) -> DirectorInterface:
        """
        创建 Director 实例

        Args:
            director_type: Director 类型 ("canonical", "none", ...)
            workspace: 工作空间路径
            config: 配置参数

        Returns:
            DirectorInterface 实例
        """
        normalized_type = cls._aliases.get(str(director_type or "").strip().lower(), director_type)
        if normalized_type not in cls._registry:
            raise ValueError(f"Unknown director type: {director_type}. Available: {list(cls._registry.keys())}")

        director_class = cls._registry[normalized_type]
        return director_class(workspace, config)

    @classmethod
    def register(cls, name: str, director_class: type) -> None:
        """注册新的 Director 类型"""
        if not issubclass(director_class, DirectorInterface):
            raise ValueError("Director class must inherit from DirectorInterface")
        cls._registry[name] = director_class

    @classmethod
    def list_available(cls) -> list[str]:
        """列出可用的 Director 类型"""
        return list(cls._registry.keys())


def create_director(
    workspace: str,
    director_type: str | None = None,
    config: dict | None = None,
) -> DirectorInterface:
    """
    创建 Director 的便捷函数

    根据环境变量或配置自动选择合适的 Director。

    Usage:
        # 自动检测
        director = create_director("/path/to/workspace")

        # 明确指定
        director = create_director("/path/to/workspace", "canonical")

        # 带配置
        director = create_director(
            "/path/to/workspace",
            "canonical",
            {"timeout": 1200}
        )
    """
    workspace_path = Path(workspace)

    # 如果没有指定类型，从环境变量或自动检测
    if director_type is None:
        director_type = os.getenv("KERNELONE_DIRECTOR_TYPE", "auto")

    return DirectorFactory.create(director_type, workspace_path, config)


# Public exports
__all__ = [
    "CanonicalDirectorAdapter",
    "DirectorFactory",
    "DirectorInterface",
    "DirectorResult",
    "DirectorTask",
    "NoDirectorAdapter",
    "create_director",
]
