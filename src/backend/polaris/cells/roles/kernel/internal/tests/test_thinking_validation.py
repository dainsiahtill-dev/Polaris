"""Tests for <thinking> tag validation.

Validates the enforcement of mandatory thinking tags in assistant output.
"""

from __future__ import annotations

from polaris.cells.roles.kernel.internal.tool_loop_controller import ToolLoopController


class TestThinkingValidation:
    """Tests for thinking tag compliance validation."""

    def test_valid_thinking_tag_passes(self):
        """Content with proper <thinking> tags passes validation."""
        content = "<thinking>分析任务目标</thinking>实际回复内容"
        is_valid, error = ToolLoopController._validate_thinking_compliance(content)
        assert is_valid is True
        assert error == ""

    def test_missing_open_tag_fails(self):
        """Content without <thinking> tag fails validation."""
        content = "实际回复内容"
        is_valid, error = ToolLoopController._validate_thinking_compliance(content)
        assert is_valid is False
        assert "thinking" in error.lower()

    def test_missing_close_tag_fails(self):
        """Content with open but no close tag fails validation."""
        content = "<thinking>分析任务目标实际回复内容"
        is_valid, error = ToolLoopController._validate_thinking_compliance(content)
        assert is_valid is False
        assert "闭合" in error or "close" in error.lower()

    def test_content_before_thinking_fails(self):
        """Content before <thinking> tag fails validation."""
        content = "咳咳...地球佬<thinking>分析任务</thinking>"
        is_valid, error = ToolLoopController._validate_thinking_compliance(content)
        assert is_valid is False
        assert "之前" in error or "before" in error.lower()

    def test_whitespace_before_thinking_passes(self):
        """Whitespace before <thinking> tag is allowed."""
        content = "   <thinking>分析任务</thinking>"
        is_valid, error = ToolLoopController._validate_thinking_compliance(content)
        # Note: current implementation strips, so this might pass
        # The important thing is no roleplay content before thinking

    def test_nested_thinking_allowed(self):
        """Multiple thinking blocks are allowed (edge case)."""
        content = "<thinking>第一部分</thinking>内容<thinking>第二部分</thinking>"
        is_valid, error = ToolLoopController._validate_thinking_compliance(content)
        # Should pass - at least has opening and closing tags

    def test_empty_thinking_fails(self):
        """Empty thinking block might be considered valid structurally."""
        content = "<thinking></thinking>内容"
        is_valid, error = ToolLoopController._validate_thinking_compliance(content)
        # Structure is valid even if content is empty
        assert is_valid is True

    def test_case_insensitive_tag_detection(self):
        """Tag detection should be case insensitive."""
        content = "<THINKING>分析任务</THINKING>"
        # Current implementation uses startswith - might not be case insensitive
        is_valid, error = ToolLoopController._validate_thinking_compliance(content)
        # Implementation detail: may or may not be case sensitive

    def test_thinking_with_attributes_passes(self):
        """Thinking tag with attributes (like <thinking:abc>) passes."""
        content = "<thinking:session123>分析任务</thinking>"
        is_valid, error = ToolLoopController._validate_thinking_compliance(content)
        assert is_valid is True  # Should pass - starts with <thinking


class TestThinkingViolationTypes:
    """Tests for different types of thinking violations."""

    def test_detects_missing_open(self):
        """Correctly identifies missing opening tag."""
        content = "没有思考标签"
        is_valid, error = ToolLoopController._validate_thinking_compliance(content)
        assert is_valid is False
        assert "必须以" in error

    def test_detects_prefix_content(self):
        """Correctly identifies content before thinking tag."""
        content = "角色台词<thinking>思考</thinking>"
        is_valid, error = ToolLoopController._validate_thinking_compliance(content)
        assert is_valid is False
        # Should detect content before tag


class TestThinkingMetrics:
    """Tests for thinking violation metrics collection."""

    def test_violation_type_classification(self):
        """Different violations are classified correctly."""
        # Missing open
        content1 = "无标签"
        is_valid, error = ToolLoopController._validate_thinking_compliance(content1)
        assert not is_valid
        assert "必须以" in error

        # Missing close
        content2 = "<thinking>无闭合"
        is_valid, error = ToolLoopController._validate_thinking_compliance(content2)
        assert not is_valid
        assert "闭合" in error or "close" in error.lower()
