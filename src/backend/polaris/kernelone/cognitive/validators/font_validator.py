"""Font Validator — 检测禁用字体并强制执行允许字体策略。

扫描 CSS 块、内联样式和 Tailwind 类中的字体声明，对使用禁用字体的内容
生成 ERROR 级别违规，对通用字体栈和 Tailwind 字体启发式生成 WARNING。
"""

from __future__ import annotations

import re
from typing import Any

from polaris.kernelone.cognitive.design_quality import DesignQualityDials
from polaris.kernelone.cognitive.validators._base import (
    _extract_css_blocks,
    _extract_inline_styles,
    _extract_tailwind_classes,
    _make_violation,
)
from polaris.kernelone.cognitive.validators.dispatcher import (
    ValidationSeverity,
    ValidationViolation,
)

# ---------------------------------------------------------------------------
# 字体策略常量
# ---------------------------------------------------------------------------

BANNED_FONTS: frozenset[str] = frozenset(
    {
        "Inter",
        "Roboto",
        "Open Sans",
        "Lato",
        "Times New Roman",
        "Georgia",
        "Garamond",
        "Palatino",
    }
)

ALLOWED_PREMIUM_FONTS: frozenset[str] = frozenset(
    {
        "Geist",
        "Outfit",
        "Cabinet Grotesk",
        "Satoshi",
        "Fraunces",
        "Gambarino",
        "Instrument Serif",
    }
)

_FONT_FAMILY_RE = re.compile(r"font-family\s*:\s*([^;]+)", re.IGNORECASE)


# ---------------------------------------------------------------------------
# FontValidator
# ---------------------------------------------------------------------------


class FontValidator:
    """字体验证器 — 检测禁用字体并推荐高级替代方案。"""

    def __init__(self, dials: DesignQualityDials | None = None) -> None:
        """初始化字体验证器。

        Args:
            dials: 可选的设计质量参数，用于上下文感知验证。
        """
        self.dials = dials

    def validate(
        self,
        content: str,
        context: dict[str, Any] | None = None,
    ) -> list[ValidationViolation]:
        """验证内容中的字体声明。

        Args:
            content: 待验证的原始内容（CSS/JSX/HTML 等）
            context: 可选上下文，可包含 design_dials、file_path 等

        Returns:
            ValidationViolation 列表。空列表表示无违规。
        """
        violations: list[ValidationViolation] = []
        dials = self._resolve_dials(context)

        violations.extend(self._scan_css_blocks(content))
        violations.extend(self._scan_inline_styles(content))
        violations.extend(self._scan_tailwind_classes(content, dials))

        return violations

    # -----------------------------------------------------------------------
    # 内部扫描方法
    # -----------------------------------------------------------------------

    def _scan_css_blocks(self, content: str) -> list[ValidationViolation]:
        """扫描 CSS 块中的 font-family 声明。"""
        violations: list[ValidationViolation] = []
        for block in _extract_css_blocks(content):
            for match in _FONT_FAMILY_RE.finditer(block):
                declaration = match.group(1).strip()
                location = block[max(0, match.start() - 60) : match.end() + 20].replace("\n", " ").strip()
                violations.extend(self._check_font_declaration(declaration, location))
        return violations

    def _scan_inline_styles(self, content: str) -> list[ValidationViolation]:
        """扫描内联 style 属性中的 font-family 声明。"""
        violations: list[ValidationViolation] = []
        for tag, style_value in _extract_inline_styles(content):
            for match in _FONT_FAMILY_RE.finditer(style_value):
                declaration = match.group(1).strip()
                location = f"inline style{' in <' + tag + '>' if tag else ''}"
                violations.extend(self._check_font_declaration(declaration, location))
        return violations

    def _scan_tailwind_classes(
        self,
        content: str,
        dials: DesignQualityDials | None,
    ) -> list[ValidationViolation]:
        """扫描 Tailwind 字体类启发式。"""
        violations: list[ValidationViolation] = []
        classes = _extract_tailwind_classes(content)

        has_explicit_font = any(
            cls.startswith("font-") and cls not in {"font-sans", "font-serif", "font-mono"} for cls in classes
        )

        for cls in classes:
            if cls == "font-sans" and not has_explicit_font:
                violations.append(
                    _make_violation(
                        rule="tailwind_font_heuristic",
                        severity=ValidationSeverity.WARNING,
                        message=(
                            "Tailwind 'font-sans' used without explicit font configuration. "
                            "This may resolve to a banned generic font."
                        ),
                        location=f"class='{cls}'",
                        fix_hint="Configure 'font-sans' to map to 'Geist' or 'Outfit' in tailwind.config.",
                    ),
                )

        return violations

    # -----------------------------------------------------------------------
    # 字体声明检查
    # -----------------------------------------------------------------------

    def _check_font_declaration(
        self,
        declaration: str,
        location: str,
    ) -> list[ValidationViolation]:
        """检查单个 font-family 声明中的违规。"""
        violations: list[ValidationViolation] = []

        # 分割多个字体（逗号分隔），去除引号和空白
        fonts = [f.strip().strip("\"'") for f in declaration.split(",")]

        # 检查禁用字体
        for font in fonts:
            for banned in BANNED_FONTS:
                if font.lower() == banned.lower():
                    violations.append(
                        _make_violation(
                            rule="banned_font_detected",
                            severity=ValidationSeverity.ERROR,
                            message=f"Banned font '{banned}' detected in font-family declaration.",
                            location=location,
                            fix_hint="Replace with 'Geist' or 'Outfit'.",
                        ),
                    )

        # 检查通用字体栈
        if self._is_generic_font_stack(fonts):
            violations.append(
                _make_violation(
                    rule="generic_font_stack",
                    severity=ValidationSeverity.WARNING,
                    message="Generic font-family stack without a specific premium font.",
                    location=location,
                    fix_hint=f"Add a specific font like '{next(iter(ALLOWED_PREMIUM_FONTS))}' to the stack.",
                ),
            )

        return violations

    def _is_generic_font_stack(self, fonts: list[str]) -> bool:
        """判断是否为过于通用的字体栈。

        条件：
        - 仅包含 generic 字体名（sans-serif, serif, monospace, cursive, fantasy）
        - 或 font-family: sans-serif 这种极简声明
        """
        generic_names = {"sans-serif", "serif", "monospace", "cursive", "fantasy"}
        non_generic = [f for f in fonts if f.lower() not in generic_names]
        return len(non_generic) == 0 and len(fonts) > 0

    # -----------------------------------------------------------------------
    # 工具方法
    # -----------------------------------------------------------------------

    def _resolve_dials(
        self,
        context: dict[str, Any] | None,
    ) -> DesignQualityDials | None:
        """从上下文或实例属性解析 DesignQualityDials。"""
        if context and "design_dials" in context:
            dials = context["design_dials"]
            if isinstance(dials, DesignQualityDials):
                return dials
        return self.dials
