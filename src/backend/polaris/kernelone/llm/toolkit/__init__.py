"""LLM Toolkit - KernelOne 平台级 LLM 工具系统

【K1-PURIFY Phase 2 - KernelOne 纯化后版本】
本模块现在只包含平台无关的能力，不再包含 Polaris 业务角色语义。
角色工具集成已迁移至: polaris.cells.llm.tool_runtime.internal.role_integrations

提供三种 LLM 工具调用方案：
1. Prompt-based: 通过 [TOOL_NAME]...[/TOOL_NAME] 标记解析
2. Tool Chain: 集成现有 Tool Chain 系统
3. Native Function Calling: 原生 OpenAI/Anthropic 工具调用

示例:
    # 基础使用 - 执行工具
    from polaris.kernelone.llm.toolkit import AgentAccelToolExecutor

    executor = AgentAccelToolExecutor(workspace=".")
    result = executor.execute("search_code", {
        "query": "class User",
        "max_results": 10
    })

    # 角色集成 - 请使用 Cell 层
    from polaris.cells.llm.tool_runtime.internal.role_integrations import (
        ROLE_TOOL_INTEGRATIONS,
        get_role_tool_integration,
    )
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

# 审计系统
from .audit import (
    AuditEvent,
    AuditEventType,
    AuditLogger,
    OperationAuditTrail,
    SessionAuditLog,
    get_audit_logger,
    set_audit_logger,
)

# ═══════════════════════════════════════════════════════════════════
# 平台级公共导出（无 Polaris 角色语义）
# ═══════════════════════════════════════════════════════════════════
# 基础契约 (core层，避免循环依赖)
from .contracts import (
    AIError,
    AIRequest,
    AIResponse,
    CompressionResult,
    ErrorCategory,
    ModelSpec,
    ProviderPort,
    ServiceLocator,
    StreamChunk,
    TaskType,
    TokenBudgetDecision,
    TokenEstimatorPort,
    Usage,
)

# 工具定义
# DEPRECATED (2026-04-05): These exports come from definitions.py which re-exports
# data from polaris.kernelone.tool_execution.contracts._TOOL_SPECS.
# Migration: Use create_default_registry() or access _TOOL_SPECS directly.
# NOTE: STANDARD_TOOLS has been removed. Use _TOOL_SPECS instead.
from .definitions import (
    ToolDefinition,
    ToolParameter,
    ToolRegistry,
    create_default_registry,
)

# 执行器
from .executor import (
    AgentAccelToolExecutor,
    KernelToolCallingRuntime,
    build_tool_feedback,
    execute_tool_call,
    execute_tool_calls,
)

# 原生 Function Calling
from .native_function_calling import (
    ConversationalToolExecutor,
    NativeFunctionCallingHandler,
    ToolCall,
    ToolEnabledAIRequest,
    ToolEnabledAIResponse,
    ToolEnabledProviderMixin,
    ToolResult,
    create_tool_request,
    execute_with_native_function_calling,
)

# 解析器 - 统一工具调用解析入口
# P0-002: CanonicalToolCall deprecated, use ToolCall from contracts.tool
from .parsers import (
    CANONICAL_ARGUMENT_KEYS,
    CanonicalToolCallParser,  # 统一解析器 (returns list[ToolCall])
    NativeFunctionCallingParser,
    ParsedToolCall,  # Alias to ToolCall
    XMLToolParser,  # XML 格式解析器 (MiniMax/Claude/Llama)
    deduplicate_tool_calls,
    extract_arguments,
    extract_tool_calls_and_remainder,
    format_tool_result,
    has_tool_calls,
    parse_tool_calls,
    parse_value,
)

# 统一协议内核 (v2.0 - Strict Mode)
from .protocol_kernel import (
    ApplyReport,
    EditType,
    # 枚举
    ErrorCode,
    # 数据类
    FileOperation,
    OperationResult,
    OperationValidator,
    # 工具类
    ProtocolParser,
    StrictOperationApplier,
    ValidationResult,
    apply_operations,
    apply_protocol_output,
    # 核心函数
    parse_protocol_output,
    validate_operations,
)

# 工具链适配
from .tool_chain_adapter import (
    AgentAccelToolChainAdapter,
    AgentAccelToolChainExecutor,
    AgentAccelToolChainPlan,
    create_tool_chain_prompt,
    execute_tool_chain,
    integrate_with_director_tools_v2,
)

__version__ = "2.1.0"  # K1-PURIFY Phase 2

__all__ = [
    "CANONICAL_ARGUMENT_KEYS",  # 规范参数键
    # ═══════════════════════════════════════════════════════════════
    # 工具定义
    # ═══════════════════════════════════════════════════════════════
    "AIError",
    # ═══════════════════════════════════════════════════════════════
    # 基础契约 (core层，避免循环依赖)
    # ═══════════════════════════════════════════════════════════════
    "AIRequest",
    "AIResponse",
    # ═══════════════════════════════════════════════════════════════
    # 工具链适配（与 Director v2 集成）
    # ═══════════════════════════════════════════════════════════════
    "AgentAccelToolChainAdapter",
    "AgentAccelToolChainExecutor",
    "AgentAccelToolChainPlan",
    # ═══════════════════════════════════════════════════════════════
    # 执行器
    # ═══════════════════════════════════════════════════════════════
    "AgentAccelToolExecutor",  # 统一工具执行器
    "ApplyReport",
    # ═══════════════════════════════════════════════════════════════
    # 审计系统
    # ═══════════════════════════════════════════════════════════════
    "AuditEvent",
    "AuditEventType",
    "AuditLogger",
    # P0-002: CanonicalToolCall deprecated, use ToolCall directly
    # 统一解析器 (新增 2026-03-28)
    "CanonicalToolCallParser",  # 统一解析器入口 (returns list[ToolCall])
    "CompressionResult",
    "ConversationalToolExecutor",
    "deduplicate_tool_calls",  # P0-002: 新增导出
    "EditType",
    "ErrorCategory",
    # ═══════════════════════════════════════════════════════════════
    # 统一协议内核 (v2.0 - Strict Mode)
    # ═══════════════════════════════════════════════════════════════
    # 枚举
    "ErrorCode",
    # 数据类
    "FileOperation",
    "KernelToolCallingRuntime",  # 工具调用运行时
    "ModelSpec",
    "NativeFunctionCallingHandler",
    # 专用解析器
    "NativeFunctionCallingParser",  # OpenAI/Anthropic/Gemini/Ollama/DeepSeek 原生格式
    "OperationAuditTrail",
    "OperationResult",
    "OperationValidator",
    # ═══════════════════════════════════════════════════════════════
    # 解析器 - 统一解析入口
    # ═══════════════════════════════════════════════════════════════
    "ParsedToolCall",
    # 工具类
    "ProtocolParser",
    "ProviderPort",
    "ServiceLocator",
    "SessionAuditLog",
    "StreamChunk",
    "StrictOperationApplier",
    "TaskType",
    "TokenBudgetDecision",
    "TokenEstimatorPort",
    # ═══════════════════════════════════════════════════════════════
    # 原生 Function Calling
    # ═══════════════════════════════════════════════════════════════
    "ToolCall",
    "ToolDefinition",
    "ToolEnabledAIRequest",
    "ToolEnabledAIResponse",
    "ToolEnabledProviderMixin",
    "ToolParameter",
    "ToolRegistry",
    "ToolResult",
    "Usage",
    "ValidationResult",
    "XMLToolParser",  # XML 格式 (MiniMax/Claude/Llama)
    # 版本
    "__version__",
    "apply_operations",
    "apply_protocol_output",
    "build_tool_feedback",  # 构建工具反馈
    "create_default_registry",
    "create_tool_chain_prompt",
    "create_tool_request",
    "execute_tool_call",
    "execute_tool_calls",
    "execute_tool_chain",
    "execute_with_native_function_calling",
    "extract_arguments",  # 参数提取
    "extract_tool_calls_and_remainder",
    "format_tool_result",
    "get_audit_logger",
    "has_tool_calls",
    "integrate_with_director_tools_v2",
    # 核心函数
    "parse_protocol_output",
    "parse_tool_calls",  # 统一解析函数
    "set_audit_logger",
    "validate_operations",
]


# ═══════════════════════════════════════════════════════════════════
# 【已迁移】Polaris 角色语义 - 请使用 Cell 层
# ═══════════════════════════════════════════════════════════════════
# 以下导出已迁移至: polaris.cells.llm.tool_runtime.internal.role_integrations
#
# 迁移指南:
#   from polaris.kernelone.llm.toolkit import (
#       PMToolIntegration,
#       ROLE_TOOL_INTEGRATIONS,
#       get_role_tool_integration,
#   )
#
#   改为:
#   from polaris.cells.llm.tool_runtime.internal.role_integrations import (
#       PMToolIntegration,
#       ROLE_TOOL_INTEGRATIONS,
#       get_role_tool_integration,
#   )
# ═══════════════════════════════════════════════════════════════════

# 【K1-PURIFY Phase 2 Note】
# 已移除 __getattr__ deprecation shim 以避免违反 KernelOne import fence。
# 现有调用方应迁移到 Cell 层导入路径。
# 旧代码: from polaris.kernelone.llm.toolkit import PMToolIntegration
# 新代码: from polaris.cells.llm.tool_runtime.internal.role_integrations import PMToolIntegration


# ═══════════════════════════════════════════════════════════════════
# 运行时防重复检查
# ═══════════════════════════════════════════════════════════════════


def _check_duplicate_modules() -> None:
    """检查是否有重复的工具模块被加载"""
    import logging
    import sys

    forbidden_modules = [
        "app.llm.usecases.role_tools",  # 之前创建的重复模块
    ]

    strict = str(
        os.environ.get("KERNELONE_TOOLKIT_STRICT_IMPORTS") or os.environ.get("POLARIS_TOOLKIT_STRICT_IMPORTS", "0")
    ).strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    logger = logging.getLogger(__name__)

    for module in forbidden_modules:
        if module in sys.modules:
            message = f"检测到重复工具模块 '{module}' 被加载，请优先使用 llm_toolkit。"
            if strict:
                raise RuntimeError(message)
            logger.warning(message)


_duplicate_module_check_done = False


def _check_duplicate_modules_once() -> None:
    """Run duplicate-module check once on first use."""
    global _duplicate_module_check_done
    if _duplicate_module_check_done:
        return
    _check_duplicate_modules()
    _duplicate_module_check_done = True
