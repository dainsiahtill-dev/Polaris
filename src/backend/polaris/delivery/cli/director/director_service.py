"""Director 核心服务 (Director Core Service)

由 cli_thin 调用的实际业务逻辑服务。
负责任务执行、代码改写、验证。

架构位置：脚本层服务 (Script Layer Service)

【Task #49 架构约束】
本文件为 host 层，必须通过 RoleRuntimeService facade 执行角色逻辑。
禁止行为:
  - 不得直接创建 LLM Provider 实例
  - 不得实现自己的 tool loop (禁止 import AgentAccelToolExecutor / parse_tool_calls)
  - 不得 import polaris.kernelone.llm.toolkit
  - 不得 import standalone_runner 内部模块
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

from polaris.cells.roles.runtime.public.contracts import ExecuteRoleSessionCommandV1
from polaris.cells.roles.runtime.public.service import RoleRuntimeService
from polaris.cells.runtime.task_runtime.public.task_board_contract import TaskBoard
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
    """Director 服务实现 — 通过 RoleRuntimeService facade 执行角色会话。"""

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
        self.task_board = TaskBoard(workspace=str(workspace))
        # RoleRuntimeService 是唯一合法的工具循环入口 (Task #49)
        self._runtime = RoleRuntimeService()

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

        # 1. 获取就绪任务
        ready_tasks = self._get_ready_tasks()
        logger.info("director ready tasks: count=%s", len(ready_tasks))

        if not ready_tasks:
            logger.info("director iteration skipped: no ready tasks")
            return {
                "success": True,
                "iteration": iteration,
                "tasks_processed": 0,
                "message": "No ready tasks",
            }

        # 2. 限制并发
        batch_size = self.max_workers if self.execution_mode == "parallel" else 1
        batch = ready_tasks[:batch_size]

        # 3. 执行任务
        results = []
        for task in batch:
            result = await self._execute_task(task)
            results.append(result)

        # 4. 汇总结果
        success_count = sum(1 for r in results if r.get("success"))

        return {
            "success": True,
            "iteration": iteration,
            "tasks_processed": len(batch),
            "tasks_succeeded": success_count,
            "tasks_failed": len(batch) - success_count,
            "results": results,
        }

    def _get_ready_tasks(self) -> list[dict]:
        """获取就绪状态的任务"""
        return [task.to_dict() for task in self.task_board.get_ready_tasks()]

    @staticmethod
    def _normalize_task_id(task_id: Any) -> int:
        token = str(task_id or "").strip()
        if not token.isdigit():
            raise ValueError(f"Invalid TaskBoard task id: {task_id}")
        return int(token)

    async def _execute_task(self, task: dict) -> dict[str, Any]:
        """执行单个任务 — 通过 RoleRuntimeService facade 执行完整角色会话。

        架构: DirectorService (host) -> RoleRuntimeService (facade)
            -> RoleExecutionKernel (tool loop) -> Agent 工具执行
        不得在此层直接操作 LLM Provider 或工具执行器 (Task #49 约束)。
        """
        task_id = task.get("id", "unknown")
        subject = task.get("subject", "unknown")

        logger.info("director executing task: id=%s subject=%s", task_id, subject)

        try:
            normalized_task_id = self._normalize_task_id(task_id)

            # 更新状态为 in_progress
            self.task_board.update(normalized_task_id, status="in_progress")

            # 构建角色会话消息 (保持工具格式说明，用于 RoleExecutionKernel 解析)
            message = self._build_director_message(task)

            # 通过 facade 执行完整角色会话 (tool loop 由 kernel 内部管理)
            command = ExecuteRoleSessionCommandV1(
                role="director",
                session_id=f"director-task-{normalized_task_id}",
                workspace=str(self.workspace),
                user_message=message,
                history=(),
                stream=False,
            )
            result_payload = await self._runtime.execute_role_session(command)

            # 提取响应文本 (与旧接口保持兼容)
            response = self._extract_response_text(result_payload)
            logger.info(
                "director task response received: id=%s length=%s",
                task_id,
                len(response),
            )

            # RoleExecutionKernel 已内部执行所有工具调用，直接更新任务状态
            self.task_board.update(
                normalized_task_id,
                status="completed",
                metadata={
                    "adapter_result": {
                        "response_length": len(response),
                        "tool_calls_executed_by_kernel": True,
                    }
                },
            )

            return {
                "success": True,
                "task_id": task_id,
                "subject": subject,
                "response_length": len(response),
            }

        except (RuntimeError, ValueError) as exc:
            logger.exception("director task execution failed: id=%s", task_id)
            try:
                normalized_task_id = self._normalize_task_id(task_id)
                self.task_board.update(
                    normalized_task_id,
                    status="failed",
                    metadata={"adapter_error": str(exc)},
                )
            except (RuntimeError, ValueError):
                logger.exception(
                    "director task state update failed after execution error: id=%s",
                    task_id,
                )
            return {
                "success": False,
                "task_id": task_id,
                "subject": subject,
                "error": str(exc),
            }

    @staticmethod
    def _extract_response_text(payload: Any) -> str:
        """从 RoleRuntimeService 返回的 payload 中提取纯文本响应。"""
        if isinstance(payload, dict):
            return str(payload.get("response") or payload.get("text") or "").strip()
        return str(payload or "").strip()

    @staticmethod
    def _build_director_message(task: dict) -> str:
        """构建 Director 角色消息。

        Runtime contract:
        - Tool execution is handled by RoleExecutionKernel native tool calling.
        - The model must not emit legacy bracketed wrappers such as [READ_FILE].
        """
        subject = task.get("subject", "")
        description = task.get("description", "")

        lines = [
            f"任务: {subject}",
            "",
        ]

        if description:
            lines.extend(["描述:", description, ""])

        lines.extend(
            [
                "请执行此任务。",
                "",
                "运行时说明:",
                "",
                "- 工具调用由运行时以原生 structured tool calls 处理。",
                "- 不要输出任何 [READ_FILE] / [WRITE_FILE] / [TOOL_CALL] 之类的文本 wrapper。",
                "- 如果不需要工具，直接给出回答；如果需要工具，正常表达你的意图，运行时会处理工具 schema。",
            ]
        )

        return "\n".join(lines)


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
        default=os.environ.get("POLARIS_DIRECTOR_MODEL", ""),
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
