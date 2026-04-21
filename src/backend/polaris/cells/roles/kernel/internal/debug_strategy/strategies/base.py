"""Base Debug Strategy - 调试策略基类。"""

from __future__ import annotations

from abc import ABC, abstractmethod

from polaris.cells.roles.kernel.internal.debug_strategy.models import (
    DebugPlan,
    ErrorContext,
)
from polaris.cells.roles.kernel.internal.debug_strategy.types import DebugStrategy


class BaseDebugStrategy(ABC):
    """调试策略基类。

    所有具体策略必须实现此接口。
    """

    def __init__(self, strategy_type: DebugStrategy) -> None:
        self.strategy_type = strategy_type

    @abstractmethod
    def can_handle(self, context: ErrorContext) -> bool:
        """判断此策略是否能处理给定的错误上下文。

        Args:
            context: 错误上下文

        Returns:
            如果能处理返回True
        """
        ...

    @abstractmethod
    def generate_plan(self, context: ErrorContext) -> DebugPlan:
        """生成调试计划。

        Args:
            context: 错误上下文

        Returns:
            调试计划
        """
        ...

    @property
    @abstractmethod
    def name(self) -> str:
        """策略名称。"""
        ...

    @property
    @abstractmethod
    def description(self) -> str:
        """策略描述。"""
        ...


__all__ = ["BaseDebugStrategy"]
