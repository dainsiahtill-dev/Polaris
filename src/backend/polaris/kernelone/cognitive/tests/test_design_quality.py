"""Tests for DesignQualityDials — Three-Axis Quality Parameter System."""

from __future__ import annotations

import pytest
from polaris.kernelone.cognitive.design_quality import (
    DesignQualityDials,
    LayoutMode,
    MotionPresetKey,
    SpacingTier,
)


class TestDesignQualityDials:
    """三轴质量参数系统测试。"""

    def test_default_construction(self) -> None:
        dials = DesignQualityDials()
        assert dials.variance == 5
        assert dials.motion == 5
        assert dials.density == 5

    def test_custom_values(self) -> None:
        dials = DesignQualityDials(variance=3, motion=7, density=9)
        assert dials.variance == 3
        assert dials.motion == 7
        assert dials.density == 9

    def test_variance_too_low(self) -> None:
        with pytest.raises(ValueError, match="variance must be in \\[1, 10\\]"):
            DesignQualityDials(variance=0)

    def test_variance_too_high(self) -> None:
        with pytest.raises(ValueError, match="variance must be in \\[1, 10\\]"):
            DesignQualityDials(variance=11)

    def test_motion_too_low(self) -> None:
        with pytest.raises(ValueError, match="motion must be in \\[1, 10\\]"):
            DesignQualityDials(motion=-1)

    def test_density_too_high(self) -> None:
        with pytest.raises(ValueError, match="density must be in \\[1, 10\\]"):
            DesignQualityDials(density=100)

    def test_layout_mode_symmetric(self) -> None:
        dials = DesignQualityDials(variance=1, motion=5, density=5)
        assert dials.layout_mode is LayoutMode.SYMMETRIC_CENTERED

    def test_layout_mode_offset(self) -> None:
        dials = DesignQualityDials(variance=5, motion=5, density=5)
        assert dials.layout_mode is LayoutMode.OFFSET_ASYMMETRIC

    def test_layout_mode_masonry(self) -> None:
        dials = DesignQualityDials(variance=10, motion=5, density=5)
        assert dials.layout_mode is LayoutMode.MASONRY_FRACTIONAL

    def test_layout_mode_boundary_3(self) -> None:
        dials = DesignQualityDials(variance=3, motion=5, density=5)
        assert dials.layout_mode is LayoutMode.SYMMETRIC_CENTERED

    def test_layout_mode_boundary_4(self) -> None:
        dials = DesignQualityDials(variance=4, motion=5, density=5)
        assert dials.layout_mode is LayoutMode.OFFSET_ASYMMETRIC

    def test_layout_mode_boundary_7(self) -> None:
        dials = DesignQualityDials(variance=7, motion=5, density=5)
        assert dials.layout_mode is LayoutMode.OFFSET_ASYMMETRIC

    def test_layout_mode_boundary_8(self) -> None:
        dials = DesignQualityDials(variance=8, motion=5, density=5)
        assert dials.layout_mode is LayoutMode.MASONRY_FRACTIONAL

    def test_spacing_tier_gallery(self) -> None:
        dials = DesignQualityDials(variance=5, motion=5, density=2)
        assert dials.spacing_tier is SpacingTier.GALLERY

    def test_spacing_tier_daily_app(self) -> None:
        dials = DesignQualityDials(variance=5, motion=5, density=5)
        assert dials.spacing_tier is SpacingTier.DAILY_APP

    def test_spacing_tier_cockpit(self) -> None:
        dials = DesignQualityDials(variance=5, motion=5, density=9)
        assert dials.spacing_tier is SpacingTier.COCKPIT

    def test_motion_preset_hover_only(self) -> None:
        dials = DesignQualityDials(variance=5, motion=2, density=5)
        assert dials.motion_preset_key is MotionPresetKey.HOVER_ONLY

    def test_motion_preset_spring_gentle(self) -> None:
        dials = DesignQualityDials(variance=5, motion=5, density=5)
        assert dials.motion_preset_key is MotionPresetKey.SPRING_GENTLE

    def test_motion_preset_spring_premium(self) -> None:
        dials = DesignQualityDials(variance=5, motion=9, density=5)
        assert dials.motion_preset_key is MotionPresetKey.SPRING_PREMIUM

    def test_frozen_immutable(self) -> None:
        dials = DesignQualityDials(variance=5, motion=5, density=5)
        with pytest.raises(AttributeError):
            dials.variance = 10  # type: ignore[misc]

    def test_to_dict(self) -> None:
        dials = DesignQualityDials(variance=3, motion=7, density=9)
        d = dials.to_dict()
        assert d == {"variance": 3, "motion": 7, "density": 9}

    def test_from_dict(self) -> None:
        dials = DesignQualityDials.from_dict({"variance": 2, "motion": 8, "density": 4})
        assert dials.variance == 2
        assert dials.motion == 8
        assert dials.density == 4

    def test_from_dict_defaults(self) -> None:
        dials = DesignQualityDials.from_dict({})
        assert dials.variance == 5
        assert dials.motion == 5
        assert dials.density == 5

    def test_preset_minimalist(self) -> None:
        dials = DesignQualityDials.minimalist()
        assert dials.variance == 3
        assert dials.motion == 2
        assert dials.density == 2
        assert dials.layout_mode is LayoutMode.SYMMETRIC_CENTERED
        assert dials.spacing_tier is SpacingTier.GALLERY
        assert dials.motion_preset_key is MotionPresetKey.HOVER_ONLY

    def test_preset_modern_premium(self) -> None:
        dials = DesignQualityDials.modern_premium()
        assert dials.variance == 6
        assert dials.motion == 6
        assert dials.density == 5
        assert dials.layout_mode is LayoutMode.OFFSET_ASYMMETRIC
        assert dials.spacing_tier is SpacingTier.DAILY_APP
        assert dials.motion_preset_key is MotionPresetKey.SPRING_GENTLE

    def test_preset_dashboard(self) -> None:
        dials = DesignQualityDials.dashboard()
        assert dials.variance == 4
        assert dials.motion == 3
        assert dials.density == 9
        assert dials.spacing_tier is SpacingTier.COCKPIT
        assert dials.motion_preset_key is MotionPresetKey.HOVER_ONLY

    def test_preset_experimental(self) -> None:
        dials = DesignQualityDials.experimental()
        assert dials.variance == 9
        assert dials.motion == 8
        assert dials.density == 4
        assert dials.layout_mode is LayoutMode.MASONRY_FRACTIONAL
        assert dials.motion_preset_key is MotionPresetKey.SPRING_PREMIUM

    def test_preset_soft(self) -> None:
        dials = DesignQualityDials.soft()
        assert dials.variance == 5
        assert dials.motion == 4
        assert dials.density == 3
        assert dials.motion_preset_key is MotionPresetKey.SPRING_GENTLE

    def test_preset_brutalist(self) -> None:
        dials = DesignQualityDials.brutalist()
        assert dials.variance == 8
        assert dials.motion == 2
        assert dials.density == 7
        assert dials.layout_mode is LayoutMode.MASONRY_FRACTIONAL
        assert dials.motion_preset_key is MotionPresetKey.HOVER_ONLY

    def test_eq_same_values(self) -> None:
        a = DesignQualityDials(variance=5, motion=5, density=5)
        b = DesignQualityDials(variance=5, motion=5, density=5)
        assert a == b

    def test_eq_different_values(self) -> None:
        a = DesignQualityDials(variance=5, motion=5, density=5)
        b = DesignQualityDials(variance=6, motion=5, density=5)
        assert a != b

    def test_hashable(self) -> None:
        dials = DesignQualityDials(variance=5, motion=5, density=5)
        s = {dials}
        assert len(s) == 1

    def test_project_default_roundtrip(self) -> None:
        original = DesignQualityDials.load_project_default()
        assert original.variance == 5

        custom = DesignQualityDials(variance=2, motion=2, density=2)
        DesignQualityDials.set_project_default(custom)
        loaded = DesignQualityDials.load_project_default()
        assert loaded == custom

        DesignQualityDials.reset_project_default()
        reset = DesignQualityDials.load_project_default()
        assert reset == original


class TestLayoutMode:
    """布局模式枚举测试。"""

    def test_members(self) -> None:
        assert LayoutMode.SYMMETRIC_CENTERED.value == "symmetric_centered"
        assert LayoutMode.OFFSET_ASYMMETRIC.value == "offset_asymmetric"
        assert LayoutMode.MASONRY_FRACTIONAL.value == "masonry_fractional"


class TestSpacingTier:
    """间距层级枚举测试。"""

    def test_members(self) -> None:
        assert SpacingTier.GALLERY.value == "gallery"
        assert SpacingTier.DAILY_APP.value == "daily_app"
        assert SpacingTier.COCKPIT.value == "cockpit"


class TestMotionPresetKey:
    """动画预设键枚举测试。"""

    def test_members(self) -> None:
        assert MotionPresetKey.HOVER_ONLY.value == "hover_only"
        assert MotionPresetKey.SPRING_GENTLE.value == "spring_gentle"
        assert MotionPresetKey.SPRING_PREMIUM.value == "spring_premium"
