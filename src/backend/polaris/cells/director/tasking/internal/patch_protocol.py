"""Director tasking patch protocol adapter.

This module is the Director tasking cell's small adapter around the KernelOne
LLM protocol kernel. KernelOne owns parsing and strict application semantics;
Director tasking owns the task-facing result projections used by worker and
execution services.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from polaris.kernelone.llm.toolkit import (
    ApplyReport,
    EditType,
    ErrorCode,
    FileOperation,
    OperationResult,
    StrictOperationApplier,
    apply_protocol_output,
    parse_protocol_output,
)

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class ApplyResult:
    """Task-facing summary for strict patch protocol application."""

    success: bool
    changed_files: list[str] = field(default_factory=list)
    failed_operations: list[tuple[FileOperation, str]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @classmethod
    def from_report(cls, report: ApplyReport) -> ApplyResult:
        """Project a KernelOne apply report into the Director tasking contract."""
        failed = [(result.operation, result.error_message) for result in report.results if not result.success]
        return cls(
            success=report.success,
            changed_files=report.changed_files,
            failed_operations=failed,
            errors=[result.error_message for result in report.results if result.error_message],
        )


@dataclass(slots=True)
class ApplyIntegrity:
    """Pre-apply validation result for tasking protocol output."""

    is_valid: bool
    can_continue: bool
    errors: list[str] = field(default_factory=list)
    parse_state: Any | None = None
    integrity: Any | None = None


def parse_delete_operations(text: str) -> list[FileOperation]:
    """Parse delete operations from tasking protocol text."""
    operations = parse_protocol_output(text)
    return [operation for operation in operations if operation.edit_type == EditType.DELETE]


def parse_search_replace_blocks(text: str) -> list[FileOperation]:
    """Parse search/replace operations from tasking protocol text."""
    operations = parse_protocol_output(text)
    return [operation for operation in operations if operation.edit_type == EditType.SEARCH_REPLACE]


def parse_full_file_blocks(text: str) -> list[FileOperation]:
    """Parse full-file create/replace operations from tasking protocol text."""
    operations = parse_protocol_output(text)
    return [operation for operation in operations if operation.edit_type in (EditType.FULL_FILE, EditType.CREATE)]


def parse_all_operations(text: str) -> list[FileOperation]:
    """Parse all supported tasking protocol operations."""
    return parse_protocol_output(text)


def apply_operation(operation: FileOperation, workspace: str) -> tuple[bool, str | None, bool]:
    """Apply one operation through KernelOne's strict operation applier.

    Returns:
        ``(ok, error, changed)`` where ``error`` is ``None`` on success.
    """
    result = StrictOperationApplier.apply(operation, workspace)
    error_msg = None if result.success else result.error_message
    return result.success, error_msg, result.changed


def apply_all_operations(text: str, workspace: str, *, verbose: bool = False) -> ApplyResult:
    """Parse and strictly apply all operations from response text."""
    report = apply_protocol_output(
        text,
        workspace,
        strict=True,
        allow_fuzzy_match=False,
    )

    if verbose and report.ops_failed > 0:
        logger.info("[director_tasking_patch_protocol] Failed operations: %s", report.ops_failed)
        for result in report.results:
            if not result.success:
                logger.info("  - %s: %s", result.operation.path, result.error_message)

    return ApplyResult.from_report(report)


def validate_before_apply(
    text: str,
    provider_metadata: dict[str, Any],
) -> ApplyIntegrity:
    """Validate protocol output before applying file operations.

    ``provider_metadata`` is accepted for future provider-specific integrity
    checks; current validation is intentionally deterministic and local.
    """
    del provider_metadata
    operations = parse_protocol_output(text)

    if not operations:
        return ApplyIntegrity(
            is_valid=False,
            can_continue=False,
            errors=["no_valid_operations"],
        )

    text_lower = text.lower()
    unclosed_blocks: list[str] = []

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


def apply_operations_strict(
    text: str,
    workspace: str,
    *,
    allow_fuzzy_match: bool = False,
) -> ApplyReport:
    """Strictly apply protocol output and return the full KernelOne report."""
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
    "EditType",
    "ErrorCode",
    "FileOperation",
    "OperationResult",
    "apply_all_operations",
    "apply_operation",
    "apply_operations_strict",
    "parse_all_operations",
    "parse_delete_operations",
    "parse_full_file_blocks",
    "parse_search_replace_blocks",
    "validate_before_apply",
]
