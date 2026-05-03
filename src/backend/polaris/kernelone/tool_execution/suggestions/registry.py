"""SuggestionRegistry - 错误建议注册表。

集中管理所有 SuggestionBuilder，按优先级排序。
"""

from __future__ import annotations

import logging
import threading
from typing import TYPE_CHECKING, Any

from polaris.kernelone.tool_execution.suggestions.protocols import SuggestionBuilder

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

_REGISTRY_LOCK = threading.Lock()

# 全局注册表：name -> builder instance
_BUILDER_REGISTRY: dict[str, SuggestionBuilder] = {}

# 按 priority 排序后的列表（缓存）
_SORTED_BUILDERS: list[SuggestionBuilder] | None = None


def _get_sorted_builders() -> list[SuggestionBuilder]:
    """Get builders sorted by priority (ascending)."""
    global _SORTED_BUILDERS
    with _REGISTRY_LOCK:
        if _SORTED_BUILDERS is None:
            _SORTED_BUILDERS = sorted(_BUILDER_REGISTRY.values(), key=lambda b: b.priority)
        return _SORTED_BUILDERS


def register_builder(builder: SuggestionBuilder) -> None:
    """注册一个 SuggestionBuilder。

    Args:
        builder: 实现 SuggestionBuilder 协议的对象
    """
    global _SORTED_BUILDERS
    with _REGISTRY_LOCK:
        _BUILDER_REGISTRY[builder.name] = builder
        _SORTED_BUILDERS = None  # invalidate cache
    logger.debug("Registered suggestion builder: %s (priority=%d)", builder.name, builder.priority)


def _register_default_builders() -> None:
    """Register all built-in builders.

    Called once at module import time.
    """
    global _BUILDER_REGISTRY
    if _BUILDER_REGISTRY:
        return  # Already registered

    from polaris.kernelone.tool_execution.suggestions.exploration import (
        ExplorationBuilder,
    )
    from polaris.kernelone.tool_execution.suggestions.fuzzy import FuzzyMatchBuilder

    register_builder(FuzzyMatchBuilder())
    register_builder(ExplorationBuilder())


def build_suggestion(
    error_result: dict[str, Any],
    **kwargs: Any,
) -> str | None:
    """为工具执行错误构建建议字符串。

    按 priority 顺序遍历所有注册的 Builder，返回第一个产生的建议。

    Args:
        error_result: 工具执行返回的错误结果字典
        **kwargs: 额外上下文（如 workspace 文件列表、可用工具列表等）

    Returns:
        建议字符串，或 None（无 builder 能处理该错误）
    """
    _register_default_builders()

    for builder in _get_sorted_builders():
        if builder.should_apply(error_result):
            try:
                suggestion = builder.build(error_result, **kwargs)
                if suggestion:
                    logger.debug(
                        "Suggestion from %s for error '%s': %s",
                        builder.name,
                        error_result.get("error", ""),
                        suggestion[:100],
                    )
                    return suggestion
            except (RuntimeError, ValueError) as exc:
                logger.warning(
                    "SuggestionBuilder '%s' raised: %s",
                    builder.name,
                    exc,
                )
                continue

    return None


# 延迟注册，在第一次 build_suggestion 调用时初始化
_register_default_builders()
