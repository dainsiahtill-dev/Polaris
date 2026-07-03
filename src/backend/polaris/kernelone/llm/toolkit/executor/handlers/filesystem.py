"""Filesystem tool handlers.

Handles file operations: read_file, write_file, edit_file, search_replace, append_to_file.

This module is the canonical import path and the orchestrator for the
filesystem handler family. It owns ``register_handlers``, the six tool handler
bodies, the two ``edit_file`` mode helpers, and the FILE_WRITTEN event
emission. The lower layers live in sibling modules and are re-exported here so
that every previously-public AND privately-imported symbol still resolves from
``...handlers.filesystem``:

- ``filesystem_guards``    — pre/post-write content guards + post-write syntax gate
- ``filesystem_io``        — transactional write primitives + path resolution / did-you-mean
- ``filesystem_policy``    — Director write-policy gate (AGENTS.md scope reading/validation)
- ``filesystem_editblocks``— edit-block heuristics (whole-file/prefix replacement detection)
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import re
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Any

from polaris.kernelone.editing.editblock_engine import (
    parse_edit_blocks,
    validate_edit_blocks,
)
from polaris.kernelone.llm.toolkit.executor.handlers.filesystem_editblocks import (
    _JSON_EDIT_FILE_KEYS,
    _JSON_EDIT_REPLACE_KEYS,
    _coerce_line_no,
    _drop_final_content_line,
    _has_search_replace_markers,
    _has_sufficient_whole_file_prefix_evidence,
    _is_placeholder_search_text,
    _looks_like_complete_file_replacement,
    _normalize_block_input,
    _normalize_edit_block_text,
    _prefix_search_candidates,
    _should_use_whole_file_placeholder_replacement,
    _should_use_whole_file_prefix_replacement,
    _strip_eof_delimiter_newline,
    _synthesize_blocks_from_json_payload,
    _synthesize_blocks_from_update_markers,
    _synthesize_line_range_block,
    _synthesize_whole_file_replacement_block,
    _unwrap_weak_file_marker,
    _unwrap_weak_replace_marker,
)
from polaris.kernelone.llm.toolkit.executor.handlers.filesystem_guards import (
    _DESTRUCTIVE_SHRINK_MAX_ADD_RATIO,
    _DESTRUCTIVE_SHRINK_MIN_REMOVED_LINES,
    _EDIT_FRAGMENT_DIRECTIVE_RE,
    _EMPTY_WRITE_GUARD_EXTENSIONS,
    _EMPTY_WRITE_SENTINEL_BASENAMES,
    _destructive_shrink_error,
    _looks_like_output_truncation,
    _syntax_check_file,
    attach_post_write_syntax_check,
    is_blank_sentinel_write,
    is_edit_fragment_write_violation,
    is_empty_write_content_violation,
)
from polaris.kernelone.llm.toolkit.executor.handlers.filesystem_io import (
    _SUGGEST_BARE_NAME_MAX_DEPTH,
    _SUGGEST_MAX_FILES,
    _SUGGEST_MAX_RESULTS,
    _not_found_error,
    _resolve_case_variant_rel,
    _resolve_workspace_rel,
    _stage_temp_verify,
    _suggest_similar_paths,
    _write_temp_verify_rename,
)
from polaris.kernelone.llm.toolkit.executor.handlers.filesystem_policy import (
    _attach_director_policy_evidence,
    _coerce_policy_scope_list,
    _director_write_allowed_scope,
    _read_workspace_agents_policy_text,
    _validate_director_policy_for_write,
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
    from polaris.kernelone.fs import KernelFileSystem
    from polaris.kernelone.llm.toolkit.executor.core import AgentAccelToolExecutor

    _KernelFileSystem = KernelFileSystem

logger = logging.getLogger(__name__)

_SOURCE_NARRATION_LEAK_RE = re.compile(
    r"(?is)^\s*(?:"
    r"i(?:'|’)ll\s+|"
    r"i\s+will\s+|"
    r"let\s+me\s+|"
    r"here(?:'|’)s\s+|"
    r"here\s+is\s+|"
    r"below\s+is\s+|"
    r"(?:the\s+)?quality\s+repair\s+mode\s+requires\s+me\b|"
    r"the\s+(?:repair\s+)?directive\s+(?:is|says|said)\b|"
    r"the\s+override\s+(?:says|instruction)\b|"
    r"the\s+(?:task|instruction|requirement|requirements)\s+(?:is|are|says|said)\b|"
    r"the\s+(?:two\s+)?(?:problem|problems|issue|issues)\s+(?:are|is)\b|"
    r"i\s+(?:also\s+)?need\s+to\b|"
    r"for\s+[\w./-]+\.(?:py|js|ts|jsx|tsx|go|rs)\s+-\s+should\b|"
    r"this\s+file\s+(?:defines|contains|implements)\b|"
    r"我(?:会|将|来)|"
    r"让我|"
    r"下面(?:是|我)"
    r")"
)

_JS_TS_BLOCK_COMMENT_EXTENSIONS = {".js", ".jsx", ".ts", ".tsx"}
_BLOCK_COMMENT_GLOB_CLOSURE_RE = re.compile(r"\*\*/(?=[A-Za-z0-9_*.[{])")
_BLOCK_COMMENT_GLOB_FOLLOW_RE = re.compile(r"[A-Za-z0-9_*.[{]")


def _find_js_ts_block_comment_close(text: str, start: int = 0) -> int:
    close_index = text.find("*/", start)
    while close_index >= 0:
        next_char = text[close_index + 2 : close_index + 3]
        if (
            close_index > 0
            and text[close_index - 1] == "*"
            and next_char
            and _BLOCK_COMMENT_GLOB_FOLLOW_RE.match(next_char)
        ):
            close_index = text.find("*/", close_index + 2)
            continue
        return close_index
    return -1


def _sanitize_js_ts_block_comment_glob_closures(rel: str, text: str) -> tuple[str, bool]:
    """Keep glob examples in JS/TS block comments from closing the comment."""

    if Path(rel).suffix.lower() not in _JS_TS_BLOCK_COMMENT_EXTENSIONS:
        return text, False
    if "**/" not in text or "/*" not in text:
        return text, False

    changed = False
    in_block_comment = False
    sanitized_lines: list[str] = []
    for line in text.splitlines(keepends=True):
        cursor = 0
        pieces: list[str] = []
        while cursor < len(line):
            if not in_block_comment:
                block_start = line.find("/*", cursor)
                if block_start < 0:
                    pieces.append(line[cursor:])
                    cursor = len(line)
                    continue
                pieces.append(line[cursor : block_start + 2])
                cursor = block_start + 2
                in_block_comment = True

            close_index = _find_js_ts_block_comment_close(line, cursor)
            segment_end = close_index if close_index >= 0 else len(line)
            segment = line[cursor:segment_end]
            repaired = _BLOCK_COMMENT_GLOB_CLOSURE_RE.sub("** /", segment)
            changed = changed or repaired != segment
            pieces.append(repaired)
            if close_index < 0:
                cursor = len(line)
                continue
            pieces.append("*/")
            cursor = close_index + 2
            in_block_comment = False
        sanitized_lines.append("".join(pieces))
    return "".join(sanitized_lines), changed


def _source_narration_leak_error(rel: str, text: str) -> dict[str, Any] | None:
    code_extensions = {".py", ".pyw", ".js", ".ts", ".jsx", ".tsx", ".go", ".rs"}
    if Path(rel).suffix.lower() not in code_extensions:
        return None
    stripped = str(text or "").lstrip()
    if not stripped:
        return None
    first_line = stripped.splitlines()[0].strip()
    if first_line.startswith(("#", "//", "/*", "*", '"""', "'''")):
        return None
    if not _SOURCE_NARRATION_LEAK_RE.search(stripped[:500]):
        return None
    return {
        "ok": False,
        "error_type": "source_narration_contamination",
        "retryable": True,
        "loop_break": False,
        "error": (
            f"Source narration contamination: write_file for {rel} received assistant prose instead of code. "
            "The content argument must contain only the complete UTF-8 source file body."
        ),
        "suggestion": (
            "Retry write_file for the same path with real source code only. Do not include explanations, plans, "
            "phrases like 'Let me fix', markdown, or reasoning text in code files."
        ),
    }


__all__ = [
    "_DESTRUCTIVE_SHRINK_MAX_ADD_RATIO",
    "_DESTRUCTIVE_SHRINK_MIN_REMOVED_LINES",
    "_EDIT_FRAGMENT_DIRECTIVE_RE",
    "_EMPTY_WRITE_GUARD_EXTENSIONS",
    "_EMPTY_WRITE_SENTINEL_BASENAMES",
    "_JSON_EDIT_FILE_KEYS",
    "_JSON_EDIT_REPLACE_KEYS",
    "_SUGGEST_BARE_NAME_MAX_DEPTH",
    "_SUGGEST_MAX_FILES",
    "_SUGGEST_MAX_RESULTS",
    "_attach_director_policy_evidence",
    "_coerce_line_no",
    "_coerce_policy_scope_list",
    "_destructive_shrink_error",
    "_director_write_allowed_scope",
    "_drop_final_content_line",
    "_edit_file_line_mode",
    "_edit_file_replace_mode",
    "_emit_file_written_event",
    "_handle_append_to_file",
    "_handle_edit_blocks",
    "_handle_edit_file",
    "_handle_read_file",
    "_handle_search_replace",
    "_handle_write_file",
    "_has_search_replace_markers",
    "_has_sufficient_whole_file_prefix_evidence",
    "_is_placeholder_search_text",
    "_looks_like_complete_file_replacement",
    "_looks_like_output_truncation",
    "_normalize_block_input",
    "_normalize_edit_block_text",
    "_not_found_error",
    "_prefix_search_candidates",
    "_read_workspace_agents_policy_text",
    "_resolve_case_variant_rel",
    "_resolve_message_bus",
    "_resolve_workspace_rel",
    "_should_use_whole_file_placeholder_replacement",
    "_should_use_whole_file_prefix_replacement",
    "_stage_temp_verify",
    "_strip_eof_delimiter_newline",
    "_suggest_similar_paths",
    "_syntax_check_file",
    "_synthesize_blocks_from_json_payload",
    "_synthesize_line_range_block",
    "_validate_director_policy_for_write",
    "_write_temp_verify_rename",
    "attach_post_write_syntax_check",
    "is_blank_sentinel_write",
    "is_edit_fragment_write_violation",
    "is_empty_write_content_violation",
    "json",
    "register_handlers",
    "tempfile",
    "verify_written_code",
]


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

    try:
        target = resolve_workspace_path(self._kernel_fs, file_path)
    except ValueError as exc:
        if "UNSUPPORTED_PATH_PREFIX" in str(exc):
            return {
                "ok": False,
                "error": (
                    f"Unsupported absolute path: {file_path}. Use a WORKSPACE-RELATIVE path (e.g. 'subdir/module.py')."
                ),
            }
        raise
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

    # RC-B: redirect a case-variant duplicate onto the existing file so weak
    # models cannot fork ``App.jsx`` / ``app.jsx`` split-brain pairs.
    if not self._kernel_fs.workspace_exists(rel):
        case_variant = _resolve_case_variant_rel(self.workspace, rel)
        if case_variant is not None and case_variant != rel:
            logger.warning("write_file case-variant redirect: %s -> %s", rel, case_variant)
            rel = case_variant
            target = resolve_workspace_path(self._kernel_fs, rel)

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
        old_lines = old_content.count("\n") + (1 if old_content and not old_content.endswith("\n") else 0)
        new_text = str(content or "")
        new_lines = new_text.count("\n") + (1 if new_text and not new_text.endswith("\n") else 0)
        if (
            old_lines >= _DESTRUCTIVE_SHRINK_MIN_REMOVED_LINES
            and new_lines <= old_lines * _DESTRUCTIVE_SHRINK_MAX_ADD_RATIO
        ):
            return _destructive_shrink_error(
                file_path,
                old_lines,
                new_lines,
                tool_hint=(
                    "write_file replaces the WHOLE file. If the intent is a partial edit, emit a "
                    "precise range/search replacement with only the changed lines so untouched code "
                    "is preserved. If the intent is a whole-file rewrite, provide a complete file "
                    "body comparable in size to the original."
                ),
            )

    normalized = normalize_patch_like_write_content(
        rel,
        content,
        existing_content=old_content if operation == "modify" else None,
    )

    if normalized.error:
        return {"ok": False, "error": normalized.error}

    text = str(normalized.content or "")
    narration_error = _source_narration_leak_error(rel, text)
    if narration_error is not None:
        return narration_error
    if is_empty_write_content_violation(rel, text):
        return {
            "ok": False,
            "error_type": "empty_write_content",
            "error": (
                f"Empty write content: write_file for {rel} received blank content. "
                "The COMPLETE file body must go in the `content` argument — do not "
                "narrate it in prose or reasoning. Re-emit write_file with the full file content."
            ),
        }
    if is_edit_fragment_write_violation(rel, text):
        return {
            "ok": False,
            "error_type": "edit_fragment_write",
            "error": (
                f"Edit-fragment write: write_file for {rel} received an edit fragment "
                "(a line-anchored insertion directive such as '在第 N 行之后添加'), not the "
                "complete file. write_file REPLACES the whole file, so its `content` must be the "
                "ENTIRE file body. To change part of an existing file use edit_blocks/edit_file; "
                "to (re)write the file, emit write_file with the full, self-contained content."
            ),
        }
    text, block_comment_glob_sanitized = _sanitize_js_ts_block_comment_glob_closures(rel, text)
    policy_result = _validate_director_policy_for_write(
        self,
        rel=rel,
        old_content=old_content,
        new_content=text,
        operation=f"write_file:{operation}",
        tool_kwargs=kwargs,
    )
    if not policy_result.get("ok"):
        return policy_result

    # ========================================================================
    # PRE-WRITE VALIDATION GATE - Validate code syntax before writing
    # Auto-fix hallucinations if possible
    # ========================================================================
    # Only validate for code files (Python, JS, TS, etc.)
    code_extensions = {".py", ".pyw", ".js", ".ts", ".jsx", ".tsx", ".go", ".rs"}
    # A blank sentinel file (__init__.py / py.typed / .gitkeep) is legitimately
    # empty — the EmptyCode syntax check must NOT block it, or the package marker
    # never lands -> the materialization quality gate reports it "missing" -> the
    # Director burns its budget in a repair read-loop and dead-letters
    # (factory-bench L4-19: empty backend/__init__.py blocked -> 0/3 successes).
    # A NON-empty sentinel still validates normally. Mirrors the Wall-2 sentinel
    # exemption (is_empty_write_wall2_violation, _EMPTY_WRITE_SENTINEL_BASENAMES).
    if not is_blank_sentinel_write(rel, text) and any(rel.endswith(ext) for ext in code_extensions):
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
                "error_type": "syntax",
                "retryable": True,
                "loop_break": False,
                "error": f"Code syntax validation failed:\n{error_msg}",
                "suggestion": (
                    "Retry with write_file using the complete corrected UTF-8 file body for the same path. "
                    "If a later retry is allowed to use edit_blocks, use line-range arguments: "
                    "file, start, end, replace. Do not append new code for syntax repair."
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
    if block_comment_glob_sanitized:
        result["block_comment_glob_sanitized"] = True

    result = attach_post_write_syntax_check(result, str(target))
    return _attach_director_policy_evidence(result, policy_result.get("director_policy"))


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

    rel, resolve_error = _resolve_workspace_rel(self, str(target_path))
    if rel is None:
        return resolve_error or {"ok": False, "error": f"Invalid path: {target_path}"}

    if not self._kernel_fs.workspace_exists(rel) or not self._kernel_fs.workspace_is_file(rel):
        return _not_found_error(self, str(target_path))

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

    rel, resolve_error = _resolve_workspace_rel(self, file)
    if rel is None:
        return resolve_error or {"ok": False, "error": f"Invalid path: {file}"}

    if not self._kernel_fs.workspace_exists(rel):
        return _not_found_error(self, str(file))
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

    policy_result = _validate_director_policy_for_write(
        self,
        rel=rel,
        old_content=content,
        new_content=new_content,
        operation="search_replace",
        tool_kwargs=kwargs,
    )
    if not policy_result.get("ok"):
        return policy_result

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

    return _attach_director_policy_evidence(
        {
            "ok": True,
            "file": rel,
            "replacements_count": replacements,
            "effect_receipt": {
                "file": rel,
                "replacements_count": replacements,
                "operation": "modify",
            },
        },
        policy_result.get("director_policy"),
    )


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

    rel, resolve_error = _resolve_workspace_rel(self, file)
    if rel is None:
        return resolve_error or {"ok": False, "error": f"Invalid path: {file}"}

    if not self._kernel_fs.workspace_exists(rel):
        return _not_found_error(self, str(file))
    if not self._kernel_fs.workspace_is_file(rel):
        return {"ok": False, "error": f"Path is not a file: {file}"}

    try:
        file_content = self._kernel_fs.workspace_read_text(rel, encoding="utf-8")
        lines = file_content.splitlines(keepends=True)
    except (OSError, UnicodeDecodeError) as e:
        return {"ok": False, "error": f"Failed to read file: {e}"}

    # Line range mode
    if start_line is not None or end_line is not None:
        return _edit_file_line_mode(self, rel, lines, start_line, end_line, content or "", tool_kwargs=kwargs)

    # Text replace mode
    if search is not None:
        return _edit_file_replace_mode(self, rel, file_content, search, replace or "", regex, tool_kwargs=kwargs)

    return {"ok": False, "error": "Must specify either line range (start_line/end_line) or search/replace"}


def _handle_edit_blocks(self: AgentAccelToolExecutor, **kwargs) -> dict[str, Any]:
    """Handle edit_blocks tool call (SEARCH/REPLACE block format).

    Implements two-phase commit (validation + execution) for atomic multi-file edits.
    Also accepts a weak-model-friendly line-range form (file + start/end + replacement),
    and normalizes code-fenced / escaped-newline payloads.

    Args:
        self: Executor instance
        **kwargs: Tool arguments

    Returns:
        Execution result dict
    """
    file = (
        kwargs.get("file")
        or kwargs.get("path")
        or kwargs.get("file_path")
        or kwargs.get("filepath")
        or kwargs.get("filePath")
        or kwargs.get("target_file")
        or kwargs.get("target_path")
        or kwargs.get("targetFile")
        or kwargs.get("targetPath")
    )
    raw_blocks_value = kwargs.get("blocks") or kwargs.get("content") or kwargs.get("edits") or kwargs.get("diff")
    blocks_text = _normalize_block_input(raw_blocks_value)

    # Weak-model line-range affordance: when start/end line numbers are supplied and the
    # payload is not already a SEARCH/REPLACE block, synthesize one from the exact file
    # lines. This removes the hardest task for low-precision models — reproducing SEARCH
    # text byte-for-byte — while reusing the same validation/apply path below.
    start = kwargs.get("start", kwargs.get("start_line"))
    end = kwargs.get("end", kwargs.get("end_line"))
    if start is not None and end is not None and not _has_search_replace_markers(blocks_text):
        replacement = (
            kwargs.get("replace")
            if kwargs.get("replace") is not None
            else kwargs.get("new_text")
            if kwargs.get("new_text") is not None
            else kwargs.get("new_content")
            if kwargs.get("new_content") is not None
            else kwargs.get("newText")
            if kwargs.get("newText") is not None
            else kwargs.get("newContent")
            if kwargs.get("newContent") is not None
            else kwargs.get("new_code")
            if kwargs.get("new_code") is not None
            else kwargs.get("newCode")
            if kwargs.get("newCode") is not None
            else kwargs.get("replacement")
            if kwargs.get("replacement") is not None
            else kwargs.get("replacement_text")
            if kwargs.get("replacement_text") is not None
            else kwargs.get("replacementText")
            if kwargs.get("replacementText") is not None
            else kwargs.get("code")
            if kwargs.get("code") is not None
            else (blocks_text or None)
        )
        synth, err = _synthesize_line_range_block(self, file, start, end, replacement)
        if err is not None:
            return err
        blocks_text = synth or ""
    elif blocks_text and not _has_search_replace_markers(blocks_text):
        marker_file, marker_body = _unwrap_weak_file_marker(blocks_text, default_file=file)
        if marker_body is not None:
            rel, resolve_error = _resolve_workspace_rel(self, str(marker_file or file or ""))
            if rel is None:
                return resolve_error or {"ok": False, "error": f"Invalid path: {marker_file or file}"}
            if self._kernel_fs.workspace_exists(rel):
                whole_file_blocks, whole_file_err = _synthesize_whole_file_replacement_block(
                    self,
                    marker_file or file,
                    marker_body,
                    force=True,
                )
                if whole_file_err is not None:
                    return whole_file_err
                if whole_file_blocks is not None:
                    blocks_text = whole_file_blocks
            else:
                result = _handle_write_file(self, file=marker_file or file, content=marker_body)
                if result.get("ok"):
                    result["normalized_from_edit_blocks_file_marker"] = True
                return result

        replace_file, replace_body = _unwrap_weak_replace_marker(blocks_text, default_file=file)
        if replace_body is not None:
            whole_file_blocks, whole_file_err = _synthesize_whole_file_replacement_block(
                self,
                replace_file or file,
                replace_body,
                force=True,
            )
            if whole_file_err is not None:
                return whole_file_err
            if whole_file_blocks is not None:
                blocks_text = whole_file_blocks
        update_blocks = (
            None
            if _has_search_replace_markers(blocks_text)
            else _synthesize_blocks_from_update_markers(blocks_text, default_file=file)
        )
        if update_blocks is not None:
            blocks_text = update_blocks
        elif not _has_search_replace_markers(blocks_text):
            whole_file_blocks, whole_file_err = _synthesize_whole_file_replacement_block(self, file, blocks_text)
            if whole_file_err is not None:
                return whole_file_err
            if whole_file_blocks is not None:
                blocks_text = whole_file_blocks
            else:
                # JSON-in-blocks affordance: a structured line-range edit hiding inside
                # the blocks argument is normalized, not rejected as prose. Parse the
                # RAW value first — _normalize_block_input's escape repair corrupts
                # valid JSON (the \n inside quoted strings must stay escaped).
                json_blocks: str | None = None
                json_err: dict[str, Any] | None = None
                if isinstance(raw_blocks_value, str):
                    json_blocks, json_err = _synthesize_blocks_from_json_payload(
                        self, raw_blocks_value, default_file=file
                    )
                if json_blocks is None and json_err is None:
                    json_blocks, json_err = _synthesize_blocks_from_json_payload(self, blocks_text, default_file=file)
                if json_err is not None and "__unwrap_blocks__" in json_err:
                    blocks_text = _normalize_block_input(json_err["__unwrap_blocks__"])
                    file = json_err.get("__unwrap_file__") or file
                    update_blocks = _synthesize_blocks_from_update_markers(blocks_text, default_file=file)
                    if update_blocks is not None:
                        blocks_text = update_blocks
                    json_err = None
                if json_err is not None:
                    return json_err
                if json_blocks is not None:
                    blocks_text = json_blocks

    if not blocks_text or not isinstance(blocks_text, str):
        return {
            "ok": False,
            "error": (
                "Missing edit payload. EASIEST: call edit_blocks with file + start + end + replace "
                "(replace lines [start,end] with the new code). Alternatively provide SEARCH/REPLACE "
                "formatted blocks."
            ),
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
        # Weak-model teaching error: live capture (Qwen3.6, 2026-06-10) showed models
        # passing their narration ("Let me first read the file...") as 'blocks'. Echo
        # what was received and show BOTH complete forms — imprecise models imitate
        # concrete examples far better than they follow format descriptions.
        preview = " ".join(blocks_text.strip().split())[:120]
        target_missing = False
        if file:
            rel_probe, _probe_err = _resolve_workspace_rel(self, str(file))
            target_missing = rel_probe is not None and not self._kernel_fs.workspace_exists(rel_probe)
        if target_missing:
            # Factory-bench live capture: models try to CREATE new files through
            # edit_blocks by stuffing whole-file code into 'blocks'. There is
            # nothing to search/replace in a nonexistent file — teach the right
            # tool instead of sending them to read_file.
            error = f"edit_blocks cannot create new files ({file} does not exist). Use write_file to create it."
            return {
                "ok": False,
                "error": error,
                "suggestion": (
                    f'Call write_file with the COMPLETE file content: {{"file": "{file}", '
                    '"content": "<the full source code>"}}. edit_blocks is only for '
                    "modifying lines of EXISTING files."
                ),
                "error_type": "new_file_via_edit_blocks",
                "retryable": True,
            }
        # Filename-plus-fenced-content shape (factory-bench L1-01 README task):
        # blocks = "README.md ```markdown <full file>```" — unambiguous
        # whole-file-write intent through the wrong tool. Teach write_file with
        # the exact filename instead of the generic prose lecture.
        stripped_lines = [line.strip() for line in blocks_text.strip().splitlines() if line.strip()]
        leading_name = stripped_lines[0].split()[0].strip("*`'\"") if stripped_lines else ""
        looks_like_filename = bool(re.fullmatch(r"[\w./-]+\.[A-Za-z0-9]{1,8}", leading_name))
        if looks_like_filename and "```" in blocks_text:
            return {
                "ok": False,
                "error": (
                    f"edit_blocks received a filename plus full file content for {leading_name}. "
                    "That is a whole-file write, not an edit."
                ),
                "suggestion": (
                    f'Call write_file instead: {{"file": "{leading_name}", '
                    '"content": "<the full file content WITHOUT the ``` fence>"}}.'
                ),
                "error_type": "whole_file_via_edit_blocks",
                "retryable": True,
            }
        if not _has_search_replace_markers(blocks_text):
            error = (
                "edit_blocks received prose/narration instead of edit content "
                f"(got: '{preview}'). The 'blocks' argument must contain ONLY the edit itself — "
                "never explanations, plans, or intentions."
            )
            error_type = "prose_narration_in_edit_blocks"
        else:
            error = "No valid edit blocks found in input"
            error_type = "no_valid_edit_blocks"
        return {
            "ok": False,
            "error": error,
            "suggestion": (
                "Two accepted forms. LINE-RANGE (easiest): "
                '{"file": "path/to/file.py", "start": <first line>, "end": <last line>, '
                '"replace": "<the complete new code for those lines>"} — no SEARCH text needed. '
                "SEARCH/REPLACE: pass 'blocks' exactly like:\n"
                "<<<< SEARCH:path/to/file.py\n"
                "<exact existing lines copied from the file>\n"
                "====\n"
                "<new lines>\n"
                ">>>> REPLACE\n"
                "If you have not read the file yet, call read_file first, then transcribe the edit. "
                "Prose descriptions are NOT executable."
            ),
            "error_type": error_type,
            "retryable": True,
            "tool": "edit_blocks",
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

    rel, resolve_error = _resolve_workspace_rel(self, str(target_file))
    if rel is None:
        return resolve_error or {"ok": False, "error": f"Invalid path: {target_file}"}

    # Check file exists
    if not self._kernel_fs.workspace_exists(rel):
        return _not_found_error(self, str(target_file))

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
        block_rel, block_resolve_error = _resolve_workspace_rel(self, str(block_file))
        if block_rel is None:
            validation_results.append(
                {
                    "index": i,
                    "file": block_file,
                    "valid": False,
                    "error": str((block_resolve_error or {}).get("error", "Invalid path")),
                }
            )
            all_valid = False
            continue

        # Check file exists
        if not self._kernel_fs.workspace_exists(block_rel):
            validation_results.append(
                {
                    "index": i,
                    "file": block_file,
                    "valid": False,
                    "error": str(_not_found_error(self, str(block_file)).get("error", "File not found")),
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
        is_code_file = any(block_rel.endswith(ext) for ext in code_extensions)

        # A synthesized EOF line-range block carries a trailing delimiter newline
        # on its SEARCH/REPLACE bodies (so the divider/terminator parse); strip it
        # back so matching/writing uses the exact on-disk slice (no spurious newline).
        block_search, block_replace = _strip_eof_delimiter_newline(block.search_text, block.replace_text, current)
        new_content, metadata = fuzzy_replace(current, block_search, block_replace)

        if metadata.get("success"):
            new_content, block_comment_glob_sanitized = _sanitize_js_ts_block_comment_glob_closures(
                block_rel, new_content
            )
            # Validate the RESULTING file, not the replace fragment. A SEARCH/REPLACE
            # replacement is legitimately partial code (leading indentation, an open
            # construct closed by surrounding lines), so parsing it standalone raises
            # spurious "unexpected indent"/"unbalanced" errors that previously rejected
            # valid edits. Checking the full post-apply content still catches edits that
            # actually break file syntax.
            if is_code_file and block.replace_text:
                file_validation = validate_code_syntax(new_content, block_rel)
                # Gate on the EDIT's effect, not the file's pre-existing state: reject
                # only when this edit INTRODUCES a syntax error (pre-edit content was
                # valid, post-edit content is not). If the pre-edit file already failed
                # validation, the edit is not the cause, so blocking it would force a
                # hardcoded bypass. This keeps the gate fail-closed without rejecting
                # legitimate edits to files that merely trip a heuristic.
                introduced_error = not file_validation.is_valid and validate_code_syntax(current, block_rel).is_valid
                if introduced_error:
                    error_msg = format_validation_error(file_validation, block_rel)
                    validation_results.append(
                        {
                            "index": i,
                            "file": block_file,
                            "valid": False,
                            "error": f"Edit introduces syntax errors: {error_msg[:200]}",
                            "search_preview": block.search_text[:100] if block.search_text else "",
                        }
                    )
                    all_valid = False
                    continue
            # Update current content for next block targeting same file
            file_contents[block_rel] = (original, new_content)
            validation_results.append(
                {
                    "index": i,
                    "file": block_file,
                    "valid": True,
                    "similarity": metadata.get("similarity", 1.0),
                    "fixes": metadata.get("fixes_applied", []),
                    "block_comment_glob_sanitized": block_comment_glob_sanitized,
                }
            )
        elif _should_use_whole_file_placeholder_replacement(
            search_text=block.search_text,
            replace_text=block.replace_text,
            rel=block_rel,
            block_count=len(blocks),
        ):
            replacement_text, block_comment_glob_sanitized = _sanitize_js_ts_block_comment_glob_closures(
                block_rel, block.replace_text
            )
            file_contents[block_rel] = (original, replacement_text)
            validation_results.append(
                {
                    "index": i,
                    "file": block_file,
                    "valid": True,
                    "mode": "whole_file_placeholder_replacement",
                    "search_preview": block.search_text[:100] if block.search_text else "",
                    "block_comment_glob_sanitized": block_comment_glob_sanitized,
                }
            )
        elif _should_use_whole_file_prefix_replacement(
            current_text=current,
            search_text=block.search_text,
            replace_text=block.replace_text,
            rel=block_rel,
            block_count=len(blocks),
        ):
            replacement_text, block_comment_glob_sanitized = _sanitize_js_ts_block_comment_glob_closures(
                block_rel, block.replace_text
            )
            file_contents[block_rel] = (original, replacement_text)
            validation_results.append(
                {
                    "index": i,
                    "file": block_file,
                    "valid": True,
                    "mode": "whole_file_prefix_replacement",
                    "search_preview": block.search_text[:100] if block.search_text else "",
                    "block_comment_glob_sanitized": block_comment_glob_sanitized,
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

    policy_evidence_by_file: dict[str, dict[str, Any]] = {}
    policy_denials: list[dict[str, Any]] = []
    for block_rel, (original, new_content) in file_contents.items():
        if original == new_content:
            continue
        policy_result = _validate_director_policy_for_write(
            self,
            rel=block_rel,
            old_content=original,
            new_content=new_content,
            operation="edit_blocks",
            tool_kwargs=kwargs,
        )
        if policy_result.get("ok"):
            policy_evidence = policy_result.get("director_policy")
            if isinstance(policy_evidence, dict):
                policy_evidence_by_file[block_rel] = policy_evidence
            continue
        policy_denials.append(
            {
                "file": block_rel,
                "error": policy_result.get("error", "Director write policy denied"),
                "director_policy": policy_result.get("director_policy"),
            }
        )

    if policy_denials:
        return {
            "ok": False,
            "error": f"Director write policy denied {len(policy_denials)} file(s). No files were modified.",
            "error_type": "director_write_policy_denied",
            "blocked": True,
            "director_policy_denials": policy_denials,
            "validation_results": validation_results,
        }

    # ========================================================================
    # PHASE 2: EXECUTION - All blocks valid, now actually write files.
    #
    # Truly atomic across files: stage + verify EVERY target to a temp file
    # first, and only os.replace() them into place once they ALL verify. If any
    # target fails to stage/verify, remove all staged temps and commit nothing,
    # so a later-file failure never leaves earlier files half-applied (which the
    # previous per-file write-then-rename loop did, contradicting the two-phase
    # commit contract in this handler's docstring).
    # ========================================================================
    pending: list[tuple[str, str, str, str]] = []  # (block_rel, full_path, original, new_content)
    for block_rel, (original, new_content) in file_contents.items():
        if original == new_content:
            continue  # No changes needed
        block_full_path = str(self._kernel_fs.resolve_workspace_path(block_rel))
        pending.append((block_rel, block_full_path, original, new_content))

    staged: list[tuple[str, str, str, str, str]] = []  # (block_rel, tmp_path, full_path, original, new_content)
    write_errors: list[dict[str, Any]] = []
    for block_rel, block_full_path, original, new_content in pending:
        stage_result = _stage_temp_verify(block_full_path, new_content, encoding="utf-8")
        if not stage_result.get("ok"):
            write_errors.append(
                {
                    "file": block_rel,
                    "error": str(stage_result.get("error", "Unknown write error")),
                }
            )
            break
        staged.append((block_rel, str(stage_result["tmp_path"]), block_full_path, original, new_content))

    if write_errors:
        # Abort the whole commit: discard every staged temp, touch no target file.
        for _block_rel, tmp_path, _full_path, _original, _new_content in staged:
            with contextlib.suppress(OSError):
                os.remove(tmp_path)
        return {
            "ok": False,
            "blocks_total": len(blocks),
            "blocks_applied": len([r for r in validation_results if r.get("valid")]),
            "files_modified": 0,
            "results": [],
            "validation_results": validation_results,
            "write_errors": write_errors,
            "error": (
                f"Failed to write {len(write_errors)} file(s). No files were modified "
                "(atomic multi-file commit aborted)."
            ),
        }

    # All targets verified — commit them.
    results: list[dict[str, Any]] = []
    for block_rel, tmp_path, block_full_path, original, new_content in staged:
        try:
            os.replace(tmp_path, block_full_path)
        except OSError as exc:
            # Best-effort cleanup of the temp; this commit-time failure is rare
            # (e.g. target became a directory) and cannot be rolled back safely.
            with contextlib.suppress(OSError):
                os.remove(tmp_path)
            write_errors.append({"file": block_rel, "error": f"Failed to commit file: {exc}"})
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
                "director_policy": policy_evidence_by_file.get(block_rel),
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
    else:
        response["effect_receipt"] = {
            "files_modified": [r["file"] for r in results],
            "operation": "modify",
            "director_policy": policy_evidence_by_file,
        }

    return response


def _edit_file_line_mode(
    self: AgentAccelToolExecutor,
    rel: str,
    lines: list[str],
    start_line: int | None,
    end_line: int | None,
    content: str,
    *,
    tool_kwargs: dict[str, Any] | None = None,
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
    if end_line is not None:
        end = min(total_lines, end_line)
    elif start_line is not None:
        # Single-bound call (start without end) means a single-line edit, NOT
        # "replace from start to EOF". Defaulting end to total_lines silently
        # truncated every line from start to the end of the file. Reserve the
        # whole-tail replacement for the explicit start_line-is-None case below.
        end = min(total_lines, start)
    else:
        end = total_lines

    new_lines = [*lines, content] if start > total_lines else [*lines[: start - 1], content, *lines[end:]]

    new_content = "".join(new_lines)
    old_content = "".join(lines)
    policy_result = _validate_director_policy_for_write(
        self,
        rel=rel,
        old_content=old_content,
        new_content=new_content,
        operation="edit_file:line_range",
        tool_kwargs=tool_kwargs,
    )
    if not policy_result.get("ok"):
        return policy_result

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

    return _attach_director_policy_evidence(
        {
            "ok": True,
            "file": rel,
            "mode": "line_range",
            "lines_affected": end - start + 1 if end >= start else 0,
            "effect_receipt": {
                "file": rel,
                "mode": "line_range",
                "operation": "modify",
            },
        },
        policy_result.get("director_policy"),
    )


def _edit_file_replace_mode(
    self: AgentAccelToolExecutor,
    rel: str,
    content: str,
    search: str,
    replace: str,
    regex: bool,
    *,
    tool_kwargs: dict[str, Any] | None = None,
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

    policy_result = _validate_director_policy_for_write(
        self,
        rel=rel,
        old_content=content,
        new_content=new_content,
        operation="edit_file:text_replace",
        tool_kwargs=tool_kwargs,
    )
    if not policy_result.get("ok"):
        return policy_result

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

    return _attach_director_policy_evidence(result, policy_result.get("director_policy"))


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

    rel, resolve_error = _resolve_workspace_rel(self, file)
    if rel is None:
        return resolve_error or {"ok": False, "error": f"Invalid path: {file}"}
    content_text = str(content) if content is not None else ""

    # File doesn't exist
    if not self._kernel_fs.workspace_exists(rel):
        if not create_if_missing:
            return _not_found_error(self, str(file))
        policy_result = _validate_director_policy_for_write(
            self,
            rel=rel,
            old_content="",
            new_content=content_text,
            operation="append_to_file:create",
            tool_kwargs=kwargs,
        )
        if not policy_result.get("ok"):
            return policy_result
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
        return _attach_director_policy_evidence(
            {
                "ok": True,
                "file": rel,
                "bytes_appended": len(content_text.encode("utf-8")),
                "created": True,
                "effect_receipt": {
                    "file": rel,
                    "bytes_appended": len(content_text.encode("utf-8")),
                    "operation": "create",
                },
            },
            policy_result.get("director_policy"),
        )

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
    policy_result = _validate_director_policy_for_write(
        self,
        rel=rel,
        old_content=old_content,
        new_content=new_content,
        operation="append_to_file:modify",
        tool_kwargs=kwargs,
    )
    if not policy_result.get("ok"):
        return policy_result

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

    return _attach_director_policy_evidence(
        {
            "ok": True,
            "file": rel,
            "bytes_appended": len(content_text.encode("utf-8")),
            "created": False,
            "effect_receipt": {
                "file": rel,
                "bytes_appended": len(content_text.encode("utf-8")),
                "operation": "modify",
            },
        },
        policy_result.get("director_policy"),
    )


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
            event_log_workspace=self.workspace,
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
