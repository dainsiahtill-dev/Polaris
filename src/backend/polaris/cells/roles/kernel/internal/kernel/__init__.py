"""Kernel Module - 角色执行内核模块

重构后的模块化架构，将 RoleExecutionKernel 拆分为多个组件：

- core.py: RoleExecutionKernel 核心类
- helpers.py: 辅助函数和常量
- suggestions.py: 错误建议提供者
- error_handler.py: 事件发射和观察值规范化
- tool_executor.py: 工具执行器
"""

from __future__ import annotations

from polaris.cells.roles.kernel.internal.kernel.core import (
    RoleExecutionKernel,
    get_suggestions_for_error,
)
from polaris.cells.roles.kernel.internal.kernel.error_handler import (
    KernelEventEmitter,
    LLMEventType,
    ObserverValueNormalizer,
    get_event_emitter,
    get_normalizer,
    normalize_observer_value,
)
from polaris.cells.roles.kernel.internal.kernel.helpers import (
    _DEFAULT_ROLE_WRITE_CALL_LIMITS,
    build_stream_event_message,
    build_text_preview,
    extract_structured_tool_calls,
    make_json_safe,
    normalize_tool_args,
    quality_result_to_dict,
    resolve_role_write_call_limit,
    summarize_args,
)
from polaris.cells.roles.kernel.internal.kernel.suggestions import (
    ErrorSuggestionProvider,
    get_suggestion_provider,
)
from polaris.cells.roles.kernel.internal.kernel.tool_executor import KernelToolExecutor
from polaris.cells.roles.kernel.internal.transaction.constants import WRITE_TOOLS

# 向后兼容：_WRITE_TOOL_NAMES 重定向到统一的 WRITE_TOOLS
_WRITE_TOOL_NAMES: frozenset[str] = WRITE_TOOLS

__all__ = [
    "_DEFAULT_ROLE_WRITE_CALL_LIMITS",
    "_WRITE_TOOL_NAMES",
    # Suggestions
    "ErrorSuggestionProvider",
    # Error Handler
    "KernelEventEmitter",
    # Tool Executor
    "KernelToolExecutor",
    "LLMEventType",
    "ObserverValueNormalizer",
    # Core
    "RoleExecutionKernel",
    "build_stream_event_message",
    "build_text_preview",
    "extract_structured_tool_calls",
    "get_event_emitter",
    "get_normalizer",
    "get_suggestion_provider",
    "get_suggestions_for_error",
    "make_json_safe",
    "normalize_observer_value",
    "normalize_tool_args",
    "quality_result_to_dict",
    "resolve_role_write_call_limit",
    "summarize_args",
]
