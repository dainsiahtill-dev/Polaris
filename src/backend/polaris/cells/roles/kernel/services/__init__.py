"""Kernel Services - 内核服务层

提供 RoleExecutionKernel 依赖的服务层组件.

该层遵循 Facade 模式, 将内核功能分解为可独立注入的服务:
- llm_invoker: LLM调用服务
- tool_executor: 工具执行服务
- prompt_builder: 提示词构建服务
- output_parser: 输出解析服务
- quality_checker: 质量检查服务
- event_emitter: 事件发射服务
"""

from __future__ import annotations

# Protocols - P0-NEW-017: Unified interface naming
from polaris.cells.roles.kernel.services.contracts import (
    CellToolExecutorPort,
    IEventEmitter,
    ILLMInvoker,
    IOutputParser,
    IPromptBuilder,
    IQualityChecker,
    IToolExecutor,
    RoleInvokeResult,
    StreamEvent,
    StructuredResult,
)

__all__ = [
    "CellToolExecutorPort",
    "IEventEmitter",
    # Protocols
    "ILLMInvoker",
    "IOutputParser",
    "IPromptBuilder",
    "IQualityChecker",
    "IToolExecutor",
    # Result types
    "RoleInvokeResult",
    "StreamEvent",
    "StructuredResult",
]
