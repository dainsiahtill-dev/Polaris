"""TurnEngine 模块化重构子包。

# -*- coding: utf-8 -*-
UTF-8 编码验证: 本文所有文本使用 UTF-8

Blueprint: §10 TurnEngine - Wave 3 Modular Extraction

职责：
    提供模块化 TurnEngine 组件的统一入口。

Wave 1 完成状态:
    - config.py: TurnEngineConfig, SafetyState ✓

Wave 2 完成状态:
    - artifacts.py: AssistantTurnArtifacts, _BracketToolWrapperFilter ✓

Wave 3 完成状态:
    - utils.py: 静态工具函数（去重、签名、归一化、合并等） ✓
    - compat.py: TurnEngineCompatMixin（Phase 3/4 兼容 API） ✓
    - results.py: RoleTurnResult 构造辅助函数 ✓

向后兼容：
    所有原有导入路径继续有效：
    from polaris.cells.roles.kernel.internal.turn_engine import TurnEngine, TurnEngineConfig
"""

from polaris.cells.roles.kernel.internal.turn_engine.artifacts import (
    AssistantTurnArtifacts,
    _BracketToolWrapperFilter,
)
from polaris.cells.roles.kernel.internal.turn_engine.config import (
    SafetyState,
    TurnEngineConfig,
)
from polaris.cells.roles.kernel.internal.turn_engine.engine import TurnEngine
from polaris.cells.roles.kernel.internal.turn_engine.utils import (
    dedupe_parsed_tool_calls,
    merge_stream_thinking,
    normalize_stream_tool_call_payload,
    resolve_empty_visible_output_error,
    tool_call_signature,
    tool_call_signature_from_parsed,
    visible_delta,
)


# Slice C (2026-04-16): Compatibility stubs for symbols removed during facade cut-over.
class ConversationState:
    """Minimal compatibility stub for removed ConversationState."""

    def __init__(self, *args: object, **kwargs: object) -> None:
        pass


def _build_stream_complete_result(*args: object, **kwargs: object) -> dict[str, object]:
    return {}


def build_stream_complete_result(*args: object, **kwargs: object) -> dict[str, object]:
    return {}


def _make_error_result(*args: object, **kwargs: object) -> dict[str, object]:
    return {}


def make_error_result(*args: object, **kwargs: object) -> dict[str, object]:
    return {}


__all__ = [
    "AssistantTurnArtifacts",
    "ConversationState",
    "SafetyState",
    # Core classes
    "TurnEngine",
    "TurnEngineConfig",
    # Private classes (for testing)
    "_BracketToolWrapperFilter",
    "_build_stream_complete_result",
    # 向后兼容别名
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
