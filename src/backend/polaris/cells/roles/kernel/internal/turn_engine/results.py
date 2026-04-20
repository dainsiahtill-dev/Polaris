"""TurnEngine result builders - RoleTurnResult construction helpers.

# -*- coding: utf-8 -*-
UTF-8 编码验证: 本文所有文本使用 UTF-8

Blueprint: §10 TurnEngine - Wave 3 Results Extraction

职责：
    提供 RoleTurnResult 构造辅助函数，供 run() / run_stream() 使用。
    这些函数是模块级的独立 helper，不依赖实例状态。

Wave 3 提取内容:
    - _make_error_result: 构造错误 RoleTurnResult
    - _build_stream_complete_result: 构造流式 complete 事件的 RoleTurnResult

设计原则：
    - 模块级函数，可独立调用
    - 类型安全，完整的类型注解
    - 保持向后兼容，不修改现有行为
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from polaris.cells.roles.profile.internal.schema import RoleTurnResult
    from polaris.cells.roles.profile.public.service import RoleProfile


def make_error_result(
    error: str,
    profile_version: str,
    prompt_fingerprint: Any,
    tool_policy_id: str,
    metadata: dict[str, Any] | None = None,
) -> RoleTurnResult:
    """构造错误 RoleTurnResult。

    Args:
        error: 错误信息。
        profile_version: Profile 版本。
        prompt_fingerprint: Prompt 指纹。
        tool_policy_id: 工具策略 ID。
        metadata: 可选的元数据。

    Returns:
        RoleTurnResult 实例，标记为失败。
    """
    from polaris.cells.roles.profile.internal.schema import RoleTurnResult

    return RoleTurnResult(
        content="",
        error=error,
        profile_version=profile_version,
        prompt_fingerprint=prompt_fingerprint,
        tool_policy_id=tool_policy_id,
        execution_stats={},
        is_complete=False,
        metadata=dict(metadata or {}),
    )


def build_stream_complete_result(
    content: str,
    thinking: str | None,
    all_tool_calls: list[dict[str, Any]],
    all_tool_results: list[dict[str, Any]],
    profile: RoleProfile,
    fingerprint: Any,
    rounds: int,
    metadata: dict[str, Any] | None = None,
    *,
    error: str | None = None,
    is_complete: bool = True,
    needs_confirmation: bool = False,
    turn_count: int | None = None,
    turn_events_metadata: list[dict[str, Any]] | None = None,
) -> RoleTurnResult:
    """构造流式 complete 事件的 RoleTurnResult。

    Args:
        content: 最终内容。
        thinking: 思考内容。
        all_tool_calls: 所有工具调用记录。
        all_tool_results: 所有工具结果记录。
        profile: RoleProfile 实例。
        fingerprint: Prompt 指纹。
        rounds: 工具循环轮次。
        metadata: 可选的元数据。
        error: 可选的错误信息。
        is_complete: 是否完成。
        needs_confirmation: 是否需要确认。
        turn_count: 可选的 turn 计数。
        turn_events_metadata: 可选的事件元数据列表。

    Returns:
        RoleTurnResult 实例。
    """
    from polaris.cells.roles.profile.internal.schema import RoleTurnResult

    execution_stats = {
        "stream_tool_rounds": rounds,
        "tool_calls_count": len(all_tool_calls),
        "tool_results_count": len(all_tool_results),
    }
    if turn_count is not None:
        execution_stats["turn_count"] = int(turn_count)

    # Aggregate error context from tool results for Workflow decisions
    # This enables Workflow to make decisions based on error_type instead of pure retry counts
    # Handle both 'ok' (AgentAccelToolExecutor native) and 'success' (RoleToolGateway wrapper) flags
    tool_execution_error: str | None = None
    should_retry = False
    for tr in all_tool_results:
        if isinstance(tr, dict):
            # Check both 'ok' (kernel path) and 'success' (RoleToolGateway path) flags
            ok_flag = tr.get("ok")
            success_flag = tr.get("success")
            is_failure = ok_flag is False or success_flag is False
            if is_failure:
                # Collect error_type from failed tools (last one wins for non-None)
                # Prefer top-level 'error_type' (RoleToolGateway), fall back to
                # 'payload.error_type' (AgentAccelToolExecutor wrapped result)
                et = tr.get("error_type")
                if not et:
                    payload = tr.get("payload", {})
                    if isinstance(payload, dict):
                        et = payload.get("error_type")
                if et:
                    tool_execution_error = et
                # If any failed tool is retryable, mark should_retry=True
                retryable = tr.get("retryable", False)
                if not retryable:
                    payload = tr.get("payload", {})
                    if isinstance(payload, dict):
                        retryable = payload.get("retryable", False)
                if retryable:
                    should_retry = True

    return RoleTurnResult(
        content=content,
        thinking=thinking,
        tool_calls=list(all_tool_calls),
        tool_results=list(all_tool_results),
        profile_version=profile.version,
        prompt_fingerprint=fingerprint,
        tool_policy_id=getattr(profile, "tool_policy_id", "") or getattr(profile.tool_policy, "policy_id", "")
        if profile
        else "",
        execution_stats=execution_stats,
        error=error,
        is_complete=is_complete,
        needs_confirmation=needs_confirmation,
        tool_execution_error=tool_execution_error,
        should_retry=should_retry,
        metadata=dict(metadata or {}),
        turn_events_metadata=list(turn_events_metadata) if turn_events_metadata else [],
    )


__all__ = [
    "build_stream_complete_result",
    "make_error_result",
]
