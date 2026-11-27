"""AGI 选工具：每轮只注入需要的 MCP 工具子集。

两层选择，都是**可审计**的（记 `mcp_tools_selected`）：
1. 类别相关：任务文本命中类别关键词。
2. 上限保护：`max_tools` 硬性封顶，超出按"类别命中数"少者优先剔除。

不在目录里的工具一律不进请求 —— 这是"低上下文 + 增强"的关键：
工具多不等于能力强，全塞进去只会稀释注意力。
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

from polaris.infrastructure.mcp.catalog import CATEGORY_KEYWORDS, McpToolCatalog

DEFAULT_MAX_TOOLS = 12


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text).lower()


def select_categories(task_text: str) -> set[str]:
    lowered = _normalize(task_text)
    return {
        category
        for category, keywords in CATEGORY_KEYWORDS.items()
        if any(_normalize(keyword) in lowered for keyword in keywords)
    }


def select_tools(
    catalog: McpToolCatalog,
    *,
    task_text: str,
    max_tools: int = DEFAULT_MAX_TOOLS,
    extra_categories: Sequence[str] = (),
) -> tuple[dict[str, Mapping[str, Any]], dict[str, Any]]:
    """返回（选中工具的 schema 映射, 选择证据）。"""

    wanted = select_categories(task_text) | {str(item) for item in extra_categories}
    matched = [entry for entry in catalog.entries.values() if entry.category in wanted]
    dropped = len(catalog.entries) - len(matched)
    # 上限：优先保留类别命中；仍超出则按名字稳定截断（可复现）。
    overflow = max(0, len(matched) - max(1, max_tools))
    if overflow:
        matched = sorted(matched, key=lambda entry: entry.name)[: max(1, max_tools)]
    schemas = {key: entry.schema for key, entry in catalog.entries.items() if entry in matched}
    evidence = {
        "wanted_categories": sorted(wanted),
        "catalog_size": len(catalog.entries),
        "matched": len(matched) + overflow,
        "selected": len(schemas),
        "dropped_by_category": dropped,
        "dropped_by_limit": overflow,
        "unavailable_servers": dict(catalog.unavailable),
    }
    return schemas, evidence
