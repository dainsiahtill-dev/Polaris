"""Operation applier for the protocol module.

Applies file operations to the workspace filesystem.
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from typing import TYPE_CHECKING

from polaris.kernelone.llm.toolkit.protocol.constants import EditType, ErrorCode
from polaris.kernelone.llm.toolkit.protocol.models import FileOperation, OperationResult

if TYPE_CHECKING:
    from polaris.kernelone.fs import KernelFileSystem

logger = logging.getLogger(__name__)


class StrictOperationApplier:
    """Applies file operations with strict semantics.

    Strict mode: SEARCH not found / multiple matches = failure.
    """

    @classmethod
    def apply(
        cls,
        operation: FileOperation,
        workspace: str,
        allow_fuzzy_match: bool = False,
    ) -> OperationResult:
        """Apply an operation to the workspace.

        Args:
            operation: File operation to apply
            workspace: Workspace root directory
            allow_fuzzy_match: Allow fuzzy matching for SEARCH

        Returns:
            OperationResult with execution status
        """
        if operation.edit_type == EditType.DELETE:
            return cls._apply_delete(operation, workspace)
        elif operation.edit_type == EditType.SEARCH_REPLACE:
            return cls._apply_search_replace(operation, workspace, allow_fuzzy_match)
        else:
            return cls._apply_full_file(operation, workspace)

    @classmethod
    def _apply_delete(cls, operation: FileOperation, workspace: str) -> OperationResult:
        """Execute DELETE operation."""
        fs = cls._workspace_fs(workspace)
        try:
            rel = fs.to_workspace_relative_path(str(Path(workspace) / operation.path))
        except ValueError:
            return OperationResult(
                operation=operation,
                success=False,
                error_code=ErrorCode.PATH_TRAVERSAL,
                error_message="Path traversal detected",
            )

        # Check if file exists
        if not fs.workspace_exists(rel):
            return OperationResult(
                operation=operation,
                success=True,
                error_code=ErrorCode.NOOP,
                changed=False,
            )

        try:
            # Read old content for audit
            old_content = ""
            old_line_count = 0
            if fs.workspace_is_file(rel):
                old_content = fs.workspace_read_text(rel, encoding="utf-8")
                old_line_count = len(old_content.splitlines())

            fs.workspace_remove(rel, missing_ok=True)

            return OperationResult(
                operation=operation,
                success=True,
                error_code=ErrorCode.OK,
                changed=True,
                old_hash=hashlib.sha256(old_content.encode("utf-8")).hexdigest()[:16],
                old_line_count=old_line_count,
                new_line_count=0,
            )
        except (RuntimeError, ValueError) as e:
            return OperationResult(
                operation=operation,
                success=False,
                error_code=ErrorCode.PERMISSION_DENIED,
                error_message=f"Delete failed: {e}",
            )

    @classmethod
    def _apply_full_file(
        cls,
        operation: FileOperation,
        workspace: str,
    ) -> OperationResult:
        """Execute full file write."""
        fs = cls._workspace_fs(workspace)
        try:
            rel = fs.to_workspace_relative_path(str(Path(workspace) / operation.path))
        except ValueError:
            return OperationResult(
                operation=operation,
                success=False,
                error_code=ErrorCode.PATH_TRAVERSAL,
                error_message="Path traversal detected",
            )
        new_content = operation.replace or ""

        # Read old content for audit
        old_content = ""
        old_line_count = 0
        if fs.workspace_exists(rel) and fs.workspace_is_file(rel):
            old_content = fs.workspace_read_text(rel, encoding="utf-8")
            old_line_count = len(old_content.splitlines())

        # Content unchanged, skip
        if old_content == new_content:
            return OperationResult(
                operation=operation,
                success=True,
                error_code=ErrorCode.NOOP,
                changed=False,
                old_hash=hashlib.sha256(old_content.encode("utf-8")).hexdigest()[:16],
                new_hash=hashlib.sha256(new_content.encode("utf-8")).hexdigest()[:16],
            )

        # Write
        fs.workspace_write_text(rel, new_content, encoding="utf-8")

        return OperationResult(
            operation=operation,
            success=True,
            error_code=ErrorCode.OK,
            changed=True,
            old_hash=hashlib.sha256(old_content.encode("utf-8")).hexdigest()[:16],
            new_hash=hashlib.sha256(new_content.encode("utf-8")).hexdigest()[:16],
            old_line_count=old_line_count,
            new_line_count=len(new_content.splitlines()),
        )

    @classmethod
    def _apply_search_replace(
        cls,
        operation: FileOperation,
        workspace: str,
        allow_fuzzy_match: bool = False,
    ) -> OperationResult:
        """Execute SEARCH/REPLACE with strict semantics."""
        search = operation.search or ""
        replace = operation.replace or ""

        # Empty search = full file replace
        if search == "":
            return cls._apply_full_file(operation, workspace)

        fs = cls._workspace_fs(workspace)
        try:
            rel = fs.to_workspace_relative_path(str(Path(workspace) / operation.path))
        except ValueError:
            return OperationResult(
                operation=operation,
                success=False,
                error_code=ErrorCode.PATH_TRAVERSAL,
                error_message="Path traversal detected",
            )

        # Read current content
        current_content = ""
        if fs.workspace_exists(rel):
            try:
                current_content = fs.workspace_read_text(rel, encoding="utf-8")
            except UnicodeDecodeError:
                return OperationResult(
                    operation=operation,
                    success=False,
                    error_code=ErrorCode.BINARY_FILE,
                    error_message="File appears to be binary",
                )

        # Search match
        actual_search = search

        if search not in current_content:
            if allow_fuzzy_match:
                # Try native fuzzy replacement
                aider_content = cls._apply_native_fuzzy_replace(
                    current_content=current_content,
                    search=search,
                    replace=replace,
                )
                if aider_content is not None:
                    if aider_content == current_content:
                        return OperationResult(
                            operation=operation,
                            success=True,
                            error_code=ErrorCode.NOOP,
                            changed=False,
                        )
                    fs.workspace_write_text(rel, aider_content, encoding="utf-8")
                    return OperationResult(
                        operation=operation,
                        success=True,
                        error_code=ErrorCode.OK,
                        changed=True,
                        old_hash=hashlib.sha256(current_content.encode("utf-8")).hexdigest()[:16],
                        new_hash=hashlib.sha256(aider_content.encode("utf-8")).hexdigest()[:16],
                        old_line_count=len(current_content.splitlines()),
                        new_line_count=len(aider_content.splitlines()),
                    )

                # Try lightweight fuzzy matching
                fuzzy = cls._find_fuzzy_match(current_content, search)
                if fuzzy is None:
                    return OperationResult(
                        operation=operation,
                        success=False,
                        error_code=ErrorCode.SEARCH_NOT_FOUND,
                        error_message=f"Search text not found in {operation.path}",
                    )
                actual_search = fuzzy
            else:
                return OperationResult(
                    operation=operation,
                    success=False,
                    error_code=ErrorCode.SEARCH_NOT_FOUND,
                    error_message=f"Search text not found in {operation.path}",
                )

        # Check multiple matches
        occurrences = current_content.count(actual_search)
        if occurrences > 1:
            return OperationResult(
                operation=operation,
                success=False,
                error_code=ErrorCode.SEARCH_AMBIGUOUS,
                error_message=f"Ambiguous search: {occurrences} matches found in {operation.path}",
            )

        # Execute replacement
        new_content = current_content.replace(actual_search, replace, 1)

        if new_content == current_content:
            return OperationResult(
                operation=operation,
                success=True,
                error_code=ErrorCode.NOOP,
                changed=False,
            )

        # Write
        fs.workspace_write_text(rel, new_content, encoding="utf-8")

        return OperationResult(
            operation=operation,
            success=True,
            error_code=ErrorCode.OK,
            changed=True,
            old_hash=hashlib.sha256(current_content.encode("utf-8")).hexdigest()[:16],
            new_hash=hashlib.sha256(new_content.encode("utf-8")).hexdigest()[:16],
            old_line_count=len(current_content.splitlines()),
            new_line_count=len(new_content.splitlines()),
        )

    @staticmethod
    def _find_fuzzy_match(content: str, search: str) -> str | None:
        """Fuzzy match allowing whitespace differences."""
        if not search or not content:
            return None

        search_lines = search.splitlines()
        content_lines = content.splitlines()

        if not search_lines:
            return None

        first_line = search_lines[0].strip()
        if not first_line:
            return None

        # Find matching first line
        for i, line in enumerate(content_lines):
            if line.strip() != first_line:
                continue
            if i + len(search_lines) > len(content_lines):
                continue

            # Check subsequent lines
            match = True
            for j, search_line in enumerate(search_lines):
                if content_lines[i + j].strip() != search_line.strip():
                    match = False
                    break

            if match:
                return "\n".join(content_lines[i : i + len(search_lines)])

        return None

    @staticmethod
    def _apply_native_fuzzy_replace(
        *,
        current_content: str,
        search: str,
        replace: str,
    ) -> str | None:
        """Try Polaris-native fuzzy replacement engine.

        Must fail-open (return None) so strict behavior remains deterministic
        if the fuzzy engine is unavailable.
        """
        try:
            from polaris.kernelone.editing import apply_fuzzy_search_replace

            return apply_fuzzy_search_replace(
                content=current_content,
                search=search,
                replace=replace,
            )
        except (RuntimeError, ValueError) as exc:
            logger.debug("native fuzzy replace unavailable: %s", exc)
            return None

    @staticmethod
    def _workspace_fs(workspace: str) -> KernelFileSystem:
        """Get workspace filesystem."""
        from polaris.kernelone.fs import KernelFileSystem
        from polaris.kernelone.fs.registry import get_default_adapter

        return KernelFileSystem(str(Path(workspace).resolve()), get_default_adapter())
