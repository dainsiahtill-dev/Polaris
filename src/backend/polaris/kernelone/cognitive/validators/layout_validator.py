"""LayoutValidator — 检测 CSS/JSX 布局反模式。"""

from __future__ import annotations

import re
from typing import Any

from polaris.kernelone.cognitive.design_quality import DesignQualityDials
from polaris.kernelone.cognitive.validators._base import (
    BaseValidator,
    _extract_css_blocks,
    _extract_tailwind_classes,
    _make_violation,
)
from polaris.kernelone.cognitive.validators.dispatcher import ValidationSeverity, ValidationViolation

# ---------------------------------------------------------------------------
# 正则模式
# ---------------------------------------------------------------------------

# vh / vw 单位检测（CSS）
_VH_VW_PATTERN = re.compile(
    r"(?:height|min-height|max-height)\s*:\s*100vh"
    r"|(?:width|min-width|max-width)\s*:\s*100vw",
    re.IGNORECASE,
)

# calc 百分比在 flex/grid 中
_CALC_PERCENTAGE_PATTERN = re.compile(r"calc\s*\([^)]*%[^)]*\)", re.IGNORECASE)

# flex / grid display 检测
_FLEX_GRID_DISPLAY_PATTERN = re.compile(r"display\s*:\s*(?:flex|grid)", re.IGNORECASE)

# 3 列等宽网格（CSS）
_EQUAL_THREE_COL_PATTERN = re.compile(
    r"grid-template-columns\s*:\s*repeat\s*\(\s*3\s*,\s*1fr\s*\)",
    re.IGNORECASE,
)

# justify-content: center + align-items: center + height: 100vh
_CENTERED_HERO_CSS_PATTERN = re.compile(
    r"justify-content\s*:\s*center"
    r".*?align-items\s*:\s*center"
    r".*?(?:height|min-height)\s*:\s*100vh",
    re.IGNORECASE | re.DOTALL,
)

# 负 margin（CSS）
_NEGATIVE_MARGIN_PATTERN = re.compile(
    r"margin-(?:left|right)\s*:\s*-"
    r"|margin\s*:\s*-\s*\d",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# LayoutValidator
# ---------------------------------------------------------------------------


class LayoutValidator:
    """检测 CSS/JSX 布局反模式，支持 design_dials 方差感知规则。"""

    def validate(
        self,
        content: str,
        context: dict[str, Any] | None = None,
    ) -> list[ValidationViolation]:
        """验证内容并返回违规列表。

        Args:
            content: 待验证的原始内容（CSS/JSX/HTML 等）
            context: 可选上下文，可包含 design_dials、file_path 等

        Returns:
            ValidationViolation 列表。空列表表示无违规。
        """
        violations: list[ValidationViolation] = []

        # 提取 design_dials 方差值
        dials = context.get("design_dials") if context else None
        variance = dials.variance if isinstance(dials, DesignQualityDials) else 5

        # 提取 CSS 块和 Tailwind 类
        css_blocks = _extract_css_blocks(content)
        tailwind_classes = _extract_tailwind_classes(content)

        # 1. vh/vw 单位禁用
        violations.extend(self._check_vh_units(css_blocks, tailwind_classes))

        # 2. calc(%) 在 flex/grid 中
        violations.extend(self._check_calc_percentage(css_blocks))

        # 3. 3 列等宽网格（方差感知）
        violations.extend(self._check_equal_three_column(css_blocks, tailwind_classes, variance))

        # 4. 居中 hero 布局（方差感知）
        violations.extend(self._check_centered_hero(css_blocks, tailwind_classes, variance))

        # 5. 过度负 margin
        violations.extend(self._check_excessive_negative_margin(css_blocks, tailwind_classes))

        return violations

    # -----------------------------------------------------------------------
    # 内部检查方法
    # -----------------------------------------------------------------------

    def _check_vh_units(
        self,
        css_blocks: list[str],
        tailwind_classes: list[str],
    ) -> list[ValidationViolation]:
        """检查 vh/vw 单位使用。"""
        violations: list[ValidationViolation] = []

        for block in css_blocks:
            for match in _VH_VW_PATTERN.finditer(block):
                violations.append(
                    _make_violation(
                        rule="vh_units_banned",
                        severity=ValidationSeverity.ERROR,
                        message=f"Viewport unit usage detected: '{match.group(0)}'",
                        location=f"offset {match.start()}",
                        fix_hint="Use `min-h-[100dvh]` instead of `h-screen` for mobile-safe viewport units.",
                    )
                )

        # Tailwind: h-screen, min-h-screen
        tailwind_text = " ".join(tailwind_classes)
        for cls in ("h-screen", "min-h-screen"):
            if cls in tailwind_text.split():
                violations.append(
                    _make_violation(
                        rule="vh_units_banned",
                        severity=ValidationSeverity.ERROR,
                        message=f"Tailwind viewport unit class detected: '{cls}'",
                        fix_hint="Use `min-h-[100dvh]` instead of `h-screen` for mobile-safe viewport units.",
                    )
                )

        return violations

    def _check_calc_percentage(self, css_blocks: list[str]) -> list[ValidationViolation]:
        """检查 flex/grid 上下文中的 calc(%) 使用。"""
        violations: list[ValidationViolation] = []

        for block in css_blocks:
            # 检查该块是否包含 flex 或 grid display
            has_flex_or_grid = _FLEX_GRID_DISPLAY_PATTERN.search(block) is not None

            if has_flex_or_grid:
                for match in _CALC_PERCENTAGE_PATTERN.finditer(block):
                    violations.append(
                        _make_violation(
                            rule="calc_percentage_in_flex",
                            severity=ValidationSeverity.ERROR,
                            message=f"calc(%) in flex/grid context detected: '{match.group(0)}'",
                            location=f"offset {match.start()}",
                            fix_hint="Use flex/grid gap and flex-basis instead of calc(%) in flex contexts.",
                        )
                    )

        return violations

    def _check_equal_three_column(
        self,
        css_blocks: list[str],
        tailwind_classes: list[str],
        variance: int,
    ) -> list[ValidationViolation]:
        """检查 3 列等宽网格（方差感知）。"""
        violations: list[ValidationViolation] = []

        # 方差 <= 4 时不标记
        if variance <= 4:
            return violations

        for block in css_blocks:
            for match in _EQUAL_THREE_COL_PATTERN.finditer(block):
                violations.append(
                    _make_violation(
                        rule="equal_three_column_grid",
                        severity=ValidationSeverity.WARNING,
                        message=f"3-column equal grid detected: '{match.group(0)}'",
                        location=f"offset {match.start()}",
                        fix_hint="Consider asymmetric column sizing for high-variance designs.",
                    )
                )

        # Tailwind: grid-cols-3
        if "grid-cols-3" in tailwind_classes:
            violations.append(
                _make_violation(
                    rule="equal_three_column_grid",
                    severity=ValidationSeverity.WARNING,
                    message="Tailwind 3-column equal grid detected: 'grid-cols-3'",
                    fix_hint="Consider asymmetric column sizing for high-variance designs.",
                )
            )

        return violations

    def _check_centered_hero(
        self,
        css_blocks: list[str],
        tailwind_classes: list[str],
        variance: int,
    ) -> list[ValidationViolation]:
        """检查居中 hero 布局（方差感知）。"""
        violations: list[ValidationViolation] = []

        # 方差 <= 4 时不标记
        if variance <= 4:
            return violations

        for block in css_blocks:
            for match in _CENTERED_HERO_CSS_PATTERN.finditer(block):
                violations.append(
                    _make_violation(
                        rule="centered_hero_layout",
                        severity=ValidationSeverity.WARNING,
                        message="Centered hero layout detected in CSS block",
                        location=f"offset {match.start()}",
                        fix_hint="Consider offset/asymmetric composition for high-variance designs.",
                    )
                )

        # Tailwind: flex justify-center items-center min-h-screen
        tw_set = set(tailwind_classes)
        if (
            "flex" in tw_set
            and "justify-center" in tw_set
            and "items-center" in tw_set
            and ("min-h-screen" in tw_set or "h-screen" in tw_set)
        ):
            violations.append(
                _make_violation(
                    rule="centered_hero_layout",
                    severity=ValidationSeverity.WARNING,
                    message="Tailwind centered hero layout detected: 'flex justify-center items-center min-h-screen'",
                    fix_hint="Consider offset/asymmetric composition for high-variance designs.",
                )
            )

        return violations

    def _check_excessive_negative_margin(
        self,
        css_blocks: list[str],
        tailwind_classes: list[str],
    ) -> list[ValidationViolation]:
        """检查过度负 margin 使用。"""
        violations: list[ValidationViolation] = []

        for block in css_blocks:
            for match in _NEGATIVE_MARGIN_PATTERN.finditer(block):
                violations.append(
                    _make_violation(
                        rule="excessive_negative_margin",
                        severity=ValidationSeverity.WARNING,
                        message=f"Negative margin detected: '{match.group(0)}'",
                        location=f"offset {match.start()}",
                        fix_hint="Use proper grid/flex placement instead of negative margin hacks.",
                    )
                )

        # Tailwind: -mx-*, -ml-*, -mr-*
        for cls in tailwind_classes:
            if cls.startswith("-mx-") or cls.startswith("-ml-") or cls.startswith("-mr-"):
                violations.append(
                    _make_violation(
                        rule="excessive_negative_margin",
                        severity=ValidationSeverity.WARNING,
                        message=f"Tailwind negative margin class detected: '{cls}'",
                        fix_hint="Use proper grid/flex placement instead of negative margin hacks.",
                    )
                )

        return violations


# 协议兼容声明
LayoutValidator: BaseValidator  # type: ignore[misc, no-redef]
