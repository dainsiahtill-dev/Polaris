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
import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from typing import Any, Literal, cast

from polaris.cells.control_plane.run_ledger.public import FailureClassV1
from polaris.cells.director.runtime.public import (
    DirectedEffectImmutableItemsV1,
    require_directed_effect_immutable_items,
)
from polaris.cells.roles.kernel.internal.directed_effect_lifecycle import (
    DirectedEffectLifecycleService,
)
from polaris.cells.roles.kernel.internal.speculation.models import (
    CancelToken,
    check_cancel,
)
from polaris.cells.roles.kernel.public.directed_effect_contracts import (
    DeferredDirectorRepairEffectBindingV1,
    DirectedEffectOperationClaimStatusV1,
    DirectedEffectRuntimeDependenciesV1,
    PreparedDirectedEffectBatchV1,
)
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
from polaris.cells.runtime.task_runtime.public import (
    TaskRuntimeExecutionAttemptAuthorityV1,
    TaskRuntimeExecutionAttemptIdentityV1,
)

logger = logging.getLogger(__name__)

_READONLY_MODES = {
    ToolExecutionMode.READONLY_PARALLEL,
    ToolExecutionMode.READONLY_SERIAL,
}


def _directed_effect_failure_partition(
    *,
    claim_status: DirectedEffectOperationClaimStatusV1 | None,
    failed_index: int,
    inventory_ids: tuple[str, ...],
) -> tuple[int, tuple[str, ...]]:
    """Choose the exact unclaimed cleanup suffix without guessing claim state."""

    if failed_index < 0 or failed_index >= len(inventory_ids):
        raise ValueError("failed_index must identify one inventory member")
    if claim_status == "not_claimed":
        return failed_index, ()
    if claim_status == "claimed":
        return failed_index + 1, (inventory_ids[failed_index],)
    return len(inventory_ids), inventory_ids[failed_index:]


def _eval_injected_read_delay_ms() -> int:
    """评测专用：对只读工具注入的人工执行延迟（毫秒）.

    仅用于评测"贵工具档"的端到端 saved_ms——把只读工具的执行墙钟人为放大到
    远超 LLM 抖动噪声，从而能在端到端 ON/OFF 对比中清晰分辨 speculation 隐藏的
    那段延迟。生产默认 0（无注入）。env: ``SPECULATION_EVAL_READ_DELAY_MS``。
    """
    raw = os.environ.get("SPECULATION_EVAL_READ_DELAY_MS")
    if not raw:
        return 0
    try:
        return max(0, int(raw))
    except (TypeError, ValueError):
        return 0


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
    effect_receipt: dict[str, Any] | None = None
    effect_receipt_commit: dict[str, Any] | None = None
    directed_effect_mutation_status: str | None = None
    directed_effect_claim_status: DirectedEffectOperationClaimStatusV1 | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "call_id": self.call_id,
            "tool_name": self.tool_name,
            "status": self.status.value,
            "result": self.result,
            "error": self.error,
            "execution_time_ms": self.execution_time_ms,
            "effect_receipt": self.effect_receipt,
            "effect_receipt_commit": self.effect_receipt_commit,
            "directed_effect_mutation_status": self.directed_effect_mutation_status,
            "directed_effect_claim_status": self.directed_effect_claim_status,
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
        executor: Callable[..., Any],  # async def executor(tool_name, arguments) -> dict
        context: ToolExecutionContext | None = None,
        *,
        directed_effect_runtime: DirectedEffectRuntimeDependenciesV1 | None = None,
        directed_effect_required: bool = False,
        directed_effect_execution_attempt: TaskRuntimeExecutionAttemptIdentityV1 | None = None,
        directed_effect_execution_attempt_authority: TaskRuntimeExecutionAttemptAuthorityV1 | None = None,
        prepared_directed_effect_batch: PreparedDirectedEffectBatchV1 | None = None,
        directed_effect_restrictions_by_call_id: tuple[tuple[str, DirectedEffectImmutableItemsV1], ...] = (),
        directed_effect_dispatch_call_ids: tuple[str, ...] | None = None,
        directed_effect_abort_call_ids: tuple[str, ...] = (),
        directed_effect_repair_bindings_by_call_id: tuple[tuple[str, DeferredDirectorRepairEffectBindingV1], ...] = (),
        directed_effect_rollback_activation_by_call_id: tuple[tuple[str, str], ...] = (),
    ) -> None:
        self.executor = executor
        self.context = context or ToolExecutionContext()
        if (
            directed_effect_runtime is not None
            and type(directed_effect_runtime) is not DirectedEffectRuntimeDependenciesV1
        ):
            raise TypeError("directed_effect_runtime must be exactly DirectedEffectRuntimeDependenciesV1")
        if directed_effect_execution_attempt is not None:
            if type(directed_effect_execution_attempt) is not TaskRuntimeExecutionAttemptIdentityV1:
                raise TypeError(
                    "directed_effect_execution_attempt must be exactly TaskRuntimeExecutionAttemptIdentityV1"
                )
            canonical_attempt = TaskRuntimeExecutionAttemptIdentityV1.from_record(
                directed_effect_execution_attempt.to_record()
            )
            if canonical_attempt != directed_effect_execution_attempt:
                raise ValueError("directed_effect_execution_attempt must be canonical")
        if directed_effect_execution_attempt_authority is not None and not isinstance(
            directed_effect_execution_attempt_authority,
            TaskRuntimeExecutionAttemptAuthorityV1,
        ):
            raise TypeError("directed_effect_execution_attempt_authority must be exact")
        if directed_effect_required and (
            directed_effect_runtime is None
            or directed_effect_execution_attempt is None
            or directed_effect_execution_attempt_authority is None
        ):
            raise ValueError("required directed-effect execution needs runtime dependencies and attempt identity")
        self.directed_effect_runtime = directed_effect_runtime
        self.directed_effect_required = bool(directed_effect_required)
        self.directed_effect_execution_attempt = directed_effect_execution_attempt
        self.directed_effect_execution_attempt_authority = directed_effect_execution_attempt_authority
        if (
            prepared_directed_effect_batch is not None
            and type(prepared_directed_effect_batch) is not PreparedDirectedEffectBatchV1
        ):
            raise TypeError("prepared_directed_effect_batch must be exact")
        canonical_restrictions: list[tuple[str, DirectedEffectImmutableItemsV1]] = []
        for call_id, restrictions in directed_effect_restrictions_by_call_id:
            normalized_call_id = str(call_id).strip()
            if not normalized_call_id or normalized_call_id != call_id:
                raise ValueError("directed-effect restriction call id must be canonical")
            canonical_restrictions.append(
                (
                    normalized_call_id,
                    require_directed_effect_immutable_items(
                        "current_job_token_restriction_evidence",
                        restrictions,
                    ),
                )
            )
        if len(dict(canonical_restrictions)) != len(canonical_restrictions):
            raise ValueError("directed-effect restriction call ids must be unique")
        if prepared_directed_effect_batch is not None:
            prepared_call_ids = tuple(
                member.member.tool_call_id for member in prepared_directed_effect_batch.prepared_members
            )
            if tuple(call_id for call_id, _ in canonical_restrictions) != prepared_call_ids:
                raise ValueError("directed-effect restrictions must cover exact prepared inventory")
        elif canonical_restrictions:
            raise ValueError("directed-effect restrictions require a prepared batch")
        self.prepared_directed_effect_batch = prepared_directed_effect_batch
        self.directed_effect_restrictions_by_call_id = tuple(canonical_restrictions)
        prepared_call_ids = (
            tuple(member.member.tool_call_id for member in prepared_directed_effect_batch.prepared_members)
            if prepared_directed_effect_batch is not None
            else ()
        )
        dispatch_call_ids = (
            prepared_call_ids if directed_effect_dispatch_call_ids is None else directed_effect_dispatch_call_ids
        )
        if not isinstance(dispatch_call_ids, tuple) or not isinstance(directed_effect_abort_call_ids, tuple):
            raise TypeError("directed-effect dispatch and abort call ids must be immutable tuples")
        if len(set(dispatch_call_ids)) != len(dispatch_call_ids) or len(set(directed_effect_abort_call_ids)) != len(
            directed_effect_abort_call_ids
        ):
            raise ValueError("directed-effect dispatch and abort call ids must be unique")
        if set(dispatch_call_ids).intersection(directed_effect_abort_call_ids):
            raise ValueError("directed-effect dispatch and abort call ids must be disjoint")
        if {*dispatch_call_ids, *directed_effect_abort_call_ids} != set(prepared_call_ids) or len(
            (*dispatch_call_ids, *directed_effect_abort_call_ids)
        ) != len(prepared_call_ids):
            raise ValueError("directed-effect dispatch and abort call ids must partition the prepared inventory")
        self.directed_effect_dispatch_call_ids = dispatch_call_ids
        self.directed_effect_abort_call_ids = directed_effect_abort_call_ids
        repair_bindings = tuple(directed_effect_repair_bindings_by_call_id)
        if not all(
            isinstance(call_id, str)
            and call_id.strip() == call_id
            and type(binding) is DeferredDirectorRepairEffectBindingV1
            and binding.tool_call_id == call_id
            for call_id, binding in repair_bindings
        ):
            raise ValueError("directed-effect repair bindings must be canonical")
        if len(dict(repair_bindings)) != len(repair_bindings):
            raise ValueError("directed-effect repair binding call ids must be unique")
        if repair_bindings and set(dict(repair_bindings)) != set(prepared_call_ids):
            raise ValueError("directed-effect repair bindings must cover exact prepared inventory")
        activation_pairs = tuple(directed_effect_rollback_activation_by_call_id)
        if len(dict(activation_pairs)) != len(activation_pairs):
            raise ValueError("directed-effect rollback activation call ids must be unique")
        if activation_pairs and (
            {rollback_id for rollback_id, _ in activation_pairs} != set(directed_effect_abort_call_ids)
            or any(forward_id not in set(dispatch_call_ids) for _, forward_id in activation_pairs)
        ):
            raise ValueError("directed-effect rollback activation must bind the dispatch partition")
        if repair_bindings and not activation_pairs:
            raise ValueError("deferred repair bindings require rollback activation mapping")
        if activation_pairs and not repair_bindings:
            raise ValueError("rollback activation mapping requires deferred repair bindings")
        self.directed_effect_repair_bindings_by_call_id = repair_bindings
        self.directed_effect_rollback_activation_by_call_id = activation_pairs

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

    @staticmethod
    def _thaw_directed_effect_value(value: object) -> object:
        items = getattr(value, "items", None)
        if isinstance(items, tuple):
            if all(isinstance(item, tuple) and len(item) == 2 and isinstance(item[0], str) for item in items):
                return {key: ToolBatchRuntime._thaw_directed_effect_value(item) for key, item in items}
            return [ToolBatchRuntime._thaw_directed_effect_value(item) for item in items]
        if isinstance(value, tuple):
            return [ToolBatchRuntime._thaw_directed_effect_value(item) for item in value]
        return value

    def _release_directed_effect_batch(self) -> None:
        runtime = self.directed_effect_runtime
        prepared = self.prepared_directed_effect_batch
        if runtime is None or prepared is None:
            return
        try:
            release = runtime.fence_admin_port.release_batch(
                prepared.parent_binding.correlation.batch_id,
                prepared.execution_attempt,
            )
        except (AttributeError, OSError, RuntimeError, TypeError, ValueError):
            logger.exception("directed-effect batch fence release failed")
            return
        if not release.ok or release.status not in {"released", "absent"}:
            logger.error(
                "directed-effect batch fence release denied: batch_id=%s error=%s",
                prepared.parent_binding.correlation.batch_id,
                release.error_code or "deo_context_release_failed",
            )

    def _deferred_repair_invocation(self, call_id: str) -> ToolInvocation:
        """Rebuild one rollback invocation from its sealed immutable effect binding."""

        binding = dict(self.directed_effect_repair_bindings_by_call_id).get(call_id)
        if binding is None or binding.tool_call_id != call_id:
            raise RuntimeError("directed_effect_repair_binding_unavailable")
        return ToolInvocation(
            call_id=ToolCallId(call_id),
            tool_name=binding.effect.tool_name,
            arguments={key: self._thaw_directed_effect_value(value) for key, value in binding.effect.arguments},
        )

    async def _execute_directed_effect(
        self,
        tool: ToolInvocation,
    ) -> ToolResult:
        """Claim, register, revalidate, consume, then physically execute one mutation."""

        start_ms = int(time.time() * 1000)
        call_id = str(tool.call_id)
        tool_name = str(tool.tool_name)
        runtime = self.directed_effect_runtime
        prepared = self.prepared_directed_effect_batch
        authority = self.directed_effect_execution_attempt_authority
        restrictions = dict(self.directed_effect_restrictions_by_call_id).get(call_id)
        repair_binding = dict(self.directed_effect_repair_bindings_by_call_id).get(call_id)
        if runtime is None or prepared is None or authority is None or restrictions is None:
            return ToolResult(
                call_id=call_id,
                tool_name=tool_name,
                status=ToolExecutionStatus.ERROR,
                error="directed_effect_prepared_context_unavailable",
                directed_effect_claim_status="not_claimed",
            )
        index = dict(prepared.call_id_index).get(call_id)
        if index is None:
            return ToolResult(
                call_id=call_id,
                tool_name=tool_name,
                status=ToolExecutionStatus.ERROR,
                error="directed_effect_member_not_prepared",
                directed_effect_claim_status="not_claimed",
            )
        lifecycle = DirectedEffectLifecycleService(
            policy_snapshot_port=runtime.policy_snapshot_port,
        )
        claim = await lifecycle.claim_execution_context(
            prepared_batch=prepared,
            execution_attempt_authority=authority,
            tool_call_id=call_id,
            current_job_token_restriction_evidence=restrictions,
        )
        context = claim.context
        if claim.status != "claimed" or context is None:
            return ToolResult(
                call_id=call_id,
                tool_name=tool_name,
                status=ToolExecutionStatus.ERROR,
                error=str(claim.error_code or "directed_effect_claim_denied"),
                execution_time_ms=int(time.time() * 1000) - start_ms,
                directed_effect_claim_status=claim.operation_claim_status,
            )
        try:
            registration = runtime.fence_admin_port.register(context)
        except (AttributeError, OSError, RuntimeError, TypeError, ValueError):
            return ToolResult(
                call_id=call_id,
                tool_name=tool_name,
                status=ToolExecutionStatus.ERROR,
                error="directed_effect_fence_registration_failed",
                execution_time_ms=int(time.time() * 1000) - start_ms,
                directed_effect_claim_status="claimed",
            )
        if (
            registration.ok is not True
            or registration.status != "registered"
            or registration.context_id != context.context_id
        ):
            return ToolResult(
                call_id=call_id,
                tool_name=tool_name,
                status=ToolExecutionStatus.ERROR,
                error=str(registration.error_code or "directed_effect_fence_registration_denied"),
                execution_time_ms=int(time.time() * 1000) - start_ms,
                directed_effect_claim_status="claimed",
            )
        prepared_member = prepared.prepared_members[index]
        bound_snapshot = prepared_member.policy_binding.bound_snapshot
        if bound_snapshot is None:
            return ToolResult(
                call_id=call_id,
                tool_name=tool_name,
                status=ToolExecutionStatus.ERROR,
                error="directed_effect_bound_snapshot_unavailable",
                execution_time_ms=int(time.time() * 1000) - start_ms,
                directed_effect_claim_status="claimed",
            )
        normalized_arguments = bound_snapshot.authorization_binding.classification_evidence.normalized_arguments
        try:
            mutation = await runtime.mutation_port.execute_mutation(
                context,
                context.normalized_tool_name,
                normalized_arguments,
                repair_binding,
            )
        except (AttributeError, OSError, RuntimeError, TypeError, ValueError):
            return ToolResult(
                call_id=call_id,
                tool_name=tool_name,
                status=ToolExecutionStatus.ERROR,
                error="directed_effect_mutation_port_failed",
                execution_time_ms=int(time.time() * 1000) - start_ms,
                directed_effect_mutation_status="unknown",
                directed_effect_claim_status="claimed",
            )
        if mutation.status != "executed" or not mutation.ok or mutation.tool_result is None:
            failure_payload = None
            if mutation.tool_result is not None:
                failure_payload = {
                    key: self._thaw_directed_effect_value(value) for key, value in mutation.tool_result.payload
                }
            return ToolResult(
                call_id=call_id,
                tool_name=tool_name,
                status=ToolExecutionStatus.ERROR,
                result=failure_payload,
                error=str(mutation.error_code or "directed_effect_mutation_failed"),
                execution_time_ms=int(time.time() * 1000) - start_ms,
                directed_effect_mutation_status=mutation.status,
                directed_effect_claim_status="claimed",
            )
        payload = {key: self._thaw_directed_effect_value(value) for key, value in mutation.tool_result.payload}
        result_payload = payload.get("result", payload)
        effect_receipt = payload.get("effect_receipt")
        effect_receipt_commit = payload.get("effect_receipt_commit")
        if effect_receipt is None and isinstance(result_payload, dict):
            effect_receipt = result_payload.get("effect_receipt")
        if effect_receipt_commit is None and isinstance(result_payload, dict):
            effect_receipt_commit = result_payload.get("effect_receipt_commit")
        if not isinstance(effect_receipt, dict):
            return ToolResult(
                call_id=call_id,
                tool_name=tool_name,
                status=ToolExecutionStatus.ERROR,
                result=result_payload,
                error="missing_effect_receipt",
                execution_time_ms=int(time.time() * 1000) - start_ms,
                directed_effect_mutation_status=mutation.status,
                directed_effect_claim_status="claimed",
            )
        return ToolResult(
            call_id=call_id,
            tool_name=tool_name,
            status=ToolExecutionStatus.SUCCESS,
            result=result_payload,
            execution_time_ms=int(time.time() * 1000) - start_ms,
            effect_receipt=effect_receipt,
            effect_receipt_commit=(effect_receipt_commit if isinstance(effect_receipt_commit, dict) else None),
            directed_effect_mutation_status=mutation.status,
            directed_effect_claim_status="claimed",
        )

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

        if self.directed_effect_required and (serial_writes or async_receipts):
            prepared = self.prepared_directed_effect_batch
            if prepared is None:
                raise RuntimeError("directed_effect_batch_not_prepared")
            planned_call_ids = tuple(str(tool.call_id) for tool in [*serial_writes, *async_receipts])
            dispatch_call_ids = self.directed_effect_dispatch_call_ids
            if set(planned_call_ids) != set(dispatch_call_ids) or len(planned_call_ids) != len(dispatch_call_ids):
                raise RuntimeError("directed_effect_prepared_inventory_mismatch")

        receipts: list[BatchReceipt] = []

        # 1. 并行执行只读工具
        if parallel_readonly:
            parallel_receipts = await self._execute_parallel(parallel_readonly, turn_id, context=effective_context)
            receipts.extend(parallel_receipts)

        # 2. 串行执行只读工具(存在顺序依赖时)
        for tool in readonly_serial:
            result = await self._execute_single(tool, turn_id, context=effective_context)
            receipts.append(self._result_to_receipt([result], turn_id))

        mutation_tools = [*serial_writes, *async_receipts]
        if mutation_tools and self.directed_effect_required:
            mutation_by_call_id = {str(tool.call_id): tool for tool in mutation_tools}
            prepared = self.prepared_directed_effect_batch
            ordered_call_ids = tuple(self.directed_effect_dispatch_call_ids)
            all_dispatched_succeeded = True
            successful_forward_call_ids: list[str] = []
            failed_result: ToolResult | None = None
            failed_index = -1
            release_fence = False
            try:
                for index, call_id in enumerate(ordered_call_ids):
                    mutation_tool = mutation_by_call_id.get(call_id)
                    if mutation_tool is None:
                        result = ToolResult(
                            call_id=call_id,
                            tool_name="unknown",
                            status=ToolExecutionStatus.ERROR,
                            error="directed_effect_prepared_inventory_mismatch",
                        )
                    else:
                        result = await self._execute_directed_effect(mutation_tool)
                    receipts.append(self._result_to_receipt([result], turn_id))
                    if result.status is not ToolExecutionStatus.SUCCESS:
                        all_dispatched_succeeded = False
                        failed_result = result
                        failed_index = index
                        break
                    successful_forward_call_ids.append(call_id)
                if all_dispatched_succeeded and self.directed_effect_abort_call_ids:
                    runtime = self.directed_effect_runtime
                    authority = self.directed_effect_execution_attempt_authority
                    if runtime is None or authority is None or prepared is None:
                        raise RuntimeError("directed_effect_abort_authority_unavailable")
                    DirectedEffectLifecycleService(
                        policy_snapshot_port=runtime.policy_snapshot_port,
                    ).abort_unclaimed_members(
                        prepared_batch=prepared,
                        execution_attempt_authority=authority,
                        tool_call_ids=self.directed_effect_abort_call_ids,
                        reason="contingency_not_activated",
                    )
                    release_fence = True
                elif all_dispatched_succeeded:
                    release_fence = True
                elif (
                    not all_dispatched_succeeded
                    and prepared is not None
                    and self.directed_effect_rollback_activation_by_call_id
                ):
                    runtime = self.directed_effect_runtime
                    authority = self.directed_effect_execution_attempt_authority
                    if runtime is None or authority is None:
                        raise RuntimeError("directed_effect_abort_authority_unavailable")
                    if failed_result is None or failed_index < 0:
                        raise RuntimeError("directed_effect_failed_result_unavailable")
                    failed_receipt_index = len(receipts) - 1
                    lifecycle = DirectedEffectLifecycleService(
                        policy_snapshot_port=runtime.policy_snapshot_port,
                    )
                    activation_by_rollback = dict(self.directed_effect_rollback_activation_by_call_id)
                    activated_forward_ids = set(successful_forward_call_ids)
                    if failed_result.directed_effect_mutation_status == "executed":
                        activated_forward_ids.add(failed_result.call_id)
                    ambiguous_forward_ids = (
                        {failed_result.call_id}
                        if failed_result.directed_effect_mutation_status in {"failed", "unknown"}
                        else set()
                    )
                    activated_rollbacks = tuple(
                        rollback_id
                        for rollback_id, forward_id in self.directed_effect_rollback_activation_by_call_id
                        if forward_id in activated_forward_ids
                    )
                    aborted_ids: list[str] = []
                    executed_rollback_ids: list[str] = []
                    preserved_ids: list[str] = []
                    inventory_ids = tuple(member.member.tool_call_id for member in prepared.prepared_members)
                    failed_member_index = dict(prepared.call_id_index)[failed_result.call_id]
                    cleanup_start, initially_preserved_ids = _directed_effect_failure_partition(
                        claim_status=failed_result.directed_effect_claim_status,
                        failed_index=failed_member_index,
                        inventory_ids=inventory_ids,
                    )
                    preserved_ids.extend(initially_preserved_ids)

                    for call_id in inventory_ids[cleanup_start:]:
                        if call_id in ordered_call_ids:
                            lifecycle.abort_unclaimed_members(
                                prepared_batch=prepared,
                                execution_attempt_authority=authority,
                                tool_call_ids=(call_id,),
                                reason="deferred_repair_forward_failed",
                            )
                            aborted_ids.append(call_id)
                            continue
                        activating_forward = activation_by_rollback.get(call_id)
                        if activating_forward is None:
                            raise RuntimeError("directed_effect_rollback_activation_unavailable")
                        if activating_forward in ambiguous_forward_ids:
                            preserved_ids.extend(
                                item
                                for item in inventory_ids[dict(prepared.call_id_index)[call_id] :]
                                if item not in preserved_ids
                            )
                            break
                        if activating_forward in activated_forward_ids:
                            rollback_result = await self._execute_directed_effect(
                                self._deferred_repair_invocation(call_id)
                            )
                            receipts.append(self._result_to_receipt([rollback_result], turn_id))
                            if rollback_result.status is not ToolExecutionStatus.SUCCESS:
                                preserved_ids.extend(
                                    item
                                    for item in inventory_ids[dict(prepared.call_id_index)[call_id] :]
                                    if item not in preserved_ids
                                )
                                break
                            executed_rollback_ids.append(call_id)
                            continue
                        lifecycle.abort_unclaimed_members(
                            prepared_batch=prepared,
                            execution_attempt_authority=authority,
                            tool_call_ids=(call_id,),
                            reason="deferred_repair_forward_failed",
                        )
                        aborted_ids.append(call_id)
                    if receipts:
                        failed_receipt = receipts[failed_receipt_index]
                        raw_results = [dict(row) for row in failed_receipt.raw_results]
                        if raw_results:
                            raw_results[0]["directed_effect_activated_rollback_call_ids"] = list(activated_rollbacks)
                            raw_results[0]["directed_effect_executed_rollback_call_ids"] = list(executed_rollback_ids)
                            raw_results[0]["directed_effect_aborted_call_ids"] = list(aborted_ids)
                            raw_results[0]["directed_effect_preserved_call_ids"] = list(preserved_ids)
                        receipts[failed_receipt_index] = failed_receipt.model_copy(update={"raw_results": raw_results})
            finally:
                # Failure/ambiguity keeps process-local fences for DEO-3
                # reconciliation. Release only after a complete success path
                # has terminalized every inactive inventory member.
                if release_fence:
                    self._release_directed_effect_batch()
        else:
            # Compatibility path for roles/runs where DEO is not enabled.
            for tool in serial_writes:
                result = await self._execute_single(tool, turn_id, context=effective_context)
                if result.status == ToolExecutionStatus.SUCCESS and not result.effect_receipt:
                    logger.error(
                        "Write tool %s (call_id=%s) succeeded without effect_receipt; marking the tool lifecycle as failed",
                        result.tool_name,
                        result.call_id,
                    )
                    result = self._missing_effect_receipt_result(result)
                receipts.append(self._result_to_receipt([result], turn_id))
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
        loop = asyncio.get_running_loop()
        _pending_tasks: list[asyncio.Task[ToolResult]] = [
            loop.create_task(self._execute_single(tool, turn_id, context=context)) for tool in tools
        ]

        try:
            results = await asyncio.gather(*_pending_tasks, return_exceptions=True)
        finally:
            # Cancel any still-running tasks on early exit (e.g. CancelledError
            # propagating from the caller or from a CancelToken check).
            for t in _pending_tasks:
                if not t.done():
                    t.cancel()
            # Allow cancelled tasks to finish their cancellation path so they
            # don't become orphaned coroutines.
            still_running = [t for t in _pending_tasks if not t.done()]
            if still_running:
                await asyncio.gather(*still_running, return_exceptions=True)

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
                successful_results.append(result)

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
                receipt = self._result_to_receipt([result], turn_id)
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

            # 执行工具（评测可对只读工具注入人工延迟，模拟贵工具档；延迟在
            # wait_for 内，受同一超时与取消约束，shadow 与 authoritative 路径
            # 共用本方法，故两边一致受影响——这正是端到端 saved_ms 的测量基础）。
            delay_ms = _eval_injected_read_delay_ms()
            is_readonly = tool.get("execution_mode") in _READONLY_MODES

            async def _invoke() -> Any:
                if delay_ms > 0 and is_readonly:
                    await asyncio.sleep(delay_ms / 1000.0)
                return await self.executor(tool_name, arguments)

            result = await asyncio.wait_for(_invoke(), timeout=timeout)

            # Phase 2: 执行后再检查取消，防止取消后仍返回 stale 结果
            check_cancel(effective_context.cancel_token)

            execution_time_ms = int(time.time() * 1000) - start_ms

            # 归一化结果
            if isinstance(result, dict):
                payload = result.get("result", result)
                effect_receipt = result.get("effect_receipt")
                effect_receipt_commit = result.get("effect_receipt_commit")
                if effect_receipt is None and isinstance(payload, dict):
                    nested_receipt = payload.get("effect_receipt")
                    if isinstance(nested_receipt, dict):
                        effect_receipt = nested_receipt
                if effect_receipt_commit is None and isinstance(payload, dict):
                    nested_commit = payload.get("effect_receipt_commit")
                    if isinstance(nested_commit, dict):
                        effect_receipt_commit = nested_commit

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
                    effect_receipt_commit=(effect_receipt_commit if isinstance(effect_receipt_commit, dict) else None),
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
                    status="pending",
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

    @staticmethod
    def _missing_effect_receipt_result(result: ToolResult) -> ToolResult:
        """Convert a write result without effect evidence into a fail-closed result."""

        return ToolResult(
            call_id=result.call_id,
            tool_name=result.tool_name,
            status=ToolExecutionStatus.ERROR,
            result={
                "failure_class": FailureClassV1.MISSING_EFFECT_RECEIPT.value,
                "responsible_layer": "tool_lifecycle",
                "original_result": result.result,
            },
            error="Write tool succeeded without effect_receipt; tool lifecycle receipt is incomplete.",
            execution_time_ms=result.execution_time_ms,
            effect_receipt=None,
        )

    @staticmethod
    def _effect_receipt_commit_for_result(result: ToolResult) -> dict[str, Any] | None:
        if isinstance(result.effect_receipt_commit, dict):
            return dict(result.effect_receipt_commit)
        if not isinstance(result.result, dict) or not isinstance(result.effect_receipt, dict):
            return None
        nested_receipt = result.result.get("effect_receipt")
        nested_commit = result.result.get("effect_receipt_commit")
        if nested_receipt != result.effect_receipt or not isinstance(nested_commit, dict):
            return None
        return dict(nested_commit)

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
                    effect_receipt_commit=self._effect_receipt_commit_for_result(r),
                )
                for r in results
            ],
            success_count=success_count,
            failure_count=failure_count,
            pending_async_count=0,
            has_pending_async=False,
            raw_results=[
                {
                    **r.to_dict(),
                    "effect_receipt_commit": self._effect_receipt_commit_for_result(r),
                }
                for r in results
            ],
            effect_receipts=[dict(r.effect_receipt) for r in results if isinstance(r.effect_receipt, dict)],
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
