"""ConversationState - 对话状态接口

# -*- coding: utf-8 -*-
UTF-8 编码验证: 本文所有文本使用 UTF-8

本文件是 Task #3 (TurnEngine) 完成前的占位符接口定义。
Task #3 完成后，本文件应被替换为 Task #3 实际实现的 ConversationState 类型。

Policy Layer 依赖 ConversationState 的字段：
- ToolPolicy.evaluate(): 需要 role_id、tool_policy、workspace
- ApprovalPolicy.requires_approval(): 需要 role_id、turn_history、token_usage
- BudgetPolicy.evaluate(): 需要 budget_state（BudgetState 的子类）
- SandboxPolicy.evaluate_fs_scope(): 需要 workspace

当前占位符仅定义最小接口，具体字段在 Task #3 完成后补充。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ConversationState:
    """对话状态占位符

    本类定义 Policy Layer 所需的最小状态接口。
    Task #3 完成后将被 Task #3 的实际 ConversationState 实现替换。

    预期字段（Task #3 完成后填充）：
    - role_id: str                     # 当前角色 ID
    - workspace: str                   # 工作区路径
    - tool_policy: RoleToolPolicy       # 角色工具策略
    - turn_history: list[...]           # 当前 turn 的历史
    - token_usage: TokenUsage          # token 使用情况
    - budget_state: BudgetState         # 预算状态
    - approval_queue: list[...]         # 待审批队列
    """

    # 占位符字段（Task #3 完成后将替换为实际类型）
    role_id: str = ""
    workspace: str = ""

    # 元数据（用于调试和追踪）
    metadata: dict[str, Any] = field(default_factory=dict)

    def get_role_id(self) -> str:
        """获取角色 ID"""
        return self.role_id

    def get_workspace(self) -> str:
        """获取工作区路径"""
        return self.workspace
