"""PM Role - 使用Role Framework实现的PM

提供项目管理能力，支持FastAPI/CLI/TUI接口。
"""

import sys
from pathlib import Path


def _bootstrap_backend_import_path():
    """Lazy import of polaris modules after path bootstrap."""
    if __package__:
        # Already in a package, imports should work
        pass
    else:
        # Running as script - ensure backend is in path
        backend_root = Path(__file__).resolve().parents[4]
        backend_root_str = str(backend_root)
        if backend_root_str not in sys.path:
            sys.path.insert(0, backend_root_str)

    from polaris.delivery.cli.pm.pm_integration import PM, get_pm
    from polaris.kernelone.single_agent.role_framework import RoleBase, RoleCapability, RoleInfo, RoleState

    return PM, get_pm, RoleBase, RoleCapability, RoleInfo, RoleState


class PMRole(RoleBase):
    """PM 角色

    继承自 RoleBase，提供标准化的接口。
    内部使用现有的 PM 类实现功能。
    """

    def __init__(self, workspace: str) -> None:
        PM, get_pm, RoleBase, RoleCapability, RoleInfo, RoleState = _bootstrap_backend_import_path()
        super().__init__(workspace, "pm")
        self._pm: PM | None = None

    @property
    def pm(self) -> PM:
        """获取内部 PM 实例"""
        if self._pm is None:
            self._pm = get_pm(str(self.workspace))
        return self._pm

    def get_info(self) -> RoleInfo:
        """获取角色信息"""
        _, _, _, _, RoleInfo, _ = _bootstrap_backend_import_path()
        return RoleInfo(
            name="pm",
            version="2.0.0",
            description="Polaris PM - Project management system",
        )
