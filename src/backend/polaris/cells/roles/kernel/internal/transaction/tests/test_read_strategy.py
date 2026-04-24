"""Tests for read_strategy module."""

from __future__ import annotations

import pytest
from polaris.cells.roles.kernel.internal.transaction.read_strategy import (
    ReadStrategy,
    _should_use_slice_mode,
    build_slice_read_plan,
    calculate_slice_ranges,
    determine_optimal_strategy,
    is_content_truncated,
    should_switch_to_slice_mode,
)


class TestShouldUseSliceMode:
    """测试 _should_use_slice_mode 函数。"""

    def test_empty_file_path_returns_false(self):
        should_slice, reason = _should_use_slice_mode("")
        assert should_slice is False
        assert "empty" in reason.lower()

    def test_small_file_returns_false(self):
        should_slice, reason = _should_use_slice_mode("test.py", content_length=1024)
        assert should_slice is False
        assert "within normal range" in reason

    def test_large_file_by_bytes_returns_true(self):
        should_slice, reason = _should_use_slice_mode("test.py", content_length=200 * 1024)
        assert should_slice is True
        assert "exceeds threshold" in reason

    def test_large_file_by_lines_returns_true(self):
        should_slice, reason = _should_use_slice_mode("test.py", line_count=2000)
        assert should_slice is True
        assert "line count" in reason

    def test_log_extension_returns_true(self):
        should_slice, reason = _should_use_slice_mode("app.log")
        assert should_slice is True
        assert "extension" in reason

    def test_jsonl_extension_returns_true(self):
        should_slice, _reason = _should_use_slice_mode("data.jsonl")
        assert should_slice is True

    def test_csv_extension_returns_true(self):
        should_slice, _reason = _should_use_slice_mode("data.csv")
        assert should_slice is True

    def test_normal_extension_returns_false(self):
        should_slice, _reason = _should_use_slice_mode("script.py")
        assert should_slice is False

    def test_custom_threshold(self):
        should_slice, reason = _should_use_slice_mode("test.py", content_length=50 * 1024, threshold_bytes=40 * 1024)
        assert should_slice is True
        assert "exceeds threshold" in reason


class TestIsContentTruncated:
    """测试 is_content_truncated 函数。"""

    def test_empty_content_returns_false(self):
        is_truncated, reason = is_content_truncated("")
        assert is_truncated is False
        assert "empty" in reason.lower()

    def test_none_content_returns_false(self):
        is_truncated, _reason = is_content_truncated(None)
        assert is_truncated is False

    def test_content_ending_with_dots(self):
        is_truncated, reason = is_content_truncated("some content...")
        assert is_truncated is True
        assert "truncation marker" in reason

    def test_content_ending_with_truncated_marker(self):
        is_truncated, _reason = is_content_truncated("some content [truncated]")
        assert is_truncated is True

    def test_content_with_truncated_in_middle(self):
        # When "truncated" is in the middle of content (not near end), it should not be detected
        # Create content where "truncated" is far from the end (>200 chars)
        content = "some [truncated] content here" + "x" * 300
        is_truncated, _reason = is_content_truncated(content)
        assert is_truncated is False

    def test_metadata_truncated_true(self):
        is_truncated, reason = is_content_truncated("some content", result_metadata={"truncated": True})
        assert is_truncated is True
        assert "metadata indicates" in reason

    def test_metadata_truncated_false(self):
        is_truncated, _reason = is_content_truncated("some content", result_metadata={"truncated": False})
        assert is_truncated is False

    def test_line_count_mismatch(self):
        content = "line1\nline2\nline3"
        is_truncated, reason = is_content_truncated(content, result_metadata={"line_count": 100})
        assert is_truncated is True
        assert "mismatch" in reason

    def test_chinese_truncation_marker(self):
        is_truncated, _reason = is_content_truncated("some content [截断]")
        assert is_truncated is True

    def test_truncated_warning_near_end(self):
        content = "a" * 500 + " (content truncated due to size)"
        is_truncated, reason = is_content_truncated(content)
        assert is_truncated is True
        assert "truncation warning" in reason


class TestCalculateSliceRanges:
    """测试 calculate_slice_ranges 函数。"""

    def test_zero_lines_returns_empty(self):
        ranges = calculate_slice_ranges(0)
        assert ranges == []

    def test_small_file_single_range(self):
        ranges = calculate_slice_ranges(50, slice_size=200)
        assert ranges == [(1, 50)]

    def test_exact_slice_boundary(self):
        ranges = calculate_slice_ranges(200, slice_size=200)
        assert ranges == [(1, 200)]

    def test_two_slices(self):
        ranges = calculate_slice_ranges(250, slice_size=200)
        assert ranges == [(1, 200), (201, 250)]

    def test_multiple_slices(self):
        ranges = calculate_slice_ranges(500, slice_size=200)
        assert ranges == [(1, 200), (201, 400), (401, 500)]

    def test_with_target_line(self):
        ranges = calculate_slice_ranges(500, slice_size=200, target_line=150)
        # 应该优先包含目标行及其上下文
        assert len(ranges) >= 2
        # 第一个范围应该包含目标行
        first_start, first_end = ranges[0]
        assert first_start <= 150 <= first_end

    def test_target_line_at_boundary(self):
        ranges = calculate_slice_ranges(500, slice_size=200, target_line=1)
        first_start, _first_end = ranges[0]
        assert first_start == 1


class TestBuildSliceReadPlan:
    """测试 build_slice_read_plan 函数。"""

    def test_basic_plan(self):
        plan = build_slice_read_plan("test.py", total_lines=500, slice_size=200)
        assert plan["file_path"] == "test.py"
        assert plan["total_lines"] == 500
        assert plan["slice_size"] == 200
        assert plan["strategy"] == "slice_mode"
        assert plan["estimated_calls"] == 3
        assert len(plan["ranges"]) == 3

    def test_small_file_single_call(self):
        plan = build_slice_read_plan("test.py", total_lines=50, slice_size=200)
        assert plan["estimated_calls"] == 1
        assert plan["ranges"] == [{"start": 1, "end": 50}]

    def test_with_target_line(self):
        plan = build_slice_read_plan("test.py", total_lines=500, slice_size=200, target_line=150)
        assert plan["estimated_calls"] >= 2


class TestDetermineOptimalStrategy:
    """测试 determine_optimal_strategy 函数。"""

    def test_normal_file(self):
        strategy = determine_optimal_strategy("test.py", content="small content")
        assert strategy.use_slice_mode is False
        assert "within normal range" in strategy.reason

    def test_truncated_content(self):
        strategy = determine_optimal_strategy(
            "test.py",
            content="some content...",
            result_metadata={"truncated": True},
        )
        assert strategy.use_slice_mode is True
        assert "truncated" in strategy.reason

    def test_large_file(self):
        large_content = "x" * (200 * 1024)
        strategy = determine_optimal_strategy("test.py", content=large_content)
        assert strategy.use_slice_mode is True
        assert "exceeds threshold" in strategy.reason

    def test_large_line_count(self):
        strategy = determine_optimal_strategy("test.py", total_lines=2000)
        assert strategy.use_slice_mode is True
        assert "line count" in strategy.reason

    def test_returns_read_strategy_object(self):
        strategy = determine_optimal_strategy("test.py")
        assert isinstance(strategy, ReadStrategy)
        assert hasattr(strategy, "use_slice_mode")
        assert hasattr(strategy, "slice_size_lines")
        assert hasattr(strategy, "reason")


class TestShouldSwitchToSliceMode:
    """测试 should_switch_to_slice_mode 便捷函数。"""

    def test_returns_false_for_normal_file(self):
        result = should_switch_to_slice_mode("test.py", content="normal")
        assert result is False

    def test_returns_true_for_truncated(self):
        result = should_switch_to_slice_mode("test.py", content="content...")
        assert result is True

    def test_returns_true_for_large_file(self):
        large_content = "x" * (200 * 1024)
        result = should_switch_to_slice_mode("test.py", content=large_content)
        assert result is True


class TestReadStrategyDataclass:
    """测试 ReadStrategy 数据类。"""

    def test_default_values(self):
        strategy = ReadStrategy(use_slice_mode=True)
        assert strategy.use_slice_mode is True
        assert strategy.slice_size_lines == 200  # 默认值
        assert strategy.reason == ""

    def test_custom_values(self):
        strategy = ReadStrategy(use_slice_mode=True, slice_size_lines=100, reason="test reason")
        assert strategy.slice_size_lines == 100
        assert strategy.reason == "test reason"

    def test_to_dict(self):
        strategy = ReadStrategy(use_slice_mode=True, slice_size_lines=150, reason="test")
        d = strategy.to_dict()
        assert d == {
            "use_slice_mode": True,
            "slice_size_lines": 150,
            "reason": "test",
        }

    def test_frozen_dataclass(self):
        strategy = ReadStrategy(use_slice_mode=True)
        with pytest.raises(AttributeError):
            strategy.use_slice_mode = False


class TestEdgeCases:
    """测试边界情况。"""

    def test_very_large_file(self):
        strategy = determine_optimal_strategy("huge.log", content="x" * (10 * 1024 * 1024))
        assert strategy.use_slice_mode is True

    def test_file_with_only_whitespace(self):
        is_truncated, _reason = is_content_truncated("   \n   \n   ")
        assert is_truncated is False

    def test_unicode_content(self):
        content = "中文内容" * 1000 + "..."
        is_truncated, _reason = is_content_truncated(content)
        assert is_truncated is True

    def test_multiple_truncation_markers(self):
        content = "content... [truncated]"
        is_truncated, _reason = is_content_truncated(content)
        assert is_truncated is True

    def test_exact_threshold_boundary(self):
        # 正好在阈值边界
        content = "x" * (100 * 1024)
        should_slice, _reason = _should_use_slice_mode("test.py", content_length=len(content))
        # 超过阈值才返回 True，所以正好在边界应该返回 False
        assert should_slice is False

    def test_one_byte_over_threshold(self):
        content = "x" * (100 * 1024 + 1)
        should_slice, _reason = _should_use_slice_mode("test.py", content_length=len(content))
        assert should_slice is True
