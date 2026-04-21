"""Color Validator — HSL 基础的颜色质量分析。

检测禁用颜色、过饱和强调色、霓虹紫/蓝签名色，以及过多强调色等问题。
对 CSS 块中的背景/文字对进行对比度启发式检查。
"""

from __future__ import annotations

import re
from typing import Any

from polaris.kernelone.cognitive.design_quality import DesignQualityDials
from polaris.kernelone.cognitive.validators._base import (
    _all_hex_colors,
    _extract_css_blocks,
    _hex_to_hsl,
    _make_violation,
)
from polaris.kernelone.cognitive.validators.dispatcher import (
    ValidationSeverity,
    ValidationViolation,
)

# ---------------------------------------------------------------------------
# 颜色策略常量
# ---------------------------------------------------------------------------

_BANNED_BLACK: str = "#000000"
_BANNED_BLACK_ALIASES: frozenset[str] = frozenset({"#000000", "#000"})

# HSL 阈值
_OVERSATURATED_THRESHOLD: int = 80
_NEON_HUE_MIN: int = 260
_NEON_HUE_MAX: int = 300
_NEON_SATURATION_THRESHOLD: int = 60
_GRAYSCALE_SATURATION_THRESHOLD: int = 5
_MAX_ACCENTS: int = 3
_MIN_CONTRAST_LIGHTNESS_DIFF: int = 40

# CSS 属性提取
_BACKGROUND_RE = re.compile(r"background(?:-color)?\s*:\s*([^;]+)", re.IGNORECASE)
_COLOR_RE = re.compile(r"(?<!background-)color\s*:\s*([^;]+)", re.IGNORECASE)


# ---------------------------------------------------------------------------
# ColorValidator
# ---------------------------------------------------------------------------


class ColorValidator:
    """颜色验证器 — 基于 HSL 的颜色质量分析与违规检测。"""

    def __init__(self, dials: DesignQualityDials | None = None) -> None:
        """初始化颜色验证器。

        Args:
            dials: 可选的设计质量参数，用于上下文感知验证。
        """
        self.dials = dials

    def validate(
        self,
        content: str,
        context: dict[str, Any] | None = None,
    ) -> list[ValidationViolation]:
        """验证内容中的颜色使用。

        Args:
            content: 待验证的原始内容（CSS/JSX/HTML 等）
            context: 可选上下文，可包含 design_dials、file_path 等

        Returns:
            ValidationViolation 列表。空列表表示无违规。
        """
        violations: list[ValidationViolation] = []

        violations.extend(self._check_banned_black(content))
        violations.extend(self._check_oversaturated_accents(content))
        violations.extend(self._check_neon_purple_blue(content))
        violations.extend(self._check_multiple_accents(content))
        violations.extend(self._check_low_contrast_pairs(content))

        return violations

    # -----------------------------------------------------------------------
    # 规则 1: 禁用纯黑
    # -----------------------------------------------------------------------

    def _check_banned_black(self, content: str) -> list[ValidationViolation]:
        """检测 #000000 禁用黑色。"""
        violations: list[ValidationViolation] = []
        for hex_value, ctx in _all_hex_colors(content):
            if hex_value.lower() in _BANNED_BLACK_ALIASES:
                violations.append(
                    _make_violation(
                        rule="banned_color_black",
                        severity=ValidationSeverity.ERROR,
                        message=f"Banned color '{hex_value}' (pure black) detected.",
                        location=ctx,
                        fix_hint="Replace with zinc-950 / #18181B for a softer, premium dark.",
                    ),
                )
        return violations

    # -----------------------------------------------------------------------
    # 规则 2: 过饱和强调色
    # -----------------------------------------------------------------------

    def _check_oversaturated_accents(self, content: str) -> list[ValidationViolation]:
        """检测饱和度超过阈值的强调色。"""
        violations: list[ValidationViolation] = []
        seen: set[str] = set()

        for hex_value, ctx in _all_hex_colors(content):
            hsl = _hex_to_hsl(hex_value)
            if hsl is None:
                continue
            hue, sat, light = hsl

            # 跳过灰度色和已报告的颜色
            if sat < _GRAYSCALE_SATURATION_THRESHOLD:
                continue
            if hex_value.lower() in seen:
                continue
            seen.add(hex_value.lower())

            if sat > _OVERSATURATED_THRESHOLD:
                violations.append(
                    _make_violation(
                        rule="oversaturated_accent",
                        severity=ValidationSeverity.ERROR,
                        message=(
                            f"Oversaturated accent color '{hex_value}' "
                            f"(HSL: {hue}, {sat}%, {light}%). Saturation > {_OVERSATURATED_THRESHOLD}%."
                        ),
                        location=ctx,
                        fix_hint="Reduce saturation for a more refined, premium feel.",
                    ),
                )

        return violations

    # -----------------------------------------------------------------------
    # 规则 3: 霓虹紫/蓝签名色
    # -----------------------------------------------------------------------

    def _check_neon_purple_blue(self, content: str) -> list[ValidationViolation]:
        """检测霓虹紫/蓝签名色（hue 260-300 且饱和度 > 60%）。"""
        violations: list[ValidationViolation] = []
        seen: set[str] = set()

        for hex_value, ctx in _all_hex_colors(content):
            hsl = _hex_to_hsl(hex_value)
            if hsl is None:
                continue
            hue, sat, light = hsl

            if sat < _GRAYSCALE_SATURATION_THRESHOLD:
                continue
            if hex_value.lower() in seen:
                continue
            seen.add(hex_value.lower())

            if _NEON_HUE_MIN <= hue <= _NEON_HUE_MAX and sat > _NEON_SATURATION_THRESHOLD:
                violations.append(
                    _make_violation(
                        rule="neon_purple_blue",
                        severity=ValidationSeverity.ERROR,
                        message=(
                            f"Neon purple/blue signature detected in '{hex_value}' (HSL: {hue}, {sat}%, {light}%)."
                        ),
                        location=ctx,
                        fix_hint="Choose a different hue outside the 260-300 range.",
                    ),
                )

        return violations

    # -----------------------------------------------------------------------
    # 规则 4: 过多强调色
    # -----------------------------------------------------------------------

    def _check_multiple_accents(self, content: str) -> list[ValidationViolation]:
        """检测超过允许数量的非灰度强调色。"""
        accents: set[str] = set()

        for hex_value, _ctx in _all_hex_colors(content):
            hsl = _hex_to_hsl(hex_value)
            if hsl is None:
                continue
            _hue, sat, _light = hsl

            if sat < _GRAYSCALE_SATURATION_THRESHOLD:
                continue
            accents.add(hex_value.lower())

        if len(accents) > _MAX_ACCENTS:
            return [
                _make_violation(
                    rule="multiple_accents",
                    severity=ValidationSeverity.WARNING,
                    message=(
                        f"Too many distinct accent colors detected: {len(accents)} (max recommended: {_MAX_ACCENTS})."
                    ),
                    location=f"Colors: {', '.join(sorted(accents))}",
                    fix_hint="Consolidate to 2-3 accent colors for visual coherence.",
                ),
            ]

        return []

    # -----------------------------------------------------------------------
    # 规则 5: 低对比度背景/文字对
    # -----------------------------------------------------------------------

    def _check_low_contrast_pairs(self, content: str) -> list[ValidationViolation]:
        """检测 CSS 块中背景色与文字色的低对比度对。

        启发式：在同一个 CSS 规则块中查找 background-color 和 color，
        计算它们的亮度差。如果无法可靠配对，则跳过。
        """
        violations: list[ValidationViolation] = []

        for block in _extract_css_blocks(content):
            # 按 CSS 规则分割（简单启发式：按 } 分割）
            rules = [r.strip() for r in block.split("}") if r.strip()]
            for rule in rules:
                # 提取规则体（{ 之后的内容）
                body_match = re.search(r"\{(.+)", rule, re.DOTALL)
                if not body_match:
                    continue
                body = body_match.group(1)

                bg_colors = self._extract_hex_from_css_value(body, _BACKGROUND_RE)
                text_colors = self._extract_hex_from_css_value(body, _COLOR_RE)

                # 只有当块中同时存在背景色和文字色时才检查
                if not bg_colors or not text_colors:
                    continue

                # 配对检查：取第一个背景色和第一个文字色
                bg_hsl = _hex_to_hsl(bg_colors[0])
                text_hsl = _hex_to_hsl(text_colors[0])

                if bg_hsl is None or text_hsl is None:
                    continue

                lightness_diff = abs(bg_hsl[2] - text_hsl[2])
                if lightness_diff < _MIN_CONTRAST_LIGHTNESS_DIFF:
                    violations.append(
                        _make_violation(
                            rule="low_contrast_pair",
                            severity=ValidationSeverity.WARNING,
                            message=(
                                f"Low contrast background/text pair: "
                                f"bg={bg_colors[0]} ({bg_hsl[2]}% L) vs "
                                f"text={text_colors[0]} ({text_hsl[2]}% L). "
                                f"Lightness diff: {lightness_diff}%."
                            ),
                            location=rule[:120].replace("\n", " ").strip(),
                            fix_hint="Increase lightness difference to at least 40% for readability.",
                        ),
                    )

        return violations

    def _extract_hex_from_css_value(self, css_body: str, pattern: re.Pattern[str]) -> list[str]:
        """从 CSS 属性值中提取 hex 颜色。

        Args:
            css_body: CSS 规则体文本
            pattern: 编译好的正则表达式（匹配 background 或 color）

        Returns:
            提取到的 hex 颜色值列表
        """
        hex_colors: list[str] = []
        for match in pattern.finditer(css_body):
            value = match.group(1).strip()
            # 从值中提取 hex 颜色
            hex_match = re.search(r"#([0-9A-Fa-f]{3}){1,2}", value)
            if hex_match:
                hex_colors.append(hex_match.group(0))
        return hex_colors
