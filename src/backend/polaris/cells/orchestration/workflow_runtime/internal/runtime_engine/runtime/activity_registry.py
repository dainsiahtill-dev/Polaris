"""Activity Registry - Activity 注册表。

管理所有 Activity 的注册和调度。
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

from polaris.kernelone.constants import DEFAULT_OPERATION_TIMEOUT_SECONDS

logger = logging.getLogger(__name__)


@dataclass
class ActivityDefinition:
    """Activity 定义"""

    name: str
    handler: Callable[..., Awaitable[Any]]
    timeout: int = DEFAULT_OPERATION_TIMEOUT_SECONDS  # seconds
    retry_policy: dict[str, Any] = field(default_factory=dict)


class ActivityRegistry:
    """Activity 注册表

    管理所有 Activity 的注册和调度。
    """

    def __init__(self) -> None:
        self._activities: dict[str, ActivityDefinition] = {}
        self._lock = asyncio.Lock()

    def register(
        self,
        name: str,
        handler: Callable[..., Awaitable[Any]],
        timeout: int = DEFAULT_OPERATION_TIMEOUT_SECONDS,
        retry_policy: dict[str, Any] | None = None,
    ) -> None:
        """注册 Activity"""
        self._activities[name] = ActivityDefinition(
            name=name,
            handler=handler,
            timeout=timeout,
            retry_policy=retry_policy or {},
        )
        logger.debug(f"Registered activity: {name}")

    def get(self, name: str) -> ActivityDefinition | None:
        """获取 Activity 定义"""
        return self._activities.get(name)

    def list_activities(self) -> list[str]:
        """列出所有 Activity"""
        return list(self._activities.keys())

    def has_activity(self, name: str) -> bool:
        """检查 Activity 是否存在"""
        return name in self._activities


# 全局注册表
_global_registry: ActivityRegistry | None = None


def get_activity_registry() -> ActivityRegistry:
    """获取全局 Activity 注册表"""
    global _global_registry
    if _global_registry is None:
        _global_registry = ActivityRegistry()
    return _global_registry


def register_activity(
    name: str,
    timeout: int = DEFAULT_OPERATION_TIMEOUT_SECONDS,
    retry_policy: dict[str, Any] | None = None,
) -> Callable[[Callable[..., Awaitable[Any]]], Callable[..., Awaitable[Any]]]:
    """Activity 注册装饰器"""

    def decorator(handler: Callable[..., Awaitable[Any]]) -> Callable[..., Awaitable[Any]]:
        registry = get_activity_registry()
        registry.register(name, handler, timeout, retry_policy)
        return handler

    return decorator
