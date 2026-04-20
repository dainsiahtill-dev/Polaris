"""Rich Console 封装模块

封装 rich.console.Console，提供统一的渲染接口。

Example:
    >>> from polaris.delivery.cli.visualization.rich_console import RichConsole
    >>> console = RichConsole()
    >>> console.print_foldable("标题", "内容", collapsed=True)
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

try:
    from rich.console import Console as RichConsoleBase
    from rich.panel import Panel
    from rich.syntax import Syntax
    from rich.table import Table
    from rich.text import Text
    from rich.tree import Tree

    RICH_AVAILABLE = True
except ImportError:
    RICH_AVAILABLE = False

from polaris.delivery.cli.visualization.render_context import RenderContext
from polaris.delivery.cli.visualization.theme import ConsoleTheme, MessageTheme, get_theme

if TYPE_CHECKING:
    from polaris.delivery.cli.visualization.message_item import MessageItem, MessageType


class RichConsole:
    """Rich Console 封装

    提供统一的终端渲染接口。

    Attributes:
        theme: 控制台主题
        force_terminal: 是否强制使用终端
        width: 终端宽度
        height: 终端高度
    """

    def __init__(
        self,
        theme: ConsoleTheme | None = None,
        force_terminal: bool = True,
        width: int | None = None,
        height: int | None = None,
    ) -> None:
        """初始化 Rich Console

        Args:
            theme: 控制台主题
            force_terminal: 是否强制使用终端
            width: 终端宽度
            height: 终端高度
        """
        self.theme = theme or get_theme()
        self._rich_console: RichConsoleBase | None = None

        if RICH_AVAILABLE:
            self._rich_console = RichConsoleBase(
                force_terminal=force_terminal,
                width=width,
                height=height,
            )
        else:
            # 回退到标准输出
            self._fallback = True

    @property
    def console(self) -> RichConsoleBase | None:
        """获取 Rich Console 实例"""
        return self._rich_console

    @property
    def available(self) -> bool:
        """是否可用 Rich"""
        return RICH_AVAILABLE and self._rich_console is not None

    def print(self, *args: Any, **kwargs: Any) -> None:
        """打印内容

        Args:
            args: 位置参数
            kwargs: 关键字参数
        """
        if self.available:
            self._rich_console.print(*args, **kwargs)  # type: ignore
        else:
            # 回退到 print
            print(*args, **kwargs)

    def print_foldable(
        self,
        title: str,
        content: str,
        collapsed: bool = True,
        msg_type: MessageType | None = None,
    ) -> None:
        """打印可折叠内容

        Args:
            title: 标题
            content: 内容
            collapsed: 是否折叠
            msg_type: 消息类型
        """
        if not self.available:
            # 回退到简单打印
            marker = self.theme.get_fold_marker(collapsed)
            print(f"{marker} {title}")
            if not collapsed:
                print(content)
            return

        base_theme = self.theme
        msg_theme: MessageTheme | ConsoleTheme
        msg_theme = self.theme.get_message_theme(msg_type) if msg_type else base_theme

        # MessageTheme has collapsed_marker/expanded_marker, ConsoleTheme has get_fold_marker
        if isinstance(msg_theme, MessageTheme):
            marker = msg_theme.collapsed_marker if collapsed else msg_theme.expanded_marker
            label = msg_theme.label
            border_style = msg_theme.style or base_theme.border_style
        else:
            marker = msg_theme.get_fold_marker(collapsed)
            label = msg_theme.get_type_label(msg_type) if msg_type else ""
            border_style = msg_theme.border_style

        # 构建标题
        title_text = Text(f"{marker} [{label}] {title}") if label else Text(f"{marker} {title}")

        if collapsed:
            # 折叠状态 - 只显示标题
            self.print(title_text)
        else:
            # 展开状态 - 显示标题和内容
            panel = Panel(
                content,
                title=title_text,
                border_style=border_style,
            )
            self.print(panel)

    def print_message_item(
        self,
        item: MessageItem,
        context: RenderContext | None = None,
    ) -> None:
        """打印消息项

        Args:
            item: 消息项
            context: 渲染上下文
        """
        ctx = context or RenderContext()
        collapsed = ctx.get_collapse_state(item.id, item.is_collapsed)

        self.print_foldable(
            title=item.title,
            content=item.content,
            collapsed=collapsed,
            msg_type=item.type,
        )

    def print_diff(
        self,
        diff_text: str,
        show_stat: bool = True,
    ) -> None:
        """打印 Diff 内容

        Args:
            diff_text: Diff 文本
            show_stat: 是否显示统计
        """
        if not self.available:
            print(diff_text)
            return

        # 语法高亮
        syntax = Syntax(diff_text, "diff", theme="monokai")
        self.print(syntax)

    def print_table(
        self,
        data: list[dict[str, Any]],
        columns: list[str] | None = None,
    ) -> None:
        """打印表格

        Args:
            data: 表格数据
            columns: 列名
        """
        if not self.available:
            # 回退到简单打印
            for row in data:
                print(" | ".join(str(v) for v in row.values()))
            return

        table = Table(show_header=True)

        if columns:
            for col in columns:
                table.add_column(col)
        elif data:
            for col in data[0]:
                table.add_column(str(col))

        for row in data:
            table.add_row(*[str(v) for v in row.values()])

        self.print(table)

    def print_tree(
        self,
        root: str,
        children: list[str] | None = None,
    ) -> None:
        """打印树形结构

        Args:
            root: 根节点
            children: 子节点
        """
        if not self.available:
            print(root)
            if children:
                for child in children:
                    print(f"  └── {child}")
            return

        tree = Tree(root)
        if children:
            for child in children:
                tree.add(child)

        self.print(tree)

    def clear(self) -> None:
        """清屏"""
        if self.available:
            self._rich_console.clear()  # type: ignore
        else:
            print("\033[2J\033[H", end="")  # ANSI 清屏

    def size(self) -> tuple[int, int]:
        """获取终端尺寸

        Returns:
            (宽度, 高度)
        """
        if self.available:
            return self._rich_console.size  # type: ignore
        return (80, 24)


# 全局 Console 实例
_default_console: RichConsole | None = None


def get_console() -> RichConsole:
    """获取全局 Console 实例"""
    global _default_console
    if _default_console is None:
        _default_console = RichConsole()
    return _default_console


def print_foldable(
    title: str,
    content: str,
    collapsed: bool = True,
    msg_type: MessageType | None = None,
) -> None:
    """打印可折叠内容的便捷函数

    Args:
        title: 标题
        content: 内容
        collapsed: 是否折叠
        msg_type: 消息类型
    """
    get_console().print_foldable(title, content, collapsed, msg_type)


def print_message(item: MessageItem) -> None:
    """打印消息项的便捷函数

    Args:
        item: 消息项
    """
    get_console().print_message_item(item)
