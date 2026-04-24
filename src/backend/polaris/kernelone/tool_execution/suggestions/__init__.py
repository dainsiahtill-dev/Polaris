"""Tool Execution Suggestions - 工具执行建议模块

为 LLM 工具调用错误提供智能建议，帮助 LLM 修正下一次尝试。

架构：
    ToolExecutor 执行工具
            │
            ▼
    SuggestionRegistry.build_suggestion(error_result)
            │
            ├──▶ FuzzyMatchBuilder    → 搜索未命中，提供最相似行 + diff
            ├──▶ ContextBuilder       → 文件过大，给出分片读取建议
            ├──▶ ExplorationBuilder   → 文件不存在，给出探索建议
            └──▶ ValidationBuilder    → 参数校验失败，给出参数规范

使用示例：
    >>> from polaris.kernelone.tool_execution.suggestions import build_suggestion
    >>> suggestion = build_suggestion({"error": "No matches found", "file": "foo.py"})
    >>> print(suggestion)
    Search='...' not found. Most similar line 4: '...' (similarity=82%). ...

    >>> # 组合多个 builder
    >>> suggestion = build_suggestion(
    ...     {"error": "File not found", "file": "foo.py"},
    ...     extra_context={"workspace_files": ["bar.py", "baz.py"]}
    ... )
    File 'foo.py' not found. Did you mean: bar.py?
"""

from __future__ import annotations

from polaris.kernelone.tool_execution.suggestions.exploration import ExplorationBuilder
from polaris.kernelone.tool_execution.suggestions.fuzzy import FuzzyMatchBuilder
from polaris.kernelone.tool_execution.suggestions.precise_matcher import (
    find_best_match,
    fuzzy_replace,
)
from polaris.kernelone.tool_execution.suggestions.protocols import SuggestionBuilder
from polaris.kernelone.tool_execution.suggestions.registry import (
    build_suggestion,
    register_builder,
)

__all__ = [
    "ExplorationBuilder",
    # Builders
    "FuzzyMatchBuilder",
    "SuggestionBuilder",
    "build_suggestion",
    "find_best_match",
    # Fuzzy matcher
    "fuzzy_replace",
    "register_builder",
]
