"""
Transaction Kernel 子模块 — 事务化 Turn 执行的核心组件。

本包将 TurnTransactionController 的 monolithic 实现拆分为职责单一、
高内聚、低耦合的协作模块。原文件保留为 Facade，所有公共 API 不变。

模块清单:
- ledger: Turn 级审计账本与配置
- constants: 工具白名单、拒绝标记等模块级常量
- intent_classifier: 用户请求意图检测（mutation / verification / analysis）
- contract_guards: 突变合约守卫 — 验证 write tool 与目标文件的一致性
- task_contract_builder: 单批次任务契约提示构建
- tool_batch_executor: 工具批次执行（含 speculative ADOPT/JOIN）
- finalization: LLM_ONCE / NONE / LOCAL 收口策略
- handoff_handlers: WORKFLOW / DEVELOPMENT 移交处理
- retry_orchestrator: 突变合约违反后的重试编排
- stream_orchestrator: 流式决策与流式 Turn 执行

架构约束:
1. 各模块之间禁止循环导入。
2. 常量全部沉淀到 constants.py，业务模块只导入常量。
3. 所有公共类/函数保留类型注解，通过 mypy --strict。
"""

from __future__ import annotations

# Re-export 核心公共类型，保持兼容
from polaris.cells.roles.kernel.internal.transaction.constants import (
    ANALYSIS_ONLY_SIGNALS,
    ASYNC_TOOLS,
    DEBUG_AND_FIX_CN_MARKERS,
    DEBUG_AND_FIX_EN_MARKERS,
    DEVOPS_CONFIG_SIGNALS,
    INTENT_MARKERS_REGISTRY,
    PLANNING_SIGNALS,
    READ_TOOLS,
    REFUSAL_MARKERS,
    REQUIRED_TOOL_EQUIVALENTS,
    SAFE_READ_BOOTSTRAP_TOOLS,
    STRONG_MUTATION_CN_MARKERS,
    STRONG_MUTATION_EN_MARKERS,
    TESTING_SIGNALS,
    TOOL_ALIASES,
    VERIFICATION_TOOLS,
    WEAK_MUTATION_CN_MARKERS,
    WEAK_MUTATION_EN_MARKERS,
    WRITE_TOOLS,
)

# contract_guards / task_contract_builder 的公共工具函数
from polaris.cells.roles.kernel.internal.transaction.contract_guards import (
    extract_invocation_tool_name,
    extract_target_file_from_invocation_args,
    has_available_write_tool,
    is_safe_read_bootstrap_tool_name,
    is_write_invocation,
    receipts_have_stale_edit_failure,
    resolve_mutation_target_guard_violation,
    rollback_state_after_retry_batch_failure,
    tool_batch_has_write_invocation,
)
from polaris.cells.roles.kernel.internal.transaction.delivery_contract import (
    BlockedReason,
    DeliveryContract,
    DeliveryMode,
    MutationObligationState,
)
from polaris.cells.roles.kernel.internal.transaction.intent_classifier import (
    classify_intent_regex,
    requires_mutation_intent,
    requires_verification_intent,
    resolve_delivery_mode,
)
from polaris.cells.roles.kernel.internal.transaction.ledger import (
    TransactionConfig,
    TurnLedger,
    VisibleOutput,
)
from polaris.cells.roles.kernel.internal.transaction.receipt_utils import record_receipts_to_ledger
from polaris.cells.roles.kernel.internal.transaction.retry_orchestrator import RetryOrchestrator
from polaris.cells.roles.kernel.internal.transaction.stream_orchestrator import StreamOrchestrator
from polaris.cells.roles.kernel.internal.transaction.task_contract_builder import (
    build_single_batch_task_contract_hint,
    extract_allowed_tool_names_from_definitions,
    extract_latest_user_message,
    extract_tool_name_from_definition,
)
from polaris.cells.roles.kernel.internal.transaction.tool_batch_executor import ToolBatchExecutor

__all__ = [
    "ANALYSIS_ONLY_SIGNALS",
    "ASYNC_TOOLS",
    "DEBUG_AND_FIX_CN_MARKERS",
    "DEBUG_AND_FIX_EN_MARKERS",
    "DEVOPS_CONFIG_SIGNALS",
    "INTENT_MARKERS_REGISTRY",
    "PLANNING_SIGNALS",
    "READ_TOOLS",
    "REFUSAL_MARKERS",
    "REQUIRED_TOOL_EQUIVALENTS",
    "SAFE_READ_BOOTSTRAP_TOOLS",
    "STRONG_MUTATION_CN_MARKERS",
    "STRONG_MUTATION_EN_MARKERS",
    "TESTING_SIGNALS",
    "TOOL_ALIASES",
    "VERIFICATION_TOOLS",
    "WEAK_MUTATION_CN_MARKERS",
    "WEAK_MUTATION_EN_MARKERS",
    "WRITE_TOOLS",
    "BlockedReason",
    "DeliveryContract",
    "DeliveryMode",
    "MutationObligationState",
    "RetryOrchestrator",
    "StreamOrchestrator",
    "ToolBatchExecutor",
    "TransactionConfig",
    "TurnLedger",
    "VisibleOutput",
    "build_single_batch_task_contract_hint",
    "classify_intent_regex",
    "extract_allowed_tool_names_from_definitions",
    "extract_invocation_tool_name",
    "extract_latest_user_message",
    "extract_target_file_from_invocation_args",
    "extract_tool_name_from_definition",
    "has_available_write_tool",
    "is_safe_read_bootstrap_tool_name",
    "is_write_invocation",
    "receipts_have_stale_edit_failure",
    "record_receipts_to_ledger",
    "requires_mutation_intent",
    "requires_verification_intent",
    "resolve_delivery_mode",
    "resolve_mutation_target_guard_violation",
    "rollback_state_after_retry_batch_failure",
    "tool_batch_has_write_invocation",
]
