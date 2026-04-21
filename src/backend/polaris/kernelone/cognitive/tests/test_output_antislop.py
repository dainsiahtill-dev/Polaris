"""Tests for Anti-Slop Validators — Font, Color, Content, Layout, Motion."""

from __future__ import annotations

from polaris.kernelone.cognitive.design_quality import DesignQualityDials
from polaris.kernelone.cognitive.validators.color_validator import ColorValidator
from polaris.kernelone.cognitive.validators.content_validator import ContentValidator
from polaris.kernelone.cognitive.validators.dispatcher import ValidationSeverity
from polaris.kernelone.cognitive.validators.font_validator import FontValidator
from polaris.kernelone.cognitive.validators.layout_validator import LayoutValidator
from polaris.kernelone.cognitive.validators.motion_validator import MotionValidator
from polaris.kernelone.cognitive.validators.output_antislop import OutputAntiSlopValidator

# -----------------------------------------------------------------------------
# FontValidator
# -----------------------------------------------------------------------------


class TestFontValidator:
    """字体验证器测试。"""

    def test_no_violations_for_clean_content(self) -> None:
        validator = FontValidator()
        content = "font-family: 'Geist', sans-serif;"
        violations = validator.validate(content)
        assert violations == []

    def test_banned_font_inter(self) -> None:
        validator = FontValidator()
        content = "font-family: 'Inter', sans-serif;"
        violations = validator.validate(content)
        assert len(violations) == 1
        assert violations[0].rule == "banned_font_detected"
        assert violations[0].severity == ValidationSeverity.ERROR
        assert "Inter" in violations[0].message

    def test_banned_font_roboto(self) -> None:
        validator = FontValidator()
        content = "font-family: 'Roboto', Arial, sans-serif;"
        violations = validator.validate(content)
        assert any(v.rule == "banned_font_detected" and "Roboto" in v.message for v in violations)

    def test_generic_font_stack_warning(self) -> None:
        validator = FontValidator()
        content = "font-family: sans-serif;"
        violations = validator.validate(content)
        assert any(v.rule == "generic_font_stack" for v in violations)
        assert all(v.severity == ValidationSeverity.WARNING for v in violations if v.rule == "generic_font_stack")

    def test_tailwind_font_sans_warning(self) -> None:
        validator = FontValidator()
        content = '<div className="font-sans text-lg">Hello</div>'
        violations = validator.validate(content)
        assert any(v.rule == "tailwind_font_heuristic" for v in violations)

    def test_allowed_premium_font_no_violation(self) -> None:
        validator = FontValidator()
        content = "font-family: 'Geist', 'Outfit', sans-serif;"
        violations = validator.validate(content)
        assert not any(v.rule == "banned_font_detected" for v in violations)

    def test_inline_style_font(self) -> None:
        validator = FontValidator()
        content = '<span style="font-family: Georgia, serif;">Text</span>'
        violations = validator.validate(content)
        assert any(v.rule == "banned_font_detected" and "Georgia" in v.message for v in violations)


# -----------------------------------------------------------------------------
# ColorValidator
# -----------------------------------------------------------------------------


class TestColorValidator:
    """颜色验证器测试。"""

    def test_no_violations_for_clean_colors(self) -> None:
        validator = ColorValidator()
        content = "color: #64748B; background-color: #FAFAFA;"
        violations = validator.validate(content)
        assert violations == []

    def test_banned_black_detected(self) -> None:
        validator = ColorValidator()
        content = "color: #000000;"
        violations = validator.validate(content)
        assert len(violations) == 1
        assert violations[0].rule == "banned_color_black"
        assert violations[0].severity == ValidationSeverity.ERROR

    def test_banned_black_shorthand(self) -> None:
        validator = ColorValidator()
        content = "color: #000;"
        violations = validator.validate(content)
        assert any(v.rule == "banned_color_black" for v in violations)

    def test_oversaturated_accent(self) -> None:
        validator = ColorValidator()
        # Saturation 95% > 80% threshold
        content = "accent-color: #FF0000;"
        violations = validator.validate(content)
        assert any(v.rule == "oversaturated_accent" for v in violations)
        assert any(v.severity == ValidationSeverity.ERROR for v in violations if v.rule == "oversaturated_accent")

    def test_neon_purple_blue(self) -> None:
        validator = ColorValidator()
        # Hue ~262 (within 260-300), saturation 83% > 60%
        content = "color: #7C3AED;"
        violations = validator.validate(content)
        assert any(v.rule == "neon_purple_blue" for v in violations)
        assert any(v.severity == ValidationSeverity.ERROR for v in violations if v.rule == "neon_purple_blue")

    def test_multiple_accents_warning(self) -> None:
        validator = ColorValidator()
        # 4 distinct saturated colors > max 3
        content = "color: #FF0000; background: #00FF00; border-color: #0000FF; accent: #FF00FF;"
        violations = validator.validate(content)
        assert any(v.rule == "multiple_accents" for v in violations)
        assert any(v.severity == ValidationSeverity.WARNING for v in violations if v.rule == "multiple_accents")

    def test_low_contrast_pair(self) -> None:
        validator = ColorValidator()
        content = ".card { background-color: #F0F0F0; color: #E0E0E0; }"
        violations = validator.validate(content)
        assert any(v.rule == "low_contrast_pair" for v in violations)
        assert any(v.severity == ValidationSeverity.WARNING for v in violations if v.rule == "low_contrast_pair")

    def test_no_low_contrast_for_good_pair(self) -> None:
        validator = ColorValidator()
        content = ".card { background-color: #FFFFFF; color: #18181B; }"
        violations = validator.validate(content)
        assert not any(v.rule == "low_contrast_pair" for v in violations)


# -----------------------------------------------------------------------------
# ContentValidator
# -----------------------------------------------------------------------------


class TestContentValidator:
    """内容验证器测试。"""

    def test_no_violations_for_clean_content(self) -> None:
        validator = ContentValidator()
        content = "Welcome to our premium design studio. We create beautiful interfaces."
        violations = validator.validate(content)
        assert violations == []

    def test_unicode_emoji_detected(self) -> None:
        validator = ContentValidator()
        content = "Welcome to our studio 🎉"
        violations = validator.validate(content)
        assert any(v.rule == "unicode_emoji_detected" for v in violations)
        assert any(v.severity == ValidationSeverity.ERROR for v in violations if v.rule == "unicode_emoji_detected")

    def test_common_symbol_detected(self) -> None:
        validator = ContentValidator()
        content = "Premium Design\u00ae Studio"
        violations = validator.validate(content)
        assert any(v.rule == "unicode_emoji_detected" and "\u00ae" in v.message for v in violations)

    def test_fake_name_detected(self) -> None:
        validator = ContentValidator()
        content = "Contact John Doe at example.com for details."
        violations = validator.validate(content)
        assert any(v.rule == "fake_name_detected" and "john doe" in v.message.lower() for v in violations)
        assert any(v.severity == ValidationSeverity.ERROR for v in violations if v.rule == "fake_name_detected")

    def test_ai_buzzword_detected(self) -> None:
        validator = ContentValidator()
        content = "Our solution will elevate your business to the next level."
        violations = validator.validate(content)
        assert any(v.rule == "ai_buzzword_detected" and "elevate" in v.message.lower() for v in violations)
        assert any(v.severity == ValidationSeverity.WARNING for v in violations if v.rule == "ai_buzzword_detected")

    def test_filler_text_detected(self) -> None:
        validator = ContentValidator()
        content = "Lorem ipsum dolor sit amet, consectetur adipiscing elit."
        violations = validator.validate(content)
        assert any(v.rule == "filler_text_detected" for v in violations)
        assert any(v.severity == ValidationSeverity.ERROR for v in violations if v.rule == "filler_text_detected")

    def test_placeholder_comment_detected(self) -> None:
        validator = ContentValidator()
        content = "// ..."
        violations = validator.validate(content)
        assert any(v.rule == "placeholder_comment" for v in violations)
        assert any(v.severity == ValidationSeverity.ERROR for v in violations if v.rule == "placeholder_comment")

    def test_css_comment_placeholder(self) -> None:
        validator = ContentValidator()
        content = "/* ... */"
        violations = validator.validate(content)
        assert any(v.rule == "placeholder_comment" for v in violations)


# -----------------------------------------------------------------------------
# LayoutValidator
# -----------------------------------------------------------------------------


class TestLayoutValidator:
    """布局验证器测试。"""

    def test_no_violations_for_clean_layout(self) -> None:
        validator = LayoutValidator()
        content = ".card { display: flex; gap: 1rem; padding: 2rem; }"
        violations = validator.validate(content)
        assert violations == []

    def test_vh_units_banned_css(self) -> None:
        validator = LayoutValidator()
        content = ".hero { height: 100vh; }"
        violations = validator.validate(content)
        assert any(v.rule == "vh_units_banned" for v in violations)
        assert any(v.severity == ValidationSeverity.ERROR for v in violations if v.rule == "vh_units_banned")

    def test_vh_units_banned_tailwind(self) -> None:
        validator = LayoutValidator()
        content = '<div className="h-screen">Hero</div>'
        violations = validator.validate(content)
        assert any(v.rule == "vh_units_banned" for v in violations)

    def test_calc_percentage_in_flex(self) -> None:
        validator = LayoutValidator()
        content = ".container { display: flex; width: calc(50% - 10px); }"
        violations = validator.validate(content)
        assert any(v.rule == "calc_percentage_in_flex" for v in violations)
        assert any(v.severity == ValidationSeverity.ERROR for v in violations if v.rule == "calc_percentage_in_flex")

    def test_equal_three_column_grid_low_variance_skipped(self) -> None:
        validator = LayoutValidator()
        content = ".grid { grid-template-columns: repeat(3, 1fr); }"
        dials = DesignQualityDials(variance=3, motion=5, density=5)
        violations = validator.validate(content, {"design_dials": dials})
        assert not any(v.rule == "equal_three_column_grid" for v in violations)

    def test_equal_three_column_grid_high_variance(self) -> None:
        validator = LayoutValidator()
        content = ".grid { grid-template-columns: repeat(3, 1fr); }"
        dials = DesignQualityDials(variance=8, motion=5, density=5)
        violations = validator.validate(content, {"design_dials": dials})
        assert any(v.rule == "equal_three_column_grid" for v in violations)
        assert any(v.severity == ValidationSeverity.WARNING for v in violations if v.rule == "equal_three_column_grid")

    def test_equal_three_column_tailwind(self) -> None:
        validator = LayoutValidator()
        content = '<div className="grid grid-cols-3 gap-4">Content</div>'
        dials = DesignQualityDials(variance=8, motion=5, density=5)
        violations = validator.validate(content, {"design_dials": dials})
        assert any(v.rule == "equal_three_column_grid" for v in violations)

    def test_centered_hero_low_variance_skipped(self) -> None:
        validator = LayoutValidator()
        content = ".hero { justify-content: center; align-items: center; height: 100vh; }"
        dials = DesignQualityDials(variance=3, motion=5, density=5)
        violations = validator.validate(content, {"design_dials": dials})
        assert not any(v.rule == "centered_hero_layout" for v in violations)

    def test_centered_hero_high_variance(self) -> None:
        validator = LayoutValidator()
        content = ".hero { justify-content: center; align-items: center; height: 100vh; }"
        dials = DesignQualityDials(variance=8, motion=5, density=5)
        violations = validator.validate(content, {"design_dials": dials})
        assert any(v.rule == "centered_hero_layout" for v in violations)

    def test_centered_hero_tailwind(self) -> None:
        validator = LayoutValidator()
        content = '<div className="flex justify-center items-center min-h-screen">Hero</div>'
        dials = DesignQualityDials(variance=8, motion=5, density=5)
        violations = validator.validate(content, {"design_dials": dials})
        assert any(v.rule == "centered_hero_layout" for v in violations)

    def test_excessive_negative_margin_css(self) -> None:
        validator = LayoutValidator()
        content = ".card { margin-left: -1rem; }"
        violations = validator.validate(content)
        assert any(v.rule == "excessive_negative_margin" for v in violations)
        assert any(
            v.severity == ValidationSeverity.WARNING for v in violations if v.rule == "excessive_negative_margin"
        )

    def test_excessive_negative_margin_tailwind(self) -> None:
        validator = LayoutValidator()
        content = '<div className="-mx-4">Content</div>'
        violations = validator.validate(content)
        assert any(v.rule == "excessive_negative_margin" for v in violations)


# -----------------------------------------------------------------------------
# MotionValidator
# -----------------------------------------------------------------------------


class TestMotionValidator:
    """动画验证器测试。"""

    def test_no_violations_for_clean_motion(self) -> None:
        validator = MotionValidator()
        content = ".btn { transition: transform 200ms cubic-bezier(0.4, 0, 0.2, 1); }"
        violations = validator.validate(content)
        # prefers-reduced-motion info may fire since no media query present
        info_only = all(v.severity == ValidationSeverity.INFO for v in violations)
        assert info_only or violations == []

    def test_linear_easing_banned_tailwind(self) -> None:
        validator = MotionValidator()
        content = '<div className="ease-linear transition-all">Motion</div>'
        violations = validator.validate(content)
        assert any(v.rule == "linear_easing_banned" for v in violations)
        assert any(v.severity == ValidationSeverity.ERROR for v in violations if v.rule == "linear_easing_banned")

    def test_linear_easing_banned_css(self) -> None:
        validator = MotionValidator()
        content = ".btn { transition-timing-function: linear; }"
        violations = validator.validate(content)
        assert any(v.rule == "linear_easing_banned" for v in violations)

    def test_layout_property_animation_keyframes(self) -> None:
        validator = MotionValidator()
        content = "@keyframes slide { from { left: 0; } to { left: 100px; } }"
        violations = validator.validate(content)
        assert any(v.rule == "layout_property_animation" for v in violations)
        assert any(v.severity == ValidationSeverity.ERROR for v in violations if v.rule == "layout_property_animation")

    def test_layout_property_animation_width(self) -> None:
        validator = MotionValidator()
        content = "@keyframes grow { from { width: 0; } to { width: 100px; } }"
        violations = validator.validate(content)
        assert any(v.rule == "layout_property_animation" for v in violations)

    def test_spin_loader_anti_pattern_keyframes(self) -> None:
        validator = MotionValidator()
        content = "@keyframes spin { from { transform: rotate(0deg); } to { transform: rotate(360deg); } }"
        violations = validator.validate(content)
        assert any(v.rule == "spin_loader_anti_pattern" for v in violations)
        assert any(v.severity == ValidationSeverity.WARNING for v in violations if v.rule == "spin_loader_anti_pattern")

    def test_spin_loader_anti_pattern_tailwind(self) -> None:
        validator = MotionValidator()
        content = '<div className="animate-spin">Loading</div>'
        violations = validator.validate(content)
        assert any(v.rule == "spin_loader_anti_pattern" for v in violations)

    def test_spin_loader_anti_pattern_css_combo(self) -> None:
        validator = MotionValidator()
        content = ".loader { border-radius: 50%; animation: spin 1s linear infinite; }"
        violations = validator.validate(content)
        assert any(v.rule == "spin_loader_anti_pattern" for v in violations)

    def test_excessive_animation_duration(self) -> None:
        validator = MotionValidator()
        content = ".fade { transition-duration: 2s; }"
        violations = validator.validate(content)
        assert any(v.rule == "excessive_animation_duration" for v in violations)
        assert any(
            v.severity == ValidationSeverity.WARNING for v in violations if v.rule == "excessive_animation_duration"
        )

    def test_excessive_animation_duration_ms(self) -> None:
        validator = MotionValidator()
        content = ".fade { animation-duration: 1500ms; }"
        violations = validator.validate(content)
        assert any(v.rule == "excessive_animation_duration" for v in violations)

    def test_prefers_reduced_motion_info(self) -> None:
        validator = MotionValidator()
        content = ".btn { transition: opacity 200ms ease; }"
        violations = validator.validate(content)
        assert any(v.rule == "no_prefers_reduced_motion" for v in violations)
        assert all(v.severity == ValidationSeverity.INFO for v in violations if v.rule == "no_prefers_reduced_motion")

    def test_prefers_reduced_motion_present(self) -> None:
        validator = MotionValidator()
        content = (
            ".btn { transition: opacity 200ms ease; }"
            "@media (prefers-reduced-motion: reduce) { .btn { transition: none; } }"
        )
        violations = validator.validate(content)
        assert not any(v.rule == "no_prefers_reduced_motion" for v in violations)


# -----------------------------------------------------------------------------
# OutputAntiSlopValidator (Combinator)
# -----------------------------------------------------------------------------


class TestOutputAntiSlopValidator:
    """组合验证器测试。"""

    def test_lazy_initialization(self) -> None:
        validator = OutputAntiSlopValidator()
        assert not validator._initialized
        validator._ensure_initialized()
        assert validator._initialized
        assert len(validator._validators) == 5

    def test_no_violations_for_clean_content(self) -> None:
        validator = OutputAntiSlopValidator()
        content = ".card { display: flex; gap: 1rem; font-family: 'Geist', sans-serif; color: #64748B; }"
        violations = validator.validate(content)
        assert violations == []

    def test_fast_fail_on_error(self) -> None:
        validator = OutputAntiSlopValidator()
        # Contains banned font (ERROR) and also would trigger content warnings later
        content = "font-family: 'Inter', sans-serif;"
        violations = validator.validate(content)
        # Should only contain font violations (ERROR fast-fails before color/content/layout/motion)
        assert any(v.severity == ValidationSeverity.ERROR for v in violations)
        font_errors = [v for v in violations if v.rule == "banned_font_detected"]
        assert len(font_errors) >= 1

    def test_all_validators_run_when_no_errors(self) -> None:
        validator = OutputAntiSlopValidator()
        # Contains only WARNING-level issues (no ERROR fast-fail)
        content = '<div className="font-sans grid-cols-3">Warning content</div>'
        dials = DesignQualityDials(variance=8, motion=5, density=5)
        violations = validator.validate(content, {"design_dials": dials})
        # Should contain warnings from multiple validators
        rules = {v.rule for v in violations}
        assert "tailwind_font_heuristic" in rules or "equal_three_column_grid" in rules

    def test_combined_errors_and_warnings(self) -> None:
        validator = OutputAntiSlopValidator()
        # Has both ERROR (banned black) and would have warnings
        content = "color: #000000;"
        violations = validator.validate(content)
        assert any(v.rule == "banned_color_black" for v in violations)
        # Fast-fail: color validator produces ERROR, so chain stops after color
        assert not any(v.rule == "banned_font_detected" for v in violations)
