"""Shared output validation for streaming and non-streaming role turns."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from polaris.cells.roles.kernel.internal.kernel.output_parser_provider import get_output_parser
from polaris.cells.roles.kernel.internal.kernel.quality_checker_provider import get_quality_checker
from polaris.cells.roles.kernel.internal.quality_checker import QualityResult

if TYPE_CHECKING:
    from polaris.cells.roles.kernel.internal.kernel.core import RoleExecutionKernel

logger = logging.getLogger(__name__)


def validate_turn_output(
    *,
    kernel: RoleExecutionKernel,
    profile: Any,
    content: str,
    response_schema: type | None,
    attempt: int,
    max_retries: int,
    last_error: str | None,
    has_tool_activity: bool,
) -> tuple[QualityResult, str | None]:
    """Validate one terminal role output without performing side effects."""

    tool_only_turn = not content.strip() and has_tool_activity
    if tool_only_turn:
        return (
            QualityResult(
                success=True,
                errors=[],
                suggestions=[],
                data={"tool_only_turn": True},
                quality_score=100.0,
                quality_passed=True,
            ),
            last_error,
        )

    pre_validated_data: dict[str, Any] | None = None
    instructor_validated = False
    if response_schema is not None:
        try:
            candidate = get_output_parser(kernel).extract_json(content)
            if candidate is None:
                raise ValueError("No JSON found in content")
            validated = response_schema(**candidate)
            pre_validated_data = validated.model_dump()
            instructor_validated = True
        except (RuntimeError, ValueError):
            pre_validated_data = None
            instructor_validated = False

    try:
        return (
            get_quality_checker(kernel).validate_output(
                content,
                profile,
                pre_validated_data=pre_validated_data,
                instructor_validated=instructor_validated,
            ),
            last_error,
        )
    except (RuntimeError, ValueError) as exc:
        logger.warning("质量检查失败 (attempt=%d): %s", attempt, exc)
        error = f"质量检查失败: {exc}"
        return (
            QualityResult(
                success=False,
                errors=[error],
                suggestions=["请确保输出内容完整准确"] if attempt < max_retries else [],
                data={"quality_check_error": True},
                quality_score=0.0,
                quality_passed=False,
            ),
            error,
        )


__all__ = ["validate_turn_output"]
