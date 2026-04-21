"""Design Tokens — 字体、颜色、动画的密度感知令牌系统。

三轴质量参数 (variance / motion / density) 驱动所有令牌决策。
各子系统通过 DesignQualityDials 消费对应轴，生成具体的设计令牌值。
"""

from polaris.kernelone.cognitive.design_tokens.motion import (
    MotionPresetKey,
    MotionPresetLibrary,
    MotionToken,
)
from polaris.kernelone.cognitive.design_tokens.palette import (
    ColorPaletteGenerator,
    Palette,
    PaletteTone,
)
from polaris.kernelone.cognitive.design_tokens.typography import (
    TypographyScale,
    TypographyToken,
    TypographyTokenSystem,
)

__all__ = [
    "ColorPaletteGenerator",
    "MotionPresetKey",
    "MotionPresetLibrary",
    "MotionToken",
    "Palette",
    "PaletteTone",
    "TypographyScale",
    "TypographyToken",
    "TypographyTokenSystem",
]
