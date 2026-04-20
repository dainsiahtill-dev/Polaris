"""Role Execution Kernel - Facade

统一执行 chat/workflow 两种模式的角色对话。

重构说明（2026-03-31）：
- 原 kernel.py (1761行) 已拆分为模块化架构
- 本文件保留为导入重定向 Facade，确保向后兼容
- 具体实现详见 kernel/ 子目录：
  - kernel/core.py: RoleExecutionKernel 核心类
  - kernel/helpers.py: 辅助函数和常量
  - kernel/suggestions.py: 错误建议提供者
  - kernel/error_handler.py: 事件发射和观察值规范化

导入路径保持不变：
    from polaris.cells.roles.kernel.internal.kernel import RoleExecutionKernel
"""

from __future__ import annotations

# 从模块化架构导入所有组件
from polaris.cells.roles.kernel.internal.kernel import (
    # Helpers
    _DEFAULT_ROLE_WRITE_CALL_LIMITS,
    # Suggestions
    ErrorSuggestionProvider,
    # Error Handler
    KernelEventEmitter,
    LLMEventType,
    ObserverValueNormalizer,
    # Core
    RoleExecutionKernel,
    build_stream_event_message,
    build_text_preview,
    extract_structured_tool_calls,
    get_event_emitter,
    get_normalizer,
    get_suggestion_provider,
    get_suggestions_for_error,
    make_json_safe,
    normalize_observer_value,
    normalize_tool_args,
    quality_result_to_dict,
    resolve_role_write_call_limit,
    summarize_args,
)
from polaris.cells.roles.kernel.internal.transaction.constants import WRITE_TOOLS

# 向后兼容：_WRITE_TOOL_NAMES 重定向到统一的 WRITE_TOOLS
_WRITE_TOOL_NAMES: frozenset[str] = WRITE_TOOLS

# 保持向后兼容的导出
__all__ = [
    "_DEFAULT_ROLE_WRITE_CALL_LIMITS",
    # Helpers (辅助函数)
    "_WRITE_TOOL_NAMES",
    # Suggestions (错误建议)
    "ErrorSuggestionProvider",
    # Error Handler (事件发射)
    "KernelEventEmitter",
    "LLMEventType",
    "ObserverValueNormalizer",
    # Core class
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
