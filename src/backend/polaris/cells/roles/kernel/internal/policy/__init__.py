"""Policy Layer - 确定性策略层

# -*- coding: utf-8 -*-
UTF-8 编码验证: 本文所有文本使用 UTF-8

Blueprint: §11 Policy Layer
Policy 必须在模型外部的确定性层，不得塞进 prompt patching。

目录结构
───────────
policy/
├── __init__.py         - 本文件，Policy Layer 入口与公共导出
├── tool_policy.py      - ToolPolicy（工具权限评估）
├── approval_policy.py  - ApprovalPolicy（人工审批闸门）
├── budget_policy.py    - BudgetPolicy（资源预算硬限制）
├── sandbox_policy.py   - SandboxPolicy（进程/FS/网络范围）
└── redaction_policy.py - RedactionPolicy（日志/prompt 脱敏）

核心约束
───────────
1. BudgetState 必须在外部确定性层，不得进模型推理。
2. 所有策略决定（allowed/denied/requires_approval）必须是纯函数，无副作用。
3. 不得在策略层直接修改状态；只能读取并返回决策。
4. UTF-8 是硬约束，所有文本操作必须显式指定 encoding="utf-8"。

与现有代码的关系
─────────────────
- RoleToolGateway (tool_gateway.py): 已实现工具权限检查 → Policy Layer 将其吸收为 ToolPolicy.evaluate()
- RoleToolPolicy (profile/schema.py): 角色配置级白名单/黑名单 → ToolPolicy 使用它作为基准配置
- ToolLoopSafetyPolicy (tool_loop_controller.py): 循环安全策略 → BudgetPolicy 吸收其 max_* 字段
- TokenBudget (token_budget.py): token 预算分配 → BudgetPolicy 整合 token 维度
- ConstitutionRules (constitution_rules.py): 宪法级禁止规则 → ApprovalPolicy 读取并强制执行
- RoleTurnResult.needs_confirmation: 已有字段 → ApprovalPolicy.evaluate() 写入此字段

与 Task #3 (TurnEngine) 的契约
──────────────────────────────────
Task #3 完成后，ConversationState 将被具体化为 RoleTurnRequest + RuntimeState。
本层的所有 evaluate() / filter() / requires_approval() 方法签名在 Task #3 完成后
需按实际 ConversationState 实现替换占位符。
"""

from __future__ import annotations

from polaris.cells.roles.kernel.internal.policy.approval_policy import (
    ApprovalPolicy,
    ApprovalRequirement,
)
from polaris.cells.roles.kernel.internal.policy.budget_policy import (
    BudgetDecision,
    BudgetPolicy,
    BudgetState,
)
from polaris.cells.roles.kernel.internal.policy.layer import (
    CanonicalToolCall,
    ExplorationToolPolicy,
    PolicyLayer,
    PolicyResult,
    PolicyViolation,
)
from polaris.cells.roles.kernel.internal.policy.redaction_policy import (
    RedactionPolicy,
)
from polaris.cells.roles.kernel.internal.policy.sandbox_policy import (
    SandboxDecision,
    SandboxPolicy,
)
from polaris.cells.roles.kernel.internal.policy.tool_policy import (
    ToolPolicy,
    ToolPolicyDecision,
)

__all__ = [
    # Approval
    "ApprovalPolicy",
    "ApprovalRequirement",
    "BudgetDecision",
    # Budget
    "BudgetPolicy",
    "BudgetState",
    # Layer
    "CanonicalToolCall",
    # Exploration
    "ExplorationToolPolicy",
    "PolicyLayer",
    "PolicyResult",
    "PolicyViolation",
    # Redaction
    "RedactionPolicy",
    "SandboxDecision",
    # Sandbox
    "SandboxPolicy",
    # Tool
    "ToolPolicy",
    "ToolPolicyDecision",
]
