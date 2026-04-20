"""Policy Layer - 确定性策略执行层 (Facade)

# -*- coding: utf-8 -*-
UTF-8 编码验证: 本文所有文本使用 UTF-8

Blueprint: §11 Policy Layer

Phase 3 状态: 实现 PolicyLayer 及其五个子策略。

本文件是导入重定向 Facade，所有实现已拆分到 layer/ 子模块。

目录结构:
    layer/
    ├── __init__.py    - 子模块导出聚合
    ├── core.py        - CanonicalToolCall, PolicyViolation, PolicyResult
    ├── budget.py      - BudgetPolicy
    ├── tool.py        - ToolPolicy
    ├── approval.py    - ApprovalPolicy
    ├── sandbox.py     - SandboxPolicy
    ├── exploration.py - ExplorationToolPolicy
    ├── redaction.py   - RedactionPolicy
    └── facade.py      - PolicyLayer (统一策略层)

迁移说明:
    - 所有核心实现已迁移到 layer/ 子模块
    - 本文件保留为向后兼容的导入 Facade
    - 新代码应直接导入 from polaris.cells.roles.kernel.internal.policy.layer import ...
"""

from __future__ import annotations

# 从子模块重新导出所有公开类（使用相对导入避免触发顶层包的循环导入）
from .layer import (
    _DEFAULT_PATTERNS,
    EXPLORATION_TOOL_CATEGORIES,
    TOOL_CATEGORIES,
    ApprovalPolicy,
    BudgetPolicy,
    CanonicalToolCall,
    EvaluationResult,
    ExplorationToolPolicy,
    PolicyLayer,
    PolicyResult,
    PolicyViolation,
    RedactionPolicy,
    SandboxPolicy,
    ToolPolicy,
)

__all__ = [
    "EXPLORATION_TOOL_CATEGORIES",
    # Constants
    "TOOL_CATEGORIES",
    "_DEFAULT_PATTERNS",
    "ApprovalPolicy",
    "BudgetPolicy",
    "CanonicalToolCall",
    "EvaluationResult",
    "ExplorationToolPolicy",
    "PolicyLayer",
    "PolicyResult",
    "PolicyViolation",
    "RedactionPolicy",
    "SandboxPolicy",
    "ToolPolicy",
]
