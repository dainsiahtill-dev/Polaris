"""Role Framework - 角色通用接口框架

提供统一的方式为角色实现 FastAPI/CLI/TUI 接口，避免重复代码。

用法:
    from role_framework import RoleBase, RoleCLI, RoleFastAPI

    class MyRole(RoleBase):
        def __init__(self, workspace: str):
            super().__init__(workspace, "myrole")

        def get_status(self) -> dict:
            return {"status": "ok"}

    # 启动 CLI
    cli = RoleCLI(MyRole)
    cli.run()

    # 启动 FastAPI
    api = RoleFastAPI(MyRole, port=50000)
    api.run()
"""

from .base import RoleBase, RoleCapability, RoleInfo, RoleState
from .cli import RoleCLI
from .fastapi import RoleFastAPI

__version__ = "1.0.0"
__all__ = [
    "RoleBase",
    "RoleCLI",
    "RoleCapability",
    "RoleFastAPI",
    "RoleState",
]
