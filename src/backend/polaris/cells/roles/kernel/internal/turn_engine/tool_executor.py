"""Single tool executor - Invoke kernel single-tool executor defensively.

# -*- coding: utf-8 -*-
UTF-8 编码验证: 本文所有文本使用 UTF-8

职责：
    封装 TurnEngine 中对 kernel._execute_single_tool 的调用，
    包括策略门控、配额槽位管理、错误边界和事件发射。
"""

from __future__ import annotations

import logging
import time
import traceback
import uuid
from typing import Any

from polaris.cells.roles.kernel.internal.tool_gateway import ToolAuthorizationError
from polaris.kernelone.events.typed import ToolError, ToolErrorKind, emit_event

from .quota_manager import TurnQuotaManager

logger = logging.getLogger(__name__)


class SingleToolExecutor:
    """Executes a single tool call with policy gating and quota control."""

    def __init__(self, kernel: Any, quota_manager: TurnQuotaManager | None = None) -> None:
        """Initialize with kernel and optional quota manager.

        Args:
            kernel: RoleExecutionKernel instance.
            quota_manager: Optional TurnQuotaManager for DI.
        """
        self._kernel = kernel
        self._quota_manager = quota_manager or TurnQuotaManager()

    async def execute(
        self,
        *,
        profile: Any,
        request: Any,
        call: Any,
        round_index: int = 0,
    ) -> dict[str, Any]:
        """Invoke kernel single-tool executor with defensive error boundaries.

        Args:
            profile: RoleProfile instance.
            request: RoleTurnRequest instance.
            call: ParsedToolCall or dict with tool/args.
            round_index: Current round index for telemetry.

        Returns:
            Tool result dict.
        """
        kernel = self._kernel
        execute_fn = kernel._execute_single_tool

        if isinstance(call, dict):
            tool_name_raw = call.get("tool")
            tool_args = call.get("args", {})
        else:
            tool_name_raw = getattr(call, "tool", None)
            tool_args = getattr(call, "args", {})

        tool_name: str = str(tool_name_raw or "").strip() if tool_name_raw else ""

        if not tool_name:
            logger.error("[TurnEngine] 工具调用缺少 tool_name: call=%s", call)
            return {
                "success": False,
                "tool": "",
                "error": "MISSING_TOOL_NAME: tool name is required",
                "error_type": "InvalidToolCall",
            }

        from polaris.cells.roles.kernel.internal.kernel.tool_executor import KernelToolExecutor

        executor = KernelToolExecutor(kernel, kernel.workspace)
        current_turn_id = str(getattr(request, "run_id", "") or "")
        _cached_gw = getattr(kernel, "_cached_tool_gateway", None)
        _cached_gw_profile = getattr(kernel, "_cached_gateway_profile", None)
        _cached_gw_turn_id = getattr(kernel, "_cached_gateway_turn_id", None) or ""
        if _cached_gw is not None and _cached_gw_profile is profile:
            gateway = _cached_gw
            if current_turn_id != _cached_gw_turn_id:
                gateway.reset_execution_count()
                kernel._cached_gateway_turn_id = current_turn_id
        else:
            gateway = executor.create_gateway(
                profile=profile,
                request=request,
                tool_gateway=getattr(kernel, "_tool_gateway", None),
            )
            kernel._cached_tool_gateway = gateway
            kernel._cached_gateway_profile = profile
            kernel._cached_gateway_turn_id = current_turn_id

        gateway.set_iteration(round_index)

        can_execute, reason = gateway.check_tool_permission(tool_name, tool_args)
        if not can_execute:
            logger.debug(
                "[TurnEngine] 工具调用被策略拦截: tool=%s reason=%s",
                tool_name,
                reason,
            )
            return {
                "success": False,
                "tool": tool_name,
                "error": f"TOOL_BLOCKED: {reason}",
                "authorized": False,
                "policy": "ToolPolicy",
                "loop_break": False,
                "authorization_failure": True,
            }

        _tool_quota_agent_id = self._quota_manager.build_agent_id(
            role=str(getattr(profile, "role_id", "") or "unknown"),
            workspace=str(kernel.workspace or ""),
            run_id=str(getattr(request, "run_id", "") or "") or None,
        )
        acquired = self._quota_manager.acquire_concurrent_tool(_tool_quota_agent_id)
        if not acquired:
            logger.debug(
                "[TurnEngine] Concurrent tool quota exceeded: agent=%s",
                _tool_quota_agent_id,
            )
            return {
                "success": False,
                "tool": tool_name,
                "error": "CONCURRENT_TOOL_QUOTA_EXCEEDED: Maximum concurrent tools limit reached",
                "error_type": "QuotaExceeded",
            }

        _start_time = time.monotonic()
        try:
            result = await execute_fn(tool_name, tool_args, context={"profile": profile, "request": request})
            return result
        except ToolAuthorizationError as exc:
            _duration_ms = int((time.monotonic() - _start_time) * 1000)
            logger.debug("[TurnEngine] 工具授权失败: tool=%s error=%s", tool_name, str(exc))
            _call_id = getattr(call, "call_id", "") or getattr(call, "id", "") or str(uuid.uuid4().hex[:12])
            _error_event = ToolError.create(
                tool_name=tool_name,
                tool_call_id=_call_id,
                error=str(exc),
                error_type=ToolErrorKind.PERMISSION,
                stack_trace=None,
                duration_ms=_duration_ms,
                run_id=str(getattr(request, "run_id", "") or ""),
                workspace=str(kernel.workspace or ""),
            )
            await emit_event(_error_event)
            return {
                "success": False,
                "tool": tool_name,
                "error": str(exc),
                "error_type": "ToolAuthorizationError",
                "retryable": False,
                "blocked": True,
                "loop_break": False,
                "authorization_failure": True,
            }
        except (RuntimeError, ValueError) as exc:
            _duration_ms = int((time.monotonic() - _start_time) * 1000)
            logger.exception("[TurnEngine] 工具执行异常: tool=%s args=%s", tool_name, tool_args)
            _call_id = getattr(call, "call_id", "") or getattr(call, "id", "") or str(uuid.uuid4().hex[:12])
            _error_event = ToolError.create(
                tool_name=tool_name,
                tool_call_id=_call_id,
                error=str(exc),
                error_type=ToolErrorKind.EXCEPTION,
                stack_trace=traceback.format_exc(),
                duration_ms=_duration_ms,
                run_id=str(getattr(request, "run_id", "") or ""),
                workspace=str(kernel.workspace or ""),
            )
            await emit_event(_error_event)
            return {
                "success": False,
                "tool": tool_name,
                "error": str(exc),
                "error_type": type(exc).__name__,
            }
        finally:
            self._quota_manager.release_concurrent_tool(_tool_quota_agent_id)


__all__ = ["SingleToolExecutor"]
