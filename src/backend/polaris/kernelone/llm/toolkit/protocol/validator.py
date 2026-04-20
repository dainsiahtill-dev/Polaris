"""Operation validator for the protocol module.

Validates file operations against workspace constraints.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from polaris.kernelone.llm.toolkit.protocol.constants import EditType, ErrorCode
from polaris.kernelone.llm.toolkit.protocol.models import ValidationResult
from polaris.kernelone.llm.toolkit.protocol.path_utils import (
    _detect_path_traversal,
    _is_path_safe,
)

if TYPE_CHECKING:
    from polaris.kernelone.llm.toolkit.protocol.models import FileOperation

logger = logging.getLogger(__name__)


class OperationValidator:
    """Validates file operations against workspace constraints.

    Ensures:
    - Paths are within workspace
    - No path traversal attempts
    - Valid operation parameters
    """

    @classmethod
    def validate(cls, operation: FileOperation, workspace: str) -> ValidationResult:
        """Validate an operation.

        Args:
            operation: File operation to validate
            workspace: Workspace root directory

        Returns:
            ValidationResult with validation status
        """
        # Check for empty path
        if not operation.path or not operation.path.strip():
            return ValidationResult(
                valid=False,
                error_code=ErrorCode.INVALID_PATH,
                error_message="Empty path",
            )

        # Check path traversal
        if _detect_path_traversal(operation.path):
            return ValidationResult(
                valid=False,
                error_code=ErrorCode.PATH_TRAVERSAL,
                error_message=f"Path traversal detected: {operation.path}",
            )

        # Check path is within workspace
        is_safe, _result = _is_path_safe(workspace, operation.path)
        if not is_safe:
            return ValidationResult(
                valid=False,
                error_code=ErrorCode.PATH_OUTSIDE_WORKSPACE,
                error_message=f"Path outside workspace: {operation.path}",
                normalized_path=operation.path,
            )

        # Check move_to path if present
        if operation.move_to:
            if _detect_path_traversal(operation.move_to):
                return ValidationResult(
                    valid=False,
                    error_code=ErrorCode.PATH_TRAVERSAL,
                    error_message=f"Move-to path traversal detected: {operation.move_to}",
                )

            is_safe, _ = _is_path_safe(workspace, operation.move_to)
            if not is_safe:
                return ValidationResult(
                    valid=False,
                    error_code=ErrorCode.PATH_OUTSIDE_WORKSPACE,
                    error_message=f"Move-to path outside workspace: {operation.move_to}",
                    normalized_path=operation.path,
                    normalized_move_to=operation.move_to,
                )

        # Check operation-specific requirements
        if operation.edit_type == EditType.SEARCH_REPLACE:
            if not operation.search:
                return ValidationResult(
                    valid=False,
                    error_code=ErrorCode.INVALID_OPERATION,
                    error_message="SEARCH_REPLACE requires search text",
                    normalized_path=operation.path,
                )
            if operation.replace is None:
                return ValidationResult(
                    valid=False,
                    error_code=ErrorCode.EMPTY_OPERATION,
                    error_message="SEARCH_REPLACE requires replace text",
                    normalized_path=operation.path,
                )

        elif operation.edit_type in (EditType.FULL_FILE, EditType.CREATE):
            if operation.replace is None:
                return ValidationResult(
                    valid=False,
                    error_code=ErrorCode.EMPTY_OPERATION,
                    error_message=f"{operation.edit_type.name} requires content",
                    normalized_path=operation.path,
                )

        # All checks passed
        return ValidationResult(
            valid=True,
            error_code=ErrorCode.OK,
            normalized_path=operation.path,
            normalized_move_to=operation.move_to or "",
        )

    @classmethod
    def validate_batch(
        cls,
        operations: list[FileOperation],
        workspace: str,
    ) -> list[tuple[FileOperation, ValidationResult]]:
        """Validate a batch of operations.

        Args:
            operations: List of operations to validate
            workspace: Workspace root directory

        Returns:
            List of (operation, result) tuples
        """
        results = []
        for op in operations:
            result = cls.validate(op, workspace)
            results.append((op, result))
        return results
