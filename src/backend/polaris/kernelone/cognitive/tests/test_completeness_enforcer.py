"""Tests for OutputCompletenessEnforcer — 输出完整性 enforcement。"""

from __future__ import annotations

from polaris.kernelone.cognitive.validators.completeness_enforcer import (
    BANNED_PLACEHOLDER_PATTERNS,
    OutputCompletenessEnforcer,
)
from polaris.kernelone.cognitive.validators.dispatcher import ValidationSeverity

# -----------------------------------------------------------------------------
# Placeholder Pattern Detection
# -----------------------------------------------------------------------------


class TestPlaceholderPatterns:
    """占位符模式检测测试。"""

    def test_no_violations_for_complete_code(self) -> None:
        enforcer = OutputCompletenessEnforcer()
        content = "function add(a, b) {\n  return a + b;\n}\n"
        violations = enforcer.validate(content)
        assert violations == []

    def test_ellipsis_comment_detected(self) -> None:
        enforcer = OutputCompletenessEnforcer()
        content = "// ..."
        violations = enforcer.validate(content)
        assert any(v.rule == "placeholder_ellipsis_comment" for v in violations)
        assert any(v.severity == ValidationSeverity.ERROR for v in violations)

    def test_block_comment_ellipsis_detected(self) -> None:
        enforcer = OutputCompletenessEnforcer()
        content = "/* ... */"
        violations = enforcer.validate(content)
        assert any(v.rule == "placeholder_ellipsis_block_comment" for v in violations)

    def test_html_comment_ellipsis_detected(self) -> None:
        enforcer = OutputCompletenessEnforcer()
        content = "<!-- ... -->"
        violations = enforcer.validate(content)
        assert any(v.rule == "placeholder_ellipsis_html_comment" for v in violations)

    def test_hash_comment_ellipsis_detected(self) -> None:
        enforcer = OutputCompletenessEnforcer()
        content = "# ..."
        violations = enforcer.validate(content)
        assert any(v.rule == "placeholder_ellipsis_hash_comment" for v in violations)

    def test_braces_ellipsis_detected(self) -> None:
        enforcer = OutputCompletenessEnforcer()
        content = "{ ... }"
        violations = enforcer.validate(content)
        assert any(v.rule == "placeholder_ellipsis_braces" for v in violations)

    def test_empty_todo_detected(self) -> None:
        enforcer = OutputCompletenessEnforcer()
        content = "// TODO"
        violations = enforcer.validate(content)
        assert any(v.rule == "placeholder_empty_todo" for v in violations)

    def test_same_pattern_phrase_detected(self) -> None:
        enforcer = OutputCompletenessEnforcer()
        content = "the rest follows the same pattern"
        violations = enforcer.validate(content)
        assert any(v.rule == "placeholder_same_pattern" for v in violations)

    def test_brevity_phrase_detected(self) -> None:
        enforcer = OutputCompletenessEnforcer()
        content = "for brevity"
        violations = enforcer.validate(content)
        assert any(v.rule == "placeholder_brevity" for v in violations)

    def test_offer_details_detected(self) -> None:
        enforcer = OutputCompletenessEnforcer()
        content = "I can provide more details"
        violations = enforcer.validate(content)
        assert any(v.rule == "placeholder_offer_details" for v in violations)

    def test_paused_detected(self) -> None:
        enforcer = OutputCompletenessEnforcer()
        content = "[PAUSED]"
        violations = enforcer.validate(content)
        assert any(v.rule == "placeholder_paused" for v in violations)

    def test_bracket_ellipsis_detected(self) -> None:
        enforcer = OutputCompletenessEnforcer()
        content = "[...]"
        violations = enforcer.validate(content)
        assert any(v.rule == "placeholder_bracket_ellipsis" for v in violations)

    def test_remaining_similar_detected(self) -> None:
        enforcer = OutputCompletenessEnforcer()
        content = "remaining methods are similar"
        violations = enforcer.validate(content)
        assert any(v.rule == "placeholder_remaining_similar" for v in violations)

    def test_remaining_todo_detected(self) -> None:
        enforcer = OutputCompletenessEnforcer()
        content = "// Add remaining"
        violations = enforcer.validate(content)
        assert any(v.rule == "placeholder_remaining_todo" for v in violations)

    def test_multiple_placeholders(self) -> None:
        enforcer = OutputCompletenessEnforcer()
        content = "// ...\n/* ... */\nfor brevity"
        violations = enforcer.validate(content)
        rules = [v.rule for v in violations]
        assert "placeholder_ellipsis_comment" in rules
        assert "placeholder_ellipsis_block_comment" in rules
        assert "placeholder_brevity" in rules


# -----------------------------------------------------------------------------
# Skeleton Output Detection
# -----------------------------------------------------------------------------


class TestSkeletonOutput:
    """骨架输出检测测试。"""

    def test_high_placeholder_ratio_detected(self) -> None:
        enforcer = OutputCompletenessEnforcer()
        # 50% placeholder lines
        content = "// TODO implement\n// FIXME placeholder\nfunction test() {}\n// placeholder\n// implement"
        violations = enforcer.validate(content)
        assert any(v.rule == "skeleton_placeholder_ratio" for v in violations)
        assert any(v.severity == ValidationSeverity.ERROR for v in violations if v.rule == "skeleton_placeholder_ratio")

    def test_midsection_omission_detected(self) -> None:
        enforcer = OutputCompletenessEnforcer()
        content = (
            "function start() {}\n"
            "\n"
            "... remaining methods omitted ...\n"
            "... skipped for brevity ...\n"
            "\n"
            "function end() {}\n"
        )
        violations = enforcer.validate(content)
        assert any(v.rule == "skeleton_midsection_omission" for v in violations)

    def test_no_skeleton_for_complete_content(self) -> None:
        enforcer = OutputCompletenessEnforcer()
        content = "function one() { return 1; }\nfunction two() { return 2; }\nfunction three() { return 3; }\n"
        violations = enforcer.validate(content)
        assert not any(v.rule.startswith("skeleton_") for v in violations)


# -----------------------------------------------------------------------------
# Minimum Lines Detection
# -----------------------------------------------------------------------------


class TestMinLines:
    """代码行数下限检测测试。"""

    def test_short_output_warning(self) -> None:
        enforcer = OutputCompletenessEnforcer()
        # 3 code lines, length > 100 chars
        content = "function a() {}\nfunction b() {}\nfunction c() {}\n" + " " * 120
        violations = enforcer.validate(content)
        assert any(v.rule == "output_too_short" for v in violations)
        assert any(v.severity == ValidationSeverity.WARNING for v in violations if v.rule == "output_too_short")

    def test_short_output_no_warning_when_below_length_threshold(self) -> None:
        enforcer = OutputCompletenessEnforcer()
        # Very short content (< 100 chars) should not trigger
        content = "x = 1\n"
        violations = enforcer.validate(content)
        assert not any(v.rule == "output_too_short" for v in violations)

    def test_long_output_no_warning(self) -> None:
        enforcer = OutputCompletenessEnforcer()
        content = "\n".join(f"function f{i}() {{ return {i}; }}" for i in range(20))
        violations = enforcer.validate(content)
        assert not any(v.rule == "output_too_short" for v in violations)


# -----------------------------------------------------------------------------
# Integration
# -----------------------------------------------------------------------------


class TestEnforcerIntegration:
    """集成测试 — 多种违规同时出现。"""

    def test_all_three_categories(self) -> None:
        enforcer = OutputCompletenessEnforcer()
        content = "// ...\n// TODO\nfunction test() {}\n// remaining methods are similar\nfunction end() {}\n"
        violations = enforcer.validate(content)
        rules = {v.rule for v in violations}
        assert "placeholder_ellipsis_comment" in rules
        assert "placeholder_empty_todo" in rules
        assert "placeholder_remaining_similar" in rules

    def test_empty_content(self) -> None:
        enforcer = OutputCompletenessEnforcer()
        violations = enforcer.validate("")
        assert violations == []

    def test_banned_patterns_constant_coverage(self) -> None:
        """确保 BANNED_PLACEHOLDER_PATTERNS 常量不为空。"""
        assert len(BANNED_PLACEHOLDER_PATTERNS) > 0
        for pattern, rule_id in BANNED_PLACEHOLDER_PATTERNS:
            assert isinstance(pattern, str)
            assert isinstance(rule_id, str)
            assert len(pattern) > 0
            assert len(rule_id) > 0
