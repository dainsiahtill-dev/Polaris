"""OutputSanitizer 单元测试.

覆盖范围:
- STRICT 策略：完全删除禁止词汇
- REPLACE 策略：替换禁止词汇为 [FILTERED]
- SOFT 策略：仅在关键位置过滤
- 同义词替换
- 大小写敏感性
- 边界情况处理
- 从 benchmark case 创建 sanitizer
"""

from __future__ import annotations

from polaris.cells.llm.evaluation.internal.output_sanitizer import (
    DEFAULT_FILTER_MARKER,
    OutputSanitizer,
    SanitizationResult,
    SanitizationStrategy,
    create_sanitizer_from_case,
    sanitize_observation_output,
)
from polaris.kernelone.benchmark.unified_models import (
    JudgeConfig,
    UnifiedBenchmarkCase,
)


class TestOutputSanitizerInit:
    """OutputSanitizer 初始化测试."""

    def test_default_initialization(self):
        """测试默认初始化."""
        sanitizer = OutputSanitizer()
        assert sanitizer.forbidden_tokens == ()
        assert sanitizer.strategy == SanitizationStrategy.STRICT
        assert sanitizer.filter_marker == DEFAULT_FILTER_MARKER
        assert sanitizer.case_sensitive is False

    def test_with_forbidden_tokens(self):
        """测试带禁止词汇的初始化."""
        sanitizer = OutputSanitizer(
            forbidden_tokens=("forbidden1", "forbidden2"),
        )
        assert sanitizer.forbidden_tokens == ("forbidden1", "forbidden2")

    def test_empty_filter_marker_defaults_to_default(self):
        """测试空 filter_marker 使用默认值."""
        sanitizer = OutputSanitizer(filter_marker="")
        assert sanitizer.filter_marker == DEFAULT_FILTER_MARKER

    def test_normalizes_empty_tokens(self):
        """测试过滤空字符串 tokens."""
        sanitizer = OutputSanitizer(
            forbidden_tokens=("valid", "", "  ", "also_valid"),
        )
        assert sanitizer.forbidden_tokens == ("valid", "also_valid")

    def test_normalizes_synonym_keys(self):
        """测试同义词映射的键值规范化."""
        sanitizer = OutputSanitizer(
            forbidden_tokens=("old_name", "new_name"),
            synonym_map={"  old_name  ": "  new_name  ", "valid": "replacement"},
        )
        assert sanitizer.synonym_map == {"old_name": "new_name", "valid": "replacement"}

    def test_removes_invalid_synonym_entries(self):
        """测试移除无效的同义词条目."""
        sanitizer = OutputSanitizer(
            synonym_map={"": "value", "key": "", "valid_key": "valid_value"},
        )
        assert sanitizer.synonym_map == {"valid_key": "valid_value"}


class TestSanitizationStrategyStrict:
    """STRICT 策略测试."""

    def test_removes_single_forbidden_token(self):
        """测试删除单个禁止词汇."""
        sanitizer = OutputSanitizer(
            forbidden_tokens=("forbidden",),
            strategy=SanitizationStrategy.STRICT,
        )
        result = sanitizer.sanitize("This contains forbidden content")
        assert result.sanitized_output == "This contains content"
        assert result.was_modified is True

    def test_removes_multiple_forbidden_tokens(self):
        """测试删除多个禁止词汇."""
        sanitizer = OutputSanitizer(
            forbidden_tokens=("bad", "ugly"),
            strategy=SanitizationStrategy.STRICT,
        )
        result = sanitizer.sanitize("This bad and ugly text")
        assert result.sanitized_output == "This and text"
        assert result.was_modified is True

    def test_removes_forbidden_at_boundaries(self):
        """测试删除边界位置的禁止词汇."""
        sanitizer = OutputSanitizer(
            forbidden_tokens=("prefix_", "_suffix"),
            strategy=SanitizationStrategy.STRICT,
        )
        result = sanitizer.sanitize("prefix_ content _suffix")
        assert result.sanitized_output == "content"

    def test_handles_duplicate_tokens(self):
        """测试处理重复出现的禁止词汇."""
        sanitizer = OutputSanitizer(
            forbidden_tokens=("token",),
            strategy=SanitizationStrategy.STRICT,
        )
        result = sanitizer.sanitize("token value token token")
        assert result.sanitized_output == "value"

    def test_case_insensitive_by_default(self):
        """测试默认大小写不敏感."""
        sanitizer = OutputSanitizer(
            forbidden_tokens=("forbidden",),
            strategy=SanitizationStrategy.STRICT,
        )
        result = sanitizer.sanitize("This Forbidden CONTENT")
        assert "forbidden" not in result.sanitized_output.lower()

    def test_respects_case_sensitive_flag(self):
        """测试大小写敏感标志."""
        sanitizer = OutputSanitizer(
            forbidden_tokens=("Forbidden",),
            strategy=SanitizationStrategy.STRICT,
            case_sensitive=True,
        )
        result = sanitizer.sanitize("This forbidden CONTENT")
        assert "forbidden" in result.sanitized_output.lower()
        assert "Forbidden" not in result.sanitized_output

    def test_cleans_up_excessive_whitespace(self):
        """测试清理过多的空白字符."""
        sanitizer = OutputSanitizer(
            forbidden_tokens=("remove",),
            strategy=SanitizationStrategy.STRICT,
        )
        result = sanitizer.sanitize("word remove word")
        assert result.sanitized_output == "word word"
        assert "  " not in result.sanitized_output

    def test_no_match_returns_unchanged(self):
        """测试无匹配时返回原内容."""
        sanitizer = OutputSanitizer(
            forbidden_tokens=("missing",),
            strategy=SanitizationStrategy.STRICT,
        )
        result = sanitizer.sanitize("This is clean content")
        assert result.sanitized_output == "This is clean content"
        assert result.was_modified is False


class TestSanitizationStrategyReplace:
    """REPLACE 策略测试."""

    def test_replaces_single_token(self):
        """测试替换单个禁止词汇."""
        sanitizer = OutputSanitizer(
            forbidden_tokens=("forbidden",),
            strategy=SanitizationStrategy.REPLACE,
        )
        result = sanitizer.sanitize("This contains forbidden content")
        assert result.sanitized_output == "This contains [FILTERED] content"
        assert result.was_modified is True

    def test_replaces_with_custom_marker(self):
        """测试使用自定义标记替换."""
        sanitizer = OutputSanitizer(
            forbidden_tokens=("bad",),
            strategy=SanitizationStrategy.REPLACE,
            filter_marker="[REDACTED]",
        )
        result = sanitizer.sanitize("This is bad content")
        assert result.sanitized_output == "This is [REDACTED] content"

    def test_replaces_multiple_tokens(self):
        """测试替换多个禁止词汇."""
        sanitizer = OutputSanitizer(
            forbidden_tokens=("bad", "ugly"),
            strategy=SanitizationStrategy.REPLACE,
        )
        result = sanitizer.sanitize("This bad and ugly text")
        assert "[FILTERED]" in result.sanitized_output
        assert "bad" not in result.sanitized_output.lower()
        assert "ugly" not in result.sanitized_output.lower()

    def test_uses_synonym_replacement(self):
        """测试使用同义词替换."""
        sanitizer = OutputSanitizer(
            forbidden_tokens=("old_name",),
            strategy=SanitizationStrategy.REPLACE,
            synonym_map={"old_name": "new_name"},
        )
        result = sanitizer.sanitize("Use old_name function here")
        assert result.sanitized_output == "Use new_name function here"
        assert result.was_modified is True

    def test_case_insensitive_replace(self):
        """测试大小写不敏感替换."""
        sanitizer = OutputSanitizer(
            forbidden_tokens=("forbidden",),
            strategy=SanitizationStrategy.REPLACE,
        )
        result = sanitizer.sanitize("This FORBIDDEN word")
        assert "[FILTERED]" in result.sanitized_output
        assert "forbidden" not in result.sanitized_output.lower()

    def test_preserves_whitespace_structure(self):
        """测试保留空白结构."""
        sanitizer = OutputSanitizer(
            forbidden_tokens=("X",),
            strategy=SanitizationStrategy.REPLACE,
        )
        result = sanitizer.sanitize("word  X  word")
        # Multiple spaces should be preserved
        assert "  " in result.sanitized_output


class TestSanitizationStrategySoft:
    """SOFT 策略测试.

    SOFT 策略只过滤括号中的禁止词汇，这是为了避免过度修改
    同时保留上下文语义。
    """

    def test_filters_token_in_parentheses(self):
        """测试过滤括号中的禁止词汇."""
        sanitizer = OutputSanitizer(
            forbidden_tokens=("forbidden",),
            strategy=SanitizationStrategy.SOFT,
        )
        result = sanitizer.sanitize("Use the (forbidden) function")
        assert "[FILTERED]" in result.sanitized_output
        assert "forbidden" not in result.sanitized_output

    def test_preserves_token_at_start_outside_parentheses(self):
        """测试保留开头位置的禁止词汇（不在括号中）."""
        sanitizer = OutputSanitizer(
            forbidden_tokens=("prefix",),
            strategy=SanitizationStrategy.SOFT,
        )
        result = sanitizer.sanitize("prefix content here")
        # SOFT 策略只过滤括号内，不修改开头的 token
        assert "prefix" in result.sanitized_output

    def test_preserves_token_at_end_outside_parentheses(self):
        """测试保留末尾位置的禁止词汇（不在括号中）."""
        sanitizer = OutputSanitizer(
            forbidden_tokens=("suffix",),
            strategy=SanitizationStrategy.SOFT,
        )
        result = sanitizer.sanitize("content here suffix")
        # SOFT 策略只过滤括号内，不修改末尾的 token
        assert "suffix" in result.sanitized_output

    def test_middle_occurrence_not_filtered(self):
        """测试中间的禁止词汇不被过滤."""
        sanitizer = OutputSanitizer(
            forbidden_tokens=("middle",),
            strategy=SanitizationStrategy.SOFT,
        )
        result = sanitizer.sanitize("word middle word")
        # "middle" 在中间位置且不在括号中，不应被过滤
        assert "middle" in result.sanitized_output

    def test_filters_token_in_both_parentheses(self):
        """测试过滤两个括号中的禁止词汇."""
        sanitizer = OutputSanitizer(
            forbidden_tokens=("test",),
            strategy=SanitizationStrategy.SOFT,
        )
        result = sanitizer.sanitize("(test) test word (test)")
        # 两个括号中的 test 应该被过滤，中间的保持不变
        assert result.sanitized_output.count("[FILTERED]") == 2
        # 中间的 "test" 应该被保留
        assert "test" in result.sanitized_output

    def test_filters_multiple_tokens_in_parentheses(self):
        """测试过滤括号中的多个禁止词汇."""
        sanitizer = OutputSanitizer(
            forbidden_tokens=("bad", "ugly"),
            strategy=SanitizationStrategy.SOFT,
        )
        result = sanitizer.sanitize("Values: (bad) and (ugly)")
        assert "[FILTERED]" in result.sanitized_output
        assert "bad" not in result.sanitized_output
        assert "ugly" not in result.sanitized_output


class TestSanitizationResult:
    """SanitizationResult 测试."""

    def test_result_tracks_matched_tokens(self):
        """测试结果追踪匹配的词汇."""
        sanitizer = OutputSanitizer(
            forbidden_tokens=("forbidden", "bad"),
            strategy=SanitizationStrategy.REPLACE,
        )
        result = sanitizer.sanitize("This forbidden and bad content")
        assert "forbidden" in result.matched_tokens
        assert "bad" in result.matched_tokens
        assert len(result.matched_tokens) == 2

    def test_result_tracks_strategy_used(self):
        """测试结果追踪使用的策略."""
        sanitizer = OutputSanitizer(
            forbidden_tokens=("word",),
            strategy=SanitizationStrategy.REPLACE,
        )
        result = sanitizer.sanitize("word content")
        assert result.strategy_used == SanitizationStrategy.REPLACE

    def test_empty_output_returns_empty_result(self):
        """测试空输出返回空结果."""
        sanitizer = OutputSanitizer(
            forbidden_tokens=("word",),
        )
        result = sanitizer.sanitize("")
        assert result.sanitized_output == ""
        assert result.was_modified is False
        assert result.matched_tokens == ()

    def test_none_output_treated_as_empty(self):
        """测试 None 输出被视为空."""
        sanitizer = OutputSanitizer(
            forbidden_tokens=("word",),
        )
        result = sanitizer.sanitize(None)  # type: ignore
        assert result.sanitized_output == ""
        assert result.was_modified is False


class TestSanitizeCaseOutput:
    """sanitize_case_output 函数测试."""

    def test_sanitizes_both_output_and_thinking(self):
        """测试同时清理 output 和 thinking."""

        case = UnifiedBenchmarkCase(
            case_id="test_case",
            role="director",
            title="Test",
            prompt="Test prompt",
            judge=JudgeConfig(
                forbidden_output_substrings=("forbidden",),
            ),
        )

        raw_output = "This contains forbidden word"
        raw_thinking = "I should use the forbidden function"

        out, thinking, out_result, thinking_result = sanitize_observation_output(raw_output, raw_thinking, case)

        assert "[FILTERED]" in out
        assert "[FILTERED]" in thinking
        assert out_result.was_modified is True
        assert thinking_result.was_modified is True

    def test_preserves_original_sanitizer(self):
        """测试保留原始 sanitizer 配置."""

        case = UnifiedBenchmarkCase(
            case_id="test_case",
            role="director",
            title="Test",
            prompt="Test prompt",
            judge=JudgeConfig(
                forbidden_output_substrings=("token1",),
            ),
        )

        sanitizer = OutputSanitizer(
            forbidden_tokens=("other",),
            strategy=SanitizationStrategy.STRICT,
        )

        # Sanitize with case-specific tokens
        result = sanitizer.sanitize_case_output("This has token1 and other", case.judge.forbidden_output_substrings)

        # Original sanitizer should not be modified
        assert sanitizer.forbidden_tokens == ("other",)
        # Result should only contain token1 as matched
        assert "token1" in result.matched_tokens
        assert "other" not in result.matched_tokens

    def test_no_forbidden_tokens_in_case(self):
        """测试 case 无禁止词汇时."""

        case = UnifiedBenchmarkCase(
            case_id="test_case",
            role="director",
            title="Test",
            prompt="Test prompt",
            judge=JudgeConfig(
                forbidden_output_substrings=(),
            ),
        )

        result = sanitizer_observation_output("some content", "", case)
        assert result[0] == "some content"


class TestCreateSanitizerFromCase:
    """create_sanitizer_from_case 函数测试."""

    def test_creates_sanitizer_with_case_tokens(self):
        """测试使用 case 词汇创建 sanitizer."""

        case = UnifiedBenchmarkCase(
            case_id="test_case",
            role="director",
            title="Test",
            prompt="Test prompt",
            judge=JudgeConfig(
                forbidden_output_substrings=("token1", "token2"),
            ),
        )

        sanitizer = create_sanitizer_from_case(case)
        assert sanitizer.forbidden_tokens == ("token1", "token2")

    def test_respects_custom_strategy(self):
        """测试使用自定义策略."""

        case = UnifiedBenchmarkCase(
            case_id="test_case",
            role="director",
            title="Test",
            prompt="Test prompt",
            judge=JudgeConfig(
                forbidden_output_substrings=("word",),
            ),
        )

        sanitizer = create_sanitizer_from_case(case, strategy=SanitizationStrategy.REPLACE)
        assert sanitizer.strategy == SanitizationStrategy.REPLACE

    def test_applies_synonym_map(self):
        """测试应用同义词映射."""

        case = UnifiedBenchmarkCase(
            case_id="test_case",
            role="director",
            title="Test",
            prompt="Test prompt",
            judge=JudgeConfig(
                forbidden_output_substrings=("old",),
            ),
        )

        synonym_map = {"old": "new"}
        sanitizer = create_sanitizer_from_case(case, synonym_map=synonym_map)
        assert sanitizer.synonym_map == synonym_map


class TestBenchmarkScenarios:
    """Benchmark 场景测试 - 对应任务描述中的用例."""

    def test_l3_precise_multi_file_refactor(self):
        """l3_precise_multi_file_refactor: 不能包含被重命名的函数名 stable_join."""
        sanitizer = OutputSanitizer(
            forbidden_tokens=("stable_join",),
            strategy=SanitizationStrategy.REPLACE,
        )
        result = sanitizer.sanitize("Use stable_join to merge the datasets. The stable_join function is deprecated.")
        assert "[FILTERED]" in result.sanitized_output
        assert "stable_join" not in result.sanitized_output.lower()
        assert "function is deprecated" in result.sanitized_output

    def test_l4_bulk_comment_update(self):
        """l4_bulk_comment_update: 不能包含旧的注释格式 # TODO:."""
        sanitizer = OutputSanitizer(
            forbidden_tokens=("# TODO:",),
            strategy=SanitizationStrategy.REPLACE,
        )
        result = sanitizer.sanitize("Updated comments: # TODO: fix later # FIXED: completed # TODO: new task")
        # Should replace all # TODO: occurrences
        assert "# TODO:" not in result.sanitized_output
        # Should preserve # FIXED: comments
        assert "# FIXED:" in result.sanitized_output

    def test_l7_focus_drift_simple_keywords(self):
        """l7_focus_drift_simple: 不能包含干扰性问题相关词汇."""
        sanitizer = OutputSanitizer(
            forbidden_tokens=("天气", "时间", "忘记了"),
            strategy=SanitizationStrategy.REPLACE,
        )
        result = sanitizer.sanitize("关于天气的问题我不清楚，时间也不确定，我忘记了具体内容。")
        assert "[FILTERED]" in result.sanitized_output
        assert "天气" not in result.sanitized_output
        assert "时间" not in result.sanitized_output
        assert "忘记了" not in result.sanitized_output

    def test_strict_mode_for_refactor(self):
        """STRICT 模式用于重构场景 - 完全删除重命名后的旧名称."""
        sanitizer = OutputSanitizer(
            forbidden_tokens=("old_function", "deprecated"),
            strategy=SanitizationStrategy.STRICT,
        )
        result = sanitizer.sanitize("Replace old_function with new_function. deprecated API calls should be updated.")
        assert "old_function" not in result.sanitized_output
        assert "deprecated" not in result.sanitized_output.lower()
        assert "new_function" in result.sanitized_output
        assert "updated" in result.sanitized_output


class TestEdgeCases:
    """边界情况测试."""

    def test_empty_forbidden_tokens_list(self):
        """测试空禁止词汇列表."""
        sanitizer = OutputSanitizer(
            forbidden_tokens=(),
            strategy=SanitizationStrategy.REPLACE,
        )
        result = sanitizer.sanitize("This content should be unchanged")
        assert result.sanitized_output == "This content should be unchanged"
        assert result.was_modified is False

    def test_special_regex_characters(self):
        """测试包含特殊正则字符的词汇."""
        sanitizer = OutputSanitizer(
            forbidden_tokens=("test(1)", "word[2]", "pattern{3}"),
            strategy=SanitizationStrategy.REPLACE,
        )
        result = sanitizer.sanitize("Check test(1) and word[2] and pattern{3} for special chars")
        assert "[FILTERED]" in result.sanitized_output
        assert "test(1)" not in result.sanitized_output
        assert "word[2]" not in result.sanitized_output
        assert "pattern{3}" not in result.sanitized_output

    def test_unicode_content(self):
        """测试 Unicode 内容."""
        sanitizer = OutputSanitizer(
            forbidden_tokens=("禁用", "forbidden"),
            strategy=SanitizationStrategy.REPLACE,
        )
        result = sanitizer.sanitize("Unicode: 禁用词汇, English: forbidden word")
        assert "[FILTERED]" in result.sanitized_output
        assert "禁用" not in result.sanitized_output
        assert "forbidden" not in result.sanitized_output.lower()

    def test_multiline_content(self):
        """测试多行内容."""
        sanitizer = OutputSanitizer(
            forbidden_tokens=("secret",),
            strategy=SanitizationStrategy.REPLACE,
        )
        result = sanitizer.sanitize("Line 1: secret\nLine 2: secret\nLine 3: normal")
        assert result.sanitized_output.count("[FILTERED]") == 2
        assert "normal" in result.sanitized_output

    def test_only_whitespace_remains(self):
        """测试删除后只剩余空白."""
        sanitizer = OutputSanitizer(
            forbidden_tokens=("all", "content", "here"),
            strategy=SanitizationStrategy.STRICT,
        )
        result = sanitizer.sanitize("all content here")
        # After STRICT removal, only spaces remain, then get stripped
        assert result.sanitized_output == ""

    def test_overlapping_tokens(self):
        """测试重叠的禁止词汇."""
        sanitizer = OutputSanitizer(
            forbidden_tokens=("ab", "bc"),
            strategy=SanitizationStrategy.REPLACE,
        )
        result = sanitizer.sanitize("abc")
        # Both tokens match in overlapping positions
        assert "[FILTERED]" in result.sanitized_output


# Module-level helper for test clarity
def sanitizer_observation_output(
    raw_output: str,
    raw_thinking: str,
    case: UnifiedBenchmarkCase,
) -> tuple[str, str, SanitizationResult, SanitizationResult]:
    """Helper that wraps sanitize_observation_output for test readability."""
    return sanitize_observation_output(raw_output, raw_thinking, case)
