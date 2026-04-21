"""Tests for Design Token System — Typography, Palette, Motion。"""

from __future__ import annotations

import pytest
from polaris.kernelone.cognitive.design_quality import DesignQualityDials, MotionPresetKey
from polaris.kernelone.cognitive.design_tokens.motion import (
    STAGGER_CASCADE_MS,
    STAGGER_RAPID_MS,
    MotionPresetLibrary,
    MotionToken,
)
from polaris.kernelone.cognitive.design_tokens.palette import (
    ColorPaletteGenerator,
    Palette,
    PaletteColor,
    PaletteTone,
)
from polaris.kernelone.cognitive.design_tokens.typography import (
    TypographyScale,
    TypographyTokenSystem,
)

# -----------------------------------------------------------------------------
# TypographyTokenSystem
# -----------------------------------------------------------------------------


class TestTypographyTokenSystem:
    """字体令牌系统测试。"""

    def test_gallery_scale(self) -> None:
        dials = DesignQualityDials(variance=5, motion=5, density=2)
        system = TypographyTokenSystem(dials)
        scale = system.generate_scale()
        assert isinstance(scale, TypographyScale)
        assert scale.display.size_px == 64
        assert scale.body.size_px == 18
        assert scale.body.max_width_ch == 65
        assert scale.mono.font_family == "mono"

    def test_daily_app_scale(self) -> None:
        dials = DesignQualityDials(variance=5, motion=5, density=5)
        system = TypographyTokenSystem(dials)
        scale = system.generate_scale()
        assert scale.display.size_px == 80
        assert scale.body.size_px == 16
        assert scale.body.max_width_ch == 70

    def test_cockpit_scale(self) -> None:
        dials = DesignQualityDials(variance=5, motion=5, density=9)
        system = TypographyTokenSystem(dials)
        scale = system.generate_scale()
        assert scale.display.size_px == 96
        assert scale.body.size_px == 13
        assert scale.body.max_width_ch == 80

    def test_tailwind_class_mapping(self) -> None:
        dials = DesignQualityDials(variance=5, motion=5, density=5)
        system = TypographyTokenSystem(dials)
        scale = system.generate_scale()
        assert system.get_tailwind_class(scale.body) == "text-base"
        assert system.get_tailwind_class(scale.display) == "text-8xl"
        assert system.get_tailwind_class(scale.caption) == "text-sm"

    def test_css_rule_generation(self) -> None:
        dials = DesignQualityDials(variance=5, motion=5, density=5)
        system = TypographyTokenSystem(dials)
        scale = system.generate_scale()
        css = system.get_css_rule(scale.body)
        assert css["font-size"] == "16px"
        assert css["font-weight"] == "400"
        assert css["letter-spacing"] == "0.0em"

    def test_scale_to_dict(self) -> None:
        dials = DesignQualityDials(variance=5, motion=5, density=2)
        system = TypographyTokenSystem(dials)
        scale = system.generate_scale()
        d = scale.to_dict()
        assert "display" in d
        assert d["display"]["size_px"] == 64
        assert d["body"]["max_width_ch"] == 65


# -----------------------------------------------------------------------------
# ColorPaletteGenerator
# -----------------------------------------------------------------------------


class TestColorPaletteGenerator:
    """颜色调色板生成器测试。"""

    def test_gallery_preset(self) -> None:
        dials = DesignQualityDials(variance=5, motion=5, density=2)
        gen = ColorPaletteGenerator(dials)
        palette = gen.generate()
        assert isinstance(palette, Palette)
        assert palette.get_by_tone(PaletteTone.BACKGROUND) is not None
        assert palette.get_by_tone(PaletteTone.PRIMARY) is not None

    def test_daily_app_preset(self) -> None:
        dials = DesignQualityDials(variance=5, motion=5, density=5)
        gen = ColorPaletteGenerator(dials)
        palette = gen.generate()
        assert palette.get_by_tone(PaletteTone.SECONDARY) is not None
        assert palette.get_by_tone(PaletteTone.ACCENT) is not None

    def test_cockpit_preset_dark_mode(self) -> None:
        dials = DesignQualityDials(variance=5, motion=5, density=9)
        gen = ColorPaletteGenerator(dials)
        palette = gen.generate()
        bg = palette.get_by_tone(PaletteTone.BACKGROUND)
        assert bg is not None
        assert bg.is_dark()
        text = palette.get_by_tone(PaletteTone.TEXT)
        assert text is not None
        assert not text.is_dark()

    def test_generate_from_base(self) -> None:
        dials = DesignQualityDials(variance=5, motion=5, density=5)
        gen = ColorPaletteGenerator(dials)
        palette = gen.generate_from_base("#3B82F6")
        assert palette.base_hex == "#3B82F6"
        primary = palette.get_by_tone(PaletteTone.PRIMARY)
        assert primary is not None
        assert primary.tone == PaletteTone.PRIMARY

    def test_generate_from_base_invalid_hex(self) -> None:
        dials = DesignQualityDials(variance=5, motion=5, density=5)
        gen = ColorPaletteGenerator(dials)
        with pytest.raises(ValueError, match="Invalid base color"):
            gen.generate_from_base("not-a-color")

    def test_saturation_calibration(self) -> None:
        dials = DesignQualityDials(variance=5, motion=5, density=5)
        gen = ColorPaletteGenerator(dials)
        # Highly saturated base color
        palette = gen.generate_from_base("#FF0000")
        primary = palette.get_by_tone(PaletteTone.PRIMARY)
        assert primary is not None
        assert primary.saturation <= 75  # calibrated down from 100%

    def test_palette_color_methods(self) -> None:
        color = PaletteColor("test", "#808080", PaletteTone.TEXT, 0, 0, 50)
        assert color.is_grayscale()
        assert not color.is_dark()

    def test_contrast_ratio(self) -> None:
        white = PaletteColor("white", "#FFFFFF", PaletteTone.BACKGROUND, 0, 0, 100)
        black = PaletteColor("black", "#000000", PaletteTone.TEXT, 0, 0, 0)
        ratio = white.contrast_ratio_with(black)
        assert ratio > 10  # very high contrast

    def test_palette_to_dict(self) -> None:
        dials = DesignQualityDials(variance=5, motion=5, density=5)
        gen = ColorPaletteGenerator(dials)
        palette = gen.generate()
        d = palette.to_dict()
        assert "base_hex" in d
        assert "colors" in d
        assert len(d["colors"]) > 0
        assert "hsl" in d["colors"][0]

    def test_validate_contrast_good(self) -> None:
        dials = DesignQualityDials(variance=5, motion=5, density=5)
        gen = ColorPaletteGenerator(dials)
        palette = gen.generate()
        warnings = gen.validate_contrast(palette)
        # Preset palettes should have good contrast
        assert all("Low contrast" not in w for w in warnings)

    def test_validate_contrast_poor(self) -> None:
        dials = DesignQualityDials(variance=5, motion=5, density=5)
        gen = ColorPaletteGenerator(dials)
        # Manually create a palette with poor contrast
        colors = (
            PaletteColor("bg", "#F0F0F0", PaletteTone.BACKGROUND, 0, 0, 94),
            PaletteColor("text", "#E8E8E8", PaletteTone.TEXT, 0, 0, 91),
        )
        palette = Palette(colors=colors)
        warnings = gen.validate_contrast(palette)
        assert any("Low contrast" in w for w in warnings)


# -----------------------------------------------------------------------------
# MotionPresetLibrary
# -----------------------------------------------------------------------------


class TestMotionPresetLibrary:
    """动画预设库测试。"""

    def test_hover_only_preset(self) -> None:
        dials = DesignQualityDials(variance=5, motion=2, density=5)
        lib = MotionPresetLibrary(dials)
        assert lib._preset_key == MotionPresetKey.HOVER_ONLY
        hover = lib.get_hover_token()
        assert hover.duration_ms == 150
        assert "cubic-bezier" in hover.easing

    def test_spring_gentle_preset(self) -> None:
        dials = DesignQualityDials(variance=5, motion=5, density=5)
        dials = DesignQualityDials(variance=5, motion=5, density=5)
        # Need to create with motion > 3 to get past HOVER_ONLY
        dials = DesignQualityDials(variance=5, motion=5, density=5)
        # Actually, motion=5 gives HOVER_ONLY since <=3 is hover_only, <=7 is spring_gentle
        # Wait, let me check: motion <= 3 = hover_only, motion <= 7 = spring_gentle, else spring_premium
        # So motion=5 should be spring_gentle
        lib = MotionPresetLibrary(dials)
        assert lib._preset_key == MotionPresetKey.SPRING_GENTLE
        hover = lib.get_hover_token()
        assert hover.duration_ms == 200
        assert "spring" in hover.easing

    def test_spring_premium_preset(self) -> None:
        dials = DesignQualityDials(variance=5, motion=9, density=5)
        lib = MotionPresetLibrary(dials)
        assert lib._preset_key == MotionPresetKey.SPRING_PREMIUM
        hover = lib.get_hover_token()
        assert hover.duration_ms == 250
        assert "spring" in hover.easing

    def test_enter_tokens_hover_only(self) -> None:
        dials = DesignQualityDials(variance=5, motion=2, density=5)
        lib = MotionPresetLibrary(dials)
        tokens = lib.get_enter_tokens()
        assert len(tokens) == 1
        assert tokens[0].name == "enter"

    def test_enter_tokens_spring_gentle(self) -> None:
        dials = DesignQualityDials(variance=5, motion=5, density=5)
        lib = MotionPresetLibrary(dials)
        tokens = lib.get_enter_tokens()
        assert len(tokens) == 2
        assert tokens[0].name == "enter-fade"
        assert tokens[1].name == "enter-slide"
        assert tokens[1].delay_ms == STAGGER_RAPID_MS

    def test_enter_tokens_spring_premium(self) -> None:
        dials = DesignQualityDials(variance=5, motion=9, density=5)
        lib = MotionPresetLibrary(dials)
        tokens = lib.get_enter_tokens()
        assert len(tokens) == 3
        assert tokens[0].name == "enter-fade"
        assert tokens[1].name == "enter-scale"
        assert tokens[2].name == "enter-blur"
        assert tokens[2].delay_ms == STAGGER_CASCADE_MS * 2

    def test_exit_tokens(self) -> None:
        dials = DesignQualityDials(variance=5, motion=9, density=5)
        lib = MotionPresetLibrary(dials)
        token = lib.get_exit_token()
        assert token.name == "exit"
        assert token.duration_ms == 300

    def test_stagger_delay(self) -> None:
        dials = DesignQualityDials(variance=5, motion=2, density=5)
        lib = MotionPresetLibrary(dials)
        assert lib.get_stagger_delay_ms() == 0

        dials = DesignQualityDials(variance=5, motion=5, density=5)
        lib = MotionPresetLibrary(dials)
        assert lib.get_stagger_delay_ms() == STAGGER_RAPID_MS

        dials = DesignQualityDials(variance=5, motion=9, density=5)
        lib = MotionPresetLibrary(dials)
        assert lib.get_stagger_delay_ms() == STAGGER_CASCADE_MS

    def test_motion_token_to_css_transition(self) -> None:
        token = MotionToken(
            name="hover",
            duration_ms=200,
            easing="spring(200, 25)",
            properties=frozenset({"opacity", "transform"}),
        )
        css = token.to_css_transition()
        assert "opacity, transform" in css or "transform, opacity" in css
        assert "200ms" in css

    def test_motion_token_to_framer_motion(self) -> None:
        token = MotionToken(
            name="hover",
            duration_ms=200,
            easing="spring(200, 25)",
        )
        config = token.to_framer_motion()
        assert config["type"] == "spring"
        assert config["stiffness"] == 200
        assert config["damping"] == 25

    def test_motion_token_to_framer_motion_non_spring(self) -> None:
        token = MotionToken(
            name="hover",
            duration_ms=200,
            easing="cubic-bezier(0.4, 0, 0.2, 1)",
        )
        config = token.to_framer_motion()
        assert config["duration"] == 0.2
        assert config["ease"] == "cubic-bezier(0.4, 0, 0.2, 1)"

    def test_motion_token_to_gsap_config(self) -> None:
        token = MotionToken(
            name="hover",
            duration_ms=200,
            easing="cubic-bezier(0.4, 0, 0.2, 1)",
            delay_ms=100,
        )
        config = token.to_gsap_config()
        assert config["duration"] == 0.2
        assert config["delay"] == 0.1
        assert config["ease"] == "cubic-bezier(0.4, 0, 0.2, 1)"

    def test_motion_token_duration_validation(self) -> None:
        with pytest.raises(ValueError):
            MotionToken(name="bad", duration_ms=6000, easing="linear")

    def test_animation_library_conflict_detection(self) -> None:
        conflicts = MotionPresetLibrary.check_animation_library_conflict(["gsap", "framer-motion"])
        assert len(conflicts) > 0
        assert any("gsap" in c and "framer" in c for c in conflicts)

    def test_no_conflict_same_library(self) -> None:
        conflicts = MotionPresetLibrary.check_animation_library_conflict(["gsap"])
        assert conflicts == []

    def test_no_conflict_allowed_pair(self) -> None:
        conflicts = MotionPresetLibrary.check_animation_library_conflict(["framer-motion", "motion"])
        assert conflicts == []
