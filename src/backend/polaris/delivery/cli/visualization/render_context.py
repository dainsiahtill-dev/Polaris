"""渲染上下文模块

管理可视化渲染的状态和配置。

Example:
    >>> from polaris.delivery.cli.visualization.render_context import RenderContext
    >>> ctx = RenderContext()
    >>> ctx.collapse_by_type('DEBUG')
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from polaris.delivery.cli.visualization.contracts import RenderMode


@dataclass
class RenderContext:
    """渲染上下文

    管理渲染过程中的状态和配置。

    Attributes:
        mode: 渲染模式
        indent: 缩进级别
        max_depth: 最大渲染深度
        show_collapsed: 是否显示折叠标记
        collapse_states: 折叠状态映射
    """

    mode: RenderMode = RenderMode.INTERACTIVE
    indent: int = 0
    max_depth: int = 10
    show_collapsed: bool = True
    collapse_states: dict[str, bool] = field(default_factory=dict)

    def copy(self) -> RenderContext:
        """创建上下文的浅拷贝"""
        return RenderContext(
            mode=self.mode,
            indent=self.indent,
            max_depth=self.max_depth,
            show_collapsed=self.show_collapsed,
            collapse_states=dict(self.collapse_states),
        )

    def with_indent(self, additional: int = 2) -> RenderContext:
        """创建增加缩进的上下文副本"""
        ctx = self.copy()
        ctx.indent += additional
        return ctx

    def with_mode(self, mode: RenderMode) -> RenderContext:
        """创建改变模式的上下文副本"""
        ctx = self.copy()
        ctx.mode = mode
        return ctx

    def get_collapse_state(self, item_id: str, default: bool = False) -> bool:
        """获取折叠状态"""
        return self.collapse_states.get(item_id, default)

    def set_collapse_state(self, item_id: str, collapsed: bool) -> None:
        """设置折叠状态"""
        self.collapse_states[item_id] = collapsed

    def toggle_collapse_state(self, item_id: str) -> bool:
        """切换折叠状态"""
        current = self.get_collapse_state(item_id)
        new_state = not current
        self.set_collapse_state(item_id, new_state)
        return new_state

    def collapse_by_type(self, msg_type: str) -> None:
        """按类型折叠"""
        for item_id in list(self.collapse_states.keys()):
            if item_id.startswith(f"{msg_type}:"):
                self.collapse_states[item_id] = True

    def expand_by_type(self, msg_type: str) -> None:
        """按类型展开"""
        for item_id in list(self.collapse_states.keys()):
            if item_id.startswith(f"{msg_type}:"):
                self.collapse_states[item_id] = False

    def collapse_all(self) -> None:
        """折叠所有"""
        for item_id in self.collapse_states:
            self.collapse_states[item_id] = True

    def expand_all(self) -> None:
        """展开所有"""
        for item_id in self.collapse_states:
            self.collapse_states[item_id] = False

    def get_indent_str(self) -> str:
        """获取缩进字符串"""
        return " " * self.indent


@dataclass
class StreamContext(RenderContext):
    """流式渲染上下文

    用于流式输出场景。
    """

    buffer: list[str] = field(default_factory=list)
    flush_callback: Any = None  # callable | None

    def append(self, content: str) -> None:
        """追加内容到缓冲区"""
        self.buffer.append(content)

    def flush(self) -> str:
        """刷新缓冲区并返回内容"""
        result = "".join(self.buffer)
        self.buffer.clear()
        if self.flush_callback:
            self.flush_callback(result)
        return result

    def __enter__(self) -> StreamContext:
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.flush()
