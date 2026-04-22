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
class TokenBudget:
    """Token 预算追踪器"""

    max_tokens: int = 128000
    system_budget: int = 32000
    task_budget: int = 16000
    conversation_budget: int = 64000
    override_budget: int = 16000

    def check_budget(self, consumed: int) -> dict:
        """检查预算使用状态"""
        return {
            "within_limit": consumed <= self.max_tokens,
            "remaining": self.max_tokens - consumed,
            "usage_ratio": consumed / self.max_tokens if self.max_tokens > 0 else 0,
        }


@dataclass
class CompressionMetrics:
    """压缩效率指标"""

    original_tokens: int = 0
    compressed_tokens: int = 0
    compression_strategy: str = "none"

    @property
    def compression_ratio(self) -> float:
        """压缩比计算"""
        if self.original_tokens == 0:
            return 1.0
        return self.compressed_tokens / self.original_tokens

    @property
    def compression_savings(self) -> int:
        """节省的 token 数"""
        return self.original_tokens - self.compressed_tokens

    @property
    def savings_percent(self) -> float:
        """节省百分比"""
        if self.original_tokens == 0:
            return 0.0
        return (1 - self.compression_ratio) * 100


@dataclass
class ContextStats:
    """上下文统计信息"""

    system_tokens: int = 0
    task_tokens: int = 0
    conversation_tokens: int = 0
    override_tokens: int = 0
    compression_strategy: str = "none"
    budget: TokenBudget | None = None
    metrics: CompressionMetrics | None = None

    def __post_init__(self) -> None:
        if self.budget is None:
            self.budget = TokenBudget()
        if self.metrics is None:
            self.metrics = CompressionMetrics(compression_strategy=self.compression_strategy)

    @property
    def total_tokens(self) -> int:
        """总 token 数"""
        return self.system_tokens + self.task_tokens + self.conversation_tokens + self.override_tokens

    @property
    def compression_ratio(self) -> float:
        """压缩比计算"""
        return self.metrics.compression_ratio if self.metrics else 1.0

    def get_budget_status(self) -> dict:
        """获取预算状态"""
        return self.budget.check_budget(self.total_tokens) if self.budget else {}

    def to_dict(self) -> dict:
        """转换为字典格式"""
        return {
            "system_tokens": self.system_tokens,
            "task_tokens": self.task_tokens,
            "conversation_tokens": self.conversation_tokens,
            "override_tokens": self.override_tokens,
            "total_tokens": self.total_tokens,
            "compression_strategy": self.compression_strategy,
            "compression_ratio": self.compression_ratio,
            "compression_savings": self.metrics.compression_savings if self.metrics else 0,
            "budget_status": self.get_budget_status(),
        }


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
