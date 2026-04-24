"""Mutation Guard 关键词检测 — 识别用户意图中的代码变更信号。

本模块提供轻量级、纯函数式的关键词检测能力，用于判断用户请求
是否包含修改代码库的意图，从而决定是否进入 MATERIALIZE_CHANGES 模式。

设计原则:
    - 单一职责：仅做关键词匹配，不做语义理解
    - 防御编程：处理空输入、异常输入
    - 性能优先：使用编译后的正则表达式，避免重复计算
    - 可测试性：纯函数，无副作用
"""

from __future__ import annotations

import re
from typing import Final

# ============================================================================
# Mutation 关键词常量
# ============================================================================

MUTATION_KEYWORDS: Final[frozenset[str]] = frozenset(
    {
        # 完善类
        "完善",
        "完善化",
        "完善一下",
        # 修改类
        "修改",
        "改动",
        "改一下",
        "修改一下",
        # 优化改进类
        "优化",
        "改进",
        "提升",
        # 补充添加类
        "补充",
        "添加",
        "增加",
        # 修复类
        "修复",
        "修正",
        "改正",
        # 重构类
        "重构",
        "重写",
    }
)


# ============================================================================
# 编译后的正则表达式（性能优化）
# ============================================================================

# 构建用于部分匹配的正则模式
# 按长度降序排列，确保长词优先匹配（如"完善一下"优先于"完善"）
_SORTED_KEYWORDS: Final[list[str]] = sorted(MUTATION_KEYWORDS, key=len, reverse=True)
_MUTATION_PATTERN: Final[re.Pattern[str]] = re.compile("|".join(re.escape(kw) for kw in _SORTED_KEYWORDS))


# ============================================================================
# 核心函数
# ============================================================================


def detect_mutation_intent(text: str) -> bool:
    """检测文本是否包含 mutation 意图。

    使用预编译的正则表达式进行关键词匹配，支持部分匹配。
    例如，"完善"可以匹配"完善一下"。

    Args:
        text: 待检测的文本，可以是用户输入或任何字符串。

    Returns:
        bool: 如果文本中包含任何 mutation 关键词则返回 True，否则返回 False。

    Examples:
        >>> detect_mutation_intent("请完善这个函数")
        True
        >>> detect_mutation_intent("帮我修改一下代码")
        True
        >>> detect_mutation_intent("请分析一下这段代码")
        False
        >>> detect_mutation_intent("")
        False
    """
    if not isinstance(text, str) or not text.strip():
        return False

    return bool(_MUTATION_PATTERN.search(text))


def should_enter_materialize_mode(
    user_prompt: str,
    recent_reads: list[str] | None = None,
) -> bool:
    """综合判断是否应进入 MATERIALIZE_CHANGES 模式。

    结合用户当前输入和最近读取的文件内容，判断是否存在代码变更意图。
    当前实现主要基于用户输入的关键词检测，recent_reads 用于未来扩展
    （如检测读取文件后是否立即请求修改）。

    Args:
        user_prompt: 用户的当前输入提示。
        recent_reads: 最近读取的文件路径列表，可选。用于上下文判断。

    Returns:
        bool: 如果应该进入 MATERIALIZE_CHANGES 模式则返回 True。

    Examples:
        >>> should_enter_materialize_mode("请优化这段代码")
        True
        >>> should_enter_materialize_mode("请解释一下", ["main.py"])
        False
        >>> should_enter_materialize_mode("", ["test.py"])
        False
    """
    # 防御性检查 + 主要判断逻辑
    return isinstance(user_prompt, str) and detect_mutation_intent(user_prompt)


# ============================================================================
# 辅助函数（用于调试和测试）
# ============================================================================


def get_matched_keywords(text: str) -> list[str]:
    """获取文本中匹配的所有 mutation 关键词。

    用于调试和测试，返回所有匹配到的关键词列表。

    Args:
        text: 待检测的文本。

    Returns:
        list[str]: 匹配到的关键词列表，无重复。

    Examples:
        >>> get_matched_keywords("请完善并优化这段代码")
        ['完善', '优化']
        >>> get_matched_keywords("请分析一下")
        []
    """
    if not isinstance(text, str) or not text.strip():
        return []

    matches: set[str] = set()
    for match in _MUTATION_PATTERN.finditer(text):
        matches.add(match.group())

    return sorted(matches)


def get_mutation_keyword_count() -> int:
    """获取当前定义的关键词数量。

    Returns:
        int: MUTATION_KEYWORDS 集合中的关键词数量。
    """
    return len(MUTATION_KEYWORDS)


# ============================================================================
# 模块自检
# ============================================================================

if __name__ == "__main__":
    # 简单的自检逻辑
    test_cases = [
        ("请完善这个函数", True),
        ("帮我修改一下代码", True),
        ("需要重构这部分", True),
        ("修复这个 bug", True),
        ("添加新功能", True),
        ("优化性能", True),
        ("请分析一下", False),
        ("", False),
    ]

    print(f"Mutation keywords count: {get_mutation_keyword_count()}")
    print("Running self-checks...")

    all_passed = True
    for text, expected in test_cases:
        result = detect_mutation_intent(text)
        status = "PASS" if result == expected else "FAIL"
        if result != expected:
            all_passed = False
        print(f"  [{status}] '{text}' -> {result} (expected {expected})")

    if all_passed:
        print("All self-checks passed!")
    else:
        print("Some self-checks failed!")
