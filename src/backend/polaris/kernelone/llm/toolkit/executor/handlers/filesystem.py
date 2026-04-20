"""Filesystem tool handlers.

Handles file operations: read_file, write_file, edit_file, search_replace, append_to_file.
"""

from __future__ import annotations

import contextlib
import logging
import os
import re
import tempfile
from typing import TYPE_CHECKING, Any

from polaris.kernelone.editing.editblock_engine import (
    parse_edit_blocks,
    validate_edit_blocks,
)
from polaris.kernelone.llm.toolkit.executor.utils import (
    BudgetExceededError,
    get_budget_remaining_lines,
    resolve_workspace_path,
    to_workspace_relative_path,
)
from polaris.kernelone.tool_execution.code_validator import (
    format_validation_error,
    validate_code_syntax,
    verify_written_code,
)
from polaris.kernelone.tool_execution.suggestions.precise_matcher import fuzzy_replace

if TYPE_CHECKING:
    from polaris.kernelone.llm.toolkit.executor.core import AgentAccelToolExecutor

logger = logging.getLogger(__name__)


def register_handlers() -> dict[str, Any]:
    """Return a dict of handler names to handler methods.

    This is used by the executor core to register all filesystem handlers.
    """
    return {
        "write_file": _handle_write_file,
        "read_file": _handle_read_file,
        "edit_file": _handle_edit_file,
        "edit_blocks": _handle_edit_blocks,
        "search_replace": _handle_search_replace,
        "append_to_file": _handle_append_to_file,
    }


def _write_temp_verify_rename(
    target_path: str,
    content: str,
    *,
    encoding: str = "utf-8",
) -> dict[str, Any]:
    """Transactional write: temp -> verify -> atomic rename.

    Returns:
        {"ok": True, "bytes_written": int} on success.
        {"ok": False, "error": str} on failure (original file untouched).
    """
    parent = os.path.dirname(target_path)
    if parent:
        os.makedirs(parent, exist_ok=True)

    suffix = f".{os.path.basename(target_path)}.tmp"
    tmp_path = ""
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding=encoding,
            suffix=suffix,
            dir=parent or ".",
            delete=False,
        ) as tmp:
            tmp.write(content)
            tmp_path = tmp.name

        verify_result = verify_written_code(tmp_path, content)
        if not verify_result.success:
            with contextlib.suppress(OSError):
                os.remove(tmp_path)
            return {
                "ok": False,
                "error": f"Post-write verification failed: {verify_result.error}",
            }

        os.replace(tmp_path, target_path)
        return {"ok": True, "bytes_written": len(content.encode(encoding, errors="replace"))}
    except OSError as exc:
        if tmp_path:
            with contextlib.suppress(OSError):
                os.remove(tmp_path)
        return {"ok": False, "error": f"Failed to write file: {exc}"}


def _handle_write_file(self: AgentAccelToolExecutor, **kwargs) -> dict[str, Any]:
    """Handle write_file tool call.

    Args:
        self: Executor instance
        **kwargs: Tool arguments

    Returns:
        Execution result dict
    """
    from polaris.kernelone.llm.toolkit.tool_normalization import (
        normalize_patch_like_write_content,
    )

    file = kwargs.get("file")
    path = kwargs.get("path")
    filepath = kwargs.get("filepath")
    content = kwargs.get("content", "")
    encoding = kwargs.get("encoding", "utf-8")

    target_path = file or path or filepath
    if not target_path:
        return {"ok": False, "error": "Missing file path"}

    if str(encoding or "utf-8").lower() != "utf-8":
        return {"ok": False, "error": "Only utf-8 encoding is supported"}

    file_path = str(target_path).strip()
    if not file_path:
        return {"ok": False, "error": "Missing file path"}

    if "\n" in file_path or "\r" in file_path:
        return {"ok": False, "error": f"Invalid file path contains newline: {file_path!r}"}

    if re.match(r"^(table|index)\s+if\s+not\s+exists\b", file_path, re.IGNORECASE):
        return {"ok": False, "error": f"Invalid file path resembles SQL statement: {file_path}"}

    target = resolve_workspace_path(self._kernel_fs, file_path)
    allowed_extensionless = {
        "makefile",
        "dockerfile",
        "readme",
        "gitignore",
        "gitattributes",
        "dockerignore",
        "env",
        "editorconfig",
        "prettierrc",
        "eslintrc",
        "bashrc",
        "zshrc",
        "profile",
        "toml",
        "ini",
    }
    # Strip leading dot for comparison (e.g., ".gitignore" -> "gitignore")
    target_name_lower = target.name.lower().lstrip(".")
    if not target.suffix and target_name_lower not in allowed_extensionless:
        return {"ok": False, "error": f"Invalid file path missing extension: {file_path}"}

    rel = to_workspace_relative_path(self._kernel_fs, target)

    old_content = ""
    operation = "create"

    if self._kernel_fs.workspace_exists(rel):
        if not self._kernel_fs.workspace_is_file(rel):
            return {"ok": False, "error": f"Path is not a file: {file_path}"}
        operation = "modify"
        try:
            old_content = self._kernel_fs.workspace_read_text(rel, encoding="utf-8")
        except UnicodeDecodeError:
            try:
                old_content = self._kernel_fs.workspace_read_bytes(rel).decode("utf-8", errors="replace")
            except OSError:
                old_content = ""
        except OSError:
            old_content = ""

    normalized = normalize_patch_like_write_content(
        rel,
        content,
        existing_content=old_content if operation == "modify" else None,
    )

    if normalized.error:
        return {"ok": False, "error": normalized.error}

    text = str(normalized.content or "")

    # ========================================================================
    # PRE-WRITE VALIDATION GATE - Validate code syntax before writing
    # Auto-fix hallucinations if possible
    # ========================================================================
    # Only validate for code files (Python, JS, TS, etc.)
    code_extensions = {".py", ".pyw", ".js", ".ts", ".jsx", ".tsx", ".go", ".rs"}
    if any(rel.endswith(ext) for ext in code_extensions):
        validation_result = validate_code_syntax(text, rel)
        if not validation_result.is_valid:
            error_msg = format_validation_error(validation_result, rel)
            logger.warning(
                "[PreWriteGuard] Blocked write to %s due to syntax errors: %s",
                rel,
                error_msg[:200],
            )
            return {
                "ok": False,
                "error": f"Code syntax validation failed:\n{error_msg}",
                "suggestion": (
                    "Use read_file() to copy the EXACT content from the source file, "
                    "then modify only the specific parts you need to change. "
                    "Pay special attention to indentation (use 4 spaces) and "
                    "make sure keywords like 'return' are followed by a space."
                ),
                "validation_errors": [
                    {"line": e.line, "column": e.column, "message": e.message} for e in (validation_result.errors or [])
                ],
            }
        # Auto-fix: use fixed code if validation result contains fixes
        if validation_result.fixed_code is not None:
            original_text = text
            text = validation_result.fixed_code
            logger.info(
                "[PreWriteGuard] Auto-fixed hallucinations in %s: %s ->\n%s",
                rel,
                original_text[:100],
                text[:100],
            )

    full_path = str(self._kernel_fs.resolve_workspace_path(rel))
    write_result = _write_temp_verify_rename(full_path, text, encoding="utf-8")
    if not write_result.get("ok"):
        return write_result

    _emit_file_written_event(
        self,
        file_path=rel,
        operation=operation,
        old_content=old_content,
        new_content=text,
    )

    result = {
        "ok": True,
        "file": rel,
        "bytes_written": int(write_result.get("bytes_written", 0)),
        "effect_receipt": {
            "file": rel,
            "bytes_written": int(write_result.get("bytes_written", 0)),
            "operation": operation,
        },
    }
    if normalized.normalized_patch_like:
        result["normalized_patch_like_write"] = True

    return result


def _handle_read_file(self: AgentAccelToolExecutor, **kwargs) -> dict[str, Any]:
    """Handle read_file with budget-aware downgrade strategy.

    Args:
        self: Executor instance
        **kwargs: Tool arguments

    Returns:
        Execution result dict or raises BudgetExceededError
    """
    file = kwargs.get("file")
    path = kwargs.get("path")
    filepath = kwargs.get("filepath")
    max_bytes = kwargs.get("max_bytes", 200000)
    start_line = kwargs.get("start_line")
    end_line = kwargs.get("end_line")
    range_required = kwargs.get("range_required", False)

    try:
        normalized_start_line = int(start_line) if start_line is not None else None
    except (TypeError, ValueError):
        return {"ok": False, "error": "start_line must be an integer"}

    try:
        normalized_end_line = int(end_line) if end_line is not None else None
    except (TypeError, ValueError):
        return {"ok": False, "error": "end_line must be an integer"}

    target_path = file or path or filepath
    if not target_path:
        return {"ok": False, "error": "Missing file path"}

    target = resolve_workspace_path(self._kernel_fs, str(target_path))
    rel = to_workspace_relative_path(self._kernel_fs, target)

    if not self._kernel_fs.workspace_exists(rel) or not self._kernel_fs.workspace_is_file(rel):
        return {
            "ok": False,
            "error": f"File not found: {target_path}",
            "suggestion": "Use repo_tree() or repo_rg() to explore workspace structure first. Do not assume files exist - always verify with exploration tools.",
        }

    # First pass: read raw bytes for size estimation
    safe_max_bytes = max(1024, min(int(max_bytes), 2_000_000))
    raw = self._kernel_fs.workspace_read_bytes(rel)

    # Estimate line count
    estimated_line_count = raw.count(b"\n") + 1

    # Budget check
    get_budget_remaining_lines(self._budget_state)
    has_range = normalized_start_line is not None or normalized_end_line is not None

    # Handle range_required enforcement
    if range_required and estimated_line_count > self._READ_WARN_LINES and not has_range:
        raise BudgetExceededError(
            f"read_file requires a range parameter for files >{self._READ_WARN_LINES} lines "
            f"(this file has ~{estimated_line_count} lines).",
            tool="read_file",
            file=rel,
            line_count=estimated_line_count,
            limit=self._READ_WARN_LINES,
            suggestion=(
                f"Use repo_read_head(file='{rel}', n=50) to read the first 50 lines, "
                f"or repo_read_slice(file='{rel}', start=1, end=200) to read a specific range. "
                f"Always specify start_line and end_line for large files."
            ),
        )

    requested_line_span: int | None = None
    if has_range:
        effective_start = max(1, normalized_start_line or 1)
        if normalized_end_line is not None:
            effective_end = max(effective_start, normalized_end_line)
            requested_line_span = max(1, effective_end - effective_start + 1)
        else:
            requested_line_span = max(1, estimated_line_count - effective_start + 1)

    # Hard limit check:
    # - full-file reads are blocked for oversized files
    # - ranged reads are allowed only when requested span is within hard limit
    if estimated_line_count > self._READ_HARD_LIMIT and (
        not has_range or (requested_line_span is not None and requested_line_span > self._READ_HARD_LIMIT)
    ):
        if has_range and requested_line_span is not None:
            message = (
                f"Requested range spans {requested_line_span} lines, exceeds hard limit of "
                f"{self._READ_HARD_LIMIT} for oversized file ({estimated_line_count} lines)"
            )
            suggestion = (
                f"Narrow the requested range to <= {self._READ_HARD_LIMIT} lines. "
                f"Example: repo_read_head(file='{rel}', n=50) or "
                f"repo_read_slice(file='{rel}', start=1, end=200)."
            )
        else:
            message = f"File has {estimated_line_count} lines, exceeds hard limit of {self._READ_HARD_LIMIT}"
            suggestion = (
                f"File is too large to read at once. Use repo_read_head(file='{rel}', n=50) to read the first 50 lines, "
                f"or repo_read_slice(file='{rel}', start=1, end=200) to read a specific range. "
                f"For large files, always specify start_line and end_line parameters."
            )
        raise BudgetExceededError(
            message,
            tool="read_file",
            file=rel,
            line_count=estimated_line_count,
            limit=self._READ_HARD_LIMIT,
            suggestion=suggestion,
        )

    # Decode and apply line range
    content_str = raw.decode("utf-8", errors="replace")
    lines = content_str.splitlines(keepends=True)

    truncated_by_range = False
    if has_range:
        total_lines = len(lines)
        start_idx = max(0, (normalized_start_line - 1) if normalized_start_line else 0)
        end_idx = min(total_lines, normalized_end_line if normalized_end_line else total_lines)
        start_idx = max(0, min(start_idx, total_lines - 1))
        end_idx = max(start_idx + 1, min(end_idx, total_lines))

        if start_idx > 0 or end_idx < total_lines:
            truncated_by_range = True
            lines = lines[start_idx:end_idx]
            actual_line_count = len(lines)
        else:
            actual_line_count = len(lines)
    else:
        actual_line_count = len(lines)

    start_offset = start_idx if has_range else 0
    formatted_lines = []
    for i, line_content in enumerate(lines):
        line_num = start_offset + 1 + i
        formatted_lines.append(f"{line_num} | {line_content}")

    content_str = "".join(formatted_lines)
    truncated = len(content_str.encode("utf-8")) > safe_max_bytes
    if truncated:
        content_str = content_str[:safe_max_bytes]

    result: dict[str, Any] = {
        "ok": True,
        "file": rel,
        "content": content_str,
        "truncated": truncated or truncated_by_range,
        "line_count": actual_line_count,
    }

    if has_range:
        result["range_used"] = {"start_line": normalized_start_line, "end_line": normalized_end_line}
    if range_required:
        result["range_required"] = True

    # Warning for large files without range
    if not has_range and estimated_line_count > self._READ_WARN_LINES:
        result["warnings"] = [
            f"Large file ({estimated_line_count} lines). "
            f"For targeted reading, use read_file with start_line and end_line parameters."
        ]

    return result


def _handle_search_replace(self: AgentAccelToolExecutor, **kwargs) -> dict[str, Any]:
    """Handle search_replace tool call.

    Args:
        self: Executor instance
        **kwargs: Tool arguments

    Returns:
        Execution result dict
    """
    import re

    file = kwargs.get("file")
    search = kwargs.get("search")
    replace = kwargs.get("replace", "")
    regex = kwargs.get("regex", False)
    replace_all = kwargs.get("replace_all", False)

    if not file or not isinstance(file, str):
        return {"ok": False, "error": "Missing or invalid file path"}
    if search is None:
        return {"ok": False, "error": "Missing search parameter"}

    target = resolve_workspace_path(self._kernel_fs, file)
    rel = to_workspace_relative_path(self._kernel_fs, target)

    if not self._kernel_fs.workspace_exists(rel):
        return {
            "ok": False,
            "error": f"File not found: {file}",
            "suggestion": "Use repo_tree() or repo_rg() to explore workspace structure first. Do not assume files exist - always verify with exploration tools.",
        }
    if not self._kernel_fs.workspace_is_file(rel):
        return {"ok": False, "error": f"Path is not a file: {file}"}

    try:
        content = self._kernel_fs.workspace_read_text(rel, encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        return {"ok": False, "error": f"Failed to read file: {e}"}

    search_text = str(search)
    replace_text = str(replace) if replace is not None else ""

    if regex:
        try:
            if replace_all:
                new_content = re.sub(search_text, replace_text, content)
                replacements = len(re.findall(search_text, content))
            else:
                new_content, replacements = re.subn(search_text, replace_text, content, count=1)
        except re.error as e:
            return {"ok": False, "error": f"Invalid regex pattern: {e}"}
    elif replace_all:
        replacements = content.count(search_text)
        new_content = content.replace(search_text, replace_text)
    elif search_text in content:
        new_content = content.replace(search_text, replace_text, 1)
        replacements = 1
    else:
        new_content = content
        replacements = 0

    if replacements == 0:
        from polaris.kernelone.tool_execution.suggestions.fuzzy import (
            _build_no_match_suggestion,
        )

        suggestion = _build_no_match_suggestion(content, search_text)
        return {
            "ok": False,
            "file": file,
            "replacements_count": 0,
            "error": "No matches found",
            "suggestion": suggestion,
        }

    full_path = str(self._kernel_fs.resolve_workspace_path(rel))
    write_result = _write_temp_verify_rename(full_path, new_content, encoding="utf-8")
    if not write_result.get("ok"):
        return write_result

    _emit_file_written_event(
        self,
        file_path=rel,
        operation="modify",
        old_content=content,
        new_content=new_content,
    )

    return {
        "ok": True,
        "file": rel,
        "replacements_count": replacements,
        "effect_receipt": {
            "file": rel,
            "replacements_count": replacements,
            "operation": "modify",
        },
    }


def _handle_edit_file(self: AgentAccelToolExecutor, **kwargs) -> dict[str, Any]:
    """Handle edit_file tool call (line range or text replace mode).

    Args:
        self: Executor instance
        **kwargs: Tool arguments

    Returns:
        Execution result dict
    """
    file = kwargs.get("file")
    start_line = kwargs.get("start_line")
    end_line = kwargs.get("end_line")
    content = kwargs.get("content")
    search = kwargs.get("search")
    replace = kwargs.get("replace")
    regex = kwargs.get("regex", False)

    if not file or not isinstance(file, str):
        return {"ok": False, "error": "Missing or invalid file path"}

    target = resolve_workspace_path(self._kernel_fs, file)
    rel = to_workspace_relative_path(self._kernel_fs, target)

    if not self._kernel_fs.workspace_exists(rel):
        return {
            "ok": False,
            "error": f"File not found: {file}",
            "suggestion": "Use repo_tree() or repo_rg() to explore workspace structure first. Do not assume files exist - always verify with exploration tools.",
        }
    if not self._kernel_fs.workspace_is_file(rel):
        return {"ok": False, "error": f"Path is not a file: {file}"}

    try:
        file_content = self._kernel_fs.workspace_read_text(rel, encoding="utf-8")
        lines = file_content.splitlines(keepends=True)
    except (OSError, UnicodeDecodeError) as e:
        return {"ok": False, "error": f"Failed to read file: {e}"}

    # Line range mode
    if start_line is not None or end_line is not None:
        return _edit_file_line_mode(self, rel, lines, start_line, end_line, content or "")

    # Text replace mode
    if search is not None:
        return _edit_file_replace_mode(self, rel, file_content, search, replace or "", regex)

    return {"ok": False, "error": "Must specify either line range (start_line/end_line) or search/replace"}


def _handle_edit_blocks(self: AgentAccelToolExecutor, **kwargs) -> dict[str, Any]:
    """Handle edit_blocks tool call (SEARCH/REPLACE block format).

    Implements two-phase commit (validation + execution) for atomic multi-file edits.

    Args:
        self: Executor instance
        **kwargs: Tool arguments

    Returns:
        Execution result dict
    """
    file = kwargs.get("file")
    blocks_text = kwargs.get("blocks") or kwargs.get("content") or kwargs.get("edits")

    if not blocks_text or not isinstance(blocks_text, str):
        return {
            "ok": False,
            "error": "Missing or invalid blocks parameter. Expected SEARCH/REPLACE formatted blocks.",
        }

    # Parse edit blocks (with file argument as default_filepath fallback)
    try:
        blocks = parse_edit_blocks(blocks_text, default_filepath=file)
    except (ValueError, TypeError, AttributeError, KeyError, IndexError) as e:
        logger.warning("Failed to parse edit blocks: %s (%s)", type(e).__name__, e)
        return {
            "ok": False,
            "error": f"Failed to parse edit blocks: {e}",
            "suggestion": "Ensure blocks follow the format: <<<< SEARCH[:filepath]\\n<original>\\n====\\n<new>\\n>>>> REPLACE",
        }

    if not blocks:
        return {
            "ok": False,
            "error": "No valid edit blocks found in input",
            "suggestion": "Check your SEARCH/REPLACE format. Example:\n<<<< SEARCH:file.py\\ndef old():\\n    pass\\n====\\ndef new():\\n    return 42\\n>>>> REPLACE",
        }

    # Validate blocks
    validation_errors = validate_edit_blocks(blocks)
    if validation_errors:
        return {
            "ok": False,
            "error": f"Invalid edit blocks: {'; '.join(validation_errors)}",
        }

    # Filter out no-op blocks (search == replace — LLM hallucination pattern)
    noop_count = sum(1 for b in blocks if b.search_text == b.replace_text)
    if noop_count:
        blocks = [b for b in blocks if b.search_text != b.replace_text]
        logger.info("Filtered %d no-op edit blocks (search == replace)", noop_count)

    if not blocks:
        return {
            "ok": False,
            "error": f"All {noop_count} edit block(s) had identical search and replace text (no-op). "
            "This usually means the content was not actually modified. "
            "Please ensure the REPLACE section contains the actual changes you want to make.",
        }

    # Determine file from blocks or args
    target_file = file
    if not target_file and blocks:
        # Use first block's filepath
        target_file = blocks[0].filepath

    if not target_file:
        return {
            "ok": False,
            "error": "No file path specified. Either provide 'file' argument or specify path in SEARCH header (<<<< SEARCH:path/to/file)",
        }

    target = resolve_workspace_path(self._kernel_fs, str(target_file))
    rel = to_workspace_relative_path(self._kernel_fs, target)

    # Check file exists
    if not self._kernel_fs.workspace_exists(rel):
        return {
            "ok": False,
            "error": f"File not found: {target_file}",
            "suggestion": "Use repo_tree() or repo_rg() to explore workspace structure first. Do not assume files exist - always verify with exploration tools.",
        }

    if not self._kernel_fs.workspace_is_file(rel):
        return {"ok": False, "error": f"Path is not a file: {target_file}"}

    # ========================================================================
    # PHASE 1: VALIDATION - Dry run all blocks to ensure they can be applied
    # ========================================================================
    file_contents: dict[str, tuple[str, str]] = {}  # rel -> (original_content, new_content)
    validation_results = []
    all_valid = True

    for i, block in enumerate(blocks):
        block_file = block.filepath or target_file
        block_target = resolve_workspace_path(self._kernel_fs, block_file)
        block_rel = to_workspace_relative_path(self._kernel_fs, block_target)

        # Check file exists
        if not self._kernel_fs.workspace_exists(block_rel):
            validation_results.append(
                {
                    "index": i,
                    "file": block_file,
                    "valid": False,
                    "error": "File not found",
                }
            )
            all_valid = False
            continue

        if not self._kernel_fs.workspace_is_file(block_rel):
            validation_results.append(
                {
                    "index": i,
                    "file": block_file,
                    "valid": False,
                    "error": "Path is not a file",
                }
            )
            all_valid = False
            continue

        # Read content (or use cached)
        if block_rel not in file_contents:
            try:
                content = self._kernel_fs.workspace_read_text(block_rel, encoding="utf-8")
                file_contents[block_rel] = (content, content)  # (original, current)
            except (OSError, UnicodeDecodeError) as e:
                validation_results.append(
                    {
                        "index": i,
                        "file": block_file,
                        "valid": False,
                        "error": f"Failed to read: {e}",
                    }
                )
                all_valid = False
                continue

        # Try to apply block (dry run)
        original, current = file_contents[block_rel]

        # ========================================================================
        # PRE-REPLACE VALIDATION - Validate replacement text syntax
        # Auto-fix hallucinations if possible
        # ========================================================================
        code_extensions = {".py", ".pyw", ".js", ".ts", ".jsx", ".tsx", ".go", ".rs"}
        if any(block_rel.endswith(ext) for ext in code_extensions) and block.replace_text:
            replace_validation = validate_code_syntax(block.replace_text, block_rel)
            if not replace_validation.is_valid:
                error_msg = format_validation_error(replace_validation, block_rel)
                validation_results.append(
                    {
                        "index": i,
                        "file": block_file,
                        "valid": False,
                        "error": f"Replacement text has syntax errors: {error_msg[:200]}",
                        "search_preview": block.search_text[:100] if block.search_text else "",
                    }
                )
                all_valid = False
                continue
            # Auto-fix: use fixed replacement text if validation result contains fixes
            if replace_validation.fixed_code is not None:
                block.replace_text = replace_validation.fixed_code
                logger.info(
                    "[PreWriteGuard] Auto-fixed hallucinations in replacement text for %s",
                    block_rel,
                )

        new_content, metadata = fuzzy_replace(current, block.search_text, block.replace_text)

        if metadata.get("success"):
            # Update current content for next block targeting same file
            file_contents[block_rel] = (original, new_content)
            validation_results.append(
                {
                    "index": i,
                    "file": block_file,
                    "valid": True,
                    "similarity": metadata.get("similarity", 1.0),
                    "fixes": metadata.get("fixes_applied", []),
                }
            )
        else:
            validation_results.append(
                {
                    "index": i,
                    "file": block_file,
                    "valid": False,
                    "error": "No match found",
                    "search_preview": block.search_text[:100] if block.search_text else "",
                }
            )
            all_valid = False

    # If validation failed, return error without modifying any files
    if not all_valid:
        failed = [r for r in validation_results if not r["valid"]]
        return {
            "ok": False,
            "error": f"Validation failed for {len(failed)} block(s). No files were modified.",
            "failed_blocks": failed,
            "suggestion": "Check that SEARCH text exactly matches file content (including whitespace). Use repo_read_slice to verify exact content.",
        }

    # ========================================================================
    # PHASE 2: EXECUTION - All blocks valid, now actually write files
    # ========================================================================
    results = []
    write_errors = []

    for block_rel, (original, new_content) in file_contents.items():
        if original == new_content:
            continue  # No changes needed

        block_full_path = str(self._kernel_fs.resolve_workspace_path(block_rel))
        write_result = _write_temp_verify_rename(block_full_path, new_content, encoding="utf-8")
        if not write_result.get("ok"):
            write_errors.append(
                {
                    "file": block_rel,
                    "error": str(write_result.get("error", "Unknown write error")),
                }
            )
            continue

        _emit_file_written_event(
            self,
            file_path=block_rel,
            operation="modify",
            old_content=original,
            new_content=new_content,
        )
        results.append(
            {
                "file": block_rel,
                "bytes_changed": len(new_content) - len(original),
            }
        )

    # Build response
    response: dict[str, Any] = {
        "ok": len(write_errors) == 0,
        "blocks_total": len(blocks),
        "blocks_applied": len([r for r in validation_results if r.get("valid")]),
        "files_modified": len(results),
        "results": results,
        "validation_results": validation_results,
    }

    if write_errors:
        response["write_errors"] = write_errors
        response["error"] = f"Failed to write {len(write_errors)} file(s)"
        # Note: At this point, some files may have been modified while others failed
        # This is a partial failure scenario that should be handled by the caller
    else:
        response["effect_receipt"] = {
            "files_modified": [r["file"] for r in results],
            "operation": "modify",
        }

    return response


def _edit_file_line_mode(
    self: AgentAccelToolExecutor,
    rel: str,
    lines: list[str],
    start_line: int | None,
    end_line: int | None,
    content: str,
) -> dict[str, Any]:
    """Execute line range edit mode."""
    total_lines = len(lines)

    # Handle negative line numbers
    if start_line is not None and start_line < 0:
        start_line = total_lines + start_line + 1
    if end_line is not None and end_line < 0:
        end_line = total_lines + end_line + 1

    # Default: replace entire file or append to end
    start = max(1, start_line) if start_line is not None else 1
    end = min(total_lines, end_line) if end_line is not None else total_lines

    new_lines = [*lines, content] if start > total_lines else [*lines[: start - 1], content, *lines[end:]]

    new_content = "".join(new_lines)
    old_content = "".join(lines)

    full_path = str(self._kernel_fs.resolve_workspace_path(rel))
    write_result = _write_temp_verify_rename(full_path, new_content, encoding="utf-8")
    if not write_result.get("ok"):
        return write_result

    _emit_file_written_event(
        self,
        file_path=rel,
        operation="modify",
        old_content=old_content,
        new_content=new_content,
    )

    return {
        "ok": True,
        "file": rel,
        "mode": "line_range",
        "lines_affected": end - start + 1 if end >= start else 0,
        "effect_receipt": {
            "file": rel,
            "mode": "line_range",
            "operation": "modify",
        },
    }


def _edit_file_replace_mode(
    self: AgentAccelToolExecutor,
    rel: str,
    content: str,
    search: str,
    replace: str,
    regex: bool,
) -> dict[str, Any]:
    """Execute text replace edit mode with fuzzy matching fallback."""
    import re

    count = 0
    new_content = content
    fuzzy_metadata = None

    if regex:
        try:
            new_content, count = re.subn(search, replace, content, count=1)
        except re.error as e:
            return {"ok": False, "error": f"Invalid regex pattern: {e}"}
    elif search in content:
        # Exact match
        new_content = content.replace(search, replace, 1)
        count = 1
    else:
        # Try fuzzy matching to handle LLM character-level hallucinations
        # e.g., 'return0' -> 'return 0', wrong indentation, etc.
        new_content, fuzzy_metadata = fuzzy_replace(content, search, replace)
        if fuzzy_metadata.get("success"):
            count = 1
            logger.info(
                "[FuzzyReplace] Applied fuzzy match: similarity=%.2f, fixes=%s",
                fuzzy_metadata.get("similarity", 0),
                fuzzy_metadata.get("fixes_applied", []),
            )

    if count == 0:
        from polaris.kernelone.tool_execution.suggestions.fuzzy import (
            _build_no_match_suggestion,
        )

        suggestion = _build_no_match_suggestion(content, search)
        return {
            "ok": False,
            "file": rel,
            "replacements_count": 0,
            "error": "No matches found",
            "suggestion": suggestion,
        }

    full_path = str(self._kernel_fs.resolve_workspace_path(rel))
    write_result = _write_temp_verify_rename(full_path, new_content, encoding="utf-8")
    if not write_result.get("ok"):
        return write_result

    result: dict[str, Any] = {
        "ok": True,
        "file": rel,
        "mode": "text_replace",
        "replacements_count": count,
        "effect_receipt": {
            "file": rel,
            "mode": "text_replace",
            "replacements_count": count,
            "operation": "modify",
        },
    }

    # Include fuzzy matching info if used
    if fuzzy_metadata and not fuzzy_metadata.get("exact", True):
        result["fuzzy_match"] = {
            "similarity": fuzzy_metadata.get("similarity"),
            "fixes_applied": fuzzy_metadata.get("fixes_applied"),
            "original_matched": fuzzy_metadata.get("original_matched"),
        }

    _emit_file_written_event(
        self,
        file_path=rel,
        operation="modify",
        old_content=content,
        new_content=new_content,
    )

    return result


def _handle_append_to_file(self: AgentAccelToolExecutor, **kwargs) -> dict[str, Any]:
    """Handle append_to_file tool call.

    Args:
        self: Executor instance
        **kwargs: Tool arguments

    Returns:
        Execution result dict
    """
    file = kwargs.get("file")
    content = kwargs.get("content", "")
    ensure_newline = kwargs.get("ensure_newline", True)
    create_if_missing = kwargs.get("create_if_missing", True)

    if not file or not isinstance(file, str):
        return {"ok": False, "error": "Missing or invalid file path"}

    target = resolve_workspace_path(self._kernel_fs, file)
    rel = to_workspace_relative_path(self._kernel_fs, target)
    content_text = str(content) if content is not None else ""

    # File doesn't exist
    if not self._kernel_fs.workspace_exists(rel):
        if not create_if_missing:
            return {
                "ok": False,
                "error": f"File not found: {file}",
                "suggestion": "Use repo_tree() or repo_rg() to explore workspace structure first. Do not assume files exist - always verify with exploration tools.",
            }
        full_path = str(self._kernel_fs.resolve_workspace_path(rel))
        write_result = _write_temp_verify_rename(full_path, content_text, encoding="utf-8")
        if not write_result.get("ok"):
            return write_result
        _emit_file_written_event(
            self,
            file_path=rel,
            operation="create",
            old_content="",
            new_content=content_text,
        )
        return {
            "ok": True,
            "file": rel,
            "bytes_appended": len(content_text.encode("utf-8")),
            "created": True,
            "effect_receipt": {
                "file": rel,
                "bytes_appended": len(content_text.encode("utf-8")),
                "operation": "create",
            },
        }

    if not self._kernel_fs.workspace_is_file(rel):
        return {"ok": False, "error": f"Path is not a file: {file}"}

    try:
        existing_content = self._kernel_fs.workspace_read_text(rel, encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        return {"ok": False, "error": f"Failed to read file: {e}"}

    old_content = existing_content
    if ensure_newline and existing_content and not existing_content.endswith("\n"):
        existing_content += "\n"

    new_content = existing_content + content_text

    full_path = str(self._kernel_fs.resolve_workspace_path(rel))
    write_result = _write_temp_verify_rename(full_path, new_content, encoding="utf-8")
    if not write_result.get("ok"):
        return write_result

    _emit_file_written_event(
        self,
        file_path=rel,
        operation="modify",
        old_content=old_content,
        new_content=new_content,
    )

    return {
        "ok": True,
        "file": rel,
        "bytes_appended": len(content_text.encode("utf-8")),
        "created": False,
        "effect_receipt": {
            "file": rel,
            "bytes_appended": len(content_text.encode("utf-8")),
            "operation": "modify",
        },
    }


def _emit_file_written_event(
    self: AgentAccelToolExecutor,
    *,
    file_path: str,
    operation: str,
    old_content: str,
    new_content: str,
) -> None:
    """Emit FILE_WRITTEN event for observer diff projection."""
    bus = _resolve_message_bus(self)
    if bus is None:
        return

    try:
        from polaris.kernelone.events.file_event_broadcaster import (
            broadcast_file_written,
            calculate_patch,
        )

        normalized_path = str(file_path or "").strip().replace("\\", "/")
        if not normalized_path:
            return
        op = str(operation or "modify").strip().lower()
        if op not in {"create", "modify", "delete"}:
            op = "modify"
        old_text = str(old_content or "")
        new_text = str(new_content or "")
        patch = calculate_patch(old_text, new_text)
        broadcast_file_written(
            file_path=normalized_path,
            operation=op,
            content_size=len(new_text),
            task_id="",
            patch=patch,
            message_bus=bus,
            worker_id=self._worker_id,
        )
    except (ImportError, AttributeError, TypeError) as exc:
        logger.debug("file edit event emit failed for %s: %s", file_path, exc)


def _resolve_message_bus(self: AgentAccelToolExecutor) -> Any | None:
    """Resolve message bus from global registry."""
    if self._message_bus is not None:
        return self._message_bus

    from polaris.kernelone.events import get_global_bus

    bus = get_global_bus()
    if bus is not None:
        self._message_bus = bus
        return bus
    return None
