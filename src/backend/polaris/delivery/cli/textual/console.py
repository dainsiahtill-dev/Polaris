"""Polaris Textual TUI Console

基于 Textual 框架实现的可折叠 CLI 界面。

Usage:
    python -m polaris.delivery.cli chat --mode console --backend textual --debug
"""

from __future__ import annotations

import json
import sys
from typing import TYPE_CHECKING, Any, cast

try:
    from textual.app import App, ComposeResult
    from textual.containers import Container, ScrollableContainer
    from textual.message import Message
    from textual.reactive import reactive
    from textual.widgets import Input, Static
except ImportError:
    print("[ERROR] Textual is not installed. Install with: pip install textual")
    print("[ERROR] Or run with --backend plain for basic console.")
    sys.exit(1)

from polaris.delivery.cli.textual.bindings import GLOBAL_BINDINGS
from polaris.delivery.cli.textual.models import DebugItem
from polaris.delivery.cli.textual.styles import get_console_css

if TYPE_CHECKING:
    from textual.binding import Binding

# =============================================================================
# 自定义消息
# =============================================================================


class DebugToggle(Message):
    """DEBUG 切换消息"""

    def __init__(self, item_id: str) -> None:
        super().__init__()
        self.item_id = item_id


# =============================================================================
# 可折叠组件
# =============================================================================


class CollapsibleDebugItem(Static):
    """可折叠的 DEBUG 消息组件

    点击标题行可展开/折叠内容。
    """

    COMPONENT_CLASSES = {"header", "content", "hint"}

    def __init__(self, debug_item: DebugItem, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.debug_item = debug_item
        self._expanded = not debug_item.is_collapsed

    def compose(self) -> ComposeResult:
        """组合组件"""
        marker = self.debug_item.marker
        title = self.debug_item.title

        # 可点击的标题行
        yield Static(
            f"{marker} {title}",
            classes="collapsible-header",
            markup=True,
        )

        # 内容区域
        if self._expanded:
            content = self.debug_item.content
            yield Static(
                content,
                classes="collapsible-content",
                markup=False,
            )
        else:
            line_count = self.debug_item.line_count
            yield Static(
                f"[dim](+{line_count} lines)[/dim] [dim][click to expand][/dim]",
                classes="collapsible-hint",
                markup=True,
            )

    def on_click(self) -> None:
        """点击时切换折叠状态"""
        self.debug_item.toggle()
        self._expanded = not self.debug_item.is_collapsed

        # 更新显示
        self.remove_children()
        for widget in self.compose():
            self.mount(widget)


# =============================================================================
# 消息组件
# =============================================================================


class UserMessage(Static):
    """用户消息组件"""

    def __init__(self, content: str, **kwargs: Any) -> None:
        super().__init__(content, classes="message-user", **kwargs)


class AssistantMessage(Static):
    """助手消息组件"""

    def __init__(self, content: str, **kwargs: Any) -> None:
        super().__init__(content, classes="message-assistant", **kwargs)


class ToolCall(Static):
    """工具调用组件"""

    def __init__(self, tool_name: str, args: dict[str, Any] | None = None, **kwargs: Any) -> None:
        content = f"[TOOL] {tool_name}"
        if args:
            content += f"\n{json.dumps(args, ensure_ascii=False, indent=2)}"
        super().__init__(content, classes="tool-call", **kwargs)


class ToolResult(Static):
    """工具结果组件"""

    def __init__(self, tool_name: str, result: Any, success: bool = True, **kwargs: Any) -> None:
        content = json.dumps(result, ensure_ascii=False, indent=2) if isinstance(result, dict) else str(result)

        status = "✓" if success else "✗"
        prefix = f"[TOOL RESULT] {status} {tool_name}\n"
        super().__init__(prefix + content, classes="tool-result", **kwargs)


class ThinkingBlock(Static):
    """思考块组件"""

    def __init__(self, content: str, collapsed: bool = True, **kwargs: Any) -> None:
        if collapsed:
            lines = len(content.splitlines())
            display = f"[THINKING] (+{lines} lines)"
        else:
            display = f"[THINKING]\n{content}"
        super().__init__(display, classes="message-thinking", **kwargs)


class ErrorMessage(Static):
    """错误消息组件"""

    def __init__(self, content: str, **kwargs: Any) -> None:
        super().__init__(content, classes="error-message", **kwargs)


# =============================================================================
# 主应用
# =============================================================================


class PolarisTextualConsole(App):
    """Polaris CLI Textual TUI 应用

    Features:
        - 可折叠的 DEBUG 消息
        - 鼠标点击展开/折叠
        - Alt+D 快捷键切换所有
        - 实时消息流
    """

    # 使用 cast 解决 list invariant 类型问题
    BINDINGS = cast("list[Binding | tuple[str, str] | tuple[str, str, str]]", GLOBAL_BINDINGS)

    CSS = get_console_css()

    # 反应式状态
    debug_items: reactive[list[DebugItem]] = reactive(list)
    debug_collapsed: reactive[bool] = reactive(True)
    debug_count: reactive[int] = reactive(0)

    def __init__(
        self,
        workspace: str,
        role: str = "director",
        session_id: str | None = None,
        debug_enabled: bool = True,
    ) -> None:
        super().__init__()
        self.workspace = workspace
        self.role = role
        self.session_id = session_id
        self.debug_enabled = debug_enabled
        self._debug_counter = 0
        self._message_counter = 0
        self._input_history: list[str] = []
        self._input_history_index: int = -1

    def compose(self) -> ComposeResult:
        """组合主界面"""
        # 标题栏
        yield Static(
            f"Polaris CLI | role={self.role} | workspace={self.workspace}",
            id="header",
        )

        # 消息区域
        yield ScrollableContainer(id="messages")

        # 输入区域
        yield Container(
            Static(f"[{self.role}]> ", id="prompt"),
            Input(placeholder="Type a message...", id="input-field"),
            id="input-area",
        )

        # 状态栏
        yield Static(
            self._get_status_text(),
            id="status-bar",
        )

    def on_mount(self) -> None:
        """应用挂载时初始化"""
        messages = self.query_one("#messages", ScrollableContainer)
        messages.border_title = "Messages"

        # 设置焦点到输入框
        input_field = self.query_one("#input-field", Input)
        input_field.focus()

        # 添加欢迎消息
        self._add_welcome_message()

    def _add_welcome_message(self) -> None:
        """添加欢迎消息"""
        messages = self.query_one("#messages", ScrollableContainer)
        welcome = Static(
            f"[dim]Welcome to Polaris CLI (Textual Mode)[/dim]\n"
            f"[dim]Role: {self.role} | Workspace: {self.workspace}[/dim]\n"
            f"[dim]Press Alt+D to toggle DEBUG | Click [▶] to expand[/dim]",
            markup=True,
        )
        messages.mount(welcome)

    def _get_status_text(self) -> str:
        """获取状态栏文本"""
        debug_state = "collapsed" if self.debug_collapsed else "expanded"
        return f"DEBUG: {debug_state} ({self.debug_count} items) | Alt+D: toggle | Click: expand | /help for commands"

    def _update_status(self) -> None:
        """更新状态栏"""
        status_bar = self.query_one("#status-bar", Static)
        status_bar.update(self._get_status_text())

    def on_input_submitted(self, event: Input.Submitted) -> None:
        """处理输入提交"""
        message = event.value.strip()
        if not message:
            return

        # 添加到历史
        self._input_history.append(message)
        self._input_history_index = len(self._input_history)

        # 处理命令
        if message.startswith("/"):
            self._handle_command(message)
        else:
            # 添加用户消息
            self._add_user_message(message)

        # 清空输入
        input_field = self.query_one("#input-field", Input)
        input_field.value = ""

    def _handle_command(self, command: str) -> None:
        """处理命令"""
        cmd = command.lower()

        if cmd in ("/help", "/?"):
            self._show_help()
        elif cmd in {"/quit", "/exit"}:
            self.exit(0)
        elif cmd == "/debug":
            self._toggle_all_debug()
        elif cmd == "/debug expand":
            self._expand_all_debug()
        elif cmd == "/debug collapse":
            self._collapse_all_debug()
        elif cmd == "/debug clear":
            self._clear_debug()
        elif cmd.startswith("/debug "):
            count = cmd.split()[1] if len(cmd.split()) > 1 else ""
            if count.isdigit():
                self._show_recent_debug(int(count))
        else:
            self._add_error_message(f"Unknown command: {command}")

    def _show_help(self) -> None:
        """显示帮助"""
        messages = self.query_one("#messages", ScrollableContainer)
        help_text = Static(
            "[bold]Commands:[/bold]\n"
            "/help, /?     - Show this help\n"
            "/debug        - Toggle all DEBUG\n"
            "/debug expand - Expand all DEBUG\n"
            "/debug collapse - Collapse all DEBUG\n"
            "/debug clear - Clear DEBUG messages\n"
            "/debug <n>   - Show last N DEBUG messages\n"
            "/exit, /quit - Exit console\n\n"
            "[bold]Shortcuts:[/bold]\n"
            "Alt+D          - Toggle all DEBUG\n"
            "Alt+Shift+D    - Collapse all DEBUG\n"
            "Ctrl+D         - Expand all DEBUG\n"
            "Ctrl+C         - Quit",
            markup=True,
        )
        messages.mount(help_text)
        messages.scroll_end()

    def _add_user_message(self, content: str) -> None:
        """添加用户消息"""
        messages = self.query_one("#messages", ScrollableContainer)
        widget = UserMessage(content)
        messages.mount(widget)
        messages.scroll_end()

        # 模拟 LLM 响应
        self._simulate_response()

    def _simulate_response(self) -> None:
        """模拟 LLM 响应（用于测试）"""
        import random

        messages = self.query_one("#messages", ScrollableContainer)

        # 添加思考块
        if self.debug_enabled:
            self.add_debug(
                category="llm",
                label="thinking",
                source="openai",
                tags={"model": "gpt-4"},
                payload={"thinking": "Let me analyze this request..."},
            )

        # 添加助手消息
        responses = [
            "I understand. Let me help you with that.",
            "Based on my analysis, here are my findings.",
            "I've processed your request. What else can I help with?",
        ]
        assistant = AssistantMessage(random.choice(responses))
        messages.mount(assistant)
        messages.scroll_end()

        # 随机添加一些 DEBUG
        if self.debug_enabled and random.random() > 0.5:
            self.add_debug(
                category="fs",
                label="read",
                source="kernelone",
                tags={"file": "config.json"},
                payload={"path": "/etc/config.json", "size": 1024},
            )

    def add_debug(
        self,
        category: str,
        label: str,
        source: str = "",
        tags: dict[str, Any] | None = None,
        payload: Any = None,
    ) -> str:
        """添加 DEBUG 消息

        Returns:
            DEBUG 消息 ID
        """
        self._debug_counter += 1
        debug_id = f"debug-{self._debug_counter:04d}"

        item = DebugItem.from_payload(
            id=debug_id,
            category=category,
            label=label,
            source=source,
            tags=tags or {},
            payload=payload,
        )

        # 根据全局折叠状态设置
        item.is_collapsed = self.debug_collapsed

        self.debug_items.append(item)
        self.debug_count = len(self.debug_items)

        # 添加到显示
        self._add_debug_widget(item)
        self._update_status()

        return debug_id

    def _add_debug_widget(self, item: DebugItem) -> None:
        """添加 DEBUG 组件到消息区域"""
        messages = self.query_one("#messages", ScrollableContainer)
        widget = CollapsibleDebugItem(
            item, classes="collapsible-debug collapsed" if item.is_collapsed else "collapsible-debug expanded"
        )
        messages.mount(widget)
        messages.scroll_end()

    def add_message(self, content: str, msg_type: str = "assistant") -> None:
        """添加普通消息"""
        messages = self.query_one("#messages", ScrollableContainer)

        # 使用 Static 类型避免类型不兼容问题
        widget: Static
        if msg_type == "user":
            widget = UserMessage(content)
        elif msg_type == "error":
            widget = ErrorMessage(content)
        else:
            widget = AssistantMessage(content)

        messages.mount(widget)
        messages.scroll_end()

    def add_tool_call(self, tool_name: str, args: dict[str, Any] | None = None) -> None:
        """添加工具调用"""
        messages = self.query_one("#messages", ScrollableContainer)
        widget = ToolCall(tool_name, args)
        messages.mount(widget)
        messages.scroll_end()

    def add_tool_result(self, tool_name: str, result: Any, success: bool = True) -> None:
        """添加工具结果"""
        messages = self.query_one("#messages", ScrollableContainer)
        widget = ToolResult(tool_name, result, success)
        messages.mount(widget)
        messages.scroll_end()

    def add_error(self, message: str) -> None:
        """添加错误消息"""
        self._add_error_message(message)

    def _add_error_message(self, content: str) -> None:
        """添加错误消息"""
        messages = self.query_one("#messages", ScrollableContainer)
        widget = ErrorMessage(content)
        messages.mount(widget)
        messages.scroll_end()

    # Actions

    def action_toggle_all_debug(self) -> None:
        """Alt+D: 切换所有 DEBUG"""
        self._toggle_all_debug()
        self.notify(
            f"DEBUG: {'collapsed' if self.debug_collapsed else 'expanded'}",
            severity="information",
        )

    def action_expand_all_debug(self) -> None:
        """Ctrl+D: 展开所有 DEBUG"""
        self._expand_all_debug()
        self.notify("DEBUG: expanded", severity="information")

    def action_collapse_all_debug(self) -> None:
        """Alt+Shift+D: 折叠所有 DEBUG"""
        self._collapse_all_debug()
        self.notify("DEBUG: collapsed", severity="information")

    async def action_quit(self) -> None:
        """退出"""
        self.exit(0)

    def _toggle_all_debug(self) -> None:
        """切换所有 DEBUG 状态"""
        self.debug_collapsed = not self.debug_collapsed

        for item in self.debug_items:
            if self.debug_collapsed:
                item.collapse()
            else:
                item.expand()

        self._refresh_all_debug_widgets()
        self._update_status()

    def _expand_all_debug(self) -> None:
        """展开所有 DEBUG"""
        self.debug_collapsed = False

        for item in self.debug_items:
            item.expand()

        self._refresh_all_debug_widgets()
        self._update_status()

    def _collapse_all_debug(self) -> None:
        """折叠所有 DEBUG"""
        self.debug_collapsed = True

        for item in self.debug_items:
            item.collapse()

        self._refresh_all_debug_widgets()
        self._update_status()

    def _clear_debug(self) -> None:
        """清空所有 DEBUG"""
        self.debug_items.clear()
        self.debug_count = 0

        # 移除所有 DEBUG 组件
        self.query_one("#messages", ScrollableContainer)
        for widget in self.query(".collapsible-debug"):
            widget.remove()

        self._update_status()
        self.notify("DEBUG messages cleared", severity="information")

    def _show_recent_debug(self, count: int) -> None:
        """显示最近的 N 条 DEBUG"""
        messages = self.query_one("#messages", ScrollableContainer)
        recent = self.debug_items[-count:] if count > 0 else self.debug_items

        info = Static(
            f"[dim]Last {len(recent)} DEBUG messages:[/dim]",
            markup=True,
        )
        messages.mount(info)

        for item in recent:
            # 临时展开以显示
            was_collapsed = item.is_collapsed
            item.expand()
            widget = CollapsibleDebugItem(item)
            messages.mount(widget)
            # 恢复状态
            if was_collapsed:
                item.collapse()

        messages.scroll_end()

    def _refresh_all_debug_widgets(self) -> None:
        """刷新所有 DEBUG 组件"""
        self.query_one("#messages", ScrollableContainer)

        # 移除旧的 DEBUG 组件
        for widget in list(self.query(".collapsible-debug")):
            widget.remove()

        # 重新添加所有 DEBUG
        for item in self.debug_items:
            self._add_debug_widget(item)

    def action_scroll_up(self) -> None:
        """向上滚动"""
        messages = self.query_one("#messages", ScrollableContainer)
        messages.scroll_up()

    def action_scroll_down(self) -> None:
        """向下滚动"""
        messages = self.query_one("#messages", ScrollableContainer)
        messages.scroll_down()

    def action_page_up(self) -> None:
        """向上翻页"""
        messages = self.query_one("#messages", ScrollableContainer)
        messages.scroll_page_up()

    def action_page_down(self) -> None:
        """向下翻页"""
        messages = self.query_one("#messages", ScrollableContainer)
        messages.scroll_page_down()

    def action_scroll_home(self) -> None:
        """滚动到顶部"""
        messages = self.query_one("#messages", ScrollableContainer)
        messages.scroll_home(animate=True)

    def action_scroll_end(self) -> None:
        """滚动到底部"""
        messages = self.query_one("#messages", ScrollableContainer)
        messages.scroll_end(animate=True)

    def action_history_up(self) -> None:
        """历史记录向上"""
        if not self._input_history:
            return

        input_field = self.query_one("#input-field", Input)

        if self._input_history_index > 0:
            self._input_history_index -= 1
            input_field.value = self._input_history[self._input_history_index]

    def action_history_down(self) -> None:
        """历史记录向下"""
        if not self._input_history:
            return

        input_field = self.query_one("#input-field", Input)

        if self._input_history_index < len(self._input_history) - 1:
            self._input_history_index += 1
            input_field.value = self._input_history[self._input_history_index]
        else:
            self._input_history_index = len(self._input_history)
            input_field.value = ""


# =============================================================================
# Runner
# =============================================================================


def run_textual_console(
    workspace: str,
    role: str = "director",
    session_id: str | None = None,
    debug: bool = True,
) -> int:
    """运行 Textual TUI 控制台

    Args:
        workspace: 工作目录
        role: 角色
        session_id: 会话 ID
        debug: 是否启用 DEBUG

    Returns:
        退出码
    """
    app = PolarisTextualConsole(
        workspace=workspace,
        role=role,
        session_id=session_id,
        debug_enabled=debug,
    )

    # app.run() 返回 Any | None，转换为 int
    result = app.run()
    return 0 if result is None else int(result)


# =============================================================================
# Demo
# =============================================================================


if __name__ == "__main__":
    run_textual_console(
        workspace="/tmp/demo",
        role="director",
        debug=True,
    )
