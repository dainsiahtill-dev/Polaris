"""Engine Registry - 引擎注册表

提供引擎的注册、查找和自动选择功能。
"""

from __future__ import annotations

import logging
import threading
from typing import Any

from .base import BaseEngine, EngineStrategy

logger = logging.getLogger(__name__)


class EngineRegistry:
    """引擎注册表

    管理所有可用的推理引擎，提供注册、查找和自动选择功能。

    使用示例:
        >>> registry = EngineRegistry()
        >>> registry.register(ReActEngine)
        >>> registry.register(PlanSolveEngine)
        >>>
        >>> # 获取指定策略的引擎
        >>> engine = registry.get(EngineStrategy.REACT)
        >>>
        >>> # 自动选择引擎
        >>> engine = registry.auto_select("探索代码库结构")
    """

    def __init__(self) -> None:
        """初始化引擎注册表"""
        self._engines: dict[EngineStrategy, type[BaseEngine]] = {}
        self._instances: dict[EngineStrategy, BaseEngine] = {}
        self._engine_defaults: dict[EngineStrategy, dict[str, Any]] = {}

    def register(self, engine_class: type[BaseEngine], **kwargs) -> None:
        """注册引擎类

        Args:
            engine_class: 引擎类（必须是 BaseEngine 的子类）
            **kwargs: 引擎初始化参数

        Raises:
            TypeError: 如果 engine_class 不是 BaseEngine 的子类
        """
        if not issubclass(engine_class, BaseEngine):
            raise TypeError(f"{engine_class} must be a subclass of BaseEngine")

        # 通过反射获取 strategy 属性而不实例化
        # 需要先实例化才能获取 @property 的值
        try:
            temp_instance = engine_class(workspace="", budget=None)
            strategy = temp_instance.strategy
        except (RuntimeError, ValueError) as e:
            raise ValueError(f"Cannot instantiate {engine_class}: {e}") from e

        # 如果传入了 workspace，从 kwargs 中提取
        workspace = kwargs.pop("workspace", "")

        self._engines[strategy] = engine_class
        # 存储默认参数用于后续创建实例
        self._engine_defaults[strategy] = {"workspace": workspace, **kwargs}
        logger.info(f"Registered engine: {strategy.value}")

    def register_instance(self, engine: BaseEngine) -> None:
        """注册引擎实例

        Args:
            engine: 引擎实例
        """
        strategy = engine.strategy
        self._instances[strategy] = engine
        logger.info(f"Registered engine instance: {strategy.value}")

    def get(self, strategy: EngineStrategy) -> BaseEngine | None:
        """获取指定策略的引擎实例

        Args:
            strategy: 引擎策略

        Returns:
            引擎实例，如果不存在则返回 None
        """
        # 优先从实例缓存获取
        if strategy in self._instances:
            return self._instances[strategy]

        # 否则创建新实例
        if strategy in self._engines:
            engine_class = self._engines[strategy]
            # 使用注册时存储的默认参数
            defaults = self._engine_defaults.get(strategy, {})
            instance = engine_class(**defaults)
            self._instances[strategy] = instance
            return instance

        return None

    def get_or_create(self, strategy: EngineStrategy, **kwargs) -> BaseEngine:
        """获取或创建引擎实例

        Args:
            strategy: 引擎策略
            **kwargs: 创建实例时的参数（优先级高于注册时的默认参数）

        Returns:
            引擎实例
        """
        # 优先从实例缓存获取
        if strategy in self._instances:
            return self._instances[strategy]

        # 否则创建新实例
        if strategy in self._engines:
            engine_class = self._engines[strategy]
            # 合并默认参数和传入参数，传入参数优先
            defaults = self._engine_defaults.get(strategy, {})
            defaults.update(kwargs)
            instance = engine_class(**defaults)
            self._instances[strategy] = instance
            return instance

        raise ValueError(f"No engine registered for strategy: {strategy.value}")

    def unregister(self, strategy: EngineStrategy) -> bool:
        """注销引擎

        Args:
            strategy: 引擎策略

        Returns:
            是否成功注销
        """
        if strategy in self._engines:
            del self._engines[strategy]
            logger.info(f"Unregistered engine: {strategy.value}")

        if strategy in self._instances:
            del self._instances[strategy]

        return True

    def list_strategies(self) -> list[EngineStrategy]:
        """列出所有已注册的策略

        Returns:
            策略列表
        """
        return list(self._engines.keys())

    def clear(self) -> None:
        """清空所有注册"""
        self._engines.clear()
        self._instances.clear()
        logger.info("Cleared all engine registrations")


# 全局注册表实例
_global_registry: EngineRegistry | None = None
_registry_lock = threading.Lock()


def get_engine_registry() -> EngineRegistry:
    """获取全局引擎注册表（线程安全）"""
    global _global_registry
    if _global_registry is None:
        with _registry_lock:
            if _global_registry is None:
                _global_registry = EngineRegistry()
    return _global_registry


def register_engine(engine_class: type[BaseEngine], **kwargs) -> None:
    """注册引擎到全局注册表

    Args:
        engine_class: 引擎类
        **kwargs: 引擎初始化参数
    """
    get_engine_registry().register(engine_class, **kwargs)


def get_engine(strategy: EngineStrategy) -> BaseEngine | None:
    """从全局注册表获取引擎

    Args:
        strategy: 引擎策略

    Returns:
        引擎实例
    """
    return get_engine_registry().get(strategy)
