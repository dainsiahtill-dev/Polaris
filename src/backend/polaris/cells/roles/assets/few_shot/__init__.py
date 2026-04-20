"""Few-shot 示例库 - 为 LLM 角色提供高质量示例

本模块提供各种编辑场景的高质量示例，帮助 LLM 学习正确的工具使用方式。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def load_edit_blocks_examples(
    language: str | None = None,
    difficulty: str | None = None,
) -> list[dict[str, Any]]:
    """加载 EDIT_BLOCKS 工具的 few-shot 示例。

    Args:
        language: 过滤特定语言 (python, typescript, javascript, yaml, json, go, rust, sql, css, html)
        difficulty: 过滤难度级别 (easy, medium, hard)

    Returns:
        示例列表
    """
    examples_file = Path(__file__).parent / "edit_blocks_examples.json"

    with open(examples_file, encoding="utf-8") as f:
        data = json.load(f)

    examples = data.get("examples", [])

    if language:
        examples = [e for e in examples if e.get("language") == language]

    if difficulty:
        examples = [e for e in examples if e.get("difficulty") == difficulty]

    return examples


def get_example_by_id(example_id: str) -> dict[str, Any] | None:
    """通过 ID 获取特定示例。

    Args:
        example_id: 示例唯一标识符

    Returns:
        示例字典，不存在时返回 None
    """
    examples = load_edit_blocks_examples()
    for example in examples:
        if example.get("id") == example_id:
            return example
    return None


def format_example_for_prompt(example: dict[str, Any]) -> str:
    """将示例格式化为提示词文本。

    Args:
        example: 示例字典

    Returns:
        格式化的提示词文本
    """
    lines = [
        f"## 示例: {example.get('scenario', 'Unknown')}",
        f"语言: {example.get('language', 'Unknown')}",
        f"难度: {example.get('difficulty', 'Unknown')}",
        "",
        f"用户请求: {example.get('user_request', '')}",
        f"上下文: {example.get('context', '')}",
        "",
        "原始代码:",
        "```",
        example.get("original_code", ""),
        "```",
        "",
    ]

    expected = example.get("expected_edit", {})
    if expected:
        lines.extend(
            [
                "正确输出:",
                f"工具: {expected.get('tool', 'edit_blocks')}",
                "```",
                expected.get("blocks", ""),
                "```",
            ]
        )

    if "notes" in example:
        lines.extend(["", f"注意: {example['notes']}"])

    return "\n".join(lines)


def get_examples_by_language() -> dict[str, list[dict[str, Any]]]:
    """按语言分组获取示例。

    Returns:
        语言 -> 示例列表 的映射
    """
    examples = load_edit_blocks_examples()
    result: dict[str, list[dict[str, Any]]] = {}

    for example in examples:
        lang = example.get("language", "unknown")
        if lang not in result:
            result[lang] = []
        result[lang].append(example)

    return result


__all__ = [
    "format_example_for_prompt",
    "get_example_by_id",
    "get_examples_by_language",
    "load_edit_blocks_examples",
]
