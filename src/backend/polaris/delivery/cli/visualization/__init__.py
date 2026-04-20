"""Polaris CLI 可视化增强模块

提供：
- Diff View 渲染器 (DiffView) - 基于 difflib 标准库
- Rich Console 封装
- 主题配置
- 消息组管理
- 键盘快捷键配置
"""

from __future__ import annotations

# 从 textual/models.py 导入共享类型
from polaris.delivery.cli.textual.models import (
    MessageItem,
    MessageType,
)
from polaris.delivery.cli.visualization.console_integration import (
    DebugMessage,
    VisualConsoleMixin,
    create_debug_handler,
)
from polaris.delivery.cli.visualization.contracts import (
    Renderable,
    RenderMode,
    RenderResult,
    VisualizationContext,
)
from polaris.delivery.cli.visualization.diff_parser import (
    DiffFile,
    DiffHunk,
    DiffLine,
    DiffResult,
    DiffStats,
    DiffView,
    compute_diff,
)
from polaris.delivery.cli.visualization.keyboard import (
    FoldShortcut,
    KeyboardShortcutConfig,
)
from polaris.delivery.cli.visualization.message_group import (
    CollapsibleMessageGroup,
)
from polaris.delivery.cli.visualization.render_context import (
    RenderContext,
    StreamContext,
)
from polaris.delivery.cli.visualization.rich_console import (
    RichConsole,
    get_console,
    print_message,
)
from polaris.delivery.cli.visualization.theme import (
    ConsoleTheme,
    DiffTheme,
    MessageTheme,
    get_theme,
    list_themes,
)

__all__ = [
    # 消息组
    "CollapsibleMessageGroup",
    # 主题
    "ConsoleTheme",
    "DebugMessage",
    "DiffFile",
    "DiffHunk",
    "DiffLine",
    "DiffResult",
    "DiffStats",
    "DiffTheme",
    # Diff (基于 difflib 标准库)
    "DiffView",
    # 键盘快捷键
    "FoldShortcut",
    "KeyboardShortcutConfig",
    # 消息类型
    "MessageItem",
    "MessageTheme",
    "MessageType",
    # 渲染
    "RenderContext",
    # 契约
    "RenderMode",
    "RenderResult",
    "Renderable",
    "RichConsole",
    "StreamContext",
    # 控制台集成
    "VisualConsoleMixin",
    "VisualizationContext",
    "compute_diff",
    "create_debug_handler",
    "get_console",
    "get_theme",
    "list_themes",
    "print_message",
]

__version__ = "1.0.0"
