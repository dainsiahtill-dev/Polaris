"""Director 角色适配器

此文件是导入重定向的 Facade。实际实现在 director/ 子目录中。

为了保持向后兼容性，所有公共接口从此文件重新导出。
"""

from __future__ import annotations

# 从新的模块结构导入所有公共接口
from polaris.cells.roles.adapters.internal.director import (
    ALLOWED_EXECUTION_COMMANDS,
    TOOLING_SECURITY_AVAILABLE,
    CommandInjectionBlocked,
    DirectorAdapter,
    DirectorPatchExecutor,
    DirectorStateTracker,
    DirectorToolExecutor,
    is_command_allowed,
    is_command_blocked,
)

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
