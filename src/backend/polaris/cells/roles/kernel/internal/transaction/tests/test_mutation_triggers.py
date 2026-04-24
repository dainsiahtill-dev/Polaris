"""Tests for mutation_triggers module.

覆盖所有关键词变体的单元测试，确保 mutation 意图检测的正确性。
"""

from __future__ import annotations

import pytest
from polaris.cells.roles.kernel.internal.transaction.mutation_triggers import (
    MUTATION_KEYWORDS,
    detect_mutation_intent,
    get_matched_keywords,
    get_mutation_keyword_count,
    should_enter_materialize_mode,
)

# ============================================================================
# 基础功能测试
# ============================================================================


class TestDetectMutationIntent:
    """测试 detect_mutation_intent 函数。"""

    # 完善类关键词测试
    @pytest.mark.parametrize(
        "text,expected",
        [
            ("请完善这个函数", True),
            ("需要完善化代码", True),
            ("帮我完善一下", True),
            ("完善文档", True),
            ("完善化处理", True),
            ("完善一下这个模块", True),
        ],
    )
    def test_wan_shan_keywords(self, text: str, expected: bool) -> None:
        """测试完善类关键词。"""
        assert detect_mutation_intent(text) is expected

    # 修改类关键词测试
    @pytest.mark.parametrize(
        "text,expected",
        [
            ("请修改这个函数", True),
            ("需要改动代码", True),
            ("帮我改一下", True),
            ("修改一下这个文件", True),
            ("修改配置", True),
            ("改动逻辑", True),
        ],
    )
    def test_xiu_gai_keywords(self, text: str, expected: bool) -> None:
        """测试修改类关键词。"""
        assert detect_mutation_intent(text) is expected

    # 优化改进类关键词测试
    @pytest.mark.parametrize(
        "text,expected",
        [
            ("请优化这段代码", True),
            ("需要改进算法", True),
            ("提升性能", True),
            ("优化一下", True),
            ("改进设计", True),
            ("提升质量", True),
        ],
    )
    def test_you_hua_keywords(self, text: str, expected: bool) -> None:
        """测试优化改进类关键词。"""
        assert detect_mutation_intent(text) is expected

    # 补充添加类关键词测试
    @pytest.mark.parametrize(
        "text,expected",
        [
            ("请补充文档", True),
            ("添加新功能", True),
            ("增加测试用例", True),
            ("补充说明", True),
            ("添加注释", True),
            ("增加日志", True),
        ],
    )
    def test_tian_jia_keywords(self, text: str, expected: bool) -> None:
        """测试补充添加类关键词。"""
        assert detect_mutation_intent(text) is expected

    # 修复类关键词测试
    @pytest.mark.parametrize(
        "text,expected",
        [
            ("请修复这个 bug", True),
            ("修正错误", True),
            ("改正问题", True),
            ("修复漏洞", True),
            ("修正逻辑", True),
            ("改正拼写", True),
        ],
    )
    def test_xiu_fu_keywords(self, text: str, expected: bool) -> None:
        """测试修复类关键词。"""
        assert detect_mutation_intent(text) is expected

    # 重构类关键词测试
    @pytest.mark.parametrize(
        "text,expected",
        [
            ("请重构这段代码", True),
            ("重写这个模块", True),
            ("需要重构", True),
            ("重写算法", True),
            ("重构架构", True),
            ("重写实现", True),
        ],
    )
    def test_zhong_gou_keywords(self, text: str, expected: bool) -> None:
        """测试重构类关键词。"""
        assert detect_mutation_intent(text) is expected

    # 非 mutation 意图测试
    @pytest.mark.parametrize(
        "text,expected",
        [
            ("请分析一下", False),
            ("查看代码", False),
            ("阅读文档", False),
            ("解释这段代码", False),
            ("总结功能", False),
            ("评估性能", False),
            ("建议改进方案", True),  # "改进"是 mutation 关键词
            ("", False),
            ("   ", False),
            ("hello world", False),
            ("这是什么", False),
        ],
    )
    def test_non_mutation_intents(self, text: str, expected: bool) -> None:
        """测试非 mutation 意图应该返回 False。"""
        assert detect_mutation_intent(text) is expected

    # 边界情况测试
    def test_empty_string(self) -> None:
        """测试空字符串。"""
        assert detect_mutation_intent("") is False

    def test_whitespace_only(self) -> None:
        """测试仅包含空白字符。"""
        assert detect_mutation_intent("   \t\n  ") is False

    def test_none_input(self) -> None:
        """测试 None 输入。"""
        assert detect_mutation_intent(None) is False  # type: ignore[arg-type]

    def test_non_string_input(self) -> None:
        """测试非字符串输入。"""
        assert detect_mutation_intent(123) is False  # type: ignore[arg-type]
        assert detect_mutation_intent(["修改"]) is False  # type: ignore[arg-type]
        assert detect_mutation_intent({"key": "修改"}) is False  # type: ignore[arg-type]


# ============================================================================
# should_enter_materialize_mode 测试
# ============================================================================


class TestShouldEnterMaterializeMode:
    """测试 should_enter_materialize_mode 函数。"""

    def test_mutation_intent_triggers_materialize(self) -> None:
        """测试 mutation 意图触发 MATERIALIZE 模式。"""
        assert should_enter_materialize_mode("请优化这段代码") is True
        assert should_enter_materialize_mode("修改一下") is True
        assert should_enter_materialize_mode("修复 bug") is True

    def test_no_mutation_intent_no_materialize(self) -> None:
        """测试无 mutation 意图不触发 MATERIALIZE 模式。"""
        assert should_enter_materialize_mode("请分析一下") is False
        assert should_enter_materialize_mode("查看代码") is False

    def test_with_recent_reads(self) -> None:
        """测试带有 recent_reads 参数的情况。"""
        # 有 mutation 意图，无论是否有 recent_reads 都返回 True
        assert should_enter_materialize_mode("修改代码", ["main.py"]) is True
        # 无 mutation 意图，即使有 recent_reads 也返回 False
        assert should_enter_materialize_mode("请分析", ["main.py"]) is False

    def test_empty_recent_reads(self) -> None:
        """测试空的 recent_reads 列表。"""
        assert should_enter_materialize_mode("修改代码", []) is True
        assert should_enter_materialize_mode("请分析", []) is False

    def test_none_recent_reads(self) -> None:
        """测试 None 作为 recent_reads。"""
        assert should_enter_materialize_mode("修改代码", None) is True
        assert should_enter_materialize_mode("请分析", None) is False

    def test_defensive_programming(self) -> None:
        """测试防御性编程。"""
        # 非字符串 user_prompt
        assert should_enter_materialize_mode(None) is False  # type: ignore[arg-type]
        assert should_enter_materialize_mode(123) is False  # type: ignore[arg-type]


# ============================================================================
# get_matched_keywords 测试
# ============================================================================


class TestGetMatchedKeywords:
    """测试 get_matched_keywords 函数。"""

    def test_single_keyword(self) -> None:
        """测试单个关键词匹配。"""
        result = get_matched_keywords("请完善这个函数")
        assert "完善" in result

    def test_multiple_keywords(self) -> None:
        """测试多个关键词匹配。"""
        result = get_matched_keywords("请完善并优化这段代码，然后修复 bug")
        assert "完善" in result
        assert "优化" in result
        assert "修复" in result

    def test_no_match(self) -> None:
        """测试无匹配情况。"""
        result = get_matched_keywords("请分析一下")
        assert result == []

    def test_duplicate_keywords(self) -> None:
        """测试重复关键词去重。"""
        result = get_matched_keywords("修改修改再修改")
        assert result == ["修改"]

    def test_empty_input(self) -> None:
        """测试空输入。"""
        assert get_matched_keywords("") == []
        assert get_matched_keywords("   ") == []

    def test_none_input(self) -> None:
        """测试 None 输入。"""
        assert get_matched_keywords(None) == []  # type: ignore[arg-type]


# ============================================================================
# 常量测试
# ============================================================================


class TestConstants:
    """测试模块常量。"""

    def test_mutation_keywords_not_empty(self) -> None:
        """测试 MUTATION_KEYWORDS 不为空。"""
        assert len(MUTATION_KEYWORDS) > 0

    def test_mutation_keywords_content(self) -> None:
        """测试 MUTATION_KEYWORDS 包含预期关键词。"""
        expected_keywords = {
            "完善",
            "完善化",
            "完善一下",
            "修改",
            "改动",
            "改一下",
            "修改一下",
            "优化",
            "改进",
            "提升",
            "补充",
            "添加",
            "增加",
            "修复",
            "修正",
            "改正",
            "重构",
            "重写",
        }
        assert expected_keywords.issubset(MUTATION_KEYWORDS)

    def test_keyword_count(self) -> None:
        """测试关键词数量。"""
        count = get_mutation_keyword_count()
        assert count == len(MUTATION_KEYWORDS)
        assert count >= 18  # 至少包含所有指定的关键词


# ============================================================================
# 集成测试
# ============================================================================


class TestIntegration:
    """集成测试场景。"""

    def test_complex_user_request(self) -> None:
        """测试复杂用户请求。"""
        request = "请帮我完善一下这个函数，优化性能，并添加单元测试"
        assert should_enter_materialize_mode(request) is True
        keywords = get_matched_keywords(request)
        assert "完善一下" in keywords  # 实际匹配的是完整词
        assert "优化" in keywords
        assert "添加" in keywords

    def test_context_switching(self) -> None:
        """测试上下文切换场景。"""
        # 用户先读取文件
        assert should_enter_materialize_mode("查看 main.py", ["main.py"]) is False
        # 然后请求修改
        assert should_enter_materialize_mode("修改这个函数", ["main.py"]) is True

    def test_mixed_chinese_english(self) -> None:
        """测试中英混合输入。"""
        assert detect_mutation_intent("Please 修改 the code") is True
        assert detect_mutation_intent("fix 这个 bug") is False  # "fix" 不在关键词中
