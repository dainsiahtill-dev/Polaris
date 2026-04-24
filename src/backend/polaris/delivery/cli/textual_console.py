"""Textual TUI Console for Polaris CLI - Claude 风格 Agent TUI

基于 Textual 框架实现的现代化、专业级 Agent TUI 界面。

Usage:
    python -m polaris.delivery.cli chat --mode console --backend textual --debug

Features:
    - Claude 风格的现代深色主题 (Catppuccin Mocha 配色)
    - 可折叠的消息面板 (用户、Agent、工具调用)
    - 动态可调高的底部输入区 (拖拽手柄)
    - Markdown 渲染与代码块语法高亮
    - 可选的扩展上下文侧边栏
    - 实时 Token 统计和状态指示器
    - 完整的键盘快捷键支持
"""

from __future__ import annotations

import contextlib
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

# Textual 导入
try:
    from textual.app import App, ComposeResult
    from textual.binding import Binding
    from textual.containers import Container, Horizontal, ScrollableContainer, Vertical
    from textual.reactive import reactive
    from textual.widgets import Button, Rule, Static, TextArea
except ImportError as e:
    print(f"[ERROR] Textual/Rich is not installed: {e}")
    print("[ERROR] Install with: pip install textual rich")
    sys.exit(1)

# 本地导入
from polaris.delivery.cli.textual.models import (
    AppState,
    ConversationContext,
    DebugItem,
    MessageContent,
    MessageItem,
    MessageType,
    ToolCallInfo,
    ToolStatus,
)
from polaris.delivery.cli.textual.styles import (
    CatppuccinLatte,
    CatppuccinMocha,
    ThemeColors,
    ThemeManager,
    ThemeMode,
    get_console_css,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from textual.events import MouseDown, MouseMove, MouseUp
    from textual.widget import Widget

import logging

logger = logging.getLogger(__name__)

# =============================================================================
# 常量定义
# =============================================================================

APP_TITLE = "AGENT_OS.v1"
INPUT_MIN_HEIGHT = 3
INPUT_MAX_HEIGHT = 20
INPUT_DEFAULT_HEIGHT = 4


# =============================================================================
# 自定义组件
# =============================================================================


class Header(Static):
    """静态头部组件 - 显示应用名称、状态、Token 统计"""

    def __init__(self, app_state: AppState, **kwargs) -> None:
        super().__init__(**kwargs)
        self.app_state = app_state
        self._time_format = "%H:%M:%S"

    def compose(self) -> ComposeResult:
        with Horizontal(id="header"):
            yield Static(APP_TITLE, id="header-title")
            yield Static(self._get_status_indicator(), id="header-status")
            yield Static(self._get_time_display(), id="header-time")
            yield Static(self._get_stats_text(), id="header-stats")
            yield Static(self._get_theme_indicator(), id="header-theme")

    def _get_status_indicator(self) -> str:
        """获取状态指示器（带颜色）"""
        status = self.app_state.status_text
        tool = self.app_state.context.current_tool

        # 状态图标
        status_icons = {
            "CONNECTED": "●",  # 绿色
            "STREAMING": "◐",  # 黄色
            "PROCESSING": "◓",  # 黄色
            "ERROR": "✗",  # 红色
            "IDLE": "○",  # 灰色
        }

        icon = status_icons.get(status, "○")
        if tool:
            return f"{icon} [{status}] {tool.upper()}"
        return f"{icon} [{status}]"

    def _get_time_display(self) -> str:
        """获取当前时间显示"""
        from datetime import datetime

        return datetime.now().strftime(self._time_format)

    def _get_stats_text(self) -> str:
        """获取统计文本"""
        tokens = self.app_state.context.tokens_display
        return f"Tokens: {tokens}"

    def _get_theme_indicator(self) -> str:
        """获取主题指示器"""
        theme_manager = ThemeManager.get_instance()
        theme_icon = "☀" if theme_manager.is_light else "☾"
        return theme_icon

    def update_status(self) -> None:
        """更新状态显示"""
        status_widget = self.query_one("#header-status", Static)
        stats_widget = self.query_one("#header-stats", Static)
        time_widget = self.query_one("#header-time", Static)
        theme_widget = self.query_one("#header-theme", Static)

        if status_widget:
            status_widget.update(self._get_status_indicator())
        if stats_widget:
            stats_widget.update(self._get_stats_text())
        if time_widget:
            time_widget.update(self._get_time_display())
        if theme_widget:
            theme_widget.update(self._get_theme_indicator())

    def start_time_update(self) -> None:
        """启动时间更新定时器"""
        self.set_interval(1.0, self._update_time)

    def _update_time(self) -> None:
        """更新时间显示"""
        if self.is_attached and hasattr(self, "query_one"):
            try:
                time_widget = self.query_one("#header-time", Static)
                if time_widget:
                    time_widget.update(self._get_time_display())
            except (RuntimeError, ValueError) as e:
                logger.debug("Failed to update time display: %s", e)


class ResizableInput(Vertical):
    """可调整高度的输入区域组件"""

    def __init__(self, on_submit: Callable[[str], None], **kwargs) -> None:
        super().__init__(**kwargs)
        self.on_submit = on_submit
        self._is_dragging = False
        self._drag_start_y = 0
        self._start_height = INPUT_DEFAULT_HEIGHT

    def compose(self) -> ComposeResult:
        # 拖拽手柄
        yield Static("━" * 50, id="resize-handle")

        # 输入区域
        with Horizontal(id="input-area"):
            yield TextArea(
                text="",
                id="input-textarea",
                language=None,
                show_line_numbers=False,
                soft_wrap=True,
            )
            yield Button("Send", id="send-button")

        # 快捷键提示
        yield Static("Ctrl+Enter: Send | Esc: Clear | Ctrl+K: Commands", id="input-hints")

    def on_mount(self) -> None:
        """组件挂载时初始化"""
        self._update_height(INPUT_DEFAULT_HEIGHT)

    def _update_height(self, height: int) -> None:
        """更新输入区域高度"""
        height = max(INPUT_MIN_HEIGHT, min(INPUT_MAX_HEIGHT, height))
        textarea = self.query_one("#input-textarea", TextArea)
        if textarea:
            textarea.styles.height = height

    def get_input_text(self) -> str:
        """获取输入文本"""
        textarea = self.query_one("#input-textarea", TextArea)
        return textarea.text if textarea else ""

    def clear_input(self) -> None:
        """清空输入"""
        textarea = self.query_one("#input-textarea", TextArea)
        if textarea:
            textarea.text = ""

    def focus_input(self) -> None:
        """聚焦输入框"""
        textarea = self.query_one("#input-textarea", TextArea)
        if textarea:
            textarea.focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """按钮点击事件"""
        if event.button.id == "send-button":
            self._do_submit()

    def _do_submit(self) -> None:
        """执行提交"""
        text = self.get_input_text().strip()
        if text and callable(self.on_submit):
            self.on_submit(text)
            self.clear_input()

    # 鼠标拖拽支持 - 修复坐标比较问题
    async def _on_mouse_down(self, event: MouseDown) -> None:
        """鼠标按下 - 使用更可靠的检测方式"""
        # 获取拖拽手柄的区域
        handle = self.query_one("#resize-handle", Static)
        if not handle:
            return

        # 检查点击是否在手柄区域（考虑手柄的高度）
        handle_region = handle.region
        if (
            handle_region
            and event.screen_x is not None
            and event.screen_y is not None
            and handle_region.x <= event.screen_x < handle_region.x + handle_region.width
            and handle_region.y <= event.screen_y < handle_region.y + handle_region.height
        ):
            self._is_dragging = True
            self._drag_start_y = event.screen_y
            textarea = self.query_one("#input-textarea", TextArea)
            self._start_height = textarea.size.height if textarea else INPUT_DEFAULT_HEIGHT
            handle.add_class("dragging")
            self.capture_mouse()
            event.stop()

    async def _on_mouse_move(self, event: MouseMove) -> None:
        """鼠标移动 - 添加边界检查"""
        if self._is_dragging:
            # 计算高度变化
            delta = self._drag_start_y - event.screen_y
            new_height = self._start_height + delta
            self._update_height(new_height)
            event.stop()

    async def _on_mouse_up(self, event: MouseUp) -> None:
        """鼠标释放 - 确保状态清理"""
        if self._is_dragging:
            self._is_dragging = False
            handle = self.query_one("#resize-handle", Static)
            if handle:
                handle.remove_class("dragging")
            with contextlib.suppress(RuntimeError, ValueError):
                self.release_mouse()
            event.stop()


class MessageWidget(Container):
    """可折叠的消息组件 - 每条消息独立 Panel 包裹

    使用自定义折叠实现，不继承 Collapsible 以避免行为冲突。
    """

    def __init__(self, message: MessageItem, **kwargs) -> None:
        super().__init__(**kwargs)
        self.message = message

    def compose(self) -> ComposeResult:
        """组合消息部件"""
        # 设置 CSS 类
        self._set_message_style()

        # 标题栏（可点击）- 不使用 ID，避免折叠时 ID 冲突
        title_text = self._build_title()
        yield Static(title_text, classes="message-header clickable")

        # 内容区域（根据折叠状态显示/隐藏）
        if not self.message.is_collapsed:
            yield self._build_message_content()

    def _set_message_style(self) -> None:
        """设置消息样式"""
        style_classes = {
            MessageType.USER: "message-panel-user",
            MessageType.ASSISTANT: "message-panel-agent",
            MessageType.STREAM: "message-panel-agent",
            MessageType.SYSTEM: "message-panel-system",
            MessageType.TOOL_CALL: "message-panel-tool",
            MessageType.TOOL_RESULT: "message-panel-tool",
            MessageType.ERROR: "message-panel-system",
            MessageType.DEBUG: "debug-panel",
        }
        css_class = style_classes.get(self.message.type, "message-panel-agent")
        self.add_class(css_class)
        self.add_class("message-panel")

    def _build_title(self) -> str:
        """构建标题"""
        marker = "▼" if not self.message.is_collapsed else "▶"
        author = self.message.author_label

        if self.message.is_collapsed:
            return f"{marker} {self.message.summary}"
        return f"{marker} {author}"

    def _build_message_content(self) -> Container:
        """Build message content widget"""
        content = self.message.content

        if isinstance(content, MessageContent):
            text = content.text
            code_blocks = content.code_blocks
            thinking = content.thinking
        else:
            text = str(content)
            code_blocks = []
            thinking = None

        # 处理 Markdown 和代码块
        widgets: list[Widget] = []

        # 思考过程（如果有）
        if thinking:
            widgets.append(Static(f"<thinking>\n{thinking}\n</thinking>", classes="message-thinking", markup=False))

        # 文本内容（禁用 markup 避免解析错误）
        if text.strip():
            widgets.append(Static(text, classes="message-content", markup=False))

        # 代码块
        for block in code_blocks:
            widgets.append(self._render_code_block(block))

        return Container(*widgets, classes="message-content-wrapper")

    def _render_code_block(self, block) -> Container:
        """渲染代码块 - 修复 Rich Syntax 对象传递"""
        from io import StringIO

        from rich.console import Console
        from rich.syntax import Syntax

        # 创建语法高亮
        syntax = Syntax(
            block.code,
            block.language or "text",
            theme="monokai",
            line_numbers=True,
            word_wrap=True,
        )

        # 将 Rich Syntax 渲染为字符串
        string_buffer = StringIO()
        console = Console(file=string_buffer, force_terminal=True, color_system="truecolor")
        console.print(syntax)
        rendered_code = string_buffer.getvalue()

        return Container(
            Static(f"```{block.language or 'text'}", classes="code-block-header"),
            Static(rendered_code, classes="code-block-content"),
            Static("```", classes="code-block-header"),
            classes="code-block",
        )

    def on_click(self, event) -> None:
        """点击标题栏时切换折叠状态"""
        # 检查点击的是否是标题栏
        if hasattr(event, "control") and event.control:
            classes = str(event.control.classes) if event.control.classes else ""
            if "message-header" in classes:
                self._toggle()

    def _toggle(self) -> None:
        """切换折叠状态"""
        self.message.toggle()

        # 重新组合内容
        self.remove_children()
        for widget in self.compose():
            self.mount(widget)


class ConversationArea(ScrollableContainer):
    """对话历史区域 - 带可折叠消息"""

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.messages: list[MessageWidget] = []

    def add_message(self, message: MessageItem) -> None:
        """添加消息"""
        widget = MessageWidget(message)
        self.messages.append(widget)
        self.mount(widget)
        self.scroll_end()

    def add_stream_chunk(self, chunk: str, counter_ref: list | None = None) -> None:
        """添加流式输出块 - 修复计数器问题

        Args:
            chunk: 流式文本块
            counter_ref: 外部计数器引用列表，用于递增 ID
        """
        # 找到最后一条 Assistant 消息或创建新消息
        if self.messages and self.messages[-1].message.type == MessageType.STREAM:
            # 追加到现有消息
            msg = self.messages[-1].message
            if isinstance(msg.content, MessageContent):
                msg.content.text += chunk
            else:
                msg.content = MessageContent(text=str(msg.content) + chunk)
            self.messages[-1]._toggle()
            self.messages[-1]._toggle()
        else:
            # 创建新的流式消息 - 使用计数器确保 ID 唯一
            msg_id = f"stream-{datetime.now().timestamp()}"
            if counter_ref is not None:
                counter_ref[0] += 1
                msg_id = f"stream-{counter_ref[0]}"

            msg = MessageItem(
                id=msg_id,
                type=MessageType.STREAM,
                title="Streaming...",
                content=MessageContent(text=chunk),
                is_collapsed=False,
            )
            self.add_message(msg)

    def finalize_stream(self) -> None:
        """结束流式输出，将 STREAM 类型转为 ASSISTANT"""
        if self.messages and self.messages[-1].message.type == MessageType.STREAM:
            self.messages[-1].message.type = MessageType.ASSISTANT
            self.messages[-1].refresh()


class Sidebar(ScrollableContainer):
    """扩展上下文侧边栏"""

    def __init__(self, context: ConversationContext, **kwargs) -> None:
        super().__init__(**kwargs)
        self.context = context

    def compose(self) -> ComposeResult:
        yield Static("Context & Tools", id="sidebar-title")

        # Token 使用情况
        yield Static("Token Usage", classes="sidebar-section-title")
        yield Static(f"Used: {self.context.token_usage.get('used', 0)}", classes="sidebar-item")
        yield Static(f"Total: {self.context.token_usage.get('total', 4096)}", classes="sidebar-item")
        yield Rule()

        # 当前工具
        if self.context.current_tool:
            yield Static("Current Tool", classes="sidebar-section-title")
            yield Static(self.context.current_tool, classes="sidebar-item tool-name")
            yield Rule()

        # 最近工具调用
        if self.context.tool_calls_history:
            yield Static("Recent Tool Calls", classes="sidebar-section-title")
            for tool in self.context.tool_calls_history[-5:]:  # 只显示最近5个
                status_icon = {
                    ToolStatus.SUCCESS: "✓",
                    ToolStatus.ERROR: "✗",
                    ToolStatus.PENDING: "○",
                    ToolStatus.RUNNING: "◐",
                }.get(tool.status, "○")
                yield Static(f"{status_icon} {tool.name}", classes="sidebar-item")
            yield Rule()

        # RAG 文档
        if self.context.rag_documents:
            yield Static("RAG Documents", classes="sidebar-section-title")
            yield Static(f"{len(self.context.rag_documents)} documents", classes="sidebar-item")

    def refresh_context(self) -> None:
        """刷新上下文显示"""
        self.remove_children()
        for widget in self.compose():
            self.mount(widget)


class ToolCallWidget(Container):
    """工具调用显示组件"""

    def __init__(self, tool_info: ToolCallInfo, **kwargs) -> None:
        super().__init__(**kwargs)
        self.tool_info = tool_info

    def compose(self) -> ComposeResult:
        status_colors = {
            ToolStatus.PENDING: "#9399b2",
            ToolStatus.RUNNING: "#f9e2af",
            ToolStatus.SUCCESS: "#a6e3a1",
            ToolStatus.ERROR: "#f38ba8",
            ToolStatus.CANCELLED: "#6c7086",
        }

        status_color = status_colors.get(self.tool_info.status, "#9399b2")
        status_text = f"[{self.tool_info.status.value.upper()}]"

        with Container(classes="tool-call-panel"):
            header = Static(f"🔧 {self.tool_info.name} {status_text}", classes="tool-call-header")
            header.styles.color = status_color
            yield header

            # 参数
            if self.tool_info.arguments:
                args_text = json.dumps(self.tool_info.arguments, indent=2, ensure_ascii=False)
                yield Static(f"Arguments:\n{args_text}", classes="tool-call-content")

            # 结果
            if self.tool_info.result is not None:
                result_text = str(self.tool_info.result)[:500]  # 截断显示
                if len(str(self.tool_info.result)) > 500:
                    result_text += "..."
                yield Static(f"Result:\n{result_text}", classes="tool-call-content")

            # 错误
            if self.tool_info.error:
                yield Static(f"Error: {self.tool_info.error}", classes="tool-call-content error")


# =============================================================================
# 主应用
# =============================================================================


class ClaudeAgentTUI(App):
    """Claude 风格 Agent TUI 应用

    现代、专业、极简的 Agent 终端界面。
    """

    CSS = get_console_css()

    BINDINGS = [
        Binding("ctrl+q", "quit", "Quit", show=True),
        Binding("ctrl+d", "toggle_debug", "Toggle Debug", show=True),
        Binding("ctrl+s", "toggle_sidebar", "Toggle Sidebar", show=True),
        Binding("ctrl+l", "clear_history", "Clear", show=True),
        Binding("ctrl+t", "toggle_theme", "Theme", show=True),
        Binding("ctrl+f", "toggle_search", "Search", show=True),
        Binding("ctrl+k", "show_command_palette", "Commands", show=True),
        Binding("ctrl+enter", "submit", "Send", show=False),
        Binding("escape", "clear_input", "Clear Input", show=False),
        Binding("tab", "focus_next", "Next Focus", show=False),
        Binding("shift+tab", "focus_previous", "Prev Focus", show=False),
        Binding("f1", "show_help", "Help", show=True),
        Binding("f3", "show_logs", "Logs", show=True),
    ]

    # 响应式状态
    show_sidebar: reactive[bool] = reactive(False)
    is_processing: reactive[bool] = reactive(False)

    def __init__(
        self,
        workspace: str = ".",
        role: str = "director",
        session_id: str | None = None,
        debug_enabled: bool = True,
    ) -> None:
        super().__init__()
        self.workspace = str(Path(workspace).resolve())
        self.role = role
        self.debug_enabled = debug_enabled

        # 应用状态 (先创建，后面会被 host 初始化覆盖)
        self.app_state = AppState(
            workspace=self.workspace,
            role=self.role,
            session_id="initializing",
            is_connected=True,
        )

        # 初始化 RoleConsoleHost
        self._init_host()

        # 计数器
        self._message_counter = 0
        self._debug_counter = 0
        self._tool_counter = 0

    def _init_host(self) -> None:
        """初始化 RoleConsoleHost"""
        from polaris.cells.roles.session.public.service import RoleHostKind
        from polaris.delivery.cli.director.console_host import RoleConsoleHost

        self._host = RoleConsoleHost(self.workspace, role=self.role)

        # 创建 session
        session_payload = self._host.create_session(
            context_config={
                "role": self.role,
                "host_kind": RoleHostKind.TUI.value,
            },
            capability_profile={
                "role": self.role,
                "debug": self.debug_enabled,
            },
        )
        self._session_id = str(session_payload.get("id") or "")
        # 更新 AppState 中的 session_id
        self.app_state.session_id = self._session_id

    def compose(self) -> ComposeResult:
        """组合主界面"""
        # 头部
        yield Header(self.app_state)

        # 主内容区
        with Horizontal(id="main-content"):
            # 对话区域
            yield ConversationArea(id="conversation-area")

            # 侧边栏 (默认隐藏)
            if self.show_sidebar:
                yield Sidebar(self.app_state.context, id="sidebar")

        # 输入区域
        yield ResizableInput(on_submit=self._handle_user_input, id="input-section")

        # 状态栏
        yield from self._compose_status_bar()

    def _compose_status_bar(self) -> ComposeResult:
        """组合状态栏"""
        with Horizontal(id="status-bar"):
            yield Static(f"WS: {self.workspace} | Role: {self.role}", id="status-bar-left")
            yield Static("Ctrl+Q:Quit | Ctrl+T:Theme | Ctrl+K:Help", id="status-bar-right")

    def on_mount(self) -> None:
        """应用挂载时初始化"""
        self._add_welcome_message()
        # 延迟聚焦，确保欢迎消息添加完成
        self.set_timer(0.1, self._do_focus)
        # 启动时间更新
        self.set_timer(1.0, self._start_time_updates)

    def _start_time_updates(self) -> None:
        """启动时间更新"""
        try:
            header = self.query_one(Header)
            header.start_time_update()
        except (RuntimeError, ValueError) as e:
            logger.debug("Failed to start time updates: %s", e)

    def _add_welcome_message(self) -> None:
        """添加欢迎消息"""
        welcome_msg = MessageItem(
            id="welcome",
            type=MessageType.ASSISTANT,
            title="Welcome",
            content=MessageContent(
                text=f"""Welcome to **{APP_TITLE}**!

I'm your AI assistant. How can I help you today?

**Quick Start:**
- Type your message and press `Ctrl+Enter` to send
- Drag the handle above the input area to resize
- Press `Ctrl+K` to see all commands

**Theme & Layout:**
- `Ctrl+T` Toggle light/dark theme
- `Ctrl+S` Toggle context sidebar
- `Ctrl+D` Toggle debug messages

**Current Session:**
- Workspace: `{self.workspace}`
- Role: `{self.role}`
- Session: `{self._session_id or "initializing"}`

**Press F1 for detailed help.**
"""
            ),
            is_collapsed=False,
        )
        self._message_counter += 1
        self._add_message(welcome_msg, increment_counter=False)

    def _handle_user_input(self, text: str) -> None:
        """处理用户输入 - 对接真实业务"""
        # 添加用户消息
        self._message_counter += 1
        user_msg = MessageItem(
            id=f"user-{self._message_counter}",
            type=MessageType.USER,
            title="User",
            content=MessageContent(text=text),
            is_collapsed=False,
        )
        self._add_message(user_msg, increment_counter=False)

        # 调用真实的 RoleConsoleHost 处理
        self._stream_agent_response(text)

    def _stream_agent_response(self, user_text: str) -> None:
        """使用 RoleConsoleHost 流式获取 Agent 响应"""

        async def respond() -> None:
            try:
                # 创建流式消息占位
                self._message_counter += 1
                stream_msg_id = f"assistant-{self._message_counter}"
                stream_content = MessageContent(text="")
                stream_msg = MessageItem(
                    id=stream_msg_id,
                    type=MessageType.STREAM,
                    title="Assistant",
                    content=stream_content,
                    is_collapsed=False,
                )
                self._add_message(stream_msg, increment_counter=False)
                self.set_status("Thinking...")

                # 调用 RoleConsoleHost 流式接口
                content_parts: list[str] = []
                thinking_parts: list[str] = []
                current_tool_call: dict | None = None

                async for event in self._host.stream_turn(
                    session_id=self._session_id,
                    message=user_text,
                    context={
                        "role": self.role,
                        "host_kind": "textual",
                    },
                    role=self.role,
                    debug=self.debug_enabled,
                ):
                    # 检查应用是否仍在运行，如果已退出则停止处理
                    if not self.is_running:
                        break

                    event_type = str(event.get("type") or "")
                    payload = event.get("data", {})

                    if event_type == "content_chunk":
                        # 检查是否退出
                        if not self.is_running:
                            break
                        chunk = str(payload.get("content") or "")
                        if chunk:
                            content_parts.append(chunk)
                            stream_content.text += chunk
                            self._update_stream_message(stream_msg_id, stream_content)

                    elif event_type == "thinking_chunk":
                        chunk = str(payload.get("content") or "")
                        if chunk:
                            thinking_parts.append(chunk)

                    elif event_type == "tool_call":
                        # 检查是否退出
                        if not self.is_running:
                            break
                        # 添加工具调用消息
                        self._message_counter += 1
                        tool_name = str(payload.get("tool") or "")
                        tool_args = payload.get("args", {})
                        # 格式化参数为可读 JSON
                        args_text = self._format_debug_content(tool_args)
                        tool_msg = MessageItem(
                            id=f"tool-call-{self._message_counter}",
                            type=MessageType.TOOL_CALL,
                            title=f"Tool: {tool_name}",
                            content=MessageContent(
                                text=f"Calling {tool_name}...\n\nArguments:\n{args_text}",
                            ),
                            metadata={
                                "tool_name": tool_name,
                                "args": tool_args,
                            },
                            is_collapsed=False,
                        )
                        self._add_message(tool_msg, increment_counter=False)
                        current_tool_call = {
                            "id": f"tool-call-{self._message_counter}",
                            "name": tool_name,
                        }
                        self.set_current_tool(tool_name)

                    elif event_type == "tool_result":
                        # 检查是否退出
                        if not self.is_running:
                            break
                        # 添加工具结果消息
                        self._message_counter += 1
                        # 格式化结果为可读字符串
                        result_data = payload.get("result") or payload.get("content") or {}
                        result_text = self._format_debug_content(result_data)
                        success = payload.get("success", True)
                        error_text = str(payload.get("error") or "")

                        tool_result_msg = MessageItem(
                            id=f"tool-result-{self._message_counter}",
                            type=MessageType.TOOL_RESULT if success else MessageType.ERROR,
                            title=f"Result: {current_tool_call['name'] if current_tool_call else 'tool'}",
                            content=MessageContent(
                                text=result_text if success else f"Error: {error_text}",
                            ),
                            is_collapsed=False,
                        )
                        self._add_message(tool_result_msg, increment_counter=False)
                        current_tool_call = None
                        self.set_current_tool(None)

                    elif event_type == "debug":
                        # 检查是否退出
                        if not self.is_running:
                            break
                        if self.debug_enabled:
                            category = str(payload.get("category") or "debug")
                            label = str(payload.get("label") or "event")
                            debug_content = payload.get("payload", {})

                            # 尝试格式化 JSON
                            formatted_content = self._format_debug_content(debug_content)

                            self._message_counter += 1
                            debug_msg = MessageItem(
                                id=f"debug-{self._message_counter:04d}",
                                type=MessageType.DEBUG,
                                title=f"[{category}][{label}]",
                                content=MessageContent(text=formatted_content),
                                is_collapsed=True,
                            )
                            self._add_message(debug_msg, increment_counter=False)

                    elif event_type == "error":
                        error_text = str(payload.get("error") or "Unknown error")
                        self._message_counter += 1
                        error_msg = MessageItem(
                            id=f"error-{self._message_counter}",
                            type=MessageType.ERROR,
                            title="Error",
                            content=MessageContent(text=error_text),
                            is_collapsed=False,
                        )
                        self._add_message(error_msg, increment_counter=False)

                    elif event_type == "complete":
                        # 完成，转换流式消息为助手消息
                        final_content = "".join(content_parts)
                        final_thinking = "".join(thinking_parts) if thinking_parts else None

                        # 更新或替换流式消息
                        stream_msg.type = MessageType.ASSISTANT
                        stream_msg.content = MessageContent(
                            text=final_content,
                            thinking=final_thinking,
                        )
                        self._update_message(stream_msg_id, stream_msg)
                        self.set_status("Ready")
                        self.set_current_tool(None)

            except (RuntimeError, ValueError) as e:
                # 处理异常
                self._message_counter += 1
                error_msg = MessageItem(
                    id=f"error-{self._message_counter}",
                    type=MessageType.ERROR,
                    title="Error",
                    content=MessageContent(text=f"Error: {e!s}"),
                    is_collapsed=False,
                )
                self._add_message(error_msg, increment_counter=False)
                self.set_status("Error")
                self.set_current_tool(None)

        self.run_worker(respond)  # type: ignore[arg-type]  # textual run_worker expects Never return type for coroutine, but respond returns None

    def _update_stream_message(self, msg_id: str, content: MessageContent) -> None:
        """更新流式消息内容"""
        # 检查应用是否已退出
        if not self.is_running:
            return
        try:
            conversation = self.query_one("#conversation-area", ConversationArea)
            for msg_widget in conversation.messages:
                if msg_widget.message.id == msg_id:
                    msg_widget.message.content = content
                    msg_widget.refresh()
                    break
        except (RuntimeError, ValueError) as e:
            logger.debug("Failed to update stream message %s: %s", msg_id, e)

    def _update_message(self, msg_id: str, message: MessageItem) -> None:
        """更新消息（用于 complete 事件）"""
        # 检查应用是否已退出
        if not self.is_running:
            return
        try:
            conversation = self.query_one("#conversation-area", ConversationArea)
            for msg_widget in conversation.messages:
                if msg_widget.message.id == msg_id:
                    # 更新整个消息对象
                    msg_widget.message = message
                    # 重新渲染
                    msg_widget.remove_children()
                    for widget in msg_widget.compose():
                        msg_widget.mount(widget)
                    break
        except (RuntimeError, ValueError) as e:
            logger.debug("Failed to update message %s: %s", msg_id, e)

    def _format_debug_content(self, content: Any) -> str:
        """格式化调试内容为可读字符串"""
        import json

        if content is None:
            return "None"
        if isinstance(content, str):
            # 尝试解析 JSON
            try:
                parsed = json.loads(content)
                return json.dumps(parsed, indent=2, ensure_ascii=False)
            except (json.JSONDecodeError, TypeError):
                return content
        if isinstance(content, dict):
            return json.dumps(content, indent=2, ensure_ascii=False)
        if isinstance(content, (list, tuple)):
            return json.dumps(list(content), indent=2, ensure_ascii=False)
        return str(content)

    def _simulate_agent_response(self, user_text: str) -> None:
        """模拟 Agent 响应 (已弃用，使用 _stream_agent_response)"""
        self._stream_agent_response(user_text)

    def _add_message(self, message: MessageItem, increment_counter: bool = True) -> None:
        """添加消息到对话区域

        Args:
            message: 要添加的消息
            increment_counter: 是否递增计数器（内部使用，避免重复递增）
        """
        if increment_counter:
            self._message_counter += 1

        # 检查应用是否已退出，如果已退出则不再更新 UI
        if not self.is_running:
            return

        # 检查应用是否已 mount（有 DOM）
        if self.is_mounted is not True:
            # 应用尚未 mount，仅存储消息供后续使用
            return

        try:
            conversation = self.query_one("#conversation-area", ConversationArea)
            if conversation:
                conversation.add_message(message)
                self._update_header()
        except (RuntimeError, ValueError):
            # DOM 查询失败，静默处理
            pass

    def _update_header(self) -> None:
        """更新头部状态"""
        # 检查应用是否已退出
        if not self.is_running:
            return
        try:
            header = self.query_one(Header)
            if header:
                header.update_status()
        except (RuntimeError, ValueError):
            # DOM 查询失败，静默处理
            pass

    def _focus_input(self) -> None:
        """聚焦输入框"""
        try:
            input_section = self.query_one("#input-section", ResizableInput)
            if input_section:
                textarea = input_section.query_one("#input-textarea", TextArea)
                if textarea:
                    textarea.focus()
        except (RuntimeError, ValueError) as e:
            print(f"Focus error: {e}")

    def _do_focus(self) -> None:
        """延迟聚焦到 TextArea"""
        self._focus_input()

    # 动作处理
    async def action_quit(self) -> None:
        """退出应用"""
        self.exit()

    def action_toggle_debug(self) -> None:
        """切换 Debug 显示"""
        self.debug_enabled = not self.debug_enabled
        self.notify(f"Debug mode: {'ON' if self.debug_enabled else 'OFF'}")

    def action_toggle_sidebar(self) -> None:
        """切换侧边栏 - 正确实现显示/隐藏"""
        self.show_sidebar = not self.show_sidebar

        # 获取主内容区域
        main_content = self.query_one("#main-content", Horizontal)
        if not main_content:
            return

        # 查找或创建侧边栏
        existing_sidebar = None
        for child in main_content.children:
            if hasattr(child, "id") and child.id == "sidebar":
                existing_sidebar = child
                break

        if self.show_sidebar:
            # 显示侧边栏
            if existing_sidebar:
                existing_sidebar.display = True
            else:
                # 创建并挂载侧边栏
                sidebar = Sidebar(self.app_state.context, id="sidebar")
                main_content.mount(sidebar)
        # 隐藏侧边栏
        elif existing_sidebar:
            existing_sidebar.display = False

        self.notify(f"Sidebar: {'ON' if self.show_sidebar else 'OFF'}")

    def action_clear_history(self) -> None:
        """清空对话历史"""
        conversation = self.query_one("#conversation-area", ConversationArea)
        if conversation:
            conversation.remove_children()
            conversation.messages = []
        self._message_counter = 0
        self._add_welcome_message()

    def action_submit(self) -> None:
        """提交输入"""
        input_section = self.query_one("#input-section", ResizableInput)
        if input_section:
            input_section._do_submit()

    def action_clear_input(self) -> None:
        """清空输入"""
        input_section = self.query_one("#input-section", ResizableInput)
        if input_section:
            input_section.clear_input()

    def action_focus_next(self) -> None:
        """聚焦下一个控件"""
        self.screen.focus_next()

    def action_focus_previous(self) -> None:
        """聚焦上一个控件"""
        self.screen.focus_previous()

    def action_toggle_theme(self) -> None:
        """切换主题（明/暗）"""
        from polaris.delivery.cli.textual.styles import get_theme_manager

        manager = get_theme_manager()
        new_mode = manager.toggle()
        theme_name = "Light" if new_mode == ThemeMode.LIGHT else "Dark"
        self.notify(f"Theme: {theme_name}")

        # 更新头部主题指示器
        self._update_header()

    def action_toggle_search(self) -> None:
        """切换搜索模式"""
        self.notify("Search: Use / to search messages | Ctrl+K for commands")

    def action_show_command_palette(self) -> None:
        """显示命令面板"""
        commands = [
            "Ctrl+Q: Quit",
            "Ctrl+T: Toggle Theme",
            "Ctrl+S: Toggle Sidebar",
            "Ctrl+D: Toggle Debug",
            "Ctrl+L: Clear History",
            "Ctrl+F: Search",
            "F1: Help",
            "F3: View Logs",
        ]
        command_text = "\n".join(commands)
        self.notify(f"Commands:\n{command_text}", severity="information", timeout=5.0)

    def action_show_help(self) -> None:
        """显示帮助信息"""
        help_text = f"""**{APP_TITLE} - Help**

**Navigation:**
- `Tab` / `Shift+Tab`: Next/Previous focus
- `Ctrl+↑/↓`: Navigate history

**Actions:**
- `Ctrl+Enter`: Send message
- `Escape`: Clear input
- `Ctrl+Q`: Quit
- `Ctrl+T`: Toggle theme
- `Ctrl+S`: Toggle sidebar
- `Ctrl+D`: Toggle debug
- `Ctrl+L`: Clear history
- `Ctrl+K`: Commands

**Current Session:**
- Workspace: `{self.workspace}`
- Role: `{self.role}`
"""
        self._message_counter += 1
        help_msg = MessageItem(
            id=f"help-{self._message_counter}",
            type=MessageType.SYSTEM,
            title="Help",
            content=MessageContent(text=help_text),
            is_collapsed=False,
        )
        self._add_message(help_msg, increment_counter=False)

    def action_show_logs(self) -> None:
        """显示日志面板"""
        self.notify("Logs viewer: Coming soon", severity="information")

    # 公共 API
    def add_user_message(self, text: str) -> None:
        """添加用户消息"""
        self._message_counter += 1
        msg = MessageItem(
            id=f"user-{self._message_counter}",
            type=MessageType.USER,
            title="You",
            content=MessageContent(text=text),
            is_collapsed=False,
        )
        self._add_message(msg, increment_counter=False)

    def add_assistant_message(self, text: str, title: str = "Assistant") -> None:
        """添加助手消息"""
        self._message_counter += 1
        msg = MessageItem(
            id=f"assistant-{self._message_counter}",
            type=MessageType.ASSISTANT,
            title=title,
            content=MessageContent(text=text, has_markdown=True),
            is_collapsed=False,
        )
        self._add_message(msg, increment_counter=False)

    def add_stream_chunk(self, chunk: str) -> None:
        """添加流式输出块 - 传递计数器引用确保 ID 唯一"""
        conversation = self.query_one("#conversation-area", ConversationArea)
        if conversation:
            # 使用列表作为引用类型传递计数器
            conversation.add_stream_chunk(chunk, counter_ref=[self._message_counter])

    def finalize_stream(self) -> None:
        """结束流式输出"""
        conversation = self.query_one("#conversation-area", ConversationArea)
        if conversation:
            conversation.finalize_stream()

    def add_tool_call(self, name: str, arguments: dict) -> str:
        """添加工具调用"""
        self._message_counter += 1
        self._tool_counter += 1
        tool_id = f"tool-{self._tool_counter}"

        msg = MessageItem(
            id=tool_id,
            type=MessageType.TOOL_CALL,
            title="Tool Call",
            content=MessageContent(text=f"Calling: {name}"),
            metadata={"tool_name": name, "args": arguments},
            is_collapsed=False,
        )
        self._add_message(msg, increment_counter=False)

        return tool_id

    def add_tool_result(self, tool_id: str, result: Any, error: str | None = None) -> None:
        """添加工具结果"""
        self._message_counter += 1

        msg = MessageItem(
            id=f"{tool_id}-result",
            type=MessageType.TOOL_RESULT,
            title="Tool Result",
            content=MessageContent(
                text=str(result) if error is None else f"Error: {error}",
            ),
            is_collapsed=False,
        )
        self._add_message(msg, increment_counter=False)

    def add_debug(
        self,
        category: str,
        label: str,
        payload: Any,
        source: str = "",
        tags: dict | None = None,
    ) -> str:
        """添加 Debug 消息"""
        if not self.debug_enabled:
            return ""

        self._debug_counter += 1
        debug_id = f"debug-{self._debug_counter:04d}"

        debug_item = DebugItem.from_payload(
            id=debug_id,
            category=category,
            label=label,
            source=source,
            tags=tags or {},
            payload=payload,
        )

        # 转换为 MessageItem 添加
        self._message_counter += 1
        msg = MessageItem(
            id=debug_id,
            type=MessageType.DEBUG,
            title=f"[{category}][{label}]",
            content=MessageContent(text=debug_item.content),
            metadata={"debug_item": debug_item},
            is_collapsed=True,  # Debug 默认折叠
        )
        self._add_message(msg, increment_counter=False)

        return debug_id

    def set_status(self, status: str) -> None:
        """设置状态"""
        self.app_state.context.status = status
        # 检查应用是否已退出
        if self.is_running:
            self._update_header()

    def set_tokens(self, used: int, total: int = 4096) -> None:
        """设置 Token 统计"""
        self.app_state.context.token_usage = {"used": used, "total": total}
        # 检查应用是否已退出
        if self.is_running:
            self._update_header()

    def set_current_tool(self, tool_name: str | None) -> None:
        """设置当前工具"""
        self.app_state.context.current_tool = tool_name
        # 检查应用是否已退出
        if self.is_running:
            self._update_header()


# =============================================================================
# 运行器
# =============================================================================


def run_claude_tui(
    workspace: str = ".",
    role: str = "assistant",
    session_id: str | None = None,
    debug: bool = True,
) -> int:
    """运行 Claude 风格 Agent TUI

    Args:
        workspace: 工作目录
        role: 角色名称
        session_id: 会话 ID
        debug: 是否启用 Debug 模式

    Returns:
        退出码
    """
    app = ClaudeAgentTUI(
        workspace=workspace,
        role=role,
        session_id=session_id,
        debug_enabled=debug,
    )
    result = app.run()
    return result if result is not None else 0


# =============================================================================
# 演示
# =============================================================================

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Polaris Textual TUI - Claude-style Agent Console",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python -m polaris.delivery.cli.textual_console
  python -m polaris.delivery.cli.textual_console --workspace /path/to/project
  python -m polaris.delivery.cli.textual_console --role director
  python -m polaris.delivery.cli.textual_console --role pm --debug
        """,
    )
    parser.add_argument(
        "--workspace",
        "-w",
        type=str,
        default=".",
        help="Workspace directory (default: .)",
    )
    parser.add_argument(
        "--role",
        "-r",
        type=str,
        default="director",
        choices=["director", "pm", "architect", "chief_engineer", "qa"],
        help="Role to use (default: director)",
    )
    parser.add_argument(
        "--session",
        "-s",
        type=str,
        default=None,
        help="Session ID to resume",
    )
    parser.add_argument(
        "--debug",
        "-d",
        action="store_true",
        default=False,
        help="Enable debug mode",
    )
    parser.add_argument(
        "--demo",
        action="store_true",
        default=False,
        help="Run in demo mode with sample messages",
    )

    args = parser.parse_args()

    if args.demo:
        print("Starting Polaris Textual TUI Demo...")
        print("Press Ctrl+Q to quit")
        app = ClaudeAgentTUI(
            workspace=args.workspace,
            role="assistant",
            debug_enabled=True,
        )

        def load_demo_messages() -> None:
            try:
                app.add_user_message("Hello, how are you?")

                def add_response() -> None:
                    app.add_assistant_message("Hello! I'm doing well, thank you for asking. How can I help you today?")

                app.set_timer(0.5, add_response)
            except (RuntimeError, ValueError) as e:
                print(f"Demo loading error: {e}")

        app.set_timer(0.5, load_demo_messages)
        app.run()
    else:
        run_claude_tui(
            workspace=args.workspace,
            role=args.role,
            session_id=args.session,
            debug=args.debug,
        )


# =============================================================================
# 向后兼容接口 (Legacy API Compatibility)
# =============================================================================


class PolarisTextualConsole(ClaudeAgentTUI):
    """向后兼容的 PolarisTextualConsole 类

    保留旧接口以确保现有代码兼容性。
    新代码应直接使用 ClaudeAgentTUI。
    """

    def __init__(
        self,
        workspace: str,
        role: str = "director",
        session_id: str | None = None,
        debug_enabled: bool = True,
    ) -> None:
        super().__init__(
            workspace=workspace,
            role=role,
            session_id=session_id,
            debug_enabled=debug_enabled,
        )
        # 保持旧属性名兼容性
        self.debug_enabled = debug_enabled

    def add_message(self, content: str, msg_type: str = "assistant") -> None:
        """添加普通消息 (旧接口)"""
        type_mapping = {
            "user": MessageType.USER,
            "assistant": MessageType.ASSISTANT,
            "error": MessageType.ERROR,
            "system": MessageType.SYSTEM,
        }
        message_type = type_mapping.get(msg_type, MessageType.ASSISTANT)

        self._message_counter += 1
        msg = MessageItem(
            id=f"legacy-{self._message_counter}",
            type=message_type,
            title=msg_type.capitalize(),
            content=MessageContent(text=content),
            is_collapsed=False,
        )
        self._add_message(msg, increment_counter=False)

    def add_tool_result(self, tool_name: str, result: str | None = None, error: str | None = None) -> None:
        """Add tool result (legacy interface)"""
        self._message_counter += 1
        self._tool_counter += 1

        msg = MessageItem(
            id=f"tool-result-{self._tool_counter}",
            type=MessageType.TOOL_RESULT,
            title="Tool Result",
            content=MessageContent(text=f"[{tool_name}]\n{result if error is None else f'Error: {error}'}"),
            is_collapsed=False,
        )
        self._add_message(msg, increment_counter=False)

    def action_toggle_all_debug(self) -> None:
        """切换所有 Debug 显示 (旧接口)"""
        self.action_toggle_debug()

    def action_expand_all_debug(self) -> None:
        """展开所有 Debug (旧接口，现为空实现)"""
        pass  # 新界面使用单条折叠控制

    def action_collapse_all_debug(self) -> None:
        """折叠所有 Debug (旧接口，现为空实现)"""
        pass  # 新界面使用单条折叠控制


def run_textual_console(
    workspace: str,
    role: str = "director",
    session_id: str | None = None,
    debug: bool = True,
) -> int:
    """运行 Textual TUI 控制台 (向后兼容)

    保留旧接口以确保现有代码兼容性。
    新代码应直接使用 run_claude_tui()。

    Args:
        workspace: 工作目录
        role: 角色
        session_id: 会话 ID
        debug: 是否启用 DEBUG

    Returns:
        退出码
    """
    return run_claude_tui(
        workspace=workspace,
        role=role,
        session_id=session_id,
        debug=debug,
    )


# 向后兼容的 DebugItem 别名 (从 models 重新导出)
# 注意：DebugItem 已经在第 48-56 行从 models 导入
# 这里不需要重复定义，只需要在 __all__ 中导出即可

# 导出公共 API
__all__ = [
    "AppState",
    "CatppuccinLatte",
    "CatppuccinMocha",
    # 新 API
    "ClaudeAgentTUI",
    "ConversationArea",
    "ConversationContext",
    "DebugItem",
    "Header",
    "MessageContent",
    # 模型 (从 models 重新导出以便外部使用)
    "MessageItem",
    "MessageType",
    "MessageWidget",
    # 旧 API (兼容性)
    "PolarisTextualConsole",
    "ResizableInput",
    "Sidebar",
    # 样式 (从 styles 重新导出)
    "ThemeColors",
    "ThemeManager",
    "ThemeMode",
    "ToolCallInfo",
    "ToolStatus",
    "get_console_css",
    "run_claude_tui",
    "run_textual_console",
]
