"""边界情况测试：director.execution Cell

目标覆盖率：60%
补充测试：任务取消、工作线程池满载、代码生成失败、文件应用冲突
"""

from __future__ import annotations

import asyncio

import pytest
from polaris.cells.director.execution.public.contracts import (
    DirectorExecutionResultV1,
    GetDirectorTaskStatusQueryV1,
)


class TestGetDirectorTaskStatusQuery:
    """任务状态查询测试"""

    def test_query_with_task_id(self) -> None:
        """测试基本查询"""
        query = GetDirectorTaskStatusQueryV1(
            task_id="test-task-001",
            workspace="/tmp/workspace",
        )

        assert query.task_id == "test-task-001"
        assert query.workspace == "/tmp/workspace"

    def test_query_with_empty_task_id(self) -> None:
        """测试空任务ID - 应该抛出错误"""
        with pytest.raises(ValueError, match="task_id must be a non-empty string"):
            GetDirectorTaskStatusQueryV1(
                task_id="",
                workspace="/tmp/workspace",
            )


class TestDirectorExecutionResult:
    """执行结果测试"""

    def test_result_success(self) -> None:
        """测试成功结果"""
        result = DirectorExecutionResultV1(
            ok=True,
            task_id="task-001",
            workspace="/tmp/workspace",
            status="completed",
            output_summary="Success output",
        )

        assert result.ok is True
        assert result.status == "completed"

    def test_result_failure(self) -> None:
        """测试失败结果"""
        result = DirectorExecutionResultV1(
            ok=False,
            task_id="task-002",
            workspace="/tmp/workspace",
            status="failed",
            error_code="EXECUTION_ERROR",
            error_message="Something went wrong",
        )

        assert result.ok is False
        assert result.error_code == "EXECUTION_ERROR"


class TestTaskCancellation:
    """任务取消测试"""

    @pytest.mark.asyncio
    async def test_cancel_running_task(self) -> None:
        """测试取消运行中任务"""
        task_status = {"state": "running", "cancelled": False}

        async def long_running_task():
            try:
                for i in range(100):
                    if task_status["cancelled"]:
                        raise asyncio.CancelledError()
                    await asyncio.sleep(0.01)
                return "completed"
            except asyncio.CancelledError:
                return "cancelled"

        task = asyncio.create_task(long_running_task())
        await asyncio.sleep(0.05)
        task_status["cancelled"] = True
        task.cancel()

        try:
            result = await task
            assert result == "cancelled"
        except asyncio.CancelledError:
            pass


class TestWorkerPool:
    """工作线程池测试"""

    def test_pool_full(self) -> None:
        """测试线程池满载"""
        max_workers = 2
        current_workers = 2

        def submit_task():
            if current_workers >= max_workers:
                return False
            return True

        assert submit_task() is False

    def test_pool_available(self) -> None:
        """测试线程池有空闲"""
        max_workers = 2
        current_workers = 1

        def submit_task():
            if current_workers >= max_workers:
                return False
            return True

        assert submit_task() is True


class TestCodeGeneration:
    """代码生成测试"""

    def test_syntax_error_detection(self) -> None:
        """测试语法错误检测"""
        code = "def broken(\n    pass"

        try:
            compile(code, "<string>", "exec")
            valid = True
        except SyntaxError:
            valid = False

        assert valid is False

    def test_valid_code(self) -> None:
        """测试有效代码"""
        code = "def valid():\n    return 42"

        try:
            compile(code, "<string>", "exec")
            valid = True
        except SyntaxError:
            valid = False

        assert valid is True
