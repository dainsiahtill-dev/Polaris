"""Policy Layer - 子模块导出聚合。

# -*- coding: utf-8 -*-
UTF-8 编码验证: 本文所有文本使用 UTF-8

Blueprint: §11 Policy Layer - 模块拆分

目录结构:
    layer/
    ├── __init__.py    - 本文件
    ├── core.py        - CanonicalToolCall, PolicyViolation, PolicyResult
    ├── budget.py      - BudgetPolicy
    ├── tool.py        - ToolPolicy
    ├── approval.py    - ApprovalPolicy
    ├── sandbox.py     - SandboxPolicy
    ├── exploration.py - ExplorationToolPolicy
    ├── redaction.py   - RedactionPolicy
    └── facade.py      - PolicyLayer (统一策略层)
"""

from __future__ import annotations

from polaris.kernelone.tool_execution.tool_categories import TOOL_CATEGORIES

from .approval import ApprovalPolicy

# Policy implementations
from .budget import BudgetPolicy, BudgetPolicyConfig

# Core dataclasses
from .core import (
    CanonicalToolCall,
    EvaluationResult,
    PolicyResult,
    PolicyViolation,
)
from .exploration import EXPLORATION_TOOL_CATEGORIES, ExplorationToolPolicy

# Facade (PolicyLayer)
from .facade import PolicyLayer
from .redaction import _DEFAULT_PATTERNS, RedactionPolicy
from .sandbox import SandboxPolicy
from .tool import ToolPolicy, ToolPolicyConfig

__all__ = [
    "EXPLORATION_TOOL_CATEGORIES",
    # Constants
    "TOOL_CATEGORIES",
    "_DEFAULT_PATTERNS",
    "ApprovalPolicy",
    # Policies
    "BudgetPolicy",
    "BudgetPolicyConfig",
    # Core
    "CanonicalToolCall",
    "EvaluationResult",
    "ExplorationToolPolicy",
    # Facade
    "PolicyLayer",
    "PolicyResult",
    "PolicyViolation",
    "RedactionPolicy",
    "SandboxPolicy",
    "ToolPolicy",
    "ToolPolicyConfig",
]
