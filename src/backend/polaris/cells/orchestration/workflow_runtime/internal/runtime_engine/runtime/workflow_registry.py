"""Workflow Registry - Workflow 注册表。

管理所有 Workflow 的注册和调度。
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable, Coroutine

from polaris.kernelone.constants import MAX_WORKFLOW_TIMEOUT_SECONDS

logger = logging.getLogger(__name__)


@dataclass
class WorkflowDefinition:
    """Workflow 定义"""

    name: str
    handler: Callable[..., Coroutine[Any, Any, dict[str, Any]]]
    timeout: int = MAX_WORKFLOW_TIMEOUT_SECONDS  # seconds
    retry_policy: dict[str, Any] = field(default_factory=dict)


class WorkflowRegistry:
    """Workflow 注册表

    管理所有 Workflow 的注册和调度。
    """

    def __init__(self) -> None:
        self._workflows: dict[str, WorkflowDefinition] = {}
        self._lock = asyncio.Lock()

    def register(
        self,
        name: str,
        handler: Callable[..., Coroutine[Any, Any, dict[str, Any]]],
        timeout: int = MAX_WORKFLOW_TIMEOUT_SECONDS,
        retry_policy: dict[str, Any] | None = None,
    ) -> None:
        """注册 Workflow"""
        self._workflows[name] = WorkflowDefinition(
            name=name,
            handler=handler,
            timeout=timeout,
            retry_policy=retry_policy or {},
        )
        logger.debug(f"Registered workflow: {name}")

    def get(self, name: str) -> WorkflowDefinition | None:
        """获取 Workflow 定义"""
        return self._workflows.get(name)

    def list_workflows(self) -> list[str]:
        """列出所有 Workflow"""
        return list(self._workflows.keys())

    def has_workflow(self, name: str) -> bool:
        """检查 Workflow 是否存在"""
        return name in self._workflows


# 全局注册表
_global_registry: WorkflowRegistry | None = None
_global_registry_lock = asyncio.Lock()


def get_workflow_registry() -> WorkflowRegistry:
    """获取全局 Workflow 注册表"""
    global _global_registry
    if _global_registry is None:
        _global_registry = WorkflowRegistry()
    return _global_registry


async def get_workflow_registry_async() -> WorkflowRegistry:
    """获取全局 Workflow 注册表（异步安全）"""
    global _global_registry
    if _global_registry is None:
        async with _global_registry_lock:
            if _global_registry is None:
                _global_registry = WorkflowRegistry()
    return _global_registry


def register_workflow(
    name: str,
    timeout: int = MAX_WORKFLOW_TIMEOUT_SECONDS,
    retry_policy: dict[str, Any] | None = None,
) -> Callable[[Callable[..., Coroutine[Any, Any, dict[str, Any]]]], Callable[..., Coroutine[Any, Any, dict[str, Any]]]]:
    """Workflow 注册装饰器"""

    def decorator(
        handler: Callable[..., Coroutine[Any, Any, dict[str, Any]]],
    ) -> Callable[..., Coroutine[Any, Any, dict[str, Any]]]:
        registry = get_workflow_registry()
        registry.register(name, handler, timeout, retry_policy)
        return handler

    return decorator
