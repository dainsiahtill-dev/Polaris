# ruff: noqa: BLE001
from __future__ import annotations

import time
from typing import Any

from polaris.cells.roles.kernel.internal.speculation.models import CancelToken
from polaris.cells.roles.kernel.internal.speculative_flags import (
    is_speculative_execution_enabled,
)
from polaris.cells.roles.kernel.internal.tool_batch_runtime import (
    ToolBatchRuntime,
    ToolExecutionContext,
)
from polaris.cells.roles.kernel.public.turn_contracts import (
    BatchId,
    ToolBatch,
    ToolCallId,
    ToolEffectType,
    ToolExecutionMode,
    ToolInvocation,
)


class SpeculativeExecutor:
    """Speculative tool executor running in an isolated read-only sandbox.

    Uses ToolBatchRuntime with readonly_parallel mode to pre-execute
    predicted tool calls without side effects.
    """

    def __init__(
        self,
        batch_runtime: ToolBatchRuntime,
        *,
        enabled: bool | None = None,
    ) -> None:
        self._batch_runtime = batch_runtime
        self._enabled = is_speculative_execution_enabled() if enabled is None else bool(enabled)

    @property
    def enabled(self) -> bool:
        return self._enabled

    async def speculate(self, tool_invocation: ToolInvocation) -> dict[str, Any]:
        """Speculate a single tool invocation in read-only sandbox mode.

        Returns a dict with keys:
        - enabled: bool
        - result: Any | None
        - error: str | None
        """
        if not self._enabled:
            return {"enabled": False, "result": None, "error": "speculative_execution_disabled"}

        batch = ToolBatch(
            batch_id=BatchId("speculative-batch"),
            parallel_readonly=[tool_invocation],
        )
        try:
            receipts = await self._batch_runtime.execute_batch(batch)
            if receipts and receipts[0].results:
                result = receipts[0].results[0]
                return {
                    "enabled": True,
                    "result": result.result if result.status == "success" else None,
                    "error": result.status if result.status != "success" else None,
                }
            return {"enabled": True, "result": None, "error": "no_results"}
        except Exception as exc:
            return {"enabled": True, "result": None, "error": str(exc)}

    async def execute_speculative(
        self,
        tool_name: str,
        args: dict[str, Any],
        *,
        timeout_ms: int,
        cancel_token: CancelToken | None = None,
    ) -> Any:
        """执行单个推测性工具调用，支持取消令牌透传.

        Args:
            tool_name: 工具名称
            args: 归一化后的参数
            timeout_ms: 超时毫秒数
            cancel_token: 取消令牌

        Returns:
            工具执行结果（成功时直接返回 result）

        Raises:
            ShadowExecutionError: 执行失败时
            asyncio.CancelledError: 任务被取消时
        """
        import asyncio

        invocation = ToolInvocation(
            call_id=ToolCallId(f"spec_{tool_name}"),
            tool_name=tool_name,
            arguments=args,
            effect_type=ToolEffectType.READ,
            execution_mode=ToolExecutionMode.READONLY_PARALLEL,
        )
        batch = ToolBatch(
            batch_id=BatchId("speculative-batch"),
            parallel_readonly=[invocation],
        )
        ctx = ToolExecutionContext(
            speculative=True,
            cancel_token=cancel_token,
            deadline_monotonic=time.monotonic() + (timeout_ms / 1000.0),
        )
        try:
            receipts = await self._batch_runtime.execute_batch(batch, context=ctx)
            if receipts and receipts[0].results:
                tool_result = receipts[0].results[0]
                if tool_result.status == "success":
                    return tool_result.result
                # A deadline miss surfaces as the TIMEOUT status. Raise the dedicated
                # ShadowTimeoutError so the registry can count it toward the timeout
                # ratio (real backpressure) instead of folding every deadline miss
                # into the generic failed bucket, which left timeout_ratio inert.
                if str(tool_result.status) == "timeout":
                    raise ShadowTimeoutError(f"speculative tool timed out: {tool_result.status}")
                raise ShadowExecutionError(f"speculative tool failed: {tool_result.status}")
            raise ShadowExecutionError("no results from speculative batch")
        except asyncio.CancelledError:
            raise
        except ShadowExecutionError:
            raise
        except Exception as exc:
            raise ShadowExecutionError(str(exc)) from exc


class ShadowExecutionError(Exception):
    """推测执行失败的统一异常类型."""

    pass


class ShadowTimeoutError(ShadowExecutionError):
    """推测执行因超过 deadline 而失败的异常类型.

    继承自 :class:`ShadowExecutionError`，因此既有 ``except ShadowExecutionError``
    捕获路径行为不变；新增一个专门类型让上层（registry ``_runner``）能识别真实的
    deadline miss 并据此累加超时计数，使 ``timeout_ratio`` 真正反映超时压力。
    """

    pass
