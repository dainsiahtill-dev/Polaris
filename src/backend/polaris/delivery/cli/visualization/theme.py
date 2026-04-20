"""主题配置模块

定义可视化组件的颜色和样式主题。

Example:
    >>> from polaris.delivery.cli.visualization.theme import ConsoleTheme
    >>> theme = ConsoleTheme.default()
    >>> print(theme.get_fold_marker(True))
    [▶]
"""

from __future__ import annotations

from dataclasses import dataclass

from polaris.delivery.cli.textual.models import MessageType


@dataclass
class MessageTheme:
    """消息类型主题

    定义每种消息类型的显示样式。
    """

    # 消息类型
    msg_type: MessageType

    # 显示名称
    label: str

    # 颜色 (ANSI 颜色代码)
    color: str

    # 背景色
    bg_color: str | None = None

    # 样式 (bold, dim, italic 等)
    style: str | None = None

    # 默认折叠状态
    default_collapsed: bool = True

    # 默认折叠标记
    collapsed_marker: str = "[▶]"
    expanded_marker: str = "[▼]"

    @classmethod
    def for_type(cls, msg_type: MessageType) -> MessageTheme:
        """获取指定类型的默认主题"""
        themes = {
            MessageType.USER: cls(
                msg_type=MessageType.USER,
                label="USER",
                color="cyan",
                default_collapsed=False,
            ),
            MessageType.ASSISTANT: cls(
                msg_type=MessageType.ASSISTANT,
                label="AI",
                color="green",
                default_collapsed=False,
            ),
            MessageType.THINKING: cls(
                msg_type=MessageType.THINKING,
                label="THINK",
                color="yellow",
                default_collapsed=True,
            ),
            MessageType.TOOL_CALL: cls(
                msg_type=MessageType.TOOL_CALL,
                label="TOOL",
                color="blue",
                default_collapsed=True,
            ),
            MessageType.TOOL_RESULT: cls(
                msg_type=MessageType.TOOL_RESULT,
                label="RESULT",
                color="blue",
                default_collapsed=True,
            ),
            MessageType.DEBUG: cls(
                msg_type=MessageType.DEBUG,
                label="DEBUG",
                color="dim",
                default_collapsed=True,
            ),
            MessageType.SYSTEM: cls(
                msg_type=MessageType.SYSTEM,
                label="SYSTEM",
                color="magenta",
                default_collapsed=True,
            ),
            MessageType.ERROR: cls(
                msg_type=MessageType.ERROR,
                label="ERROR",
                color="red",
                style="bold",
                default_collapsed=False,
            ),
            MessageType.METADATA: cls(
                msg_type=MessageType.METADATA,
                label="META",
                color="dim",
                default_collapsed=True,
            ),
        }
        return themes.get(msg_type, themes[MessageType.USER])


@dataclass
class DiffTheme:
    """Diff 视图主题"""

    # 新增行颜色
    add_color: str = "green"

    # 删除行颜色
    delete_color: str = "red"

    # 上下文行颜色
    context_color: str = "white"

    # 文件头颜色
    header_color: str = "cyan"

    # 行号颜色
    line_no_color: str = "dim"

    # 添加前缀
    add_prefix: str = "+"

    # 删除前缀
    delete_prefix: str = "-"

    # 上下文前缀
    context_prefix: str = " "


@dataclass
class ConsoleTheme:
    """控制台主题

    定义整体显示样式。
    """

    name: str = "default"

    # 折叠标记
    fold_collapsed: str = "[▶]"
    fold_expanded: str = "[▼]"

    # 消息主题
    message_themes: dict[MessageType, MessageTheme] | None = None

    # Diff 主题
    diff_theme: DiffTheme | None = None

    # 边框样式
    border_style: str = "round"

    # 宽度
    max_width: int = 120

    @classmethod
    def default(cls) -> ConsoleTheme:
        """获取默认主题"""
        return cls(
            name="default",
            message_themes={msg_type: MessageTheme.for_type(msg_type) for msg_type in MessageType},
            diff_theme=DiffTheme(),
        )

    @classmethod
    def minimal(cls) -> ConsoleTheme:
        """获取最小化主题（无颜色）"""
        return cls(
            name="minimal",
            message_themes={
                msg_type: MessageTheme(
                    msg_type=msg_type,
                    label=msg_type.name,
                    color="",
                    default_collapsed=MessageTheme.for_type(msg_type).default_collapsed,
                )
                for msg_type in MessageType
            },
        )

    @classmethod
    def dark(cls) -> ConsoleTheme:
        """获取暗色主题"""
        theme = cls.default()
        theme.name = "dark"
        # 调整某些颜色为暗色友好的版本
        return theme

    def get_message_theme(self, msg_type: MessageType) -> MessageTheme:
        """获取消息类型主题"""
        if self.message_themes and msg_type in self.message_themes:
            return self.message_themes[msg_type]
        return MessageTheme.for_type(msg_type)

    def get_fold_marker(self, is_collapsed: bool) -> str:
        """获取折叠标记"""
        return self.fold_collapsed if is_collapsed else self.fold_expanded

    def get_type_label(self, msg_type: MessageType) -> str:
        """获取类型标签"""
        return self.get_message_theme(msg_type).label

    def get_type_color(self, msg_type: MessageType) -> str:
        """获取类型颜色"""
        return self.get_message_theme(msg_type).color


# 预设主题
THEMES: dict[str, ConsoleTheme] = {
    "default": ConsoleTheme.default(),
    "minimal": ConsoleTheme.minimal(),
    "dark": ConsoleTheme.dark(),
}


def get_theme(name: str = "default") -> ConsoleTheme:
    """获取指定主题

    Args:
        name: 主题名称

    Returns:
        主题实例
    """
    return THEMES.get(name, ConsoleTheme.default())


def list_themes() -> list[str]:
    """列出所有可用主题"""
    return list(THEMES.keys())
