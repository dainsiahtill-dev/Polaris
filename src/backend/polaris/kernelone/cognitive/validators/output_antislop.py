"""OutputAntiSlopValidator — 组合验证器，聚合所有 5 个子验证器。

按顺序运行 Font → Color → Content → Layout → Motion 验证器，
支持 ERROR 级别快速失败（fast-fail），最小化延迟。
"""

from __future__ import annotations

from typing import Any

from polaris.kernelone.cognitive.validators._base import BaseValidator
from polaris.kernelone.cognitive.validators.dispatcher import (
    ValidationSeverity,
    ValidationViolation,
)


class OutputAntiSlopValidator(BaseValidator):
    """Anti-Slop 组合验证器。

    懒加载并顺序执行 5 个子验证器（Font, Color, Content, Layout, Motion）。
    当检测到 ERROR 级别违规时立即终止链式执行，返回已收集的违规项。
    """

    def __init__(self) -> None:
        self._validators: list[BaseValidator] = []
        self._initialized: bool = False

    def _ensure_initialized(self) -> None:
        """懒初始化：首次调用 validate() 时才实例化子验证器。"""
        if self._initialized:
            return

        # 按指定顺序导入并实例化子验证器
        # FontValidator
        from polaris.kernelone.cognitive.validators.font_validator import FontValidator

        self._validators.append(FontValidator())

        # ColorValidator
        from polaris.kernelone.cognitive.validators.color_validator import ColorValidator

        self._validators.append(ColorValidator())

        # ContentValidator
        from polaris.kernelone.cognitive.validators.content_validator import ContentValidator

        self._validators.append(ContentValidator())

        # LayoutValidator
        from polaris.kernelone.cognitive.validators.layout_validator import LayoutValidator

        self._validators.append(LayoutValidator())

        # MotionValidator
        from polaris.kernelone.cognitive.validators.motion_validator import MotionValidator

        self._validators.append(MotionValidator())

        self._initialized = True

    def validate(
        self,
        content: str,
        context: dict[str, Any] | None = None,
    ) -> list[ValidationViolation]:
        """执行 Anti-Slop 验证链。

        按 Font → Color → Content → Layout → Motion 顺序执行。
        遇到 ERROR 级别违规时立即快速失败，返回已收集的违规项。

        Args:
            content: 待验证的原始内容（CSS/JSX/HTML 等）
            context: 可选上下文，可包含 design_dials、file_path 等

        Returns:
            ValidationViolation 列表。空列表表示无违规。
        """
        self._ensure_initialized()

        all_violations: list[ValidationViolation] = []

        for validator in self._validators:
            violations = validator.validate(content, context)
            all_violations.extend(violations)

            # 快速失败：发现 ERROR 级别违规立即停止链式执行
            if any(v.severity == ValidationSeverity.ERROR for v in violations):
                break

        return all_violations
