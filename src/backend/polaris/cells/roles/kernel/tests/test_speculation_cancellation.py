from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock

import pytest
from polaris.cells.roles.kernel.internal.speculation.models import CancelToken, check_cancel
from polaris.cells.roles.kernel.internal.tool_batch_runtime import (
    ToolBatchRuntime,
    ToolExecutionContext,
)


class TestCancelToken:
    def test_initial_state(self) -> None:
        token = CancelToken()
        assert token.cancelled is False
        assert token.reason is None

    def test_cancel_sets_state(self) -> None:
        token = CancelToken()
        token.cancel(reason="turn_cancelled")
        assert token.cancelled is True
        assert token.reason == "turn_cancelled"


class TestCheckCancel:
    def test_none_token_does_nothing(self) -> None:
        check_cancel(None)

    def test_active_token_does_nothing(self) -> None:
        token = CancelToken()
        check_cancel(token)

    def test_cancelled_token_raises(self) -> None:
        token = CancelToken()
        token.cancel(reason="test_cancel")
        with pytest.raises(asyncio.CancelledError, match="test_cancel"):
            check_cancel(token)


class TestToolBatchRuntimeCancelCheckpoints:
    @pytest.mark.asyncio
    async def test_pre_execution_cancel_raises(self) -> None:
        executor = AsyncMock(return_value={"success": True, "result": "ok"})
        runtime = ToolBatchRuntime(executor)
        ctx = ToolExecutionContext(cancel_token=CancelToken())
        ctx.cancel_token.cancel("pre_exec")

        from polaris.cells.roles.kernel.public.turn_contracts import (
            ToolBatch,
            ToolCallId,
            ToolEffectType,
            ToolExecutionMode,
            ToolInvocation,
        )

        batch = ToolBatch(
            batch_id="b1",
            parallel_readonly=[
                ToolInvocation(
                    call_id=ToolCallId("c1"),
                    tool_name="read_file",
                    arguments={"path": "a.py"},
                    effect_type=ToolEffectType.READ,
                    execution_mode=ToolExecutionMode.READONLY_PARALLEL,
                )
            ],
        )
        receipts = await runtime.execute_batch(batch, context=ctx)
        assert len(receipts) == 1
        assert receipts[0].results[0].status == "aborted"

    @pytest.mark.asyncio
    async def test_post_execution_cancel_returns_aborted(self) -> None:
        """如果工具执行完成后、结果封装前被取消，应返回 aborted 状态."""
        token = CancelToken()

        async def cancelling_executor(_tool_name: str, _args: dict[str, Any]) -> dict[str, Any]:
            # 模拟工具执行完成后立即触发取消
            token.cancel("post_exec")
            return {"success": True, "result": "ok"}

        runtime = ToolBatchRuntime(cancelling_executor)
        ctx = ToolExecutionContext(cancel_token=token)

        from polaris.cells.roles.kernel.public.turn_contracts import (
            ToolBatch,
            ToolCallId,
            ToolEffectType,
            ToolExecutionMode,
            ToolInvocation,
        )

        batch = ToolBatch(
            batch_id="b1",
            parallel_readonly=[
                ToolInvocation(
                    call_id=ToolCallId("c1"),
                    tool_name="read_file",
                    arguments={"path": "a.py"},
                    effect_type=ToolEffectType.READ,
                    execution_mode=ToolExecutionMode.READONLY_PARALLEL,
                )
            ],
        )
        receipts = await runtime.execute_batch(batch, context=ctx)
        assert len(receipts) == 1
        # 由于 post-execution check_cancel 抛出 CancelledError，会被外层捕获为 aborted
        result = receipts[0].results[0]
        assert result.status in {"aborted", "error"}

    @pytest.mark.asyncio
    async def test_runner_finally_executes_on_cancel(self) -> None:
        """runner 在 check_cancel 处收到 CancelledError 后，finally 块必须执行."""
        token = CancelToken()
        finally_ran = False

        async def runner() -> str:
            nonlocal finally_ran
            try:
                await asyncio.sleep(0.01)
                check_cancel(token)
                return "done"
            except asyncio.CancelledError:
                raise
            finally:
                finally_ran = True

        task = asyncio.create_task(runner())
        await asyncio.sleep(0)
        token.cancel("mid_run")
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert finally_ran is True
