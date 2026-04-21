"""Tests for DesignSystemSpec and DesignSystemExporter."""

from __future__ import annotations

import pytest
from polaris.kernelone.cognitive.design_quality import DesignQualityDials
from polaris.kernelone.cognitive.design_system import (
    AntiPatternsSpec,
    ColorRole,
    ColorToken,
    ComponentSpec,
    DesignSystemExporter,
    DesignSystemSpec,
    LayoutSpec,
    MotionSpec,
    TypographySpec,
)

# -----------------------------------------------------------------------------
# ColorToken
# -----------------------------------------------------------------------------


class TestColorToken:
    """颜色令牌测试。"""

    def test_valid_hex_6(self) -> None:
        token = ColorToken("primary", "#3B82F6", ColorRole.PRIMARY)
        assert token.name == "primary"
        assert token.hex_value == "#3B82F6"
        assert token.role is ColorRole.PRIMARY

    def test_valid_hex_3(self) -> None:
        token = ColorToken("red", "#F00", ColorRole.ACCENT)
        assert token.hex_value == "#F00"

    def test_invalid_hex_length(self) -> None:
        with pytest.raises(ValueError, match="3 or 6 digit hex"):
            ColorToken("bad", "#12345", ColorRole.PRIMARY)

    def test_invalid_hex_chars(self) -> None:
        with pytest.raises(ValueError, match="invalid hex"):
            ColorToken("bad", "#GGGGGG", ColorRole.PRIMARY)

    def test_frozen(self) -> None:
        token = ColorToken("primary", "#3B82F6", ColorRole.PRIMARY)
        with pytest.raises(AttributeError):
            token.name = "secondary"  # type: ignore[misc]


# -----------------------------------------------------------------------------
# TypographySpec
# -----------------------------------------------------------------------------


class TestTypographySpec:
    """字体规范测试。"""

    def test_defaults(self) -> None:
        spec = TypographySpec(display_font="Outfit", body_font="Geist", mono_font="JetBrains Mono")
        assert spec.base_size_px == 16
        assert spec.scale_ratio == 1.25

    def test_base_size_too_small(self) -> None:
        with pytest.raises(ValueError, match="base_size_px must be in"):
            TypographySpec(display_font="A", body_font="B", mono_font="C", base_size_px=4)

    def test_base_size_too_large(self) -> None:
        with pytest.raises(ValueError, match="base_size_px must be in"):
            TypographySpec(display_font="A", body_font="B", mono_font="C", base_size_px=64)

    def test_scale_ratio_too_small(self) -> None:
        with pytest.raises(ValueError, match="scale_ratio must be in"):
            TypographySpec(display_font="A", body_font="B", mono_font="C", scale_ratio=0.5)

    def test_scale_ratio_too_large(self) -> None:
        with pytest.raises(ValueError, match="scale_ratio must be in"):
            TypographySpec(display_font="A", body_font="B", mono_font="C", scale_ratio=3.0)


# -----------------------------------------------------------------------------
# ComponentSpec
# -----------------------------------------------------------------------------


class TestComponentSpec:
    """组件规范测试。"""

    def test_defaults(self) -> None:
        spec = ComponentSpec()
        assert spec.border_radius_px == 8
        assert spec.shadow_depth == "md"

    def test_invalid_shadow(self) -> None:
        with pytest.raises(ValueError, match="shadow_depth must be one of"):
            ComponentSpec(shadow_depth="xxl")


# -----------------------------------------------------------------------------
# LayoutSpec
# -----------------------------------------------------------------------------


class TestLayoutSpec:
    """布局规范测试。"""

    def test_defaults(self) -> None:
        spec = LayoutSpec()
        assert spec.grid_columns == 12
        assert spec.max_width == "1280px"

    def test_grid_columns_too_low(self) -> None:
        with pytest.raises(ValueError, match="grid_columns must be in"):
            LayoutSpec(grid_columns=0)

    def test_grid_columns_too_high(self) -> None:
        with pytest.raises(ValueError, match="grid_columns must be in"):
            LayoutSpec(grid_columns=25)


# -----------------------------------------------------------------------------
# MotionSpec
# -----------------------------------------------------------------------------


class TestMotionSpec:
    """动画规范测试。"""

    def test_defaults(self) -> None:
        spec = MotionSpec()
        assert spec.duration_base_ms == 200
        assert spec.prefers_reduced_motion is True

    def test_duration_negative(self) -> None:
        with pytest.raises(ValueError, match="duration_base_ms must be in"):
            MotionSpec(duration_base_ms=-1)

    def test_duration_too_high(self) -> None:
        with pytest.raises(ValueError, match="duration_base_ms must be in"):
            MotionSpec(duration_base_ms=5000)


# -----------------------------------------------------------------------------
# AntiPatternsSpec
# -----------------------------------------------------------------------------


class TestAntiPatternsSpec:
    """反模式规范测试。"""

    def test_default_banned_fonts(self) -> None:
        spec = AntiPatternsSpec()
        assert "Inter" in spec.banned_fonts
        assert "Roboto" in spec.banned_fonts

    def test_default_banned_colors(self) -> None:
        spec = AntiPatternsSpec()
        assert "#000000" in spec.banned_colors

    def test_custom_override(self) -> None:
        spec = AntiPatternsSpec(banned_fonts=frozenset({"Comic Sans"}))
        assert spec.banned_fonts == frozenset({"Comic Sans"})


# -----------------------------------------------------------------------------
# DesignSystemSpec
# -----------------------------------------------------------------------------


class TestDesignSystemSpec:
    """设计系统规范测试。"""

    def test_from_dials_minimalist(self) -> None:
        dials = DesignQualityDials.minimalist()
        spec = DesignSystemSpec.from_dials(dials)
        assert spec.atmosphere == {"variance": 3, "motion": 2, "density": 2}
        assert spec.typography.display_font == "Cabinet Grotesk"
        assert spec.components.border_radius_px == 12
        assert spec.layout.max_width == "1024px"
        assert spec.motion.duration_base_ms == 150
        assert spec.motion.stagger_delay_ms == 0

    def test_from_dials_modern_premium(self) -> None:
        dials = DesignQualityDials.modern_premium()
        spec = DesignSystemSpec.from_dials(dials)
        assert spec.atmosphere == {"variance": 6, "motion": 6, "density": 5}
        assert spec.typography.display_font == "Outfit"
        assert spec.typography.base_size_px == 15
        assert spec.components.shadow_depth == "md"
        assert spec.layout.gutter == "24px"
        assert spec.motion.duration_base_ms == 200
        assert spec.motion.stagger_delay_ms == 60

    def test_from_dials_dashboard(self) -> None:
        dials = DesignQualityDials.dashboard()
        spec = DesignSystemSpec.from_dials(dials)
        assert spec.atmosphere == {"variance": 4, "motion": 3, "density": 9}
        assert spec.typography.display_font == "Satoshi"
        assert spec.typography.base_size_px == 13
        assert spec.components.border_radius_px == 4
        assert spec.components.shadow_depth == "none"
        assert spec.layout.grid_columns == 12
        assert spec.layout.max_width == "1280px"
        assert spec.motion.duration_base_ms == 150

    def test_from_dials_experimental(self) -> None:
        dials = DesignQualityDials.experimental()
        spec = DesignSystemSpec.from_dials(dials)
        assert spec.atmosphere == {"variance": 9, "motion": 8, "density": 4}
        assert spec.layout.grid_columns == 16
        assert spec.layout.gutter == "16px"
        assert spec.motion.duration_base_ms == 250
        assert spec.motion.stagger_delay_ms == 80

    def test_missing_atmosphere_key(self) -> None:
        with pytest.raises(ValueError, match="missing required keys"):
            DesignSystemSpec(
                atmosphere={"variance": 5, "motion": 5},
                colors=(),
                typography=TypographySpec(display_font="A", body_font="B", mono_font="C"),
                components=ComponentSpec(),
                layout=LayoutSpec(),
                motion=MotionSpec(),
                anti_patterns=AntiPatternsSpec(),
            )

    def test_atmosphere_out_of_range(self) -> None:
        with pytest.raises(ValueError, match=r"atmosphere\.density must be in"):
            DesignSystemSpec(
                atmosphere={"variance": 5, "motion": 5, "density": 11},
                colors=(),
                typography=TypographySpec(display_font="A", body_font="B", mono_font="C"),
                components=ComponentSpec(),
                layout=LayoutSpec(),
                motion=MotionSpec(),
                anti_patterns=AntiPatternsSpec(),
            )

    def test_to_dict_roundtrip(self) -> None:
        dials = DesignQualityDials.modern_premium()
        spec = DesignSystemSpec.from_dials(dials)
        d = spec.to_dict()
        assert d["atmosphere"] == {"variance": 6, "motion": 6, "density": 5}
        assert "colors" in d
        assert "typography" in d
        assert "components" in d
        assert "layout" in d
        assert "motion" in d
        assert "anti_patterns" in d
        assert "primary" in {c["role"] for c in d["colors"]}

    def test_colors_derive_gallery(self) -> None:
        dials = DesignQualityDials(variance=5, motion=5, density=2)
        spec = DesignSystemSpec.from_dials(dials)
        roles = {c.role for c in spec.colors}
        assert ColorRole.BACKGROUND in roles
        assert ColorRole.PRIMARY in roles
        assert ColorRole.ACCENT in roles
        # Gallery has no secondary
        assert ColorRole.SECONDARY not in roles

    def test_colors_derive_cockpit(self) -> None:
        dials = DesignQualityDials(variance=5, motion=5, density=9)
        spec = DesignSystemSpec.from_dials(dials)
        roles = {c.role for c in spec.colors}
        assert ColorRole.SECONDARY in roles
        bg = next(c for c in spec.colors if c.role is ColorRole.BACKGROUND)
        assert bg.hex_value == "#0F172A"

    def test_frozen(self) -> None:
        dials = DesignQualityDials.minimalist()
        spec = DesignSystemSpec.from_dials(dials)
        with pytest.raises(AttributeError):
            spec.atmosphere = {"variance": 1, "motion": 1, "density": 1}  # type: ignore[misc]


# -----------------------------------------------------------------------------
# DesignSystemExporter
# -----------------------------------------------------------------------------


class TestDesignSystemExporter:
    """设计系统导出器测试。"""

    def test_render_contains_all_sections(self) -> None:
        dials = DesignQualityDials.modern_premium()
        spec = DesignSystemSpec.from_dials(dials)
        exporter = DesignSystemExporter()
        md = exporter.render(spec)
        assert "# 1. Atmosphere" in md
        assert "# 2. Colors" in md
        assert "# 3. Typography" in md
        assert "# 4. Components" in md
        assert "# 5. Layout" in md
        assert "# 6. Motion" in md
        assert "# 7. Anti-patterns" in md

    def test_render_atmosphere_content(self) -> None:
        dials = DesignQualityDials(variance=5, motion=5, density=5)
        spec = DesignSystemSpec.from_dials(dials)
        exporter = DesignSystemExporter()
        md = exporter.render(spec)
        assert "Variance" in md
        assert "Motion" in md
        assert "Density" in md
        assert "Offset / Asymmetric" in md
        assert "Gentle Spring" in md
        assert "Daily App" in md

    def test_render_colors_content(self) -> None:
        dials = DesignQualityDials.modern_premium()
        spec = DesignSystemSpec.from_dials(dials)
        exporter = DesignSystemExporter()
        md = exporter.render(spec)
        assert "#3B82F6" in md
        assert "primary" in md

    def test_render_typography_content(self) -> None:
        dials = DesignQualityDials.modern_premium()
        spec = DesignSystemSpec.from_dials(dials)
        exporter = DesignSystemExporter()
        md = exporter.render(spec)
        assert "Outfit" in md
        assert "Geist" in md
        assert "JetBrains Mono" in md

    def test_render_components_content(self) -> None:
        dials = DesignQualityDials.modern_premium()
        spec = DesignSystemSpec.from_dials(dials)
        exporter = DesignSystemExporter()
        md = exporter.render(spec)
        assert "Border radius" in md
        assert "Shadow depth" in md

    def test_render_layout_content(self) -> None:
        dials = DesignQualityDials.modern_premium()
        spec = DesignSystemSpec.from_dials(dials)
        exporter = DesignSystemExporter()
        md = exporter.render(spec)
        assert "Grid columns" in md
        assert "Gutter" in md
        assert "Breakpoints" in md

    def test_render_motion_content(self) -> None:
        dials = DesignQualityDials.modern_premium()
        spec = DesignSystemSpec.from_dials(dials)
        exporter = DesignSystemExporter()
        md = exporter.render(spec)
        assert "Base duration" in md
        assert "Long duration" in md
        assert "Stagger delay" in md
        assert "Prefers reduced motion" in md

    def test_render_anti_patterns_content(self) -> None:
        dials = DesignQualityDials.modern_premium()
        spec = DesignSystemSpec.from_dials(dials)
        exporter = DesignSystemExporter()
        md = exporter.render(spec)
        assert "Banned Fonts" in md
        assert "Inter" in md
        assert "Banned Colors" in md
        assert "#000000" in md

    def test_render_experimental_atmosphere_labels(self) -> None:
        dials = DesignQualityDials.experimental()
        spec = DesignSystemSpec.from_dials(dials)
        exporter = DesignSystemExporter()
        md = exporter.render(spec)
        assert "Masonry / Fractional" in md
        assert "Premium Spring" in md
        assert "Daily App" in md

    def test_render_cockpit_atmosphere_labels(self) -> None:
        dials = DesignQualityDials.dashboard()
        spec = DesignSystemSpec.from_dials(dials)
        exporter = DesignSystemExporter()
        md = exporter.render(spec)
        assert "Cockpit (Information Dense)" in md
        assert "Hover Only" in md
