"""可视化契约接口模块

定义可视化组件的抽象接口和契约。

Example:
    >>> from polaris.delivery.cli.visualization.contracts import (
    ...     VisualizationContext,
    ...     RenderMode,
    ...     VisualizationContract,
    ... )
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Protocol, Union


class RenderMode(Enum):
    """渲染模式枚举"""

    COLLAPSED = "collapsed"
    EXPANDED = "expanded"
    INTERACTIVE = "interactive"
    SUMMARY = "summary"


class VisualizationContract(Protocol):
    """可视化契约接口

    所有可视化组件必须实现此接口。
    """

    def render(self, mode: RenderMode = RenderMode.INTERACTIVE) -> str:
        """渲染可视化内容

        Args:
            mode: 渲染模式

        Returns:
            渲染后的字符串
        """
        ...

    def get_fold_state(self, item_id: str) -> bool:
        """获取折叠状态

        Args:
            item_id: 项目 ID

        Returns:
            折叠状态
        """
        ...

    def set_fold_state(self, item_id: str, collapsed: bool) -> None:
        """设置折叠状态

        Args:
            item_id: 项目 ID
            collapsed: 是否折叠
        """
        ...


@dataclass
class VisualizationContext:
    """可视化渲染上下文

    管理渲染状态和配置。

    Attributes:
        mode: 当前渲染模式
        show_metadata: 是否显示元信息
        show_timestamps: 是否显示时间戳
        max_content_length: 最大内容长度
        theme_name: 主题名称
    """

    mode: RenderMode = RenderMode.INTERACTIVE
    show_metadata: bool = True
    show_timestamps: bool = False
    max_content_length: int = 500
    theme_name: str = "default"
    collapse_states: dict[str, bool] = field(default_factory=dict)

    def get_fold_state(self, item_id: str, default: bool | None = None) -> bool:
        """获取项目的折叠状态

        Args:
            item_id: 项目 ID
            default: 默认值

        Returns:
            折叠状态
        """
        if item_id in self.collapse_states:
            return self.collapse_states[item_id]
        return default if default is not None else False

    def set_fold_state(self, item_id: str, collapsed: bool) -> None:
        """设置项目的折叠状态

        Args:
            item_id: 项目 ID
            collapsed: 是否折叠
        """
        self.collapse_states[item_id] = collapsed

    def toggle_fold_state(self, item_id: str) -> bool:
        """切换折叠状态

        Args:
            item_id: 项目 ID

        Returns:
            切换后的状态
        """
        current = self.get_fold_state(item_id, False)
        new_state = not current
        self.set_fold_state(item_id, new_state)
        return new_state

    def expand_all(self) -> None:
        """展开所有项目"""
        for item_id in self.collapse_states:
            self.collapse_states[item_id] = False

    def collapse_all(self) -> None:
        """折叠所有项目"""
        for item_id in self.collapse_states:
            self.collapse_states[item_id] = True


@dataclass
class RenderResult:
    """渲染结果

    Attributes:
        content: 渲染后的内容
        metadata: 渲染元信息
        errors: 渲染错误列表
    """

    content: str
    metadata: dict[str, Any] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)

    @property
    def is_success(self) -> bool:
        """是否成功"""
        return len(self.errors) == 0

    def add_error(self, error: str) -> None:
        """添加错误"""
        self.errors.append(error)


class Renderable(ABC):
    """可渲染接口

    抽象基类，所有可渲染对象应实现此接口。
    """

    @abstractmethod
    def render(self, context: VisualizationContext | None = None) -> RenderResult:
        """渲染对象

        Args:
            context: 渲染上下文

        Returns:
            渲染结果
        """
        ...

    @abstractmethod
    def to_summary(self) -> str:
        """转换为摘要格式

        Returns:
            摘要字符串
        """
        ...

    @abstractmethod
    def to_expanded(self) -> str:
        """转换为展开格式

        Returns:
            展开格式字符串
        """
        ...
