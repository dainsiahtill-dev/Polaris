"""File apply service for WorkerExecutor.

This module contains all file I/O operations, including writing files with broadcast,
collecting workspace files, and applying response operations.

All text operations MUST explicitly use UTF-8 encoding.
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any

logger = logging.getLogger(__name__)


_FENCED_FILE_BLOCK_RE = re.compile(
    r"```file:\s*([^\r\n`]+)\r?\n(.*?)```",
    re.DOTALL | re.IGNORECASE,
)
_FENCED_FILE_HEADER_RE = re.compile(r"^```file:\s*([^`\r\n]+?)\s*$", re.IGNORECASE)
_FENCE_CLOSE_RE = re.compile(r"^```\s*$")


def _next_nonempty_line_index(lines: list[str], start_index: int) -> int | None:
    for index in range(start_index, len(lines)):
        if lines[index].strip():
            return index
    return None


def _is_nested_markdown_fence_open(line: str) -> bool:
    stripped = line.strip()
    if not stripped.startswith("```"):
        return False
    if _FENCE_CLOSE_RE.match(stripped):
        return False
    return not stripped.lower().startswith("```file:")


def _escape_nested_markdown_fences_for_protocol(path: str, content: str) -> str:
    lower_path = path.lower()
    if not lower_path.endswith((".md", ".mdx")):
        return content
    escaped_lines: list[str] = []
    for line in content.split("\n"):
        stripped = line.lstrip()
        indent = line[: len(line) - len(stripped)]
        if stripped.startswith("```") and not stripped.lower().startswith("```file:"):
            escaped_lines.append(f"{indent}~~~{stripped[3:]}")
        else:
            escaped_lines.append(line)
    return "\n".join(escaped_lines)


def _normalize_fenced_file_blocks(response: str) -> str:
    """Convert ```file: path fences into protocol FILE blocks.

    The Director proposal bridge asks models for fenced file sections because
    several providers reliably produce that shape. The protocol apply kernel
    remains the single validation/apply path, so we normalize into its native
    FILE/END FILE syntax instead of writing these blocks directly.
    """

    text = str(response or "")
    if "```file:" not in text.lower():
        return text

    lines = text.splitlines()
    output: list[str] = []
    index = 0
    changed = False
    while index < len(lines):
        header = _FENCED_FILE_HEADER_RE.match(lines[index].strip())
        if header is None:
            output.append(lines[index])
            index += 1
            continue

        changed = True
        path = header.group(1).strip()
        index += 1
        nested_fence_depth = 0
        content_lines: list[str] = []
        while index < len(lines):
            current_line = lines[index]
            current_header = _FENCED_FILE_HEADER_RE.match(current_line.strip())
            if current_header is not None:
                break
            if _is_nested_markdown_fence_open(current_line):
                nested_fence_depth += 1
                content_lines.append(current_line)
                index += 1
                continue
            if _FENCE_CLOSE_RE.match(current_line.strip()):
                if nested_fence_depth > 0:
                    nested_fence_depth -= 1
                    content_lines.append(current_line)
                    index += 1
                    continue
                next_index = _next_nonempty_line_index(lines, index + 1)
                if next_index is None or _FENCED_FILE_HEADER_RE.match(lines[next_index].strip()) is not None:
                    index += 1
                    break
            content_lines.append(current_line)
            index += 1

        content = "\n".join(content_lines).strip("\r\n")
        content = _escape_nested_markdown_fences_for_protocol(path, content)
        output.append(f"FILE: {path}\n{content}\nEND FILE")

    if changed:
        return "\n".join(output)

    def _replace(match: re.Match[str]) -> str:
        path = match.group(1).strip()
        content = match.group(2).strip("\r\n")
        return f"FILE: {path}\n{content}\nEND FILE"

    return _FENCED_FILE_BLOCK_RE.sub(_replace, text)


class FileApplyService:
    """Service for file operations with broadcast and diff tracking.

    Responsibilities:
    - Write files with broadcast events
    - Collect files from workspace
    - Apply patch/file operations from LLM responses
    - Track diff statistics
    """

    def __init__(
        self,
        workspace: str,
        message_bus: Any | None = None,
        worker_id: str = "",
    ) -> None:
        self.workspace = workspace
        self._bus = message_bus
        self._worker_id = worker_id

    # === File Writing ===

    def write_files(self, files: list[dict], task_id: str = "") -> list[dict]:
        """Write generated files to workspace with broadcast support.

        Args:
            files: List of file dictionaries with 'path' and 'content' keys
            task_id: Optional task ID for tracking

        Returns:
            List of successfully written file dictionaries
        """
        files_created: list[dict] = []

        # Import here to avoid circular dependencies
        from polaris.kernelone.events.file_event_broadcaster import write_file_with_broadcast

        for file_info in files:
            file_path = str(file_info.get("path") or "").strip()
            content = str(file_info.get("content") or "")
            if not file_path or not content:
                continue
            try:
                # Use unified broadcast-enabled write
                result = write_file_with_broadcast(
                    workspace=self.workspace,
                    file_path=file_path,
                    content=content,
                    message_bus=self._bus,
                    worker_id=self._worker_id,
                    task_id=task_id,
                )
                if result.get("ok"):
                    files_created.append({"path": file_path, "content": content})
                    logger.debug("Created: %s", file_path)
            except OSError as exc:
                logger.warning("Skip file '%s': %s", file_path, exc)
        return files_created

    # === File Collection ===

    def collect_workspace_files(self, paths: list[str], task_id: str = "", operation: str = "modify") -> list[dict]:
        """Collect file payloads from workspace after patch/apply execution.

        Args:
            paths: List of relative file paths to collect
            task_id: Optional task ID for tracking
            operation: Operation type for broadcast event ('modify', 'create', 'delete')

        Returns:
            List of file dictionaries with 'path' and 'content' keys
        """
        files_created: list[dict] = []
        seen: set[str] = set()

        # Import here to avoid circular dependencies
        from polaris.kernelone.events.file_event_broadcaster import broadcast_file_written

        for raw_path in paths:
            path = str(raw_path or "").strip()
            if not path or path in seen:
                continue
            seen.add(path)
            full_path = os.path.join(self.workspace, path)
            if os.path.isfile(full_path):
                try:
                    with open(full_path, encoding="utf-8") as handle:
                        content = handle.read()
                except OSError as e:
                    logger.debug(f"Failed to read file {full_path}: {e}")
                    content = ""
                files_created.append({"path": path, "content": content})
                # Broadcast file edit event for patch/apply operations
                broadcast_file_written(
                    file_path=path,
                    operation=operation,
                    content_size=len(content),
                    task_id=task_id,
                    message_bus=self._bus,
                    worker_id=self._worker_id,
                )
            else:
                files_created.append({"path": path, "content": "", "deleted": True})
                # Broadcast deletion event
                broadcast_file_written(
                    file_path=path,
                    operation="delete",
                    content_size=0,
                    task_id=task_id,
                    message_bus=self._bus,
                    worker_id=self._worker_id,
                )
        return files_created

    # === Response Operations ===

    def apply_response_operations(
        self,
        response: str,
        task_id: str = "",
        llm_metadata: dict[str, Any] | None = None,
    ) -> tuple[list[dict], list[str]]:
        """Apply patch/file operations from LLM response with pre-apply validation.

        Uses protocol_kernel v2.0 with strict mode (no fallback to full file).

        Args:
            response: LLM response text
            task_id: Optional task ID for tracking
            llm_metadata: Optional metadata from LLM (may contain truncation info)

        Returns:
            Tuple of (applied_files, errors)
        """
        # Import here to avoid circular dependencies
        from polaris.kernelone.llm.toolkit import apply_protocol_output

        normalized_response = _normalize_fenced_file_blocks(response)

        # Phase 1: Pre-apply integrity validation
        if llm_metadata:
            # Import from public contract (execution.public.service re-exports it)
            from polaris.cells.director.execution.public.service import validate_before_apply

            integrity = validate_before_apply(normalized_response, llm_metadata)

            if not integrity.is_valid:
                if integrity.can_continue:
                    # Truncated but can try continuation
                    return [], [f"TRUNCATED: {integrity.errors[0]}"]
                else:
                    # Fail-closed: block the output
                    return [], [f"BLOCKED: {integrity.errors[0]}"]

        # Phase 2: Parse and apply (strict mode, no fallback)
        report = apply_protocol_output(
            normalized_response,
            self.workspace,
            strict=True,  # 严格模式
            allow_fuzzy_match=False,  # 禁用模糊匹配
        )

        if report.ops_failed > 0:
            errors = [f"{r.operation.path}: {r.error_message}" for r in report.results if not r.success]
            # 即使有失败，也返回已应用的文件
            if report.changed_files:
                return (
                    self.collect_workspace_files(report.changed_files, task_id=task_id, operation="modify"),
                    errors,
                )
            return [], errors

        if not report.changed_files:
            return [], ["no_changes"]

        return (
            self.collect_workspace_files(report.changed_files, task_id=task_id, operation="modify"),
            [],
        )

    # === Diff Statistics ===

    def calculate_diff_stats(
        self,
        old_content: str,
        new_content: str,
    ) -> dict[str, Any]:
        """Calculate diff statistics between old and new content.

        Args:
            old_content: Original file content
            new_content: New file content

        Returns:
            Dictionary with diff statistics
        """
        from polaris.kernelone.events.file_event_broadcaster import calculate_patch

        patch = calculate_patch(old_content, new_content)
        return {
            "old_size": len(old_content),
            "new_size": len(new_content),
            "patch_size": len(patch),
            "patch": patch,
        }
