"""PM Role - 使用Role Framework实现的PM

提供项目管理能力，支持FastAPI/CLI/TUI接口。
"""

import sys
from pathlib import Path
from typing import Any


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


class PMRole:  # type: ignore[misc]  # structurally broken: RoleBase imported lazily inside _bootstrap_backend_import_path; mypy cannot resolve it at class definition time
    """PM 角色

    继承自 RoleBase，提供标准化的接口。
    内部使用现有的 PM 类实现功能。
    """

    def __init__(self, workspace: str) -> None:
        _pm_cls, _get_pm, _role_base, _role_capability, _role_info, _role_state = _bootstrap_backend_import_path()
        # super().__init__(workspace, "pm")  # type: ignore[misc]  # RoleBase lazily imported
        self.workspace = workspace
        self._pm: Any | None = None

    @property
    def pm(self) -> Any:
        """获取内部 PM 实例"""
        _pm_cls, get_pm, _, _, _, _ = _bootstrap_backend_import_path()
        if self._pm is None:
            self._pm = get_pm(str(self.workspace))
        return self._pm

    def get_info(self) -> Any:
        """获取角色信息"""
        _, _, _, _, role_info_cls, _ = _bootstrap_backend_import_path()
        return role_info_cls(
            name="pm",
            version="2.0.0",
            description="Polaris PM - Project management system",
        )
