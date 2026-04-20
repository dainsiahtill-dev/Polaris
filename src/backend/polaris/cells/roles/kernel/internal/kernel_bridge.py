"""TurnEngine 桥接到现有 kernel.run() / run_stream()

# -*- coding: utf-8 -*-
UTF-8 编码验证: 本文所有文本使用 UTF-8

Phase 7 状态: ✅ TurnEngine.run() / run_stream() 已实现（Phase 7 完成）
kernel.run() / kernel.run_stream() 已收敛（Phase 7 完成）

本文档记录 Phase 2 → Phase 7 的迁移路径，已废弃，仅作历史参考。

Phase 7 完成情况：
    ✓ TurnEngine.run() 已实现，使用 kernel._llm_caller + kernel._output_parser
    ✓ TurnEngine.run_stream() 已实现，共用核心循环逻辑
    ✓ kernel.run() facade 已收敛
    ✓ kernel.run_stream() facade 已收敛
    ✓ workflow_adapter.execute_role_with_tools() for 循环已移除

Phase 3 目标（待做）：
    - ProviderAdapter 替代 kernel._llm_caller
    - PolicyLayer 替代 ConversationState.is_within_budget()
    - Phase 4 待接入: ToolRuntime 替代 kernel._execute_single_tool

使用示例（Phase 7 已过时，保留供参考）：
    engine = TurnEngine(kernel=kernel_instance)
    result = await engine.run(request, role)
    async for event in engine.run_stream(request, role):
        yield event
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from polaris.cells.roles.kernel.public.transcript_ir import TranscriptDelta
    from polaris.cells.roles.profile.internal.schema import RoleTurnRequest, RoleTurnResult


# ─────────────────────────────────────────────────────────────────────────────
# Phase 2 桥接常量（供 kernel.py 在 Phase 2 → 3 迁移时参考）
# ─────────────────────────────────────────────────────────────────────────────

# Phase 2: kernel 仍使用 ToolLoopController
# Phase 3: 替换为 TurnEngine.SafetyState
KERNEL_TO_TURN_ENGINE_STALL_THRESHOLD_ENV = "POLARIS_TOOL_LOOP_MAX_STALL_CYCLES"
KERNEL_TO_TURN_ENGINE_MAX_TOOL_CALLS_ENV = "POLARIS_TOOL_LOOP_MAX_TOTAL_CALLS"
KERNEL_TO_TURN_ENGINE_MAX_WALL_TIME_ENV = "POLARIS_TOOL_LOOP_MAX_WALL_TIME_SECONDS"


# ─────────────────────────────────────────────────────────────────────────────
# 转换函数（Phase 3 迁移时使用）
# ─────────────────────────────────────────────────────────────────────────────


def to_kernel_request(
    role: str,
    request: RoleTurnRequest | None,
) -> dict:
    """将 RoleTurnRequest 转换为 kernel 内部格式（Phase 2 兼容）。

    Phase 3: 当 TurnEngine 直接接受 RoleTurnRequest 时，本函数废弃。

    Args:
        role: 角色标识。
        request: RoleTurnRequest 或 None。

    Returns:
        dict，兼容 kernel.run() / run_stream() 的内部请求格式。
    """
    # TODO [Phase3]: 当 TurnEngine 直接接受 RoleTurnRequest 时删除本函数
    if request is None:
        return {}
    return {
        "role": role,
        "task_id": getattr(request, "task_id", None),
        "message": getattr(request, "message", ""),
        "history": getattr(request, "history", []),
        "context_override": getattr(request, "context_override", None),
    }


def from_kernel_result(
    result: RoleTurnResult,
) -> TranscriptDelta:
    """将 kernel 结果转换为 TranscriptDelta（Phase 2 兼容）。

    Phase 3: TurnEngine 直接输出 TranscriptDelta，本函数废弃。

    Args:
        result: kernel.run() 返回的 RoleTurnResult。

    Returns:
        TranscriptDelta，包含 tool_calls 和 transcript_items。
    """
    # TODO [Phase3]: 当 TurnEngine 直接输出 TranscriptDelta 时删除本函数
    from polaris.cells.roles.kernel.public.transcript_ir import (
        ToolCall,
        TranscriptDelta,
    )

    tool_calls = []
    for tc_dict in getattr(result, "tool_calls", []) or []:
        tool_name = tc_dict.get("tool", "") if isinstance(tc_dict, dict) else str(tc_dict)
        args = tc_dict.get("args", {}) if isinstance(tc_dict, dict) else {}
        tool_calls.append(ToolCall(tool_name=tool_name, args=args))

    return TranscriptDelta(
        transcript_items=list(tool_calls),
        tool_calls=tool_calls,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Phase 2 Facade 说明
# ─────────────────────────────────────────────────────────────────────────────
#
# 在 Phase 2，kernel.run() / kernel.run_stream() 保持不变。
# TurnEngine 作为并行实现逐步完善。
#
# 当 TurnEngine 满足以下条件时，kernel.py 迁移到 TurnEngine：
#
#   ✓ TurnEngine.run() / run_stream() 不抛 NotImplementedError
#   ✓ ProviderAdapter 已实现（Phase 3）
#   ✓ PolicyLayer 已实现（Phase 3）
#   ✓ ToolRuntime 已实现（Phase 4）
#   ✓ 所有 kernel.run() / run_stream() 的测试在 TurnEngine 上通过
#
# 迁移步骤：
#   1. 将 kernel.run() 替换为 TurnEngine.run() 调用
#   2. 将 kernel.run_stream() 替换为 TurnEngine.run_stream() 调用
#   3. 删除 kernel_bridge.py
#   4. 运行全量回归测试
#


__all__ = [
    "KERNEL_TO_TURN_ENGINE_MAX_TOOL_CALLS_ENV",
    "KERNEL_TO_TURN_ENGINE_MAX_WALL_TIME_ENV",
    "KERNEL_TO_TURN_ENGINE_STALL_THRESHOLD_ENV",
    "from_kernel_result",
    "to_kernel_request",
]
