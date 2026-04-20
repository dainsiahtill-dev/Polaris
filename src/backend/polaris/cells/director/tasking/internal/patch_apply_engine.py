"""Unified patch/file operation parser and applier for Director responses.

此模块现作为 protocol_kernel 的兼容层存在，所有核心逻辑已迁移至
llm_toolkit.protocol_kernel。保留此模块以保持向后兼容。

迁移说明:
- 新代码应直接使用: from polaris.kernelone.llm.toolkit import apply_protocol_output
- 此模块将逐步弃用，统一使用 protocol_kernel
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

# 导入新的统一协议内核
from polaris.kernelone.llm.toolkit import (
    ApplyReport,
    EditType,
    ErrorCode,
    FileOperation,
    OperationResult,
    apply_protocol_output,
    parse_protocol_output,
)

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════
# 向后兼容的数据类（映射到新内核）
# ═══════════════════════════════════════════════════════════════════


@dataclass
class ApplyResult:
    """Result of applying unified operations.

    向后兼容 - 内部使用 ApplyReport。
    """

    success: bool
    changed_files: list[str] = field(default_factory=list)
    failed_operations: list[tuple[FileOperation, str]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @classmethod
    def from_report(cls, report: ApplyReport) -> ApplyResult:
        """从新的 ApplyReport 转换。"""
        failed = []
        for r in report.results:
            if not r.success:
                failed.append((r.operation, r.error_message))

        return cls(
            success=report.success,
            changed_files=report.changed_files,
            failed_operations=failed,
            errors=[r.error_message for r in report.results if r.error_message],
        )


@dataclass
class ApplyIntegrity:
    """Result of pre-apply integrity validation."""

    is_valid: bool
    can_continue: bool
    errors: list[str] = field(default_factory=list)
    parse_state: Any | None = None
    integrity: Any | None = None


# ═══════════════════════════════════════════════════════════════════
# 向后兼容的解析函数
# ═══════════════════════════════════════════════════════════════════


def parse_delete_operations(text: str) -> list[FileOperation]:
    """Parse DELETE_FILE/DELETE operations.

    向后兼容 - 实际调用 protocol_kernel。
    """
    ops = parse_protocol_output(text)
    return [op for op in ops if op.edit_type == EditType.DELETE]


def parse_search_replace_blocks(text: str) -> list[FileOperation]:
    """Parse PATCH_FILE SEARCH/REPLACE blocks.

    向后兼容 - 实际调用 protocol_kernel。
    """
    ops = parse_protocol_output(text)
    return [op for op in ops if op.edit_type == EditType.SEARCH_REPLACE]


def parse_full_file_blocks(text: str) -> list[FileOperation]:
    """Parse FILE/CREATE full-file blocks.

    向后兼容 - 实际调用 protocol_kernel。
    """
    ops = parse_protocol_output(text)
    return [op for op in ops if op.edit_type in (EditType.FULL_FILE, EditType.CREATE)]


def parse_all_operations(text: str) -> list[FileOperation]:
    """Parse all operations from mixed LLM output.

    向后兼容 - 实际调用 protocol_kernel。
    """
    return parse_protocol_output(text)


# ═══════════════════════════════════════════════════════════════════
# 向后兼容的执行函数
# ═══════════════════════════════════════════════════════════════════


def apply_operation(
    operation: FileOperation,
    workspace: str,
    *,
    fallback_to_full_file: bool = True,  # DEPRECATED: 不再使用，保留参数用于兼容
) -> tuple[bool, str | None, bool]:
    """Apply one operation.

    Args:
        operation: File operation to apply
        workspace: Workspace root directory
        fallback_to_full_file: DEPRECATED - 严格模式下不再使用兜底

    Returns:
        (ok, error, changed)
    """
    from polaris.kernelone.llm.toolkit import StrictOperationApplier

    result = StrictOperationApplier.apply(operation, workspace)

    error_msg = None if result.success else result.error_message
    return result.success, error_msg, result.changed


def apply_all_operations(
    text: str,
    workspace: str,
    *,
    fallback_to_full_file: bool = True,  # DEPRECATED: 不再使用
    verbose: bool = False,
) -> ApplyResult:
    """Parse and apply all operations from response text.

    注意：v2.0+ 使用严格模式，SEARCH未命中将失败，不再自动兜底为全文件覆盖。

    Args:
        text: LLM response text containing file operations
        workspace: Workspace root directory
        fallback_to_full_file: DEPRECATED - 不再使用，保留参数用于兼容
        verbose: Whether to print verbose output

    Returns:
        ApplyResult with success/failure details
    """
    # 使用新的协议内核（严格模式）
    report = apply_protocol_output(
        text,
        workspace,
        strict=True,  # 严格模式
        allow_fuzzy_match=False,  # 禁用模糊匹配
    )

    if verbose and report.ops_failed > 0:
        logger.info(
            "[unified_apply] Failed operations: %s",
            report.ops_failed,
        )
        for r in report.results:
            if not r.success:
                logger.info("  - %s: %s", r.operation.path, r.error_message)

    return ApplyResult.from_report(report)


# ═══════════════════════════════════════════════════════════════════
# 完整性验证（向后兼容）
# ═══════════════════════════════════════════════════════════════════


def validate_before_apply(
    text: str,
    provider_metadata: dict[str, Any],
) -> ApplyIntegrity:
    """Validate output integrity before applying file operations.

    向后兼容 - 实际调用 protocol_kernel。
    """
    # 简化的完整性验证
    ops = parse_protocol_output(text)

    if not ops:
        return ApplyIntegrity(
            is_valid=False,
            can_continue=False,
            errors=["no_valid_operations"],
        )

    # 检查截断（简单启发式）
    text_lower = text.lower()
    unclosed_blocks = []

    if "patch_file" in text_lower and "end patch_file" not in text_lower:
        unclosed_blocks.append("PATCH_FILE")
    if "<<<<<<< search" in text_lower and ">>>>>>> replace" not in text_lower:
        unclosed_blocks.append("SEARCH/REPLACE")

    if unclosed_blocks:
        return ApplyIntegrity(
            is_valid=False,
            can_continue=True,
            errors=[f"unclosed_blocks: {', '.join(unclosed_blocks)}"],
        )

    return ApplyIntegrity(
        is_valid=True,
        can_continue=False,
        errors=[],
    )


# ═══════════════════════════════════════════════════════════════════
# 新的推荐接口（v2.0）
# ═══════════════════════════════════════════════════════════════════


def apply_operations_strict(
    text: str,
    workspace: str,
    *,
    allow_fuzzy_match: bool = False,
) -> ApplyReport:
    """严格模式执行协议输出（推荐新接口）.

    Args:
        text: LLM response text
        workspace: Workspace root directory
        allow_fuzzy_match: Whether to allow fuzzy matching for SEARCH

    Returns:
        ApplyReport with full details
    """
    return apply_protocol_output(
        text,
        workspace,
        strict=True,
        allow_fuzzy_match=allow_fuzzy_match,
    )


__all__ = [
    "ApplyIntegrity",
    "ApplyReport",
    "ApplyResult",
    # 向后兼容
    "EditType",
    "ErrorCode",
    "FileOperation",
    "OperationResult",
    "apply_all_operations",
    "apply_operation",
    # 新接口
    "apply_operations_strict",
    "parse_all_operations",
    "parse_delete_operations",
    "parse_full_file_blocks",
    "parse_search_replace_blocks",
    "validate_before_apply",
]
