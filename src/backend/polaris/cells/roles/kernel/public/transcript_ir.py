"""Public Transcript IR - 角色内核 Transcript 中间表示（Public Boundary）

# -*- coding: utf-8 -*-
UTF-8 编码验证: 本文所有文本使用 UTF-8

本模块是 `roles.kernel` Cell 的公开 Transcript IR 边界。

类型层次:
    ToolCall       - 工具调用条目（P1-TYPE-004: 从 canonical 导入）
    ToolResult     - 工具执行结果条目（P1-TYPE-003: 重命名为 TranscriptToolResult）
    ControlEvent   - 控制流事件条目（stop/continue/handoff/approval_required/...）
    ReasoningSummary - 推理摘要条目
    SystemInstruction - 系统级指令条目
    UserMessage    - 用户消息条目
    AssistantMessage - 助手消息条目
    ParsedToolPlan - 解析后的工具调用计划（Chief Architect 裁决 #2）
    TranscriptAppendRequest - transcript 追加请求（Chief Architect 裁决 #2）
    SanitizedOutput - 经过消毒的用户可见文本（NewType，Chief Architect 裁决 #2）
    TranscriptItem - 上述类型的联合类型别名
    TranscriptDelta - 一次 delta 输出的完整 transcript 增量

P1-TYPE-003/004: 类型统一
    - ToolCall: 从 polaris.kernelone.llm.contracts.tool 导入（canonical）
    - ToolResult: 重命名为 TranscriptToolResult 以避免与 canonical ToolExecutionResult 冲突

迁移说明（Chief Architect 裁决 #3）:
    本模块从 `roles.kernel.internal` 迁移至 `roles.kernel.public`。
    原因：ToolCall / ToolResult 被外部 Cell（如 llm.tool_runtime orchestrator）使用。
    旧路径 `roles.kernel.internal.transcript_ir` 保留为兼容重导出（短期过渡）。
"""

from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Literal, NewType, cast

if TYPE_CHECKING:
    from polaris.cells.roles.kernel.internal.output_parser import ToolCallResult


# ---------------------------------------------------------------------------
# NewType: SanitizedOutput（Chief Architect 裁决 #2）
# ---------------------------------------------------------------------------

# P1-TYPE-004: NOTE - canonical ToolCall is in polaris.kernelone.llm.contracts.tool
# The TranscriptToolCall in this module is intentionally different (transcript layer vs execution layer)

SanitizedOutput = NewType("SanitizedOutput", str)
"""经过消毒的用户可见文本。

使用 NewType 而非直接 str 可以：
1. 在函数签名中明确区分"已消毒"与"未消毒"文本
2. 提供类型级安全：sanitize() 函数返回 SanitizedOutput，调用方须显式处理

示例用法::

    def sanitize(raw: str) -> SanitizedOutput:
        ...
        return SanitizedOutput(cleaned_text)

    def append_to_transcript(clean: SanitizedOutput) -> None:
        # 明确知道输入是已消毒的
        ...
"""


# ---------------------------------------------------------------------------
# Literal 类型约束
# ---------------------------------------------------------------------------

ToolResultStatus = Literal["success", "error", "blocked", "timeout"]

ControlEventType = Literal["stop", "continue", "handoff", "approval_required", "budget_hit", "compacted"]


# ---------------------------------------------------------------------------
# Chief Architect 裁决 #2: 新增 dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ParsedToolPlan:
    """解析后的工具调用计划。

    表示从 assistant 输出中解析出的完整工具调用计划。

    与 `ToolCall` IR 类型的区别：
        - ToolCall：是 transcript 条目，记录历史
        - ParsedToolPlan：是解析阶段的输出，携带解析元数据
    """

    calls: list[CanonicalToolCallEntry] = field(default_factory=list)
    """解析出的工具调用列表。"""

    remainder_text: str = ""
    """提取工具调用后的剩余文本。"""

    raw_text: str = ""
    """原始 assistant 输出文本。"""

    from_native: bool = False
    """是否来自 provider 原生工具调用（非文本解析）。"""

    parse_error: str | None = None
    """解析错误信息（如果解析失败）。"""

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["calls"] = [c.to_dict() for c in self.calls]
        return result

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ParsedToolPlan:
        calls = [CanonicalToolCallEntry.from_dict(c) for c in data.get("calls", [])]
        return cls(
            calls=calls,
            remainder_text=str(data.get("remainder_text", "")),
            raw_text=str(data.get("raw_text", "")),
            from_native=bool(data.get("from_native", False)),
            parse_error=data.get("parse_error"),
        )


@dataclass(frozen=True, slots=True)
class CanonicalToolCallEntry:
    """解析后的单个工具调用条目（用于 ParsedToolPlan）。"""

    tool: str
    args: dict[str, Any]
    call_id: str = ""
    raw: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool": self.tool,
            "args": self.args,
            "call_id": self.call_id,
            "raw": self.raw,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CanonicalToolCallEntry:
        return cls(
            tool=str(data.get("tool", "")),
            args=dict(data.get("args", {})),
            call_id=str(data.get("call_id", "")),
            raw=str(data.get("raw", "")),
        )


@dataclass(frozen=True)
class TranscriptAppendRequest:
    """向 transcript 追加条目的结构化请求。

    用于 typed 化的 transcript 追加操作，替代隐式的 (role, content) tuple。

    优点：
        1. 显式字段，避免 tuple 位置错误
        2. 支持元数据字段（meta）传递额外信息
        3. 便于日志审计（结构化 vs 字符串拼接）
    """

    session_id: str | None = None
    """目标会话 ID（用于持久化）。"""

    role: str = ""
    """角色标签：user | assistant | system | tool。"""

    content: str | SanitizedOutput = ""
    """消息内容（可为已消毒文本 SanitizedOutput）。"""

    thinking: str | None = None
    """推理内容（仅 assistant 消息）。"""

    meta: dict[str, Any] = field(default_factory=dict)
    """附加元数据（tool_name, call_id, status 等）。"""

    compact: bool = False
    """是否由 compaction 产生的条目。"""

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "role": self.role,
            "content": str(self.content),
            "thinking": self.thinking,
            "meta": self.meta,
            "compact": self.compact,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TranscriptAppendRequest:
        content = data.get("content", "")
        # 如果 content 已经是 SanitizedOutput 标记，从 str 重建
        return cls(
            session_id=data.get("session_id"),
            role=str(data.get("role", "")),
            content=str(content) if content else "",
            thinking=data.get("thinking"),
            meta=dict(data.get("meta", {})),
            compact=bool(data.get("compact", False)),
        )


# ---------------------------------------------------------------------------
# 核心条目类型（从 internal 迁移）
# P1-TYPE-004: Renamed to TranscriptToolCall to avoid conflict with
# canonical ToolCall from polaris.kernelone.llm.contracts.tool
# ---------------------------------------------------------------------------


@dataclass
class TranscriptToolCall:
    """Transcript中单次工具调用的 typed 条目。

    P1-TYPE-004: This is distinct from canonical ToolCall in
    polaris.kernelone.llm.contracts.tool which has different fields
    (id, name, arguments, source, raw, parse_error).

    This class is for transcript persistence and has fields like
    call_id, tool_name, args, provider, provider_meta, raw_reference, created_at.
    """

    call_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    tool_name: str = ""
    args: dict[str, Any] = field(default_factory=dict)
    provider: str | None = None
    provider_meta: dict[str, Any] | None = None
    raw_reference: Any | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict[str, Any]:
        """序列化为字典（用于 transcript 持久化）。"""
        result = asdict(self)
        result["created_at"] = self.created_at.isoformat()
        return result

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TranscriptToolCall:
        """从字典构造（用于 transcript 加载）。"""
        raw_created = data.get("created_at")
        created_at: datetime
        if isinstance(raw_created, str):
            created_at = datetime.fromisoformat(raw_created)
        elif isinstance(raw_created, datetime):
            created_at = raw_created
        else:
            created_at = datetime.now(timezone.utc)
        return cls(
            call_id=str(data.get("call_id", uuid.uuid4().hex)),
            tool_name=str(data.get("tool_name", "")),
            args=dict(data.get("args", {})),
            provider=data.get("provider"),
            provider_meta=data.get("provider_meta"),
            raw_reference=data.get("raw_reference"),
            created_at=created_at,
        )

    @classmethod
    def from_output_parser_result(
        cls,
        result: ToolCallResult,
        *,
        provider: str | None = None,
        provider_meta: dict[str, Any] | None = None,
        raw_reference: Any | None = None,
    ) -> TranscriptToolCall:
        """从 `output_parser.ToolCallResult` 构造。"""
        return cls(
            tool_name=str(result.tool or ""),
            args=dict(result.args) if isinstance(result.args, dict) else {},
            provider=provider,
            provider_meta=provider_meta,
            raw_reference=raw_reference,
        )


# Backward compatibility alias
ToolCall = TranscriptToolCall


@dataclass
class TranscriptToolResult:
    """Transcript中单次工具执行结果的 typed 条目。

    P1-TYPE-003: Renamed from ToolResult to avoid conflict with
    canonical ToolExecutionResult in polaris.kernelone.llm.contracts.tool.
    """

    call_id: str
    tool_name: str
    status: ToolResultStatus = "success"
    content: str | None = None
    artifact_refs: list[str] = field(default_factory=list)
    metrics: dict[str, Any] | None = None
    retryable: bool = False
    error_code: str | None = None
    error_message: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict[str, Any]:
        """序列化为字典（用于 transcript 持久化）。"""
        result = asdict(self)
        result["created_at"] = self.created_at.isoformat()
        return result

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TranscriptToolResult:
        """从字典构造（用于 transcript 加载）。"""
        raw_created = data.get("created_at")
        created_at: datetime
        if isinstance(raw_created, str):
            created_at = datetime.fromisoformat(raw_created)
        elif isinstance(raw_created, datetime):
            created_at = raw_created
        else:
            created_at = datetime.now(timezone.utc)
        return cls(
            call_id=str(data.get("call_id", "")),
            tool_name=str(data.get("tool_name", "")),
            status=cast(
                "ToolResultStatus",
                str(data.get("status", "success"))
                if data.get("status") in {"success", "error", "blocked", "timeout"}
                else "success",
            ),
            content=data.get("content"),
            artifact_refs=list(data.get("artifact_refs", [])),
            metrics=data.get("metrics"),
            retryable=bool(data.get("retryable", False)),
            error_code=data.get("error_code"),
            error_message=data.get("error_message"),
            created_at=created_at,
        )

    @classmethod
    def from_tool_call_and_result_dict(
        cls,
        tool_call: TranscriptToolCall,
        result_dict: dict[str, Any],
    ) -> TranscriptToolResult:
        """从 TranscriptToolCall 和原始结果字典构造。

        兼容 `tool_loop_controller` 中 `tool_results: list[dict[str, Any]]` 格式。
        """
        raw_status = str(result_dict.get("status", result_dict.get("success", True)))
        if raw_status in ("success", "error", "blocked", "timeout"):
            status: ToolResultStatus = raw_status  # type: ignore[assignment]
        elif isinstance(result_dict.get("success"), bool):
            status = "success" if result_dict["success"] else "error"  # type: ignore[assignment]
        else:
            status = "error"  # type: ignore[assignment]

        artifact_refs: list[str] = []
        result_value = result_dict.get("result") or result_dict.get("content")
        if isinstance(result_value, str) and result_value.startswith("artifact://"):
            artifact_refs = [result_value]
        elif isinstance(result_dict.get("artifact_refs"), list):
            artifact_refs = [str(r) for r in result_dict["artifact_refs"]]

        return cls(
            call_id=tool_call.call_id,
            tool_name=tool_call.tool_name,
            status=status,
            content=str(result_value) if result_value is not None else None,
            artifact_refs=artifact_refs,
            metrics={k: v for k, v in result_dict.items() if k in {"latency_ms", "token_count", "authorized", "cached"}}
            or None,
            retryable=bool(result_dict.get("retryable", False)),
            error_code=str(result_dict.get("error_code", "")) or None,
            error_message=str(result_dict.get("error", result_dict.get("error_message", ""))) or None,
        )


# Backward compatibility alias
ToolResult = TranscriptToolResult


@dataclass
class ControlEvent:
    """控制流事件的 typed 条目。"""

    event_type: ControlEventType = "continue"
    reason: str | None = None
    approval_required: bool = False
    budget_hit: bool = False
    compacted: bool = False
    handoff_target: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict[str, Any]:
        """序列化为字典（用于 transcript 持久化）。"""
        result = asdict(self)
        result["created_at"] = self.created_at.isoformat()
        return result

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ControlEvent:
        """从字典构造（用于 transcript 加载）。"""
        raw_created = data.get("created_at")
        created_at: datetime
        if isinstance(raw_created, str):
            created_at = datetime.fromisoformat(raw_created)
        elif isinstance(raw_created, datetime):
            created_at = raw_created
        else:
            created_at = datetime.now(timezone.utc)

        raw_event_type = data.get("event_type", "continue")
        valid_types = {"stop", "continue", "handoff", "approval_required", "budget_hit", "compacted"}
        event_type: ControlEventType = raw_event_type if raw_event_type in valid_types else "continue"  # type: ignore[assignment]

        return cls(
            event_type=event_type,
            reason=data.get("reason"),
            approval_required=bool(data.get("approval_required", False)),
            budget_hit=bool(data.get("budget_hit", False)),
            compacted=bool(data.get("compacted", False)),
            handoff_target=data.get("handoff_target"),
            metadata=dict(data.get("metadata", {})),
            created_at=created_at,
        )

    @classmethod
    def from_safety_violation(
        cls,
        reason: str,
        *,
        metadata: dict[str, Any] | None = None,
    ) -> ControlEvent:
        """从 `ToolLoopController.register_cycle` 返回的安全违规 reason 构造。"""
        reason_lower = reason.lower()
        if "tool_loop_safety_exceeded" in reason_lower or "total tool calls exceeded" in reason_lower:
            event_type: ControlEventType = "stop"
            budget_hit = True
        elif "wall time exceeded" in reason_lower:
            event_type = "stop"
            budget_hit = True
        elif "tool_loop_stalled" in reason_lower:
            event_type = "stop"
            budget_hit = False
        elif "handoff" in reason_lower:
            event_type = "handoff"
            budget_hit = False
        else:
            event_type = "stop"
            budget_hit = False

        return cls(
            event_type=event_type,
            reason=reason,
            budget_hit=budget_hit,
            metadata=metadata or {},
        )


@dataclass
class ReasoningSummary:
    """推理摘要条目（如 <thinking> 标签内容）。"""

    content: str = ""
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict[str, Any]:
        """序列化为字典（用于 transcript 持久化）。"""
        result = asdict(self)
        result["created_at"] = self.created_at.isoformat()
        return result

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ReasoningSummary:
        """从字典构造（用于 transcript 加载）。"""
        raw_created = data.get("created_at")
        created_at: datetime
        if isinstance(raw_created, str):
            created_at = datetime.fromisoformat(raw_created)
        elif isinstance(raw_created, datetime):
            created_at = raw_created
        else:
            created_at = datetime.now(timezone.utc)
        return cls(
            content=str(data.get("content", "")),
            created_at=created_at,
        )

    @classmethod
    def from_assistant_thinking(cls, thinking: str | None) -> ReasoningSummary | None:
        """从 assistant 消息中提取的 thinking 内容构造。"""
        if not thinking:
            return None
        return cls(content=thinking.strip())


# ---------------------------------------------------------------------------
# 消息条目类型（Blueprint §5 新增）
# ---------------------------------------------------------------------------


@dataclass
class SystemInstruction:
    """系统级指令条目（注入的系统提示）。"""

    content: str = ""
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict[str, Any]:
        """序列化为字典（含 __type__ 用于 from_dict 区分）。"""
        result = asdict(self)
        result["created_at"] = self.created_at.isoformat()
        result["__type__"] = "SystemInstruction"
        return result

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SystemInstruction:
        """从字典构造。"""
        raw_created = data.get("created_at")
        created_at: datetime
        if isinstance(raw_created, str):
            created_at = datetime.fromisoformat(raw_created)
        elif isinstance(raw_created, datetime):
            created_at = raw_created
        else:
            created_at = datetime.now(timezone.utc)
        return cls(
            content=str(data.get("content", "")),
            created_at=created_at,
        )


@dataclass
class UserMessage:
    """用户消息条目。"""

    content: str = ""
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict[str, Any]:
        """序列化为字典（含 __type__ 用于 from_dict 区分）。"""
        result = asdict(self)
        result["created_at"] = self.created_at.isoformat()
        result["__type__"] = "UserMessage"
        return result

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> UserMessage:
        """从字典构造。"""
        raw_created = data.get("created_at")
        created_at: datetime
        if isinstance(raw_created, str):
            created_at = datetime.fromisoformat(raw_created)
        elif isinstance(raw_created, datetime):
            created_at = raw_created
        else:
            created_at = datetime.now(timezone.utc)
        return cls(
            content=str(data.get("content", "")),
            created_at=created_at,
        )


@dataclass
class AssistantMessage:
    """助手消息条目。"""

    content: str = ""
    thinking: str | None = None  # 推理内容（提供商输出）
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict[str, Any]:
        """序列化为字典（含 __type__ 用于 from_dict 区分）。"""
        result = asdict(self)
        result["created_at"] = self.created_at.isoformat()
        result["__type__"] = "AssistantMessage"
        return result

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AssistantMessage:
        """从字典构造。"""
        raw_created = data.get("created_at")
        created_at: datetime
        if isinstance(raw_created, str):
            created_at = datetime.fromisoformat(raw_created)
        elif isinstance(raw_created, datetime):
            created_at = raw_created
        else:
            created_at = datetime.now(timezone.utc)
        return cls(
            content=str(data.get("content", "")),
            thinking=data.get("thinking"),
            created_at=created_at,
        )


# ---------------------------------------------------------------------------
# 联合类型别名
# ---------------------------------------------------------------------------

TranscriptItem = (
    SystemInstruction | UserMessage | AssistantMessage | ToolCall | ToolResult | ControlEvent | ReasoningSummary
)
"""所有 transcript 条目类型的联合别名。"""

# ---------------------------------------------------------------------------
# TranscriptDelta - 一次 delta 输出的完整 transcript 增量
# ---------------------------------------------------------------------------


@dataclass
class TranscriptDelta:
    """一次 streaming delta 的完整 transcript 增量。

    用于 `kernel.py` 中向 caller 推送 transcript 增量事件。
    """

    transcript_items: list[TranscriptItem] = field(default_factory=list)
    tool_calls: list[ToolCall] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """序列化为字典（用于 transcript 持久化或事件传递）。"""
        items: list[dict[str, Any]] = []
        for item in self.transcript_items:
            d = item.to_dict()
            if "__type__" not in d:
                d["__type__"] = type(item).__name__
            items.append(d)
        return {
            "transcript_items": items,
            "tool_calls": [tc.to_dict() for tc in self.tool_calls],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TranscriptDelta:
        """从字典构造（用于 transcript 加载）。"""
        transcript_items: list[TranscriptItem] = []
        for item_data in data.get("transcript_items", []):
            item_type = item_data.get("__type__") or item_data.get("type")
            if item_type == "SystemInstruction":
                transcript_items.append(SystemInstruction.from_dict(item_data))
            elif item_type == "UserMessage":
                transcript_items.append(UserMessage.from_dict(item_data))
            elif item_type == "AssistantMessage":
                transcript_items.append(AssistantMessage.from_dict(item_data))
            elif item_type == "ToolCall":
                transcript_items.append(ToolCall.from_dict(item_data))
            elif item_type == "ToolResult":
                transcript_items.append(ToolResult.from_dict(item_data))
            elif item_type == "ControlEvent":
                transcript_items.append(ControlEvent.from_dict(item_data))
            elif item_type == "ReasoningSummary":
                transcript_items.append(ReasoningSummary.from_dict(item_data))
            elif "tool_name" in item_data and "call_id" in item_data:
                if "status" in item_data or "content" in item_data:
                    transcript_items.append(ToolResult.from_dict(item_data))
                else:
                    transcript_items.append(ToolCall.from_dict(item_data))
            elif "event_type" in item_data:
                transcript_items.append(ControlEvent.from_dict(item_data))
            elif "thinking" in item_data:
                transcript_items.append(AssistantMessage.from_dict(item_data))
            elif item_type in {"system_instruction", "system"}:
                transcript_items.append(SystemInstruction.from_dict(item_data))
            elif item_type in {"user", "user_message"}:
                transcript_items.append(UserMessage.from_dict(item_data))

        tool_calls = [ToolCall.from_dict(tc) for tc in data.get("tool_calls", []) if isinstance(tc, dict)]

        return cls(
            transcript_items=transcript_items,
            tool_calls=tool_calls,
        )

    def merge(self, other: TranscriptDelta) -> TranscriptDelta:
        """合并另一个 delta（用于追加 transcript）。"""
        return TranscriptDelta(
            transcript_items=self.transcript_items + other.transcript_items,
            tool_calls=self.tool_calls + other.tool_calls,
        )


# ---------------------------------------------------------------------------
# 工厂方法（独立函数形式，供 caller 使用）
# ---------------------------------------------------------------------------


def from_assistant_message(
    text: str,
    *,
    thinking: str | None = None,
    native_tool_calls: list[ToolCallResult] | None = None,
    provider: str | None = None,
    provider_meta: dict[str, Any] | None = None,
) -> TranscriptDelta:
    """从 LLM assistant 消息构造 TranscriptDelta。"""
    items: list[TranscriptItem] = []
    tool_calls: list[ToolCall] = []

    summary = ReasoningSummary.from_assistant_thinking(thinking)
    if summary:
        items.append(summary)

    if native_tool_calls:
        for result in native_tool_calls:
            tc = ToolCall.from_output_parser_result(
                result,
                provider=provider,
                provider_meta=provider_meta,
            )
            tool_calls.append(tc)
            items.append(tc)

    return TranscriptDelta(transcript_items=items, tool_calls=tool_calls)


def from_tool_result(
    tool_call: TranscriptToolCall,
    result_dict: dict[str, Any],
) -> TranscriptToolResult:
    """从 TranscriptToolCall 和原始结果字典构造 TranscriptToolResult。"""
    return TranscriptToolResult.from_tool_call_and_result_dict(tool_call, result_dict)


def from_control_event(
    event_type: ControlEventType,
    *,
    reason: str | None = None,
    approval_required: bool = False,
    budget_hit: bool = False,
    compacted: bool = False,
    handoff_target: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> ControlEvent:
    """构造 ControlEvent。"""
    return ControlEvent(
        event_type=event_type,
        reason=reason,
        approval_required=approval_required,
        budget_hit=budget_hit,
        compacted=compacted,
        handoff_target=handoff_target,
        metadata=metadata or {},
    )


# ---------------------------------------------------------------------------
# 公开导出
# ---------------------------------------------------------------------------

__all__ = [
    "AssistantMessage",
    "CanonicalToolCallEntry",
    "ControlEvent",
    "ControlEventType",
    "ParsedToolPlan",
    "ReasoningSummary",
    # 新增类型（Chief Architect 裁决 #2）
    "SanitizedOutput",
    # 条目类型
    "SystemInstruction",
    # P1-TYPE-003/004: Renamed to Transcript* to avoid conflicts
    "ToolCall",  # Backward compat alias for TranscriptToolCall
    "ToolResult",  # Backward compat alias for TranscriptToolResult
    # Literal 约束
    "ToolResultStatus",
    "TranscriptAppendRequest",
    "TranscriptDelta",
    # 联合类型
    "TranscriptItem",
    "TranscriptToolCall",
    "TranscriptToolResult",
    "UserMessage",
    # 工厂函数
    "from_assistant_message",
    "from_control_event",
    "from_tool_result",
]
