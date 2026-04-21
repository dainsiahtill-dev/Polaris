"""Color Palette Generator — P1-B: 颜色校准器。

由 density 轴和可选基色驱动的调色板生成。
基于 HSL 颜色空间进行校准，确保对比度、饱和度符合 taste-skill 规范。

核心能力:
1. 从基色生成完整调色板（主色、辅色、强调色、中性色）
2. 自动校准饱和度（防止 oversaturated / neon 颜色）
3. 确保背景/文字对比度符合 WCAG 启发式
4. 与 DesignQualityDials 无缝衔接
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from polaris.kernelone.cognitive.design_quality import DesignQualityDials, SpacingTier
from polaris.kernelone.cognitive.validators._base import _hex_to_hsl

# ---------------------------------------------------------------------------
# 调色板类型
# ---------------------------------------------------------------------------


class PaletteTone(str, Enum):
    """调色板色调角色。"""

    PRIMARY = "primary"
    SECONDARY = "secondary"
    ACCENT = "accent"
    BACKGROUND = "background"
    SURFACE = "surface"
    TEXT = "text"
    MUTED = "muted"
    BORDER = "border"


@dataclass(frozen=True)
class PaletteColor:
    """调色板中的单个颜色 — 名称 + hex + HSL + 角色。"""

    name: str
    hex_value: str
    tone: PaletteTone
    hue: int
    saturation: int
    lightness: int

    def is_grayscale(self) -> bool:
        """是否为灰度色（饱和度 < 5%）。"""
        return self.saturation < 5

    def is_dark(self) -> bool:
        """是否为深色（亮度 < 50%）。"""
        return self.lightness < 50

    def contrast_ratio_with(self, other: PaletteColor) -> float:
        """计算与另一颜色的近似对比度比率（启发式）。

        使用相对亮度近似公式（非精确 WCAG，但足够用于设计校验）。
        """
        l1 = self.lightness / 100
        l2 = other.lightness / 100
        # 简化的对比度公式
        lighter = max(l1, l2)
        darker = min(l1, l2)
        return (lighter + 0.05) / (darker + 0.05)


@dataclass(frozen=True)
class Palette:
    """完整调色板 — 一组 PaletteColor。"""

    colors: tuple[PaletteColor, ...]
    base_hex: str | None = None  # 生成此调色板的基色（如果有）

    def get_by_tone(self, tone: PaletteTone) -> PaletteColor | None:
        """按角色获取颜色。"""
        for c in self.colors:
            if c.tone is tone:
                return c
        return None

    def get_by_name(self, name: str) -> PaletteColor | None:
        """按名称获取颜色。"""
        for c in self.colors:
            if c.name == name:
                return c
        return None

    def accents(self) -> list[PaletteColor]:
        """获取所有非灰度强调色。"""
        return [
            c
            for c in self.colors
            if c.tone in {PaletteTone.PRIMARY, PaletteTone.SECONDARY, PaletteTone.ACCENT} and not c.is_grayscale()
        ]

    def to_dict(self) -> dict[str, Any]:
        """序列化为 dict。"""
        return {
            "base_hex": self.base_hex,
            "colors": [
                {
                    "name": c.name,
                    "hex_value": c.hex_value,
                    "tone": c.tone.value,
                    "hsl": [c.hue, c.saturation, c.lightness],
                }
                for c in self.colors
            ],
        }


# ---------------------------------------------------------------------------
# 预设调色板
# ---------------------------------------------------------------------------

# Gallery (低密度 / 极简) 调色板 — 高对比，克制
_GALLERY_PALETTE = Palette(
    colors=(
        PaletteColor("background", "#FAFAFA", PaletteTone.BACKGROUND, 0, 0, 98),
        PaletteColor("surface", "#FFFFFF", PaletteTone.SURFACE, 0, 0, 100),
        PaletteColor("text", "#18181B", PaletteTone.TEXT, 240, 5, 10),
        PaletteColor("muted", "#71717A", PaletteTone.MUTED, 240, 4, 46),
        PaletteColor("primary", "#18181B", PaletteTone.PRIMARY, 240, 5, 10),
        PaletteColor("accent", "#DC2626", PaletteTone.ACCENT, 0, 80, 50),
        PaletteColor("border", "#E4E4E7", PaletteTone.BORDER, 240, 4, 90),
    ),
)

# Daily App (中等密度) 调色板 — 双色调
_DAILY_APP_PALETTE = Palette(
    colors=(
        PaletteColor("background", "#F8FAFC", PaletteTone.BACKGROUND, 210, 20, 98),
        PaletteColor("surface", "#FFFFFF", PaletteTone.SURFACE, 0, 0, 100),
        PaletteColor("text", "#0F172A", PaletteTone.TEXT, 222, 47, 11),
        PaletteColor("muted", "#64748B", PaletteTone.MUTED, 215, 16, 47),
        PaletteColor("primary", "#3B82F6", PaletteTone.PRIMARY, 217, 91, 60),
        PaletteColor("secondary", "#8B5CF6", PaletteTone.SECONDARY, 258, 90, 66),
        PaletteColor("accent", "#F59E0B", PaletteTone.ACCENT, 38, 92, 50),
        PaletteColor("border", "#E2E8F0", PaletteTone.BORDER, 214, 32, 91),
    ),
)

# Cockpit (高密度) 调色板 — 深色模式，功能性
_COCKPIT_PALETTE = Palette(
    colors=(
        PaletteColor("background", "#0F172A", PaletteTone.BACKGROUND, 222, 47, 11),
        PaletteColor("surface", "#1E293B", PaletteTone.SURFACE, 217, 33, 17),
        PaletteColor("text", "#F1F5F9", PaletteTone.TEXT, 210, 40, 96),
        PaletteColor("muted", "#94A3B8", PaletteTone.MUTED, 215, 16, 62),
        PaletteColor("primary", "#38BDF8", PaletteTone.PRIMARY, 199, 95, 60),
        PaletteColor("secondary", "#818CF8", PaletteTone.SECONDARY, 234, 89, 74),
        PaletteColor("accent", "#34D399", PaletteTone.ACCENT, 158, 64, 52),
        PaletteColor("border", "#334155", PaletteTone.BORDER, 217, 19, 27),
    ),
)


# ---------------------------------------------------------------------------
# 调色板生成器
# ---------------------------------------------------------------------------


class ColorPaletteGenerator:
    """颜色校准器 — 基于基色 + dials 生成符合 taste-skill 规范的调色板。

    使用方式:
        >>> from polaris.kernelone.cognitive.design_quality import DesignQualityDials
        >>> dials = DesignQualityDials.modern_premium()
        >>> gen = ColorPaletteGenerator(dials)
        >>> palette = gen.generate()
        >>> palette.get_by_tone(PaletteTone.PRIMARY)
        PaletteColor(name='primary', hex_value='#3B82F6', ...)

        # 从自定义基色生成
        >>> palette = gen.generate_from_base("#FF6B6B")
    """

    def __init__(self, dials: DesignQualityDials) -> None:
        """初始化调色板生成器。

        Args:
            dials: 三轴质量参数，density 轴驱动调色板选择
        """
        self._dials = dials
        self._spacing_tier = dials.spacing_tier

    def generate(self) -> Palette:
        """根据当前 density 生成预设调色板。"""
        if self._spacing_tier is SpacingTier.GALLERY:
            return _GALLERY_PALETTE
        if self._spacing_tier is SpacingTier.DAILY_APP:
            return _DAILY_APP_PALETTE
        return _COCKPIT_PALETTE

    def generate_from_base(self, base_hex: str) -> Palette:
        """从自定义基色生成完整调色板。

        基于基色的 HSL，生成主色、辅色、强调色、中性色的完整系统。
        自动校准饱和度（防止 oversaturated）。

        Args:
            base_hex: 基色 hex 值（如 "#3B82F6"）

        Returns:
            完整调色板
        """
        hsl = _hex_to_hsl(base_hex)
        if hsl is None:
            raise ValueError(f"Invalid base color: {base_hex}")

        hue, sat, light = hsl

        # 自动校准饱和度
        calibrated_sat = min(sat, 75)  # 限制饱和度上限

        # 根据 density 调整亮度
        if self._spacing_tier is SpacingTier.GALLERY:
            bg_lightness = 98
            text_lightness = 10
        elif self._spacing_tier is SpacingTier.DAILY_APP:
            bg_lightness = 98
            text_lightness = 11
        else:
            bg_lightness = 11
            text_lightness = 96

        # 生成调色板
        colors: list[PaletteColor] = []

        # 主色（基色本身，饱和度校准）
        primary_hex = self._hsl_to_hex(hue, calibrated_sat, light)
        colors.append(PaletteColor("primary", primary_hex, PaletteTone.PRIMARY, hue, calibrated_sat, light))

        # 辅色（hue + 30°，同饱和度）
        secondary_hue = (hue + 30) % 360
        secondary_hex = self._hsl_to_hex(secondary_hue, calibrated_sat, light)
        colors.append(
            PaletteColor("secondary", secondary_hex, PaletteTone.SECONDARY, secondary_hue, calibrated_sat, light)
        )

        # 强调色（hue + 180° 互补色，更高饱和度）
        accent_hue = (hue + 180) % 360
        accent_sat = min(calibrated_sat + 10, 80)
        accent_light = max(light, 50) if bg_lightness > 50 else min(light, 55)
        accent_hex = self._hsl_to_hex(accent_hue, accent_sat, accent_light)
        colors.append(PaletteColor("accent", accent_hex, PaletteTone.ACCENT, accent_hue, accent_sat, accent_light))

        # 背景色
        bg_hex = self._hsl_to_hex(hue, 5, bg_lightness)
        colors.append(PaletteColor("background", bg_hex, PaletteTone.BACKGROUND, hue, 5, bg_lightness))

        # 表面色（比背景稍浅/深）
        surface_light = bg_lightness + 2 if bg_lightness > 50 else bg_lightness + 6
        surface_hex = self._hsl_to_hex(hue, 5, min(surface_light, 100))
        colors.append(PaletteColor("surface", surface_hex, PaletteTone.SURFACE, hue, 5, min(surface_light, 100)))

        # 文字色
        text_hex = self._hsl_to_hex(hue, 5, text_lightness)
        colors.append(PaletteColor("text", text_hex, PaletteTone.TEXT, hue, 5, text_lightness))

        # 静音色（中间亮度）
        muted_light = (bg_lightness + text_lightness) // 2
        muted_hex = self._hsl_to_hex(hue, 4, muted_light)
        colors.append(PaletteColor("muted", muted_hex, PaletteTone.MUTED, hue, 4, muted_light))

        # 边框色（接近背景色）
        border_light = bg_lightness + 2 if bg_lightness > 50 else bg_lightness + 10
        border_hex = self._hsl_to_hex(hue, 4, min(border_light, 95))
        colors.append(PaletteColor("border", border_hex, PaletteTone.BORDER, hue, 4, min(border_light, 95)))

        return Palette(colors=tuple(colors), base_hex=base_hex)

    @staticmethod
    def _hsl_to_hex(hue: int, saturation: int, lightness: int) -> str:
        """将 HSL 转换为 hex 颜色字符串。

        Args:
            hue: 0-359
            saturation: 0-100
            lightness: 0-100

        Returns:
            #RRGGBB 格式字符串
        """
        h = hue / 360.0
        s = saturation / 100.0
        lum = lightness / 100.0

        def _hue_to_rgb(p: float, q: float, t: float) -> float:
            if t < 0:
                t += 1
            if t > 1:
                t -= 1
            if t < 1 / 6:
                return p + (q - p) * 6 * t
            if t < 1 / 2:
                return q
            if t < 2 / 3:
                return p + (q - p) * (2 / 3 - t) * 6
            return p

        if s == 0:
            r = g = b = lum
        else:
            q = lum * (1 + s) if lum < 0.5 else lum + s - lum * s
            p = 2 * lum - q
            r = _hue_to_rgb(p, q, h + 1 / 3)
            g = _hue_to_rgb(p, q, h)
            b = _hue_to_rgb(p, q, h - 1 / 3)

        return f"#{round(r * 255):02X}{round(g * 255):02X}{round(b * 255):02X}"

    def validate_contrast(self, palette: Palette) -> list[str]:
        """校验调色板对比度。

        Returns:
            警告消息列表。空列表表示对比度全部合格。
        """
        warnings: list[str] = []
        bg = palette.get_by_tone(PaletteTone.BACKGROUND)
        text = palette.get_by_tone(PaletteTone.TEXT)

        if bg and text:
            ratio = bg.contrast_ratio_with(text)
            if ratio < 3.0:
                warnings.append(
                    f"Low contrast between background ({bg.hex_value}) and text ({text.hex_value}): "
                    f"ratio ~{ratio:.1f}:1"
                )

        primary = palette.get_by_tone(PaletteTone.PRIMARY)
        if primary and bg:
            ratio = primary.contrast_ratio_with(bg)
            if ratio < 1.5:
                warnings.append(
                    f"Low contrast between primary ({primary.hex_value}) and background ({bg.hex_value}): "
                    f"ratio ~{ratio:.1f}:1"
                )

        return warnings
