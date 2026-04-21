"""ContentValidator — 检测 AI slop 内容：emoji、假名、AI 套话、填充文本、占位注释。"""

from __future__ import annotations

import re
from typing import Any

from polaris.kernelone.cognitive.validators._base import BaseValidator, _make_violation
from polaris.kernelone.cognitive.validators.dispatcher import ValidationSeverity, ValidationViolation

# ---------------------------------------------------------------------------
# 常量定义
# ---------------------------------------------------------------------------

_FAKE_NAMES: frozenset[str] = frozenset(
    {
        "john doe",
        "jane smith",
        "jane doe",
        "acme corp",
        "acme corporation",
        "foo bar",
        "foo",
        "bar",
        "baz",
        "qux",
        "example.com",
    }
)

_AI_BUZZWORDS: frozenset[str] = frozenset(
    {
        "elevate",
        "seamless",
        "unleash",
        "next-gen",
        "synergy",
        "empower",
        "revolutionary",
        "cutting-edge",
        "disruptive",
        "holistic",
        "leverage",
        "streamline",
        "optimize",
        "transform",
        "innovative",
        "groundbreaking",
        "world-class",
        "best-in-class",
        "state-of-the-art",
    }
)

# Unicode emoji 范围
_EMOJI_PATTERN = re.compile(
    "["
    "\U0001f600-\U0001f64f"  # emoticons
    "\U0001f300-\U0001f5ff"  # symbols & pictographs
    "\U0001f680-\U0001f6ff"  # transport & map
    "\U0001f1e0-\U0001f1ff"  # flags
    "\U00002702-\U000027b0"  # dingbats
    "\U000024c2-\U0001f251"  # enclosed characters
    "]+",
    re.UNICODE,
)

# 也匹配常见符号 ©, ®, ™
_COMMON_SYMBOL_PATTERN = re.compile(r"[©®™]+")

# 占位注释模式
_PLACEHOLDER_COMMENT_PATTERN = re.compile(
    r"//\s*\.\.\."
    r"|/\*\s*\.\.\.\s*\*/"
    r"|//\s*TODO\s*$"
    r"|<!--\s*\.\.\.\s*-->",
    re.IGNORECASE,
)

# 填充文本
_FILLER_PATTERN = re.compile(
    r"\b(lorem ipsum|filler text|placeholder text)\b",
    re.IGNORECASE,
)

# 用于从混合内容中提取纯文本（移除 HTML 标签）
_HTML_TAG_PATTERN = re.compile(r"<[^>]*>")

# CSS 注释提取
_CSS_COMMENT_PATTERN = re.compile(r"/\*.*?\*/", re.DOTALL)


# ---------------------------------------------------------------------------
# ContentValidator
# ---------------------------------------------------------------------------


class ContentValidator:
    """检测 AI slop 内容：emoji、假名、AI 套话、填充文本、占位注释。"""

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

        # 判断内容类型：如果是 CSS，只扫描注释；否则扫描全部文本
        text_to_scan, css_comments = self._prepare_scan_text(content)

        # 1. Unicode Emoji
        violations.extend(self._check_unicode_emoji(text_to_scan))

        # 2. Fake names / placeholders
        violations.extend(self._check_fake_names(text_to_scan))

        # 3. AI buzzwords
        violations.extend(self._check_ai_buzzwords(text_to_scan))

        # 4. Lorem ipsum / filler text
        violations.extend(self._check_filler_text(text_to_scan))

        # 5. Placeholder comments（在原始内容和 CSS 注释中都检查）
        violations.extend(self._check_placeholder_comments(content, css_comments))

        return violations

    # -----------------------------------------------------------------------
    # 内部检查方法
    # -----------------------------------------------------------------------

    def _prepare_scan_text(self, content: str) -> tuple[str, list[str]]:
        """准备待扫描文本。

        对于 CSS 内容，只提取注释内容扫描；对于 HTML/JSX，先剥离标签。

        Returns:
            (text_to_scan, css_comments)
        """
        css_comments: list[str] = []

        # 判断是否是纯 CSS 内容（包含 CSS 规则）
        if re.search(r"[.#@][^{]+\{[^}]+\}", content):
            # 提取 CSS 注释作为扫描文本
            css_comments = _CSS_COMMENT_PATTERN.findall(content)
            scan_text = " ".join(css_comments)
        else:
            # 混合内容：剥离 HTML 标签后扫描
            scan_text = _HTML_TAG_PATTERN.sub(" ", content)

        return scan_text, css_comments

    def _check_unicode_emoji(self, text: str) -> list[ValidationViolation]:
        """检查 Unicode emoji 字符。"""
        violations: list[ValidationViolation] = []

        for match in _EMOJI_PATTERN.finditer(text):
            violations.append(
                _make_violation(
                    rule="unicode_emoji_detected",
                    severity=ValidationSeverity.ERROR,
                    message=f"Unicode emoji detected: '{match.group(0)}'",
                    location=f"offset {match.start()}",
                    fix_hint="Remove emoji characters from generated content.",
                )
            )

        for match in _COMMON_SYMBOL_PATTERN.finditer(text):
            violations.append(
                _make_violation(
                    rule="unicode_emoji_detected",
                    severity=ValidationSeverity.ERROR,
                    message=f"Common symbol detected: '{match.group(0)}'",
                    location=f"offset {match.start()}",
                    fix_hint="Remove symbol characters (©, ®, ™) from generated content.",
                )
            )

        return violations

    def _check_fake_names(self, text: str) -> list[ValidationViolation]:
        """检查假名 / 占位符名称。"""
        violations: list[ValidationViolation] = []

        for name in _FAKE_NAMES:
            # 整词匹配
            pattern = re.compile(rf"\b{re.escape(name)}\b", re.IGNORECASE)
            for match in pattern.finditer(text):
                violations.append(
                    _make_violation(
                        rule="fake_name_detected",
                        severity=ValidationSeverity.ERROR,
                        message=f"Fake or placeholder name detected: '{match.group(0)}'",
                        location=f"offset {match.start()}",
                        fix_hint="Replace placeholder names with realistic, context-appropriate names.",
                    )
                )

        return violations

    def _check_ai_buzzwords(self, text: str) -> list[ValidationViolation]:
        """检查 AI 套话 buzzwords。"""
        violations: list[ValidationViolation] = []

        for word in _AI_BUZZWORDS:
            pattern = re.compile(rf"\b{re.escape(word)}\b", re.IGNORECASE)
            for match in pattern.finditer(text):
                violations.append(
                    _make_violation(
                        rule="ai_buzzword_detected",
                        severity=ValidationSeverity.WARNING,
                        message=f"AI buzzword detected: '{match.group(0)}'",
                        location=f"offset {match.start()}",
                        fix_hint="Consider using more specific, concrete language instead of generic marketing terms.",
                    )
                )

        return violations

    def _check_filler_text(self, text: str) -> list[ValidationViolation]:
        """检查填充文本（lorem ipsum 等）。"""
        violations: list[ValidationViolation] = []

        for match in _FILLER_PATTERN.finditer(text):
            violations.append(
                _make_violation(
                    rule="filler_text_detected",
                    severity=ValidationSeverity.ERROR,
                    message=f"Filler text detected: '{match.group(0)}'",
                    location=f"offset {match.start()}",
                    fix_hint="Replace filler text with meaningful, context-appropriate content.",
                )
            )

        return violations

    def _check_placeholder_comments(
        self,
        original_content: str,
        css_comments: list[str],
    ) -> list[ValidationViolation]:
        """检查空占位注释。"""
        violations: list[ValidationViolation] = []

        # 扫描原始内容中的占位注释
        for match in _PLACEHOLDER_COMMENT_PATTERN.finditer(original_content):
            violations.append(
                _make_violation(
                    rule="placeholder_comment",
                    severity=ValidationSeverity.ERROR,
                    message=f"Placeholder comment detected: '{match.group(0)}'",
                    location=f"offset {match.start()}",
                    fix_hint="Remove empty placeholder comments or replace with meaningful TODOs containing actual task descriptions.",
                )
            )

        # 扫描 CSS 注释中的占位符
        for comment in css_comments:
            for match in _PLACEHOLDER_COMMENT_PATTERN.finditer(comment):
                violations.append(
                    _make_violation(
                        rule="placeholder_comment",
                        severity=ValidationSeverity.ERROR,
                        message=f"Placeholder CSS comment detected: '{match.group(0)}'",
                        location=f"offset {match.start()}",
                        fix_hint="Remove empty placeholder comments or replace with meaningful TODOs containing actual task descriptions.",
                    )
                )

        return violations


# 协议兼容声明
ContentValidator: BaseValidator  # type: ignore[misc, no-redef]
