"""Turn processing component package.

# -*- coding: utf-8 -*-
UTF-8 编码验证: 本文所有文本使用 UTF-8

职责：
    提供非执行类的 turn 组件入口。执行入口已收敛到
    TransactionKernel / TurnTransactionController；本包不得重新导出
    TurnEngine 执行门面。

Wave 1 完成状态:
    - config.py: TurnEngineConfig, SafetyState ✓

Wave 2 完成状态:
    - artifacts.py: AssistantTurnArtifacts, _BracketToolWrapperFilter ✓

Wave 3 完成状态:
    - utils.py: 静态工具函数（去重、签名、归一化、合并等） ✓
    - results.py: RoleTurnResult 构造辅助函数 ✓

已收敛:
    - compat.py / TurnEngineCompatMixin 已移除。旧 Phase 3/4 helper API
      不再作为执行面存在；新行为必须进入 TransactionKernel/RoleExecutionKernel。
    - engine.py / TurnEngine 执行门面已移除。包根只导出真实组件，不得重新添加
      execution facade 或空 stub。
"""

from polaris.cells.roles.kernel.internal.conversation_state import ConversationState
from polaris.cells.roles.kernel.internal.turn_engine.artifacts import (
    AssistantTurnArtifacts,
    _BracketToolWrapperFilter,
)
from polaris.cells.roles.kernel.internal.turn_engine.config import (
    SafetyState,
    TurnEngineConfig,
)
from polaris.cells.roles.kernel.internal.turn_engine.results import (
    build_stream_complete_result,
    make_error_result,
)
from polaris.cells.roles.kernel.internal.turn_engine.utils import (
    dedupe_parsed_tool_calls,
    merge_stream_thinking,
    normalize_stream_tool_call_payload,
    resolve_empty_visible_output_error,
    tool_call_signature,
    tool_call_signature_from_parsed,
    visible_delta,
)

_build_stream_complete_result = build_stream_complete_result
_make_error_result = make_error_result


__all__ = [
    "AssistantTurnArtifacts",
    "ConversationState",
    "SafetyState",
    "TurnEngineConfig",
    # Private classes (for testing)
    "_BracketToolWrapperFilter",
    "_build_stream_complete_result",
    "_make_error_result",
    "build_stream_complete_result",
    # Utils functions
    "dedupe_parsed_tool_calls",
    # Helper functions
    "make_error_result",
    "merge_stream_thinking",
    "normalize_stream_tool_call_payload",
    "resolve_empty_visible_output_error",
    "tool_call_signature",
    "tool_call_signature_from_parsed",
    "visible_delta",
]
