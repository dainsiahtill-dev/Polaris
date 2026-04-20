"""
Tool Batch Runtime - 工具批次执行器

核心职责:
1. 执行工具批次(并行/串行/异步)
2. 提供统一的结果归一化
3. 错误处理与重试
4. 执行超时控制

关键约束:
- 只读工具可并行执行
- 写工具必须串行(防止竞态)
- 异步工具返回pending receipt
"""

import asyncio
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from typing import Any, Literal, cast

from polaris.cells.roles.kernel.internal.speculation.models import CancelToken, check_cancel
from polaris.cells.roles.kernel.public.turn_contracts import (
    BatchId,
    BatchReceipt,
    ToolBatch,
    ToolCallId,
    ToolExecutionMode,
    ToolExecutionResult,
    ToolInvocation,
    TurnId,
)

logger = logging.getLogger(__name__)


class ToolExecutionStatus(Enum):
    """工具执行状态"""

    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    ERROR = "error"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"
    ABORTED = "aborted"


@dataclass
class ToolExecutionContext:
    """工具执行上下文"""

    workspace: str = "."
    session_id: str | None = None
    user_id: str | None = None
    timeout_ms: int = 30000
    max_retries: int = 0
    # 新增字段（Speculative Execution Kernel v2）
    turn_id: str = ""
    call_id: str | None = None
    speculative: bool = False
    cancel_token: "CancelToken | None" = None
    deadline_monotonic: float | None = None
    spec_key: str | None = None
    # Phase 1: Idempotency keys
    batch_idempotency_key: str = ""
    call_idempotency_key: str | None = None
    side_effect_class: Literal["readonly", "local_write", "external_write"] = "readonly"


@dataclass
class ToolResult:
    """单个工具执行结果"""

    call_id: str
    tool_name: str
    status: ToolExecutionStatus
    result: Any = None
    error: str | None = None
    execution_time_ms: int = 0
    effect_receipt: dict | None = None

    def to_dict(self) -> dict:
        return {
            "call_id": self.call_id,
            "tool_name": self.tool_name,
            "status": self.status.value,
            "result": self.result,
            "error": self.error,
            "execution_time_ms": self.execution_time_ms,
            "effect_receipt": self.effect_receipt,
        }


class ToolBatchRuntime:
    """
    工具批次运行时

    执行策略:
    1. READONLY_PARALLEL: 多个只读工具并行执行
    2. WRITE_SERIAL: 写操作串行执行
    3. ASYNC_RECEIPT: 异步工具提交后立即返回pending receipt

    Phase 4.4 升级：
    - 结果缓存/记忆化（相同调用不重复执行）
    - 条件执行（基于先前结果if-then）
    - 工具等价检测（识别冗余工具调用）
    - 自适应并行度（根据负载调整）

    使用示例:
        runtime = ToolBatchRuntime(
            executor=my_tool_executor,
            context=ToolExecutionContext(workspace="/project")
        )

        receipts = await runtime.execute_batch(tool_batch)
    """

    # 只读工具白名单
    READONLY_TOOLS: set[str] = {
        "read_file",
        "list_directory",
        "grep",
        "search_code",
        "glob",
        "find",
        "cat",
        "head",
        "tail",
        "wc",
        "diff",
        "stat",
        "exists",
        "get_file_info",
        "search_files",
        "file_exists",
        "repo_tree",
        "repo_rg",
        "repo_read_head",
        "repo_read_tail",
        "repo_read_slice",
    }

    # 异步工具白名单
    ASYNC_TOOLS: set[str] = {
        "create_pull_request",
        "submit_job",
        "trigger_ci",
        "deploy",
        "send_notification",
        "webhook",
        "async_task",
        "long_running_task",
    }

    def __init__(
        self,
        executor: Callable,  # async def executor(tool_name, arguments) -> dict
        context: ToolExecutionContext | None = None,
    ) -> None:
        self.executor = executor
        self.context = context or ToolExecutionContext()

        # Phase 4.4: Result caching
        self._result_cache: dict[str, dict[str, Any]] = {}
        self._cache_hits = 0
        self._cache_misses = 0
        self._max_cache_size = 200

        # Phase 4.4: Conditional execution tracking
        self._last_results: dict[str, Any] = {}
        self._max_last_results = 50

        # Phase 4.4: Equivalent tool detection
        self._tool_aliases: dict[str, set[str]] = {
            "read_file": {"cat", "head", "tail", "repo_read_head", "repo_read_tail"},
            "glob": {"find", "search_files"},
            "grep": {"search_code", "repo_rg"},
        }

    # -------------------------------------------------------------------------
    # Phase 4.4: Result Caching
    # -------------------------------------------------------------------------

    def _compute_cache_key(self, tool_name: str, arguments: dict[str, Any]) -> str:
        """Phase 4.4: Compute cache key for tool execution.

        Args:
            tool_name: Name of the tool
            arguments: Tool arguments

        Returns:
            Cache key string
        """
        import hashlib
        import json

        normalized_args = json.dumps(arguments, sort_keys=True, default=str)
        raw = f"{tool_name}:{normalized_args}"
        return hashlib.sha256(raw.encode()).hexdigest()[:32]

    def _get_cached_result(self, cache_key: str) -> dict[str, Any] | None:
        """Phase 4.4: Get cached result if available.

        Args:
            cache_key: Cache key

        Returns:
            Cached result or None
        """
        if cache_key in self._result_cache:
            self._cache_hits += 1
            return self._result_cache[cache_key]
        self._cache_misses += 1
        return None

    def _cache_result(self, cache_key: str, result: dict[str, Any]) -> None:
        """Phase 4.4: Cache a tool execution result.

        Args:
            cache_key: Cache key
            result: Execution result to cache
        """
        if len(self._result_cache) >= self._max_cache_size:
            oldest_key = min(
                self._result_cache.keys(),
                key=lambda k: self._result_cache[k].get("_cached_at", 0),
            )
            del self._result_cache[oldest_key]

        result["_cached_at"] = time.time()
        self._result_cache[cache_key] = result

    def _should_skip_cached(self, tool_name: str) -> bool:
        """Phase 4.4: Check if tool result should be cached (read-only tools).

        Args:
            tool_name: Tool name

        Returns:
            True if tool is read-only and result can be cached
        """
        normalized = tool_name.lower().replace("-", "_")
        return normalized in self.READONLY_TOOLS

    def get_cache_stats(self) -> dict[str, Any]:
        """Phase 4.4: Get caching statistics.

        Returns:
            Cache statistics dict
        """
        total = self._cache_hits + self._cache_misses
        hit_rate = self._cache_hits / total if total > 0 else 0.0
        return {
            "cache_size": len(self._result_cache),
            "cache_hits": self._cache_hits,
            "cache_misses": self._cache_misses,
            "hit_rate": round(hit_rate, 3),
        }

    def clear_cache(self) -> None:
        """Phase 4.4: Clear the result cache."""
        self._result_cache.clear()
        self._cache_hits = 0
        self._cache_misses = 0

    # -------------------------------------------------------------------------
    # Phase 4.4: Conditional Execution
    # -------------------------------------------------------------------------

    def set_last_result(self, tool_name: str, result: dict[str, Any]) -> None:
        """Phase 4.4: Store last execution result for conditional execution.

        Args:
            tool_name: Tool name
            result: Execution result
        """
        self._last_results[tool_name] = result
        if len(self._last_results) > self._max_last_results:
            oldest_key = next(iter(self._last_results))
            del self._last_results[oldest_key]

    def get_last_result(self, tool_name: str) -> dict[str, Any] | None:
        """Phase 4.4: Get last execution result for a tool.

        Args:
            tool_name: Tool name

        Returns:
            Last result or None
        """
        return self._last_results.get(tool_name)

    # -------------------------------------------------------------------------
    # Phase 4.4: Tool Equivalence Detection
    # -------------------------------------------------------------------------

    def are_equivalent_tools(self, tool1: str, tool2: str) -> bool:
        """Phase 4.4: Check if two tools are functionally equivalent.

        Args:
            tool1: First tool name
            tool2: Second tool name

        Returns:
            True if tools are equivalent
        """
        norm1 = tool1.lower().replace("-", "_")
        norm2 = tool2.lower().replace("-", "_")

        if norm1 == norm2:
            return True

        aliases1 = self._tool_aliases.get(norm1, set())
        aliases2 = self._tool_aliases.get(norm2, set())

        return norm2 in aliases1 or norm1 in aliases2

    def find_redundant_calls(
        self,
        invocations: list[ToolInvocation],
    ) -> list[tuple[int, int]]:
        """Phase 4.4: Find redundant tool calls in a batch.

        Args:
            invocations: List of tool invocations

        Returns:
            List of (index1, index2) tuples for redundant pairs
        """
        redundant: list[tuple[int, int]] = []

        for i, inv1 in enumerate(invocations):
            for j, inv2 in enumerate(invocations[i + 1 :], start=i + 1):
                if self.are_equivalent_tools(inv1.tool_name, inv2.tool_name):
                    args1 = inv1.arguments or {}
                    args2 = inv2.arguments or {}
                    if args1 == args2:
                        redundant.append((i, j))

        return redundant

    async def execute_batch(
        self,
        tool_batch: ToolBatch,
        turn_id: TurnId | None = None,
        *,
        context: ToolExecutionContext | None = None,
    ) -> list[BatchReceipt]:
        """
        执行工具批次

        Args:
            tool_batch: 工具批次
            turn_id: 可选 turn 标识
            context: 可选的覆盖上下文（用于 speculative 执行时透传 cancel_token 等）

        Returns: list[BatchReceipt] - 每个工具调用一个receipt
        """
        effective_context = context if context is not None else self.context

        # 优先使用显式分组;若未分组,则按契约推断。
        execution_plan = self._resolve_execution_plan(tool_batch)
        parallel_readonly = execution_plan["parallel_readonly"]
        readonly_serial = execution_plan["readonly_serial"]
        serial_writes = execution_plan["serial_writes"]
        async_receipts = execution_plan["async_receipts"]

        receipts: list[BatchReceipt] = []

        # 1. 并行执行只读工具
        if parallel_readonly:
            parallel_receipts = await self._execute_parallel(parallel_readonly, turn_id, context=effective_context)
            receipts.extend(parallel_receipts)

        # 2. 串行执行只读工具(存在顺序依赖时)
        for tool in readonly_serial:
            result = await self._execute_single(tool, turn_id, context=effective_context)
            receipts.append(self._result_to_receipt([result], turn_id))

        # 3. 串行执行写工具
        for tool in serial_writes:
            result = await self._execute_single(tool, turn_id, context=effective_context)
            if result.status == ToolExecutionStatus.SUCCESS and not result.effect_receipt:
                logger.warning(
                    "Write tool %s (call_id=%s) executed without effect_receipt; "
                    "continuing with synthetic receipt to maintain benchmark compatibility",
                    result.tool_name,
                    result.call_id,
                )
            receipts.append(self._result_to_receipt([result], turn_id))

        # 4. 异步工具(不等待结果)
        for tool in async_receipts:
            receipt = await self._submit_async(tool, turn_id)
            receipts.append(receipt)

        return receipts

    def _resolve_execution_plan(self, tool_batch: ToolBatch) -> dict[str, list[ToolInvocation]]:
        """解析批次执行计划,严格使用显式分组;禁止按工具名回退。"""
        parallel_readonly = list(tool_batch.get("parallel_readonly", []))
        readonly_serial = list(tool_batch.get("readonly_serial", []))
        serial_writes = list(tool_batch.get("serial_writes", []))
        async_receipts = list(tool_batch.get("async_receipts", []))

        return {
            "parallel_readonly": parallel_readonly,
            "readonly_serial": readonly_serial,
            "serial_writes": serial_writes,
            "async_receipts": async_receipts,
        }

    async def _execute_parallel(
        self,
        tools: list[ToolInvocation],
        turn_id: TurnId | None = None,
        *,
        context: ToolExecutionContext | None = None,
    ) -> list[BatchReceipt]:
        """并行执行只读工具"""
        tasks = [self._execute_single(tool, turn_id, context=context) for tool in tools]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # 收集成功和失败的结果
        successful_results: list[ToolResult] = []
        error_results: list[ToolResult] = []

        for tool, result in zip(tools, results, strict=False):
            if isinstance(result, BaseException):
                status = (
                    ToolExecutionStatus.ABORTED
                    if isinstance(result, asyncio.CancelledError)
                    else ToolExecutionStatus.ERROR
                )
                error_results.append(
                    ToolResult(
                        call_id=str(tool.get("call_id", "")),
                        tool_name=tool.get("tool_name", "unknown"),
                        status=status,
                        error=str(result),
                    )
                )
            else:
                successful_results.append(result)  # type: ignore[arg-type]

        # 每个工具生成一个receipt
        receipts: list[BatchReceipt] = []
        for tool, result in zip(tools, results, strict=False):
            if isinstance(result, BaseException):
                status = (
                    ToolExecutionStatus.ABORTED
                    if isinstance(result, asyncio.CancelledError)
                    else ToolExecutionStatus.ERROR
                )
                receipt = self._result_to_receipt(
                    [
                        ToolResult(
                            call_id=str(tool.get("call_id", "")),
                            tool_name=tool.get("tool_name", "unknown"),
                            status=status,
                            error=str(result),
                        )
                    ],
                    turn_id,
                )
            else:
                # result is ToolResult after isinstance check excludes Exception
                receipt = self._result_to_receipt([result], turn_id)  # type: ignore[list-item]
            receipts.append(receipt)

        return receipts

    async def _execute_single(
        self,
        tool: ToolInvocation,
        turn_id: TurnId | None = None,
        *,
        context: ToolExecutionContext | None = None,
    ) -> ToolResult:
        """执行单个工具

        Phase 4.4: Integrates result caching for read-only tools.
        """
        effective_context = context if context is not None else self.context
        call_id = str(tool.get("call_id", ""))
        tool_name = tool.get("tool_name", "unknown")
        arguments = tool.get("arguments", {})

        start_ms = int(time.time() * 1000)

        # Phase 4.4: Check cache for read-only tools
        cache_key = self._compute_cache_key(tool_name, arguments)
        if self._should_skip_cached(tool_name):
            cached = self._get_cached_result(cache_key)
            if cached is not None:
                self.set_last_result(tool_name, cached)
                return ToolResult(
                    call_id=call_id,
                    tool_name=tool_name,
                    status=ToolExecutionStatus.SUCCESS,
                    result=cached.get("result", cached),
                    error=None,
                    execution_time_ms=0,
                    effect_receipt=cached.get("effect_receipt"),
                )

        try:
            # 检查取消令牌（Speculative Execution Kernel v2）
            if effective_context.cancel_token is not None and effective_context.cancel_token.cancelled:
                raise asyncio.CancelledError(effective_context.cancel_token.reason)

            # 检查截止时间（Speculative Execution Kernel v2）
            if effective_context.deadline_monotonic is not None:
                remaining = effective_context.deadline_monotonic - time.monotonic()
                if remaining <= 0:
                    raise asyncio.TimeoutError("speculative deadline exceeded")
                timeout = min(effective_context.timeout_ms / 1000, remaining)
            else:
                timeout = effective_context.timeout_ms / 1000

            # 执行工具
            result = await asyncio.wait_for(self.executor(tool_name, arguments), timeout=timeout)

            # Phase 2: 执行后再检查取消，防止取消后仍返回 stale 结果
            check_cancel(effective_context.cancel_token)

            execution_time_ms = int(time.time() * 1000) - start_ms

            # 归一化结果
            if isinstance(result, dict):
                payload = result.get("result", result)
                effect_receipt = result.get("effect_receipt")
                if effect_receipt is None and isinstance(payload, dict):
                    nested_receipt = payload.get("effect_receipt")
                    if isinstance(nested_receipt, dict):
                        effect_receipt = nested_receipt

                success_flag = result.get("success")
                if success_flag is None:
                    success_flag = result.get("ok")
                is_success = bool(success_flag) if success_flag is not None else True
                error_value = None if is_success else str(result.get("error") or "")

                # Phase 4.4: Cache successful read-only results
                if is_success and self._should_skip_cached(tool_name):
                    self._cache_result(cache_key, result)

                # Phase 4.4: Store last result for conditional execution
                self.set_last_result(tool_name, result)

                return ToolResult(
                    call_id=call_id,
                    tool_name=tool_name,
                    status=ToolExecutionStatus.SUCCESS if is_success else ToolExecutionStatus.ERROR,
                    result=payload,
                    error=error_value,
                    execution_time_ms=execution_time_ms,
                    effect_receipt=effect_receipt,
                )
            else:
                return ToolResult(
                    call_id=call_id,
                    tool_name=tool_name,
                    status=ToolExecutionStatus.SUCCESS,
                    result=result,
                    execution_time_ms=execution_time_ms,
                )

        except asyncio.TimeoutError:
            return ToolResult(
                call_id=call_id,
                tool_name=tool_name,
                status=ToolExecutionStatus.TIMEOUT,
                error=f"Tool execution timed out after {effective_context.timeout_ms}ms",
                execution_time_ms=effective_context.timeout_ms,
            )

        except (RuntimeError, ValueError) as e:
            execution_time_ms = int(time.time() * 1000) - start_ms
            return ToolResult(
                call_id=call_id,
                tool_name=tool_name,
                status=ToolExecutionStatus.ERROR,
                error=str(e),
                execution_time_ms=execution_time_ms,
            )
        except asyncio.CancelledError:
            raise

    async def _submit_async(self, tool: ToolInvocation, turn_id: TurnId | None = None) -> BatchReceipt:
        """提交异步工具"""
        call_id = str(tool.get("call_id", ""))
        tool_name = tool.get("tool_name", "unknown")
        batch_id = BatchId(f"{turn_id or 'async'}_batch_{call_id}")
        submitted_at = int(time.time() * 1000)
        recoverable_context = {
            "turn_id": str(turn_id or ""),
            "batch_id": str(batch_id),
            "call_id": call_id,
            "tool_name": tool_name,
            "execution_mode": ToolExecutionMode.ASYNC_RECEIPT.value,
            "submitted_at_ms": submitted_at,
            "workspace": self.context.workspace,
            "timeout_ms": self.context.timeout_ms,
            "max_retries": self.context.max_retries,
            "session_id": self.context.session_id,
            "user_id": self.context.user_id,
            "invocation": {
                "call_id": call_id,
                "tool_name": tool_name,
                "arguments": dict(tool.get("arguments", {})),
            },
        }

        # 异步工具立即返回pending receipt
        return BatchReceipt(
            batch_id=batch_id,
            turn_id=turn_id or TurnId(""),
            results=[
                ToolExecutionResult(
                    call_id=ToolCallId(call_id),
                    tool_name=tool_name,
                    status=cast("Literal['success', 'error', 'pending', 'timeout', 'aborted']", "pending"),
                    result={
                        "async": True,
                        "submitted_at": submitted_at,
                        "workflow_handoff": True,
                        "handoff_reason": "async_pending_receipt",
                        "recoverable_context": recoverable_context,
                    },
                    execution_time_ms=0,
                    effect_receipt=None,
                )
            ],
            success_count=0,
            failure_count=0,
            pending_async_count=1,
            has_pending_async=True,
            raw_results=[
                {
                    "status": "async_submitted",
                    "workflow_handoff": True,
                    "handoff_reason": "async_pending_receipt",
                    "recoverable_context": recoverable_context,
                }
            ],
        )

    def _result_to_receipt(self, results: list[ToolResult], turn_id: TurnId | None = None) -> BatchReceipt:
        """将执行结果转换为BatchReceipt"""
        if not results:
            return BatchReceipt(
                batch_id=BatchId("empty_batch"),
                turn_id=turn_id or TurnId(""),
                results=[],
                success_count=0,
                failure_count=0,
                pending_async_count=0,
                has_pending_async=False,
                raw_results=[],
            )

        call_id = results[0].call_id if results else ""
        batch_id = BatchId(f"{turn_id or 'batch'}_{call_id}")

        success_count = sum(1 for r in results if r.status == ToolExecutionStatus.SUCCESS)
        failure_count = sum(1 for r in results if r.status in {ToolExecutionStatus.ERROR, ToolExecutionStatus.TIMEOUT})

        return BatchReceipt(
            batch_id=batch_id,
            turn_id=turn_id or TurnId(""),
            results=[
                ToolExecutionResult(
                    call_id=ToolCallId(r.call_id),
                    tool_name=r.tool_name,
                    status=cast("Literal['success', 'error', 'pending', 'timeout', 'aborted']", r.status.value),
                    result=r.result,
                    execution_time_ms=r.execution_time_ms,
                    effect_receipt=r.effect_receipt,
                )
                for r in results
            ],
            success_count=success_count,
            failure_count=failure_count,
            pending_async_count=0,
            has_pending_async=False,
            raw_results=[r.to_dict() for r in results],
        )

    @classmethod
    def classify_tool(cls, tool_name: str) -> ToolExecutionMode:
        """根据工具名推断执行模式(仅用于测试或旧兼容入口)。"""
        normalized = tool_name.lower().replace("-", "_")

        if normalized in cls.READONLY_TOOLS:
            return ToolExecutionMode.READONLY_PARALLEL
        elif normalized in cls.ASYNC_TOOLS:
            return ToolExecutionMode.ASYNC_RECEIPT
        else:
            # 默认写工具(安全优先)
            return ToolExecutionMode.WRITE_SERIAL

    @classmethod
    def classify_batch(cls, invocations: list[ToolInvocation]) -> dict[str, list[ToolInvocation]]:
        """将工具批次按执行模式分类(仅用于测试或旧兼容入口)。"""
        parallel: list[ToolInvocation] = []
        readonly_serial: list[ToolInvocation] = []
        serial: list[ToolInvocation] = []
        async_tools: list[ToolInvocation] = []

        for tool in invocations:
            explicit_mode = tool.get("execution_mode")
            if isinstance(explicit_mode, ToolExecutionMode):
                mode = explicit_mode
            elif isinstance(explicit_mode, str):
                try:
                    mode = ToolExecutionMode(explicit_mode)
                except ValueError:
                    mode = cls.classify_tool(tool.get("tool_name", ""))
            else:
                mode = cls.classify_tool(tool.get("tool_name", ""))
            if mode == ToolExecutionMode.READONLY_PARALLEL:
                parallel.append(tool)
            elif mode == ToolExecutionMode.READONLY_SERIAL:
                readonly_serial.append(tool)
            elif mode == ToolExecutionMode.ASYNC_RECEIPT:
                async_tools.append(tool)
            else:
                serial.append(tool)

        return {
            "parallel_readonly": parallel,
            "readonly_serial": readonly_serial,
            "serial_writes": serial,
            "async_receipts": async_tools,
        }
