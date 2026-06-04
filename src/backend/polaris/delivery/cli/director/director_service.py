"""Director CLI compatibility service.

This delivery-layer service is intentionally thin.  It delegates task
discovery and execution to ``DirectorOrchestrator``, whose execution path is
the canonical roles.adapters Director adapter with write receipts and
materialization quality gates.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any

from polaris.application.orchestration.director_orchestrator import (
    DirectorExecutionConfig,
    DirectorIterationResult,
    DirectorOrchestrator,
    DirectorTaskResult,
)
from polaris.kernelone.constants import DEFAULT_DIRECTOR_MAX_PARALLELISM

logger = logging.getLogger(__name__)


def _bootstrap_backend_import_path() -> None:
    """Ensure backend package path when running file directly."""
    if __package__:
        return
    backend_root = Path(__file__).resolve().parents[4]
    backend_root_str = str(backend_root)
    if backend_root_str not in sys.path:
        sys.path.insert(0, backend_root_str)


_bootstrap_backend_import_path()


class DirectorService:
    """Director delivery facade backed by the canonical adapter orchestrator."""

    def __init__(
        self,
        workspace: Path,
        model: str = "",
        max_workers: int = DEFAULT_DIRECTOR_MAX_PARALLELISM,
        execution_mode: str = "parallel",
    ) -> None:
        self.workspace = workspace
        self.model = model
        self.max_workers = max_workers
        self.execution_mode = execution_mode
        self._orchestrator = DirectorOrchestrator(
            DirectorExecutionConfig(
                workspace=str(workspace),
                model=model,
                max_workers=max_workers,
                execution_mode=execution_mode,
            )
        )

    async def run_iteration(self, iteration: int = 1) -> dict[str, Any]:
        """运行 Director 迭代。

        Args:
            iteration: 当前迭代次数

        Returns:
            执行结果
        """
        logger.info(
            "director iteration start: iteration=%s workspace=%s execution_mode=%s",
            iteration,
            self.workspace,
            self.execution_mode,
        )

        result = await self._orchestrator.run_iteration(iteration=iteration)
        return self._iteration_to_dict(result)

    def _get_ready_tasks(self) -> list[dict]:
        """Return ready task rows through the canonical orchestrator."""
        return self._orchestrator.get_ready_tasks()

    @staticmethod
    def _normalize_task_id(task_id: Any) -> int:
        token = str(task_id or "").strip()
        if not token.isdigit():
            raise ValueError(f"Invalid TaskBoard task id: {task_id}")
        return int(token)

    async def _execute_task(self, task: dict) -> dict[str, Any]:
        """Execute one task through ``DirectorOrchestrator``."""
        result = await self._orchestrator.execute_task(task)
        return self._task_result_to_dict(result)

    @staticmethod
    def _task_result_to_dict(result: DirectorTaskResult) -> dict[str, Any]:
        return {
            "success": result.success,
            "task_id": result.task_id,
            "subject": result.subject,
            "status": result.status,
            "response_length": result.response_length,
            "error": result.error,
            "metadata": result.metadata,
        }

    @classmethod
    def _iteration_to_dict(cls, result: DirectorIterationResult) -> dict[str, Any]:
        return {
            "success": result.success,
            "iteration": result.iteration,
            "tasks_processed": result.tasks_processed,
            "tasks_succeeded": result.tasks_succeeded,
            "tasks_failed": result.tasks_failed,
            "message": result.notes,
            "results": [cls._task_result_to_dict(item) for item in result.results],
        }


def create_parser() -> argparse.ArgumentParser:
    """创建参数解析器"""
    parser = argparse.ArgumentParser(
        prog="director-service",
        description="Polaris Director Core Service",
    )

    parser.add_argument(
        "--workspace",
        "-w",
        type=str,
        required=True,
        help="Workspace directory",
    )

    parser.add_argument(
        "--iteration",
        type=int,
        default=1,
        help="Iteration number",
    )

    parser.add_argument(
        "--max-workers",
        type=int,
        default=3,
        help="Maximum parallel workers",
    )

    parser.add_argument(
        "--execution-mode",
        type=str,
        choices=["serial", "parallel"],
        default="parallel",
        help="Execution mode",
    )

    parser.add_argument(
        "--model",
        type=str,
        default=os.environ.get("KERNELONE_DIRECTOR_MODEL", ""),
        help="LLM model",
    )

    parser.add_argument(
        "--token-budget",
        type=int,
        default=None,
        help="Token budget limit",
    )

    return parser


async def main() -> int:
    """服务入口"""
    parser = create_parser()
    args = parser.parse_args()

    workspace = Path(args.workspace).resolve()
    if not workspace.exists():
        logger.error(f"Error: Workspace does not exist: {workspace}")
        return 1

    service = DirectorService(
        workspace=workspace,
        model=args.model,
        max_workers=args.max_workers,
        execution_mode=args.execution_mode,
    )

    result = await service.run_iteration(iteration=args.iteration)

    # 输出结果
    logger.info(json.dumps(result, ensure_ascii=False, indent=2))

    return 0 if result.get("success") else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
