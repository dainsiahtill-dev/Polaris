"""Director Role - 使用Role Framework实现的Director

提供任务执行能力，支持FastAPI/CLI/TUI接口。
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

# Bootstrap path if running as script
_backend_root: Path | None = None


def _bootstrap_backend_import_path():
    """Lazy import of polaris modules after path bootstrap."""
    global _backend_root

    if __package__:
        # Already in a package, imports should work
        pass
    else:
        # Running as script - ensure backend is in path
        if _backend_root is None:
            _backend_root = Path(__file__).resolve().parents[4]
        backend_root_str = str(_backend_root)
        if backend_root_str not in sys.path:
            sys.path.insert(0, backend_root_str)

    from polaris.kernelone.constants import DEFAULT_DIRECTOR_MAX_PARALLELISM, DEFAULT_OPERATION_TIMEOUT_SECONDS
    from polaris.kernelone.single_agent.role_framework import RoleBase, RoleCapability, RoleInfo, RoleState

    return (
        DEFAULT_DIRECTOR_MAX_PARALLELISM,
        DEFAULT_OPERATION_TIMEOUT_SECONDS,
        RoleBase,
        RoleCapability,
        RoleInfo,
        RoleState,
    )


# Import RoleBase at module level for class definition
try:
    from polaris.kernelone.single_agent.role_framework import RoleBase, RoleCapability, RoleInfo, RoleState
except ImportError:
    # Fallback for bootstrap scenario
    RoleBase = None  # type: ignore[assignment, misc]
    RoleCapability = None  # type: ignore[misc, assignment]
    RoleInfo = None  # type: ignore[misc, assignment]
    RoleState = None  # type: ignore[misc, assignment]


logger = logging.getLogger(__name__)

# Import Director v2 components if available
try:
    from polaris.cells.director.execution.public.service import (
        DirectorConfig,
        DirectorService,
    )
    from polaris.domain.entities import TaskPriority, TaskStatus

    DIRECTOR_V2_AVAILABLE = True
except ImportError:
    DIRECTOR_V2_AVAILABLE = False
    DirectorService = None  # type: ignore[misc, assignment]
    DirectorConfig = None  # type: ignore[misc, assignment]
    TaskStatus = None  # type: ignore[misc, assignment]
    TaskPriority = None  # type: ignore[misc, assignment]


class DirectorRole(RoleBase):
    """Director Role - Director"""

    def __init__(self, workspace: str | None = None) -> None:
        self.workspace = workspace or os.getcwd()  # type: ignore[assignment]
        self.role_id = "director"
        self.role_name = "Director"
        self.capabilities = [RoleCapability.TASK_EXECUTION, RoleCapability.FILE_OPERATIONS]  # type: ignore[attr-defined]
