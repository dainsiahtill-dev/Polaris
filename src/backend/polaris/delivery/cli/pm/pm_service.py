"""PM 核心服务 (PM Core Service)

由 cli_thin 调用的实际业务逻辑服务。
负责：
- Architect 阶段：需求分析 → 设计文档
- PM 阶段：任务生成 → 合同编写 → Director 调度

架构位置：脚本层服务 (Script Layer Service)
"""

from __future__ import annotations

import logging
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

    from polaris.cells.audit.verdict.public import ArtifactService
    from polaris.cells.llm.dialogue.public.service import generate_role_response
    from polaris.cells.runtime.task_runtime.public.task_board_contract import TaskBoard

    return ArtifactService, generate_role_response, TaskBoard


logger = logging.getLogger(__name__)


class PMService:
    """PM 服务实现"""

    def __init__(
        self,
        workspace: Path,
        model: str = "glm-4.7-flash:latest",
        backend: str = "auto",
    ) -> None:
        ArtifactService, generate_role_response, TaskBoard = _bootstrap_backend_import_path()
        self.workspace = workspace
        self.model = model
        self.backend = backend
        self.task_board = TaskBoard(workspace=str(workspace))
