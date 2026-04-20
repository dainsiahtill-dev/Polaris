"""Compatibility re-export shim — roles.kernel.internal.transcript_ir

# -*- coding: utf-8 -*-
UTF-8 编码验证: 本文所有文本使用 UTF-8

本文件是 Chief Architect 裁决 #3 迁移的一部分。

本文件不再包含实际类型定义（已迁移至 public.transcript_ir）。
此处保留为短期兼容重导出，防止未修改完毕的内部引用在迁移期间断裂。

迁移进度:
- [x] public/transcript_ir.py 已创建（完整定义 + 新增类型）
- [x] public/__init__.py 已更新（导出所有类型）
- [x] 本文件已替换为重导出 shim
- [ ] turn_engine.py       -> public.transcript_ir
- [ ] conversation_state.py -> public.transcript_ir
- [ ] kernel_bridge.py     -> public.transcript_ir
- [ ] test_transcript_ir.py -> public.transcript_ir
- [ ] test_transcript_leak_guard.py -> public.transcript_ir

内部文件更新完毕后，可安全删除本文件。
"""

from __future__ import annotations

# Re-export all public types — internal code should migrate to public.transcript_ir
from polaris.cells.roles.kernel.public.transcript_ir import (
    AssistantMessage,
    CanonicalToolCallEntry,
    ControlEvent,
    ControlEventType,
    ParsedToolPlan,
    ReasoningSummary,
    SanitizedOutput,
    SystemInstruction,
    ToolCall,
    ToolResult,
    ToolResultStatus,
    TranscriptAppendRequest,
    TranscriptDelta,
    TranscriptItem,
    UserMessage,
    from_assistant_message,
    from_control_event,
    from_tool_result,
)

__all__ = [
    "AssistantMessage",
    "CanonicalToolCallEntry",
    "ControlEvent",
    "ControlEventType",
    "ParsedToolPlan",
    "ReasoningSummary",
    "SanitizedOutput",
    "SystemInstruction",
    "ToolCall",
    "ToolResult",
    "ToolResultStatus",
    "TranscriptAppendRequest",
    "TranscriptDelta",
    "TranscriptItem",
    "UserMessage",
    "from_assistant_message",
    "from_control_event",
    "from_tool_result",
]
