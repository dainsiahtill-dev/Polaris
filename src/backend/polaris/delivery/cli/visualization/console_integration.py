"""可视化控制台集成模块

将可视化模块集成到 terminal_console。

提供：
- DEBUG 信息折叠显示
- 消息类型过滤
- 快捷键处理
- 与 terminal_console.py 的无缝集成

Usage:
    from polaris.delivery.cli.visualization.console_integration import (
        VisualConsoleMixin,
        DebugMessage,
    )
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any

from polaris.delivery.cli.textual.models import MessageItem, MessageType
from polaris.delivery.cli.visualization.keyboard import FoldShortcut, KeyboardShortcutConfig
from polaris.delivery.cli.visualization.message_group import CollapsibleMessageGroup
from polaris.delivery.cli.visualization.render_context import RenderContext
from polaris.delivery.cli.visualization.theme import ConsoleTheme, get_theme

if TYPE_CHECKING:
    from collections.abc import Callable


@dataclass
class DebugMessage:
    """DEBUG 消息封装

    用于将 DEBUG 信息转换为可折叠的 MessageItem。
    """

    category: str = "debug"
    label: str = "event"
    source: str = ""
    tags: dict[str, Any] = field(default_factory=dict)
    payload: Any = None
    timestamp: datetime = field(default_factory=datetime.now)

    def to_message_item(self) -> MessageItem:
        """转换为 MessageItem"""
        title = f"[{self.category}][{self.label}]"
        if self.source:
            title += f"[{self.source}]"
        if self.tags:
            tag_str = " ".join(f"{k}={v}" for k, v in sorted(self.tags.items()))
            title += f" {tag_str}"

        content = str(self.payload) if self.payload is not None else ""

        return MessageItem(
            id=f"debug-{uuid.uuid4().hex[:8]}",
            type=MessageType.DEBUG,
            title=title,
            content=content,
            is_collapsed=True,  # DEBUG 默认折叠
            timestamp=self.timestamp,
            metadata={
                "category": self.category,
                "source": self.source,
                "tags": self.tags,
            },
        )


class VisualConsoleMixin:
    """可视化控制台混入类

    用于扩展 terminal_console 的可视化能力。

    Example:
        class MyConsole(VisualConsoleMixin):
            pass

        console = MyConsole()
        console.print_debug(category="fs", label="read", source="kernelone")
    """

    def __init__(
        self,
        theme: ConsoleTheme | None = None,
        shortcut_config: KeyboardShortcutConfig | None = None,
    ) -> None:
        """初始化可视化控制台

        Args:
            theme: 控制台主题
            shortcut_config: 快捷键配置
        """
        self._theme = theme or get_theme()
        self._shortcut_config = shortcut_config or KeyboardShortcutConfig.default()
        self._message_group = CollapsibleMessageGroup(
            id=f"console-{uuid.uuid4().hex[:8]}",
            items=[],
        )
        self._render_context = RenderContext()

    @property
    def message_group(self) -> CollapsibleMessageGroup:
        """获取消息组"""
        return self._message_group

    @property
    def theme(self) -> ConsoleTheme:
        """获取主题"""
        return self._theme

    def add_message(self, item: MessageItem) -> None:
        """添加消息到组

        Args:
            item: 消息项
        """
        self._message_group.items.append(item)

    def add_debug(
        self,
        category: str = "debug",
        label: str = "event",
        source: str = "",
        tags: dict[str, Any] | None = None,
        payload: Any = None,
    ) -> MessageItem:
        """添加 DEBUG 消息

        Args:
            category: 分类
            label: 标签
            source: 来源
            tags: 标签字典
            payload: 内容

        Returns:
            创建的 MessageItem
        """
        debug = DebugMessage(
            category=category,
            label=label,
            source=source,
            tags=tags or {},
            payload=payload,
        )
        item = debug.to_message_item()
        self.add_message(item)
        return item

    def print_message_item(self, item: MessageItem) -> None:
        """打印消息项

        根据折叠状态打印消息。

        Args:
            item: 消息项
        """
        collapsed = self._render_context.get_collapse_state(
            item.id,
            item.is_collapsed,
        )

        msg_theme = self._theme.get_message_theme(item.type)
        marker = msg_theme.collapsed_marker if collapsed else msg_theme.expanded_marker
        label = msg_theme.label
        title = item.title

        # 构建输出
        header = f"{marker} [{label}] {title}"
        print(header)

        if not collapsed and item.content:
            # 展开状态 - 打印内容
            # 处理 MessageContent | str 类型
            content_text: str
            content_text = item.content if isinstance(item.content, str) else item.content.text

            for line in content_text.splitlines():
                print(f"    {line}")

    def print_all_messages(self) -> None:
        """打印所有消息"""
        for item in self._message_group.items:
            self.print_message_item(item)

    def expand_by_type(self, msg_type: MessageType) -> None:
        """按类型展开

        Args:
            msg_type: 消息类型
        """
        self._message_group.expand_by_type(msg_type)

    def collapse_by_type(self, msg_type: MessageType) -> None:
        """按类型折叠

        Args:
            msg_type: 消息类型
        """
        self._message_group.collapse_by_type(msg_type)

    def expand_all_debug(self) -> None:
        """展开所有 DEBUG"""
        self.expand_by_type(MessageType.DEBUG)

    def collapse_all_debug(self) -> None:
        """折叠所有 DEBUG"""
        self.collapse_by_type(MessageType.DEBUG)

    def handle_shortcut(self, shortcut: FoldShortcut) -> bool:
        """处理快捷键

        Args:
            shortcut: 快捷键

        Returns:
            是否处理了快捷键
        """
        handlers = {
            FoldShortcut.EXPAND_ALL_DEBUG: self.expand_all_debug,
            FoldShortcut.COLLAPSE_ALL_DEBUG: self.collapse_all_debug,
            FoldShortcut.EXPAND_ALL: self._message_group.expand_all,
            FoldShortcut.COLLAPSE_ALL: self._message_group.collapse_all,
        }

        handler = handlers.get(shortcut)
        if handler:
            handler()
            return True
        return False

    def toggle_debug_visible(self) -> None:
        """切换 DEBUG 可见性"""
        debug_items = self._message_group.get_items_by_type(MessageType.DEBUG)
        if not debug_items:
            return

        # 检查当前状态
        first_debug = debug_items[0]
        if first_debug.is_collapsed:
            self.expand_all_debug()
        else:
            self.collapse_all_debug()

    def get_debug_count(self) -> int:
        """获取 DEBUG 消息数量"""
        return len(self._message_group.get_items_by_type(MessageType.DEBUG))

    def clear(self) -> None:
        """清空所有消息"""
        self._message_group.items.clear()


def create_debug_handler(console: VisualConsoleMixin) -> Callable[[Any], None]:
    """创建 DEBUG 消息处理器

    用于集成到 terminal_console 的事件流中。

    Args:
        console: 可视化控制台实例

    Returns:
        处理函数
    """

    def handle_debug(payload: dict[str, Any]) -> None:
        category = payload.get("category", "debug")
        label = payload.get("label", "event")
        source = payload.get("source", "")
        tags = payload.get("tags", {})
        msg_payload = payload.get("payload")

        console.add_debug(
            category=category,
            label=label,
            source=source,
            tags=tags,
            payload=msg_payload,
        )

    return handle_debug
