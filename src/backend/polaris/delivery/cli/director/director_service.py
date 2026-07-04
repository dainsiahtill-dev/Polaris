"""Director CLI service.

This delivery-layer service is intentionally thin.  It delegates task
discovery to the runtime task-row projection and execution to the
``director.execution`` public Cell contract.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import re
import sys
from pathlib import Path
from typing import Any

from polaris.cells.director.execution.public import (
    ExecuteDirectorTaskCommandV1,
    execute_director_task,
)
from polaris.cells.runtime.task_runtime.public.service import TaskRuntimeService
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
    """Director delivery facade backed by public Cell contracts."""

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
        self._task_runtime: TaskRuntimeService | None = None

    def _get_task_runtime(self) -> TaskRuntimeService:
        if self._task_runtime is None:
            self._task_runtime = TaskRuntimeService(workspace=str(self.workspace))
        return self._task_runtime

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

        ready_tasks = self._get_ready_tasks()
        if not ready_tasks:
            return {
                "success": True,
                "iteration": iteration,
                "tasks_processed": 0,
                "tasks_succeeded": 0,
                "tasks_failed": 0,
                "message": "No ready tasks",
                "results": [],
            }

        batch_size = self.max_workers if self.execution_mode == "parallel" else 1
        batch = ready_tasks[: max(1, int(batch_size or 1))]
        if self.execution_mode == "parallel" and len(batch) > 1:
            raw_results = list(await asyncio.gather(*[self._execute_task(task) for task in batch], return_exceptions=True))
            results = [
                self._exception_result(batch[index], item) if isinstance(item, BaseException) else item
                for index, item in enumerate(raw_results)
            ]
        else:
            results = []
            for task in batch:
                results.append(await self._execute_task(task))

        tasks_succeeded = sum(1 for result in results if bool(result.get("success")))
        return {
            "success": True,
            "iteration": iteration,
            "tasks_processed": len(batch),
            "tasks_succeeded": tasks_succeeded,
            "tasks_failed": len(batch) - tasks_succeeded,
            "message": "",
            "results": results,
        }

    def _get_ready_tasks(self) -> list[dict[str, Any]]:
        """Return ready task rows through the runtime task-row projection."""
        rows = self._get_task_runtime().list_task_rows(include_terminal=False)
        ready_rows: list[dict[str, Any]] = []
        for row in rows:
            status = str(row.get("status") or "").strip().lower()
            blocked_by = row.get("blocked_by") or row.get("blockedBy") or []
            claimed_by = str(row.get("claimed_by") or "").strip()
            if status in {"pending", "ready"} and not blocked_by and not claimed_by:
                ready_rows.append(row)
        return ready_rows

    @staticmethod
    def _normalize_task_id(task_id: Any) -> int:
        token = str(task_id or "").strip()
        token = re.sub(r"^(task[-_])+", "", token, flags=re.IGNORECASE)
        if not token.isdigit():
            raise ValueError(f"Invalid task runtime id: {task_id}")
        return int(token)

    async def _execute_task(self, task: dict) -> dict[str, Any]:
        """Execute one task through ``director.execution`` public contract."""
        task_id = str(task.get("id") or task.get("task_id") or "").strip()
        subject = str(task.get("subject") or task.get("title") or task_id).strip()
        description = str(task.get("description") or task.get("goal") or subject).strip()
        metadata = self._build_execution_metadata(task)
        result = execute_director_task(
            ExecuteDirectorTaskCommandV1(
                task_id=task_id,
                workspace=str(self.workspace),
                instruction=description or subject or task_id,
                metadata=metadata,
            )
        )
        result_payload = self._execution_result_to_dict(result, subject=subject)
        self._update_task_board(task_id, result_payload)
        return result_payload

    def _build_execution_metadata(self, task: dict[str, Any]) -> dict[str, Any]:
        metadata_raw = task.get("metadata")
        metadata = dict(metadata_raw) if isinstance(metadata_raw, dict) else {}
        task_id = str(task.get("id") or task.get("task_id") or "").strip()
        metadata.update(
            {
                "task_id": task_id,
                "pm_task_id": str(metadata.get("pm_task_id") or task_id),
                "subject": str(task.get("subject") or task.get("title") or "").strip(),
                "goal": str(task.get("description") or task.get("goal") or task.get("subject") or "").strip(),
                "source": "delivery.cli.director_service",
                "model": str(self.model or ""),
                "max_workers": max(1, int(self.max_workers or 1)),
                "execution_mode": "parallel" if self.execution_mode == "parallel" else "serial",
            }
        )
        return metadata

    @staticmethod
    def _execution_result_to_dict(result: Any, *, subject: str) -> dict[str, Any]:
        metadata = dict(getattr(result, "metadata", {}) or {})
        evidence_paths = list(getattr(result, "evidence_paths", ()) or ())
        changed_files = list(metadata.get("changed_files") or evidence_paths)
        return {
            "success": bool(getattr(result, "ok", False)),
            "task_id": str(getattr(result, "task_id", "")),
            "subject": subject,
            "status": str(getattr(result, "status", "")),
            "response_length": len(str(getattr(result, "output_summary", "") or "")),
            "error": str(getattr(result, "error_message", "") or getattr(result, "error_code", "") or ""),
            "metadata": {
                **metadata,
                "adapter": "director.execution.public",
                "canonical_execution_contract": "ExecuteDirectorTaskCommandV1",
                "changed_files": changed_files,
                "evidence_paths": evidence_paths,
            },
        }

    def _update_task_board(self, task_id: str, result: dict[str, Any]) -> None:
        status = "completed" if bool(result.get("success")) else "failed"
        try:
            self._get_task_runtime().update_task_row(
                self._normalize_task_id(task_id),
                status=status,
                metadata=dict(result.get("metadata") or {}),
            )
        except (AttributeError, RuntimeError, TypeError, ValueError) as exc:
            logger.warning("Failed to update Director task %s after public execution: %s", task_id, exc)

    @staticmethod
    def _exception_result(task: dict[str, Any], exc: BaseException) -> dict[str, Any]:
        task_id = str(task.get("id") or task.get("task_id") or "unknown").strip()
        subject = str(task.get("subject") or task.get("title") or task_id).strip()
        return {
            "success": False,
            "task_id": task_id,
            "subject": subject,
            "status": "failed",
            "response_length": 0,
            "error": str(exc),
            "metadata": {"error_type": type(exc).__name__},
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
