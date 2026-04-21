"""Typography Token System — P1-A: 字体层级令牌。

密度感知（density-aware）的字体令牌生成。
由 density 轴驱动，生成不同密度等级下的字体规范。

| Role  | Density 1-3 (Gallery) | Density 4-7 (Daily) | Density 8-10 (Cockpit) |
|-------|----------------------|---------------------|------------------------|
| Display | text-4xl, tracking-tight | text-5xl | text-6xl, leading-none |
| Body | text-base, max-w-[65ch] | text-base | text-sm, font-mono numbers |
| Mono | font-mono | font-mono | font-mono |
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from polaris.kernelone.cognitive.design_quality import DesignQualityDials, SpacingTier

# ---------------------------------------------------------------------------
# 令牌类型
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TypographyToken:
    """单个字体令牌 — 命名大小、行高、字重、字间距。"""

    name: str  # e.g., "display", "heading-1", "body", "caption"
    size_px: int
    line_height: float
    weight: int
    letter_spacing: float  # em
    font_family: str  # display | body | mono
    max_width_ch: int | None = None  # 正文最大行长


@dataclass(frozen=True)
class TypographyScale:
    """完整字体层级 — 一组密度感知的 TypographyToken。"""

    display: TypographyToken
    heading_1: TypographyToken
    heading_2: TypographyToken
    heading_3: TypographyToken
    body: TypographyToken
    body_small: TypographyToken
    caption: TypographyToken
    mono: TypographyToken

    def to_dict(self) -> dict[str, dict[str, Any]]:
        """序列化为 dict。"""
        result: dict[str, dict[str, Any]] = {}
        for key, token in self.__dict__.items():
            result[key] = {
                "name": token.name,
                "size_px": token.size_px,
                "line_height": token.line_height,
                "weight": token.weight,
                "letter_spacing": token.letter_spacing,
                "font_family": token.font_family,
                "max_width_ch": token.max_width_ch,
            }
        return result


# ---------------------------------------------------------------------------
# 密度映射表
# ---------------------------------------------------------------------------

# 各密度等级下的字体参数
_GALLERY_TOKENS = TypographyScale(
    display=TypographyToken(
        name="display",
        size_px=64,
        line_height=1.0,
        weight=700,
        letter_spacing=-0.02,
        font_family="display",
    ),
    heading_1=TypographyToken(
        name="heading-1",
        size_px=48,
        line_height=1.1,
        weight=600,
        letter_spacing=-0.015,
        font_family="display",
    ),
    heading_2=TypographyToken(
        name="heading-2",
        size_px=36,
        line_height=1.2,
        weight=600,
        letter_spacing=-0.01,
        font_family="display",
    ),
    heading_3=TypographyToken(
        name="heading-3",
        size_px=28,
        line_height=1.3,
        weight=500,
        letter_spacing=-0.005,
        font_family="body",
    ),
    body=TypographyToken(
        name="body",
        size_px=18,
        line_height=1.65,
        weight=400,
        letter_spacing=0.0,
        font_family="body",
        max_width_ch=65,
    ),
    body_small=TypographyToken(
        name="body-small",
        size_px=16,
        line_height=1.5,
        weight=400,
        letter_spacing=0.0,
        font_family="body",
        max_width_ch=65,
    ),
    caption=TypographyToken(
        name="caption",
        size_px=14,
        line_height=1.4,
        weight=400,
        letter_spacing=0.01,
        font_family="body",
    ),
    mono=TypographyToken(
        name="mono",
        size_px=14,
        line_height=1.5,
        weight=400,
        letter_spacing=0.0,
        font_family="mono",
    ),
)

_DAILY_APP_TOKENS = TypographyScale(
    display=TypographyToken(
        name="display",
        size_px=80,
        line_height=1.0,
        weight=700,
        letter_spacing=-0.02,
        font_family="display",
    ),
    heading_1=TypographyToken(
        name="heading-1",
        size_px=56,
        line_height=1.1,
        weight=600,
        letter_spacing=-0.015,
        font_family="display",
    ),
    heading_2=TypographyToken(
        name="heading-2",
        size_px=40,
        line_height=1.2,
        weight=600,
        letter_spacing=-0.01,
        font_family="display",
    ),
    heading_3=TypographyToken(
        name="heading-3",
        size_px=30,
        line_height=1.3,
        weight=500,
        letter_spacing=-0.005,
        font_family="body",
    ),
    body=TypographyToken(
        name="body",
        size_px=16,
        line_height=1.6,
        weight=400,
        letter_spacing=0.0,
        font_family="body",
        max_width_ch=70,
    ),
    body_small=TypographyToken(
        name="body-small",
        size_px=14,
        line_height=1.5,
        weight=400,
        letter_spacing=0.0,
        font_family="body",
        max_width_ch=70,
    ),
    caption=TypographyToken(
        name="caption",
        size_px=13,
        line_height=1.4,
        weight=400,
        letter_spacing=0.01,
        font_family="body",
    ),
    mono=TypographyToken(
        name="mono",
        size_px=13,
        line_height=1.5,
        weight=400,
        letter_spacing=0.0,
        font_family="mono",
    ),
)

_COCKPIT_TOKENS = TypographyScale(
    display=TypographyToken(
        name="display",
        size_px=96,
        line_height=0.95,
        weight=700,
        letter_spacing=-0.03,
        font_family="display",
    ),
    heading_1=TypographyToken(
        name="heading-1",
        size_px=64,
        line_height=1.0,
        weight=600,
        letter_spacing=-0.02,
        font_family="display",
    ),
    heading_2=TypographyToken(
        name="heading-2",
        size_px=44,
        line_height=1.1,
        weight=600,
        letter_spacing=-0.015,
        font_family="display",
    ),
    heading_3=TypographyToken(
        name="heading-3",
        size_px=32,
        line_height=1.2,
        weight=500,
        letter_spacing=-0.01,
        font_family="body",
    ),
    body=TypographyToken(
        name="body",
        size_px=13,
        line_height=1.5,
        weight=400,
        letter_spacing=0.0,
        font_family="body",
        max_width_ch=80,
    ),
    body_small=TypographyToken(
        name="body-small",
        size_px=12,
        line_height=1.4,
        weight=400,
        letter_spacing=0.0,
        font_family="body",
        max_width_ch=80,
    ),
    caption=TypographyToken(
        name="caption",
        size_px=11,
        line_height=1.3,
        weight=400,
        letter_spacing=0.01,
        font_family="body",
    ),
    mono=TypographyToken(
        name="mono",
        size_px=12,
        line_height=1.5,
        weight=400,
        letter_spacing=0.0,
        font_family="mono",
    ),
)


# ---------------------------------------------------------------------------
# 令牌系统
# ---------------------------------------------------------------------------


class TypographyTokenSystem:
    """字体层级令牌系统 — 密度感知的字体规范生成器。

    使用方式:
        >>> from polaris.kernelone.cognitive.design_quality import DesignQualityDials
        >>> dials = DesignQualityDials.minimalist()  # density=2
        >>> system = TypographyTokenSystem(dials)
        >>> scale = system.generate_scale()
        >>> scale.display.size_px
        64
    """

    def __init__(self, dials: DesignQualityDials) -> None:
        """初始化字体令牌系统。

        Args:
            dials: 三轴质量参数，density 轴驱动字体大小决策
        """
        self._dials = dials
        self._spacing_tier = dials.spacing_tier

    def generate_scale(self) -> TypographyScale:
        """根据当前 density 生成字体层级。"""
        if self._spacing_tier is SpacingTier.GALLERY:
            return _GALLERY_TOKENS
        if self._spacing_tier is SpacingTier.DAILY_APP:
            return _DAILY_APP_TOKENS
        return _COCKPIT_TOKENS

    def get_tailwind_class(self, token: TypographyToken) -> str:
        """将 TypographyToken 映射到近似 Tailwind 类名。

        这是一个启发式映射，用于提示词生成或 validator 检查。
        """
        size_map = {
            11: "text-xs",
            12: "text-xs",
            13: "text-sm",
            14: "text-sm",
            16: "text-base",
            18: "text-lg",
            28: "text-2xl",
            30: "text-3xl",
            32: "text-3xl",
            36: "text-4xl",
            40: "text-4xl",
            44: "text-5xl",
            48: "text-5xl",
            56: "text-6xl",
            64: "text-7xl",
            80: "text-8xl",
            96: "text-9xl",
        }
        # 找到最接近的大小
        closest_size = min(size_map.keys(), key=lambda s: abs(s - token.size_px))
        return size_map.get(closest_size, "text-base")

    def get_css_rule(self, token: TypographyToken) -> dict[str, str]:
        """生成 CSS 规则 dict。"""
        return {
            "font-size": f"{token.size_px}px",
            "line-height": str(token.line_height),
            "font-weight": str(token.weight),
            "letter-spacing": f"{token.letter_spacing}em",
        }
