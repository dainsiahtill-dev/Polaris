"""Validator Base — 共享协议、工具函数和类型入口。

所有子 validator 应从此模块导入基础类型，保持接口一致性。
"""

from __future__ import annotations

import re
from typing import Any, Protocol

from polaris.kernelone.cognitive.validators.dispatcher import (
    GenerationDomain,
    ValidationSeverity,
    ValidationViolation,
)

# ---------------------------------------------------------------------------
# 统一协议
# ---------------------------------------------------------------------------


class BaseValidator(Protocol):
    """所有子 validator 必须实现的协议。"""

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
        ...


# ---------------------------------------------------------------------------
# 共享工具函数
# ---------------------------------------------------------------------------


def _extract_css_blocks(content: str) -> list[str]:
    """从混合内容中提取 CSS 块（<style> 标签内 或 独立 CSS）。"""
    blocks: list[str] = []
    # <style> 标签
    blocks.extend(re.findall(r"<style[^>]*>(.*?)</style>", content, re.DOTALL | re.IGNORECASE))
    # 如果内容本身看起来就是 CSS（有 { } 规则），整体视为一块
    if re.search(r"[.#@][^{]+\{[^}]+\}", content):
        blocks.append(content)
    return blocks if blocks else [content]


def _extract_inline_styles(content: str) -> list[tuple[str | None, str]]:
    """提取 style="..." 属性值。返回 [( surrounding_tag_or_none, style_value )]。"""
    pattern = re.compile(r'style=["\']([^"\']+)["\']', re.IGNORECASE)
    results: list[tuple[str | None, str]] = []
    for match in pattern.finditer(content):
        # 尝试提取周围的标签名
        before = content[max(0, match.start() - 80) : match.start()]
        tag_match = re.search(r"<(\w+)[^>]*$", before)
        tag = tag_match.group(1) if tag_match else None
        results.append((tag, match.group(1)))
    return results


def _extract_tailwind_classes(content: str) -> list[str]:
    """提取 Tailwind 风格的 className/class 属性值。"""
    pattern = re.compile(r'(?:class|className)=["\']([^"\']+)["\']')
    classes: list[str] = []
    for match in pattern.finditer(content):
        classes.extend(match.group(1).split())
    return classes


def _hex_to_hsl(hex_color: str) -> tuple[int, int, int] | None:
    """将 hex 颜色（#RRGGBB 或 #RGB）转换为 HSL 元组。

    Returns:
        (hue: 0-359, saturation: 0-100, lightness: 0-100) 或 None（解析失败）
    """
    hex_norm = hex_color.strip().lstrip("#")
    if len(hex_norm) == 3:
        hex_norm = "".join(c * 2 for c in hex_norm)
    if len(hex_norm) != 6:
        return None
    try:
        r = int(hex_norm[0:2], 16) / 255.0
        g = int(hex_norm[2:4], 16) / 255.0
        b = int(hex_norm[4:6], 16) / 255.0
    except ValueError:
        return None

    max_c = max(r, g, b)
    min_c = min(r, g, b)
    diff = max_c - min_c

    # Lightness
    lum = (max_c + min_c) / 2

    # Saturation
    s = 0 if diff == 0 else diff / (1 - abs(2 * lum - 1))

    # Hue
    h: float
    if diff == 0:
        h = 0.0
    elif max_c == r:
        h = (60 * ((g - b) / diff) + 360) % 360
    elif max_c == g:
        h = (60 * ((b - r) / diff) + 120) % 360
    else:
        h = (60 * ((r - g) / diff) + 240) % 360

    return (round(h), round(s * 100), round(lum * 100))


def _all_hex_colors(content: str) -> list[tuple[str, str | None]]:
    """从内容中提取所有 hex 颜色。

    Returns:
        [(hex_value, surrounding_context_or_None), ...]
    """
    pattern = re.compile(r"#([0-9A-Fa-f]{3}){1,2}")
    results: list[tuple[str, str | None]] = []
    for match in pattern.finditer(content):
        # 提取周围上下文（用于定位）
        start = max(0, match.start() - 40)
        end = min(len(content), match.end() + 40)
        ctx = content[start:end].replace("\n", " ").strip()
        results.append((match.group(0), ctx))
    return results


def _make_violation(
    rule: str,
    severity: ValidationSeverity,
    message: str,
    location: str | None = None,
    fix_hint: str | None = None,
) -> ValidationViolation:
    """便捷构造 ValidationViolation。"""
    return ValidationViolation(
        rule=rule,
        severity=severity,
        message=message,
        location=location,
        domain=GenerationDomain.UI_COMPONENT,
        fix_hint=fix_hint,
    )
