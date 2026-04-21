"""Output Completeness Enforcer — P0-C: 输出完整性 enforcement。

来源: output-skill + research/laziness — AI 截断是"行为选择"，必须有 scope locking。

核心职责:
1. 检测 banned placeholder 模式（// ..., /* ... */, "for brevity" 等）
2. 检测骨架输出（只生成头尾，中间省略）
3. 检测代码行数下限（防止极简骨架）
"""

from __future__ import annotations

import re
from typing import Any

from polaris.kernelone.cognitive.validators._base import _make_violation
from polaris.kernelone.cognitive.validators.dispatcher import ValidationSeverity, ValidationViolation

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

# Banned placeholder patterns — 这些明确表明 AI 在偷懒
BANNED_PLACEHOLDER_PATTERNS: tuple[tuple[str, str], ...] = (
    # 正则模式, 规则标识符
    (r"//\s*\.\.\.", "placeholder_ellipsis_comment"),
    (r"/\*\s*\.\.\.\s*\*/", "placeholder_ellipsis_block_comment"),
    (r"<!--\s*\.\.\.\s*-->", "placeholder_ellipsis_html_comment"),
    (r"#\s*\.\.\.", "placeholder_ellipsis_hash_comment"),  # Python/Ruby/Shell
    (r"\{\s*\.\.\.\s*\}", "placeholder_ellipsis_braces"),  # JSX/JSON
    (r"//\s*TODO\s*(?:\n|$)", "placeholder_empty_todo"),
    (r"the rest follows the same pattern", "placeholder_same_pattern"),
    (r"for brevity", "placeholder_brevity"),
    (r"I can provide more details", "placeholder_offer_details"),
    (r"\[PAUSED\]", "placeholder_paused"),
    (r"\[\.\.\.\]", "placeholder_bracket_ellipsis"),
    (r"remaining (methods|fields|props|components) are (similar|omitted|skipped)", "placeholder_remaining_similar"),
    (r"// (Add|Implement|TODO:|FIXME:) (remaining|rest|other|more)", "placeholder_remaining_todo"),
)

# 骨架输出检测阈值
_SKELETON_MIN_LINES: int = 10  # 低于此行数视为骨架（仅前端代码）
_SKELETON_MAX_CONTENT_RATIO: float = 0.3  # 注释/占位符占比超过此值视为骨架


class OutputCompletenessEnforcer:
    """输出完整性 enforcement — 检测 AI 截断、骨架输出和占位符。"""

    def validate(
        self,
        content: str,
        context: dict[str, Any] | None = None,
    ) -> list[ValidationViolation]:
        """验证生成内容的完整性。

        Args:
            content: 生成的代码内容
            context: 可选上下文，可包含 expected_scope、file_path 等

        Returns:
            ValidationViolation 列表
        """
        violations: list[ValidationViolation] = []

        # 1. Banned placeholder 模式检测
        violations.extend(self._check_placeholder_patterns(content))

        # 2. 骨架输出检测
        violations.extend(self._check_skeleton_output(content))

        # 3. 代码行数下限检测
        violations.extend(self._check_min_lines(content))

        return violations

    # -----------------------------------------------------------------------
    # 子检测器
    # -----------------------------------------------------------------------

    @staticmethod
    def _check_placeholder_patterns(content: str) -> list[ValidationViolation]:
        """检测 banned placeholder 模式。"""
        violations: list[ValidationViolation] = []
        content_lower = content.lower()

        for pattern, rule_id in BANNED_PLACEHOLDER_PATTERNS:
            for match in re.finditer(pattern, content_lower, re.IGNORECASE):
                # 提取位置上下文
                line_start = content.rfind("\n", 0, match.start()) + 1
                line_end = content.find("\n", match.end())
                if line_end == -1:
                    line_end = len(content)
                line_context = content[line_start:line_end].strip()

                violations.append(
                    _make_violation(
                        rule=rule_id,
                        severity=ValidationSeverity.ERROR,
                        message=f"Placeholder pattern detected: '{line_context}'",
                        location=f"line:{content[: match.start()].count(chr(10)) + 1}",
                        fix_hint="Remove placeholder and implement the actual code.",
                    )
                )

        return violations

    @staticmethod
    def _check_skeleton_output(content: str) -> list[ValidationViolation]:
        """检测骨架输出（只生成头尾，中间省略）。"""
        violations: list[ValidationViolation] = []

        lines = content.splitlines()
        non_empty_lines = [ln for ln in lines if ln.strip()]

        if not non_empty_lines:
            return violations

        # 检测指标 1: 注释/空行占比过高
        placeholder_lines = sum(
            1
            for ln in non_empty_lines
            if re.search(r"^\s*(//|/\*|\*|#|<!--)\s*(\.\.\.|TODO|FIXME|placeholder|implement)", ln, re.IGNORECASE)
        )
        placeholder_ratio = placeholder_lines / len(non_empty_lines) if non_empty_lines else 0

        if placeholder_ratio > _SKELETON_MAX_CONTENT_RATIO:
            violations.append(
                _make_violation(
                    rule="skeleton_placeholder_ratio",
                    severity=ValidationSeverity.ERROR,
                    message=f"Skeleton output detected: {placeholder_ratio:.0%} of lines are placeholders/empty.",
                    fix_hint="Implement all declared functions/components instead of leaving placeholders.",
                )
            )

        # 检测指标 2: 明显的骨架模式（开头 + 省略 + 结尾）
        # 寻找 "..." 或 "remaining" 出现在文件中间的模式
        mid_start = len(content) // 4
        mid_end = 3 * len(content) // 4
        mid_section = content[mid_start:mid_end].lower()

        skeleton_markers = [
            "...",
            "remaining",
            "omitted",
            "skipped",
            "similar",
            "follows the same",
        ]
        mid_skeleton_score = sum(1 for marker in skeleton_markers if marker in mid_section)

        if mid_skeleton_score >= 2:
            violations.append(
                _make_violation(
                    rule="skeleton_midsection_omission",
                    severity=ValidationSeverity.ERROR,
                    message="Skeleton output detected: middle section contains omission markers.",
                    fix_hint="Implement the complete component/function without omitting middle sections.",
                )
            )

        return violations

    @staticmethod
    def _check_min_lines(content: str) -> list[ValidationViolation]:
        """检测代码行数是否低于合理下限。"""
        violations: list[ValidationViolation] = []

        lines = content.splitlines()
        non_empty_lines = [ln for ln in lines if ln.strip()]
        code_lines = [ln for ln in non_empty_lines if not re.match(r"^\s*(//|/\*|\*|#|<!--)", ln.strip())]

        # 只对前端代码进行行数检查（HTML/CSS/JSX 通常较长）
        if len(code_lines) < _SKELETON_MIN_LINES and len(content) > 100:
            violations.append(
                _make_violation(
                    rule="output_too_short",
                    severity=ValidationSeverity.WARNING,
                    message=f"Output may be incomplete: only {len(code_lines)} lines of actual code found.",
                    fix_hint="Ensure all requested components and functions are fully implemented.",
                )
            )

        return violations
