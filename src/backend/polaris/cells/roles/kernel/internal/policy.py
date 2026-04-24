"""Policy Layer - 确定性策略执行层（重新导出模块）。

# -*- coding: utf-8 -*-
UTF-8 编码验证: 本文所有文本使用 UTF-8

Blueprint: §11 Policy Layer

本文件是向后兼容的重导出模块，所有实现已迁移到 layer/ 子模块。

迁移说明：
    - 所有策略类已拆分到 layer/ 目录下的独立模块
    - layer/__init__.py 是新的权威导出点
    - 本文件保持向后兼容，重新导出所有 layer/__init__.py 的内容

职责：
    policy 必须放在模型外部的确定性层。所有工具调用、预算检查、
    审批请求、沙箱约束在进入模型之前由 PolicyLayer 评估。

设计原则：
    1. 确定性：PolicyLayer 不做任何模型推理，所有判断都是确定性的。
    2. 分层：每个策略独立评估，最后汇总。
    3. 非阻塞：策略拦截产生 blocked_calls，不是直接崩溃。
    4. 累积：PolicyResult 可以跨 turn 累积，用于 budget tracking。

子策略：
    1. ToolPolicy           — 工具权限（基于 RoleToolGateway 逻辑）
    2. BudgetPolicy         — 预算执行（基于 ConversationState.Budgets）
    3. ExplorationToolPolicy — 探索工具冷却机制（新增）
    4. ApprovalPolicy       — 人工审批队列
    5. SandboxPolicy        — 沙箱约束（路径穿越、危险命令）
    6. RedactionPolicy      — 脱敏（日志、trace 中的敏感字段）

架构优化说明（2026-04-04）：
    - 消除 PolicyLayer 重复定义（policy.py vs layer/facade.py）
    - policy.py → 重新导出 layer/__init__.py
    - layer/facade.py 是权威实现（包含 ExplorationToolPolicy）
"""

from __future__ import annotations

# 从 layer/__init__.py 重新导出所有内容
from polaris.cells.roles.kernel.internal.policy.layer import (
    _DEFAULT_PATTERNS,
    EXPLORATION_TOOL_CATEGORIES,
    TOOL_CATEGORIES,
    ApprovalPolicy,
    BudgetPolicy,
    BudgetPolicyConfig,
    CanonicalToolCall,
    EvaluationResult,
    ExplorationToolPolicy,
    PolicyLayer,
    PolicyResult,
    PolicyViolation,
    RedactionPolicy,
    SandboxPolicy,
    ToolPolicy,
    ToolPolicyConfig,
)

__all__ = [
    "EXPLORATION_TOOL_CATEGORIES",
    "TOOL_CATEGORIES",
    "_DEFAULT_PATTERNS",
    # isort: alphabetically
    "ApprovalPolicy",
    "BudgetPolicy",
    "BudgetPolicyConfig",
    "CanonicalToolCall",
    "EvaluationResult",
    "ExplorationToolPolicy",
    "PolicyLayer",
    "PolicyResult",
    "PolicyViolation",
    "RedactionPolicy",
    "SandboxPolicy",
    "ToolPolicy",
    "ToolPolicyConfig",
]
