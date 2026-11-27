"""AGI 选工具：首轮让 Director 声明本任务需要哪些 MCP 工具类别。

不是把所有 MCP 工具塞给 LLM —— 目录里有什么、任务要什么，由 AGI 说。
选择轮只做一次（进程内缓存），失败或未开启时退回确定性关键词规则（更快、可复现）。
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Mapping, Sequence
from typing import Any

from polaris.infrastructure.mcp.catalog import CATEGORY_KEYWORDS, DEFAULT_CATEGORY, McpToolCatalog

logger = logging.getLogger(__name__)

SELECT_SYSTEM = (
    "You pick which external tool categories this engineering task needs. "
    "Answer with a JSON array of category names only, no prose."
)

VALID_CATEGORIES = frozenset({"rea", "playwright", "codegraph", DEFAULT_CATEGORY})

_PROMPT_TEMPLATE = """Task:
{task}

Available tool categories:
{categories}

Which categories does this task need? Answer a JSON array, e.g. ["codegraph"].
If none are needed, answer []."""


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text).lower()


def parse_categories(text: str) -> list[str]:
    """从模型回答里取类别数组；取不到就用确定性规则。"""

    lowered = text.strip()
    try:
        payload = json.loads(lowered)
    except json.JSONDecodeError:
        start, end = lowered.find("["), lowered.rfind("]")
        if start < 0 or end <= start:
            return []
        try:
            payload = json.loads(lowered[start : end + 1])
        except json.JSONDecodeError:
            return []
    if not isinstance(payload, list):
        return []
    seen: list[str] = []
    for item in payload:
        name = _normalize(str(item)).strip()
        if name in VALID_CATEGORIES and name not in seen:
            seen.append(name)
    return seen


def fallback_categories(task_text: str) -> list[str]:
    lowered = _normalize(task_text)
    return sorted(
        category
        for category, keywords in CATEGORY_KEYWORDS.items()
        if any(_normalize(keyword) in lowered for keyword in keywords)
    )


def select_categories(
    catalog: McpToolCatalog,
    *,
    task_text: str,
    agi_answer: str | None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """AGI 回答优先；回答为空/非法时退回确定性关键词。"""

    chosen = parse_categories(agi_answer) if agi_answer else []
    source = "agi" if chosen else "fallback"
    if not chosen:
        chosen = fallback_categories(task_text) or [DEFAULT_CATEGORY]
    schemas: dict[str, Any] = {}
    for key, entry in catalog.entries.items():
        if entry.category in chosen:
            schemas[key] = entry.schema
    evidence = {
        "source": source,
        "chosen_categories": chosen,
        "catalog_size": len(catalog.entries),
        "selected": len(schemas),
    }
    return schemas, evidence
