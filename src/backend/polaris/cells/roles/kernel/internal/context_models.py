"""Context Models - 上下文分层模型

定义上下文的不同层次结构。
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class SystemContext:
    """系统级上下文（角色提示词、工具定义等）"""

    system_prompt: str
    tool_definitions: list[dict] = field(default_factory=list)
    role_profile_version: str = ""
    security_boundary: str = ""


@dataclass
class TaskContext:
    """任务级上下文"""

    task_id: str
    task_description: str
    constraints: list[str] = field(default_factory=list)
    acceptance_criteria: list[str] = field(default_factory=list)
    priority: str = "medium"


@dataclass
class ConversationHistory:
    """对话历史"""

    messages: list[dict[str, str]] = field(default_factory=list)  # [{"role": "user", "content": "..."}]
    max_turns: int = 10
    created_at: datetime = field(default_factory=datetime.now)

    @property
    def turn_count(self) -> int:
        """获取对话轮数"""
        return len([m for m in self.messages if m.get("role") in ("user", "assistant")])


@dataclass
class ContextOverride:
    """外部注入的上下文覆盖"""

    overrides: dict[str, Any] = field(default_factory=dict)
    source: str = ""  # "pm", "director", "external"
    created_at: datetime = field(default_factory=datetime.now)


@dataclass
class MemorySnippet:
    """记忆片段（用于摘要压缩）"""

    content: str
    importance: float = 0.5  # 0-1
    embedding: list[float] | None = None
    source_turn: int = 0
    created_at: datetime = field(default_factory=datetime.now)

    def should_summarize(self, threshold: float = 0.3) -> bool:
        """判断是否应该被摘要（低重要性）"""
        return self.importance < threshold


@dataclass
class ContextStats:
    """上下文统计信息"""

    system_tokens: int = 0
    task_tokens: int = 0
    conversation_tokens: int = 0
    override_tokens: int = 0
    total_tokens: int = 0

    compression_strategy: str = "none"  # "none", "truncate", "sliding_window", "summarize"
    original_tokens: int = 0
    compressed_tokens: int = 0

    @property
    def compression_ratio(self) -> float:
        if self.original_tokens == 0:
            return 1.0
        return self.compressed_tokens / self.original_tokens
