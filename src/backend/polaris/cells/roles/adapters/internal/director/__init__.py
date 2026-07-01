"""Director 角色适配器模块

提供 Director 角色的统一编排接口。

主要导出:
- DirectorAdapter: 核心适配器类
- CommandInjectionBlocked: 命令注入阻断异常
- DirectorStateTracker: 状态追踪服务
- DirectorPatchExecutor: PATCH 执行器
- DirectorToolExecutor: 工具执行器
"""

from __future__ import annotations

from .adapter import DirectorAdapter
from .execution import DirectorPatchExecutor
from .execution_tools import DirectorToolExecutor
from .security import (
    ALLOWED_EXECUTION_COMMANDS,
    TOOLING_SECURITY_AVAILABLE,
    CommandInjectionBlocked,
    is_command_allowed,
    is_command_blocked,
)
from .state_tracking import DirectorStateTracker

__all__ = [
    "ALLOWED_EXECUTION_COMMANDS",
    "TOOLING_SECURITY_AVAILABLE",
    "CommandInjectionBlocked",
    "DirectorAdapter",
    "DirectorPatchExecutor",
    "DirectorStateTracker",
    "DirectorToolExecutor",
    "is_command_allowed",
    "is_command_blocked",
]
