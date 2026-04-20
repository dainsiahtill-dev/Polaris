"""Textual TUI 快捷键绑定

定义全局键盘快捷键。
"""

from __future__ import annotations

from textual.binding import Binding

# 全局绑定
GLOBAL_BINDINGS = [
    Binding("alt+d", "toggle_all_debug", "Toggle DEBUG", show=True, priority=True),
    Binding("alt+shift+d", "collapse_all_debug", "Collapse DEBUG", show=True, priority=True),
    Binding("ctrl+d", "expand_all_debug", "Expand DEBUG", show=True, priority=True),
    Binding("ctrl+c", "quit", "Quit", show=True, priority=True),
    Binding("ctrl+t", "toggle_theme", "Theme", show=True, priority=True),
    Binding("ctrl+k", "show_command_palette", "Commands", show=True, priority=True),
    Binding("ctrl+f", "toggle_search", "Search", show=True, priority=True),
    Binding("f1", "show_help", "Help", show=True, priority=True),
    Binding("f3", "show_logs", "Logs", show=True, priority=True),
]

# 消息区域绑定
MESSAGE_BINDINGS = [
    Binding("up", "scroll_up", "Scroll up", show=False),
    Binding("down", "scroll_down", "Scroll down", show=False),
    Binding("pageup", "page_up", "Page up", show=False),
    Binding("pagedown", "page_down", "Page down", show=False),
    Binding("home", "scroll_home", "Top", show=False),
    Binding("end", "scroll_end", "Bottom", show=False),
    Binding("/", "focus_search", "Search", show=False),
]

# 输入区域绑定
INPUT_BINDINGS = [
    Binding("up", "history_up", "History up", show=False),
    Binding("down", "history_down", "History down", show=False),
    Binding("ctrl+a", "select_all", "Select all", show=False),
    Binding("ctrl+e", "end_of_line", "End of line", show=False),
    Binding("ctrl+u", "clear_line", "Clear line", show=False),
]


def get_bindings_for_mode(mode: str) -> list[Binding]:
    """获取指定模式的绑定"""
    if mode == "global":
        return GLOBAL_BINDINGS
    elif mode == "messages":
        return MESSAGE_BINDINGS
    elif mode == "input":
        return INPUT_BINDINGS
    else:
        return GLOBAL_BINDINGS
