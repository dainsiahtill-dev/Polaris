"""Textual TUI 数据模型 - Claude 风格 Agent TUI

定义可折叠消息、DEBUG 项、工具调用等数据结构。

P1-TYPE-008: ToolStatus 说明
    - 本地 ToolStatus 枚举保留，用于 TUI 显示状态
    - canonical ToolStatus 位于 polaris.kernelone.agent.tools.contracts
    - 两者有语义差异：本地有 PENDING/RUNNING，canonical 有 BLOCKED/TIMEOUT
    - 这两个是不同层级的状态：TUI 显示层 vs 执行层
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


class MessageType(Enum):
    """消息类型枚举"""

    USER = "user"
    ASSISTANT = "assistant"
    THINKING = "thinking"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    SYSTEM = "system"
    ERROR = "error"
    DEBUG = "debug"
    METADATA = "metadata"
    STREAM = "stream"  # 流式输出


class ToolStatus(Enum):
    """工具调用状态（TUI 显示层）

    P1-TYPE-008: 此枚举与 canonical ToolStatus 有语义差异
    - 本地: PENDING, RUNNING, SUCCESS, ERROR, CANCELLED (显示层)
    - canonical: SUCCESS, ERROR, BLOCKED, TIMEOUT, CANCELLED (执行层)

    用途：TUI 需要细粒度显示状态（PENDING/RUNNING），
    而执行层只需要结果状态。
    """

    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    ERROR = "error"
    CANCELLED = "cancelled"


@dataclass
class CodeBlock:
    """代码块数据"""

    language: str
    code: str
    filename: str | None = None

    @property
    def display_title(self) -> str:
        """获取显示标题"""
        if self.filename:
            return f"{self.language}: {self.filename}"
        return self.language or "code"


@dataclass
class MessageContent:
    """消息内容，支持 Markdown、代码块等"""

    text: str = ""
    code_blocks: list[CodeBlock] = field(default_factory=list)
    has_markdown: bool = False
    thinking: str | None = None

    def add_code_block(self, language: str, code: str, filename: str | None = None) -> None:
        """添加代码块"""
        self.code_blocks.append(CodeBlock(language, code, filename))

    @property
    def plain_text(self) -> str:
        """获取纯文本内容"""
        return self.text


@dataclass
class MessageItem:
    """可折叠的消息项 - Claude 风格"""

    id: str
    type: MessageType
    title: str
    content: MessageContent | str
    is_collapsed: bool = False
    timestamp: datetime = field(default_factory=datetime.now)
    metadata: dict[str, Any] = field(default_factory=dict)
    avatar: str = ""

    def __post_init__(self) -> None:
        if isinstance(self.content, str):
            self.content = MessageContent(text=self.content)

    @property
    def marker(self) -> str:
        return "▼" if not self.is_collapsed else "▶"

    @property
    def author_label(self) -> str:
        """获取作者标签"""
        labels = {
            MessageType.USER: "You",
            MessageType.ASSISTANT: "Assistant",
            MessageType.SYSTEM: "System",
            MessageType.TOOL_CALL: "Tool",
            MessageType.TOOL_RESULT: "Tool Result",
            MessageType.ERROR: "Error",
            MessageType.DEBUG: "Debug",
            MessageType.THINKING: "Thinking",
            MessageType.STREAM: "Assistant",
        }
        return labels.get(self.type, "Unknown")

    @property
    def summary(self) -> str:
        """获取折叠时的摘要"""
        if self.type == MessageType.USER:
            # 用户消息显示前 50 个字符
            content_text = self.content.text if isinstance(self.content, MessageContent) else str(self.content)
            text = content_text[:50] if content_text else ""
            if len(content_text) > 50:
                text += "..."
            return f"{self.author_label}: {text}"
        elif self.type == MessageType.TOOL_CALL:
            tool_name = self.metadata.get("tool_name", "Unknown")
            return f"{self.author_label}: {tool_name}"
        else:
            return f"{self.author_label}: {self.title}"

    def toggle(self) -> None:
        self.is_collapsed = not self.is_collapsed

    def expand(self) -> None:
        self.is_collapsed = False

    def collapse(self) -> None:
        self.is_collapsed = True


@dataclass
class ToolCallInfo:
    """工具调用信息"""

    id: str
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)
    status: ToolStatus = ToolStatus.PENDING
    result: Any = None
    error: str | None = None
    timestamp: datetime = field(default_factory=datetime.now)
    duration_ms: float = 0.0

    def to_display_dict(self) -> dict[str, Any]:
        """转换为显示字典"""
        return {
            "tool": self.name,
            "status": self.status.value,
            "args": self.arguments,
            "result": self.result,
            "error": self.error,
            "duration_ms": self.duration_ms,
        }


@dataclass
class DebugItem:
    """DEBUG 消息项，支持折叠/展开"""

    id: str
    category: str
    label: str
    content: str
    source: str = ""
    tags: dict[str, Any] = field(default_factory=dict)
    timestamp: datetime = field(default_factory=datetime.now)
    is_collapsed: bool = True  # DEBUG 默认折叠
    severity: str = "info"  # info, warning, error

    def __post_init__(self) -> None:
        if not self.content:
            self.content = ""

    @property
    def line_count(self) -> int:
        return len(self.content.splitlines()) if self.content else 0

    @property
    def title(self) -> str:
        parts = [f"[{self.category}][{self.label}]"]
        if self.source:
            parts.append(f"[{self.source}]")
        if self.tags:
            tag_str = " ".join(f"{k}={v}" for k, v in sorted(self.tags.items())[:3])
            parts.append(tag_str)
        return " ".join(parts)

    @property
    def marker(self) -> str:
        return "▼" if not self.is_collapsed else "▶"

    @property
    def severity_icon(self) -> str:
        icons = {
            "info": "ℹ",
            "warning": "⚠",
            "error": "✗",
        }
        return icons.get(self.severity, "ℹ")

    def toggle(self) -> None:
        self.is_collapsed = not self.is_collapsed

    def expand(self) -> None:
        self.is_collapsed = False

    def collapse(self) -> None:
        self.is_collapsed = True

    @classmethod
    def from_payload(
        cls,
        id: str,
        category: str,
        label: str,
        source: str,
        tags: dict[str, Any],
        payload: Any,
        severity: str = "info",
    ) -> DebugItem:
        """从事件 payload 创建 DebugItem"""
        if isinstance(payload, dict):
            content = json.dumps(payload, ensure_ascii=False, indent=2)
        elif isinstance(payload, str):
            content = payload
        elif payload is None:
            content = ""
        else:
            content = str(payload)

        return cls(
            id=id,
            category=category,
            label=label,
            source=source,
            tags=tags,
            content=content,
            severity=severity,
        )


@dataclass
class ConversationContext:
    """对话上下文信息"""

    rag_documents: list[dict[str, Any]] = field(default_factory=list)
    tool_calls_history: list[ToolCallInfo] = field(default_factory=list)
    agent_plan: str = ""
    token_usage: dict[str, int] = field(default_factory=dict)
    current_tool: str | None = None
    status: str = "idle"  # idle, processing, streaming, error

    @property
    def total_tokens(self) -> int:
        """获取总 Token 数"""
        return self.token_usage.get("total", 0)

    @property
    def tokens_display(self) -> str:
        """获取 Token 显示字符串"""
        used = self.token_usage.get("used", 0)
        total = self.token_usage.get("total", 4096)
        return f"{used}/{total}"


@dataclass
class AppState:
    """应用状态"""

    workspace: str = ""
    role: str = "assistant"
    session_id: str = ""
    is_connected: bool = False
    is_processing: bool = False
    context: ConversationContext = field(default_factory=ConversationContext)

    @property
    def status_text(self) -> str:
        """获取状态文本"""
        if self.is_processing:
            return "PROCESSING"
        elif self.context.status == "streaming":
            return "STREAMING"
        elif self.is_connected:
            return "CONNECTED"
        return "IDLE"

    @property
    def status_color(self) -> str:
        """获取状态颜色"""
        if self.context.status == "error":
            return "#f38ba8"  # red
        elif self.is_processing or self.context.status == "streaming":
            return "#f9e2af"  # yellow
        elif self.is_connected:
            return "#a6e3a1"  # green
        return "#6c7086"  # gray
