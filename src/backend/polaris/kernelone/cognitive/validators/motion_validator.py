"""MotionValidator — 检测 CSS/JSX 内容中的动画反模式。

覆盖以下规则：
- linear_easing_banned: 禁止使用 linear 缓动
- layout_property_animation: 禁止在 @keyframes 中动画化布局属性
- spin_loader_anti_pattern: 检测 CSS 旋转加载骨架反模式
- excessive_animation_duration: 动画/过渡时长超过 1s 的警告
- no_prefers_reduced_motion: 缺少 prefers-reduced-motion 媒体查询的提示
"""

from __future__ import annotations

import re
from typing import Any

from polaris.kernelone.cognitive.validators._base import (
    BaseValidator,
    _extract_css_blocks,
    _extract_tailwind_classes,
    _make_violation,
)
from polaris.kernelone.cognitive.validators.dispatcher import (
    ValidationSeverity,
    ValidationViolation,
)


class MotionValidator(BaseValidator):
    """动画反模式验证器。

    扫描 CSS 块与 Tailwind 类名，检测性能与体验相关的动画违规。
    """

    # 触发布局重排的属性
    _LAYOUT_PROPERTIES: frozenset[str] = frozenset(
        {
            "top",
            "left",
            "right",
            "bottom",
            "width",
            "height",
            "margin",
            "padding",
        }
    )

    # Tailwind 动画相关类名
    _TAILWIND_ANIMATION_CLASSES: frozenset[str] = frozenset(
        {
            "animate-spin",
            "animate-ping",
            "animate-bounce",
            "ease-linear",
        }
    )

    def validate(
        self,
        content: str,
        context: dict[str, Any] | None = None,
    ) -> list[ValidationViolation]:
        """验证内容中的动画反模式。

        Args:
            content: 待验证的原始内容（CSS/JSX/HTML 等）
            context: 可选上下文

        Returns:
            ValidationViolation 列表。
        """
        violations: list[ValidationViolation] = []

        # 1. Tailwind 类名检查（ease-linear, animate-spin 等）
        violations.extend(self._check_tailwind_classes(content))

        # 2. CSS 块级检查
        css_blocks = _extract_css_blocks(content)
        for block in css_blocks:
            violations.extend(self._check_css_block(block))

        return violations

    def _check_tailwind_classes(self, content: str) -> list[ValidationViolation]:
        """检查 Tailwind 类名中的动画反模式。"""
        violations: list[ValidationViolation] = []
        classes = _extract_tailwind_classes(content)

        for cls in classes:
            if cls == "ease-linear":
                violations.append(
                    _make_violation(
                        rule="linear_easing_banned",
                        severity=ValidationSeverity.ERROR,
                        message=f"Tailwind class '{cls}' uses linear easing.",
                        location=f"className: {cls}",
                        fix_hint=("Use spring physics or cubic-bezier(0.4, 0, 0.2, 1) instead of linear easing."),
                    )
                )
            elif cls == "animate-spin":
                # spin_loader_anti_pattern: 在 Tailwind 中 animate-spin 就是旋转加载器
                violations.append(
                    _make_violation(
                        rule="spin_loader_anti_pattern",
                        severity=ValidationSeverity.WARNING,
                        message=f"Tailwind class '{cls}' indicates a spinning loader.",
                        location=f"className: {cls}",
                        fix_hint=(
                            "Consider a skeleton pulse or content-aware loading state instead of a spinning circle."
                        ),
                    )
                )

        return violations

    def _check_css_block(self, block: str) -> list[ValidationViolation]:
        """检查单个 CSS 块中的动画反模式。"""
        violations: list[ValidationViolation] = []

        # 检查 @keyframes 规则
        violations.extend(self._check_keyframes(block))

        # 检查选择器规则中的动画/过渡属性
        violations.extend(self._check_selector_rules(block))

        # 检查 prefers-reduced-motion
        violations.extend(self._check_reduced_motion(block))

        return violations

    def _check_keyframes(self, block: str) -> list[ValidationViolation]:
        """检查 @keyframes 规则中的反模式。"""
        violations: list[ValidationViolation] = []

        # 匹配 @keyframes name { ... }
        keyframes_pattern = re.compile(
            r"@keyframes\s+([A-Za-z0-9_-]+)\s*\{([^}]*(?:\{[^}]*\}[^}]*)*)\}",
            re.IGNORECASE | re.DOTALL,
        )

        for match in keyframes_pattern.finditer(block):
            animation_name = match.group(1)
            keyframes_body = match.group(2)

            # layout_property_animation: 扫描 keyframes 体中的布局属性
            for prop in self._LAYOUT_PROPERTIES:
                # 匹配 property: value 或 property:value
                prop_pattern = re.compile(
                    rf"\b{re.escape(prop)}\s*:",
                    re.IGNORECASE,
                )
                if prop_pattern.search(keyframes_body):
                    violations.append(
                        _make_violation(
                            rule="layout_property_animation",
                            severity=ValidationSeverity.ERROR,
                            message=(f"@keyframes '{animation_name}' animates layout property '{prop}'."),
                            location=f"@keyframes {animation_name}",
                            fix_hint=(
                                "Use transform: translate3d() / scale() / opacity "
                                "instead of layout properties for animation."
                            ),
                        )
                    )
                    # 每个 keyframes 中同一属性只报一次
                    break

            # 检查是否包含 spin 相关命名
            if "spin" in animation_name.lower():
                violations.append(
                    _make_violation(
                        rule="spin_loader_anti_pattern",
                        severity=ValidationSeverity.WARNING,
                        message=f"@keyframes '{animation_name}' appears to be a spin animation.",
                        location=f"@keyframes {animation_name}",
                        fix_hint=(
                            "Consider a skeleton pulse or content-aware loading state instead of a spinning circle."
                        ),
                    )
                )

        return violations

    def _check_selector_rules(self, block: str) -> list[ValidationViolation]:
        """检查 CSS 选择器规则中的动画/过渡属性。"""
        violations: list[ValidationViolation] = []

        # 提取选择器规则: selector { declarations }
        # 注意：这里使用简化的规则提取，处理嵌套可能不完美
        rule_pattern = re.compile(
            r"([^{@]+)\{([^}]*)\}",
            re.DOTALL,
        )

        for match in rule_pattern.finditer(block):
            selector = match.group(1).strip()
            declarations = match.group(2)

            # linear_easing_banned
            if re.search(
                r"(?:transition|animation)-timing-function\s*:\s*linear",
                declarations,
                re.IGNORECASE,
            ):
                violations.append(
                    _make_violation(
                        rule="linear_easing_banned",
                        severity=ValidationSeverity.ERROR,
                        message=(f"Selector '{selector}' uses linear easing for transition/animation."),
                        location=selector,
                        fix_hint=("Use spring physics or cubic-bezier(0.4, 0, 0.2, 1) instead of linear easing."),
                    )
                )

            # excessive_animation_duration
            duration_violations = self._check_duration(declarations, selector)
            violations.extend(duration_violations)

            # spin_loader_anti_pattern: border-radius: 50% + animation: spin
            has_border_radius_50 = re.search(
                r"border-radius\s*:\s*50%",
                declarations,
                re.IGNORECASE,
            )
            has_spin_animation = re.search(
                r"animation\s*:\s*\s*spin",
                declarations,
                re.IGNORECASE,
            )
            if has_border_radius_50 and has_spin_animation:
                violations.append(
                    _make_violation(
                        rule="spin_loader_anti_pattern",
                        severity=ValidationSeverity.WARNING,
                        message=(f"Selector '{selector}' combines border-radius: 50% with a spin animation."),
                        location=selector,
                        fix_hint=(
                            "Consider a skeleton pulse or content-aware loading state instead of a spinning circle."
                        ),
                    )
                )

        return violations

    def _check_duration(
        self,
        declarations: str,
        selector: str,
    ) -> list[ValidationViolation]:
        """检查动画/过渡时长是否过长。"""
        violations: list[ValidationViolation] = []

        # 匹配 animation-duration 或 transition-duration
        duration_pattern = re.compile(
            r"(?:animation|transition)-duration\s*:\s*([0-9.]+)\s*(ms|s)",
            re.IGNORECASE,
        )

        for match in duration_pattern.finditer(declarations):
            value = float(match.group(1))
            unit = match.group(2).lower()

            # 统一转换为毫秒
            duration_ms = value * 1000 if unit == "s" else value

            if duration_ms > 1000:
                violations.append(
                    _make_violation(
                        rule="excessive_animation_duration",
                        severity=ValidationSeverity.WARNING,
                        message=(f"Selector '{selector}' has animation/transition duration of {value}{unit} (> 1s)."),
                        location=selector,
                        fix_hint="Keep animations under 500ms for UI responsiveness.",
                    )
                )

        return violations

    def _check_reduced_motion(self, block: str) -> list[ValidationViolation]:
        """检查是否缺少 prefers-reduced-motion 媒体查询。"""
        violations: list[ValidationViolation] = []

        # 检查是否有动画相关声明
        has_animation = re.search(
            r"(?:animation|transition)\s*:",
            block,
            re.IGNORECASE,
        )
        has_keyframes = re.search(
            r"@keyframes",
            block,
            re.IGNORECASE,
        )

        if not has_animation and not has_keyframes:
            return violations

        # 检查是否已有 prefers-reduced-motion 媒体查询
        has_reduced_motion = re.search(
            r"@media\s*\(\s*prefers-reduced-motion",
            block,
            re.IGNORECASE,
        )

        if not has_reduced_motion:
            violations.append(
                _make_violation(
                    rule="no_prefers_reduced_motion",
                    severity=ValidationSeverity.INFO,
                    message=("Animation detected without @media (prefers-reduced-motion) guard."),
                    location=None,
                    fix_hint=(
                        "Consider wrapping animations in a prefers-reduced-motion media query for accessibility."
                    ),
                )
            )

        return violations
