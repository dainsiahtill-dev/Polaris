"""Workflow Decorator Adapters - 工作流装饰器适配器。

提供与 Workflow 兼容的装饰器，同时支持 Embedded 和 Workflow 运行时。
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from polaris.cells.orchestration.workflow_runtime.internal.runtime_engine.runtime.workflow_registry import (
    get_workflow_registry,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Coroutine

from polaris.kernelone.constants import MAX_WORKFLOW_TIMEOUT_SECONDS

logger = logging.getLogger(__name__)


class WorkflowAPI:
    """Workflow API 适配器 - 兼容 Workflow 装饰器"""

    @staticmethod
    def defn(cls: type | None = None, **kwargs: Any) -> Callable[[type], type]:
        """@workflow.defn 装饰器 - 定义工作流类"""

        def decorator(c: type) -> type:
            # 注册到 embedded registry
            registry = get_workflow_registry()
            name = kwargs.get("name") or c.__name__
            registry.register(name, c, timeout=kwargs.get("timeout", MAX_WORKFLOW_TIMEOUT_SECONDS))
            logger.debug(f"Registered workflow: {name}")
            return c

        if cls is None:
            return decorator
        return decorator(cls)

    @staticmethod
    def run(
        fn: Callable[..., Coroutine[Any, Any, Any]] | None = None, **kwargs: Any
    ) -> Callable[..., Coroutine[Any, Any, Any]]:
        """@workflow.run 装饰器 - 定义工作流入口"""

        def decorator(f: Callable[..., Coroutine[Any, Any, Any]]) -> Callable[..., Coroutine[Any, Any, Any]]:
            # 标记为工作流入口方法
            f._is_workflow_run = True  # type: ignore[attr-defined]
            return f

        if fn is None:
            return decorator  # type: ignore[return-value]
        return decorator(fn)

    @staticmethod
    def query(fn: Callable[..., Any] | None = None, **kwargs: Any) -> Callable[..., Any]:
        """@workflow.query 装饰器 - 定义查询方法"""

        def decorator(f: Callable[..., Any]) -> Callable[..., Any]:
            # 标记为查询方法
            f._is_workflow_query = True  # type: ignore
            f._query_name = kwargs.get("name") or f.__name__  # type: ignore
            return f

        if fn is None:
            return decorator
        return decorator(fn)

    @staticmethod
    def signal(fn: Callable[..., Any] | None = None, **kwargs: Any) -> Callable[..., Any]:
        """@workflow.signal 装饰器 - 定义信号处理方法"""

        def decorator(f: Callable[..., Any]) -> Callable[..., Any]:
            # 标记为信号处理方法
            f._is_workflow_signal = True  # type: ignore
            f._signal_name = kwargs.get("name") or f.__name__  # type: ignore
            return f

        if fn is None:
            return decorator
        return decorator(fn)


class ActivityAPI:
    """Activity API 适配器 - 兼容 Workflow 装饰器"""

    @staticmethod
    def defn(fn: Callable[..., Any] | None = None, **kwargs: Any) -> Callable[..., Any]:
        """@activity.defn 装饰器 - 定义 Activity"""

        def decorator(f: Callable[..., Any]) -> Callable[..., Any]:
            from .runtime.activity_registry import get_activity_registry

            # 注册到 embedded registry
            registry = get_activity_registry()
            name = kwargs.get("name") or f.__name__
            registry.register(name, f, timeout=kwargs.get("timeout", 300))
            logger.debug(f"Registered activity: {name}")
            return f

        if fn is None:
            return decorator
        return decorator(fn)


# 全局实例
workflow = WorkflowAPI()
activity = ActivityAPI()


def get_workflow_api() -> WorkflowAPI:
    """获取 Workflow API（兼容 Workflow）"""
    return workflow


def get_activity_api() -> ActivityAPI:
    """获取 Activity API（兼容 Workflow）"""
    return activity


def is_workflow_run_method(fn: Callable[..., Any]) -> bool:
    """检查函数是否为工作流入口方法"""
    return getattr(fn, "_is_workflow_run", False) is True


def is_workflow_query_method(fn: Callable[..., Any]) -> bool:
    """检查函数是否为查询方法"""
    return getattr(fn, "_is_workflow_query", False) is True


def is_workflow_signal_method(fn: Callable[..., Any]) -> bool:
    """检查函数是否为信号处理方法"""
    return getattr(fn, "_is_workflow_signal", False) is True


def get_workflow_method_metadata(fn: Callable[..., Any]) -> dict[str, Any]:
    """获取工作流方法元数据"""
    return {
        "is_run": is_workflow_run_method(fn),
        "is_query": is_workflow_query_method(fn),
        "is_signal": is_workflow_signal_method(fn),
        "query_name": getattr(fn, "_query_name", None),
        "signal_name": getattr(fn, "_signal_name", None),
    }
