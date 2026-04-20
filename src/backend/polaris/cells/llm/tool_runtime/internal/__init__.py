"""Internal module exports for `llm.tool_runtime`.

【K1-PURIFY Phase 2】
role_integrations.py 从 polaris.kernelone.llm.toolkit.integrations 迁移而来，
承载 Polaris 业务角色语义。KernelOne 不得依赖此模块。
"""

from polaris.cells.llm.tool_runtime.internal.orchestrator import (
    RoleToolRoundOrchestrator,
    RoleToolRoundResult,
)

# 角色工具集成（从 KernelOne 迁移）
from polaris.cells.llm.tool_runtime.internal.role_integrations import (
    # 注册表
    ROLE_TOOL_INTEGRATIONS,
    ArchitectToolIntegration,
    ChiefEngineerToolIntegration,
    DirectorToolIntegration,
    # 角色集成类
    PMToolIntegration,
    QAToolIntegration,
    ScoutToolIntegration,
    # 兼容层
    ToolEnabledLLMClient,
    # 便捷函数
    enhance_chief_engineer_prompt,
    enhance_director_prompt,
    get_role_tool_integration,
)

__all__ = [
    "ROLE_TOOL_INTEGRATIONS",
    "ArchitectToolIntegration",
    "ChiefEngineerToolIntegration",
    "DirectorToolIntegration",
    # 角色集成
    "PMToolIntegration",
    "QAToolIntegration",
    # Orchestrator
    "RoleToolRoundOrchestrator",
    "RoleToolRoundResult",
    "ScoutToolIntegration",
    "ToolEnabledLLMClient",
    "enhance_chief_engineer_prompt",
    "enhance_director_prompt",
    "get_role_tool_integration",
]
