"""Protocol module - Split from protocol_kernel.py (1113 lines).

This module provides unified file operation protocol parsing and execution.
The original protocol_kernel.py has been split into the following structure:

protocol/
    __init__.py       # Unified exports and convenience functions
    constants.py      # ErrorCode, EditType enums
    models.py         # FileOperation, ValidationResult, OperationResult, ApplyReport
    path_utils.py     # Path safety utilities
    parser.py         # ProtocolParser
    validator.py      # OperationValidator
    applier.py        # StrictOperationApplier
"""

from __future__ import annotations

import hashlib

from polaris.kernelone.llm.toolkit.protocol.applier import StrictOperationApplier

# Re-export constants
from polaris.kernelone.llm.toolkit.protocol.constants import (
    EditType,
    ErrorCode,
)

# Re-export models
from polaris.kernelone.llm.toolkit.protocol.models import (
    ApplyReport,
    FileOperation,
    OperationResult,
    ValidationResult,
    _normalize_path,
)

# Re-export core classes
from polaris.kernelone.llm.toolkit.protocol.parser import ProtocolParser

# Re-export path utilities (including internal functions for backwards compatibility)
from polaris.kernelone.llm.toolkit.protocol.path_utils import (
    _detect_path_traversal,
    _is_path_safe,
)
from polaris.kernelone.llm.toolkit.protocol.validator import OperationValidator

__all__ = [
    "ApplyReport",
    "EditType",
    # Constants
    "ErrorCode",
    # Models
    "FileOperation",
    "OperationResult",
    "OperationValidator",
    # Core classes
    "ProtocolParser",
    "StrictOperationApplier",
    "ValidationResult",
    # Path utilities (internal)
    "_detect_path_traversal",
    "_is_path_safe",
    "_normalize_path",  # Internal model helper
]


# ============================================================================
# Convenience functions (moved from protocol_kernel.py)
# ============================================================================


def parse_protocol_output(text: str) -> list[FileOperation]:
    """Parse protocol output, returning unified IR list.

    Args:
        text: LLM output raw text

    Returns:
        FileOperation list (deduplicated)
    """
    return ProtocolParser.parse(text)


def validate_operations(
    operations: list[FileOperation], workspace: str
) -> list[tuple[FileOperation, ValidationResult]]:
    """Batch validate operations.

    Args:
        operations: List of operations
        workspace: Workspace path

    Returns:
        List of (operation, result) tuples
    """
    results = []
    for op in operations:
        result = OperationValidator.validate(op, workspace)
        results.append((op, result))
    return results


def apply_operations(
    operations: list[FileOperation],
    workspace: str,
    *,
    strict: bool = True,
    allow_fuzzy_match: bool = False,
) -> ApplyReport:
    """Batch apply operations, generating complete report.

    Args:
        operations: List of operations
        workspace: Workspace path
        strict: Strict mode (stop on first error)
        allow_fuzzy_match: Allow fuzzy matching

    Returns:
        ApplyReport complete report
    """
    report = ApplyReport(success=True, ops_total=len(operations))

    for operation in operations:
        result = StrictOperationApplier.apply(operation, workspace, allow_fuzzy_match=allow_fuzzy_match)
        report.results.append(result)

        if result.success:
            if result.changed:
                report.ops_applied += 1
                if operation.path not in report.changed_files:
                    report.changed_files.append(operation.path)
            else:
                report.ops_skipped += 1
        else:
            report.ops_failed += 1
            if result.error_code not in report.error_codes:
                report.error_codes.append(result.error_code)

        # Strict mode: stop on first error
        if strict and not result.success:
            report.success = False
            break

    # Final success check
    if report.ops_failed > 0:
        report.success = False

    return report


def apply_protocol_output(
    text: str,
    workspace: str,
    *,
    strict: bool = True,
    allow_fuzzy_match: bool = False,
) -> ApplyReport:
    """One-stop protocol application: Parse -> Validate -> Apply.

    Args:
        text: LLM output raw text
        workspace: Workspace path
        strict: Strict mode
        allow_fuzzy_match: Allow fuzzy matching

    Returns:
        ApplyReport complete report
    """
    # 1. Parse
    operations = parse_protocol_output(text)
    if not operations:
        operations = _route_rich_edit_operations(text, workspace)

    if not operations:
        return ApplyReport(
            success=False,
            ops_total=0,
            error_codes=[ErrorCode.EMPTY_OPERATION],
        )

    # 2. Apply
    report = apply_operations(
        operations,
        workspace,
        strict=strict,
        allow_fuzzy_match=allow_fuzzy_match,
    )

    # 3. Record original input hash for audit
    report.original_text_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]

    return report


def _route_rich_edit_operations(text: str, workspace: str) -> list[FileOperation]:
    """Route rich edit operations."""
    try:
        from polaris.kernelone.editing import route_edit_operations
    except (ImportError, AttributeError) as exc:
        logger.debug("route_edit_operations not available: %s", exc)
        return []

    root = _list_workspace_relative_files(workspace)
    routed = route_edit_operations(text, inchat_files=root)
    operations: list[FileOperation] = []

    for op in routed:
        if not op.path:
            continue
        if op.kind == "delete":
            operations.append(FileOperation(path=op.path, edit_type=EditType.DELETE))
        elif op.kind == "create":
            operations.append(
                FileOperation(
                    path=op.path,
                    edit_type=EditType.CREATE,
                    replace=op.content,
                    move_to=op.move_to or None,
                )
            )
        elif op.kind == "full_file":
            operations.append(
                FileOperation(
                    path=op.path,
                    edit_type=EditType.FULL_FILE,
                    replace=op.content,
                    move_to=op.move_to or None,
                )
            )
        elif op.kind == "search_replace":
            operations.append(
                FileOperation(
                    path=op.path,
                    edit_type=EditType.SEARCH_REPLACE,
                    search=op.search,
                    replace=op.replace,
                    move_to=op.move_to or None,
                )
            )

    return operations


def _list_workspace_relative_files(workspace: str, *, max_files: int = 5000) -> list[str]:
    """List workspace relative file paths."""
    root = _Path(workspace).resolve()
    rels: list[str] = []
    try:
        for p in root.rglob("*"):
            if not p.is_file():
                continue
            rels.append(p.relative_to(root).as_posix())
            if len(rels) >= max_files:
                break
    except (RuntimeError, ValueError) as exc:
        logger.debug("workspace file listing failed: %s", exc)
    return rels


# For type hints
from pathlib import Path as _Path  # noqa: E402

logger = __import__("logging").getLogger(__name__)
