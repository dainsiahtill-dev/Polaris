"""File apply service for WorkerExecutor.

This module contains all file I/O operations, including writing files with broadcast,
collecting workspace files, and applying response operations.

All text operations MUST explicitly use UTF-8 encoding.
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any

import tomllib

logger = logging.getLogger(__name__)


_FENCED_FILE_BLOCK_RE = re.compile(
    r"```file:\s*([^\r\n`]+)\r?\n(.*?)```",
    re.DOTALL | re.IGNORECASE,
)
_FENCED_FILE_HEADER_RE = re.compile(r"^```file:\s*([^`\r\n]+?)\s*$", re.IGNORECASE)
_FENCE_CLOSE_RE = re.compile(r"^```\s*$")
_PROTOCOL_PATH_RE = re.compile(r"(?im)^\s*(?:PATCH_FILE|FILE|DELETE(?:_FILE)?)\s*(?::|\s+)\s*([^\r\n]+?)\s*$")


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


def _collect_fenced_file_blocks(response: str) -> list[tuple[str, str]]:
    """Extract explicit ```file: path fenced sections from an LLM response."""

    text = str(response or "")
    if "```file:" not in text.lower():
        return []

    lines = text.splitlines()
    blocks: list[tuple[str, str]] = []
    index = 0
    while index < len(lines):
        header = _FENCED_FILE_HEADER_RE.match(lines[index].strip())
        if header is None:
            index += 1
            continue

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
                index += 1
                break
            content_lines.append(current_line)
            index += 1

        content = "\n".join(content_lines).strip("\r\n")
        content = _escape_nested_markdown_fences_for_protocol(path, content)
        if path and content:
            blocks.append((path, content))

    return blocks


def _fenced_file_blocks_to_protocol(response: str) -> str:
    blocks = _collect_fenced_file_blocks(response)
    return "\n".join(f"FILE: {path}\n{content}\nEND FILE" for path, content in blocks)


def _strip_fenced_file_blocks(response: str) -> str:
    """Remove explicit ```file: path fenced sections, preserving surrounding protocol text."""

    text = str(response or "")
    if "```file:" not in text.lower():
        return text

    lines = text.splitlines()
    output: list[str] = []
    index = 0
    while index < len(lines):
        header = _FENCED_FILE_HEADER_RE.match(lines[index].strip())
        if header is None:
            output.append(lines[index])
            index += 1
            continue

        index += 1
        nested_fence_depth = 0
        while index < len(lines):
            current_line = lines[index]
            current_header = _FENCED_FILE_HEADER_RE.match(current_line.strip())
            if current_header is not None:
                break
            if _is_nested_markdown_fence_open(current_line):
                nested_fence_depth += 1
                index += 1
                continue
            if _FENCE_CLOSE_RE.match(current_line.strip()):
                if nested_fence_depth > 0:
                    nested_fence_depth -= 1
                    index += 1
                    continue
                index += 1
                break
            index += 1

    return "\n".join(output).strip()


def _has_patch_or_delete_protocol(response: str) -> bool:
    return bool(re.search(r"(?im)^\s*(?:PATCH_FILE|DELETE(?:_FILE)?)\s*(?::|\s+)", str(response or "")))


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

    def _resolve_workspace_path(self, relative_path: str) -> str | None:
        """Resolve a workspace-relative path inside the workspace boundary."""
        path = str(relative_path or "").strip()
        if not path or os.path.isabs(path):
            return None
        workspace_abs = os.path.abspath(self.workspace)
        full_path = os.path.abspath(os.path.join(workspace_abs, path))
        try:
            if os.path.commonpath([workspace_abs, full_path]) != workspace_abs:
                return None
        except ValueError:
            return None
        return full_path

    def _snapshot_files(self, paths: list[str]) -> dict[str, str | None]:
        """Snapshot existing file contents before applying a risky operation."""
        snapshots: dict[str, str | None] = {}
        for raw_path in paths:
            path = str(raw_path or "").strip()
            if not path or path in snapshots:
                continue
            full_path = self._resolve_workspace_path(path)
            if full_path is None:
                continue
            if not os.path.exists(full_path):
                snapshots[path] = None
                continue
            try:
                with open(full_path, encoding="utf-8") as handle:
                    snapshots[path] = handle.read()
            except OSError:
                snapshots[path] = None
        return snapshots

    def _restore_snapshots(self, snapshots: dict[str, str | None]) -> None:
        """Restore files after a post-apply validation failure."""
        for path, content in snapshots.items():
            full_path = self._resolve_workspace_path(path)
            if full_path is None:
                continue
            try:
                if content is None:
                    if os.path.exists(full_path):
                        os.remove(full_path)
                    continue
                os.makedirs(os.path.dirname(full_path), exist_ok=True)
                with open(full_path, "w", encoding="utf-8", newline="") as handle:
                    handle.write(content)
            except OSError as exc:
                logger.warning("Failed to restore invalid structured file '%s': %s", path, exc)

    @staticmethod
    def _protocol_paths(response: str) -> list[str]:
        """Extract explicit file operation paths from protocol text."""
        paths: list[str] = []
        seen: set[str] = set()
        for match in _PROTOCOL_PATH_RE.finditer(str(response or "")):
            path = match.group(1).strip().strip("`'\"")
            if path and path not in seen:
                seen.add(path)
                paths.append(path)
        return paths

    @staticmethod
    def _structured_file_validation_error(path: str, content: str) -> str | None:
        """Return a syntax error for structured files, if validation fails."""
        normalized = str(path or "").strip().replace("\\", "/").lower()
        if normalized.endswith(".json"):
            try:
                json.loads(content)
            except json.JSONDecodeError as exc:
                return f"{path}: invalid JSON at line {exc.lineno}, column {exc.colno}: {exc.msg}"
        elif normalized.endswith(".toml"):
            try:
                tomllib.loads(content)
            except tomllib.TOMLDecodeError as exc:
                return f"{path}: invalid TOML: {exc}"
        return None

    def _validate_structured_files(self, files: list[dict]) -> list[str]:
        """Validate structured files included in applied file records."""
        errors: list[str] = []
        for item in files:
            if bool(item.get("deleted")):
                continue
            path = str(item.get("path") or "").strip()
            content = str(item.get("content") or "")
            if not path:
                continue
            error = self._structured_file_validation_error(path, content)
            if error:
                errors.append(error)
        return errors

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
        fenced_blocks = _collect_fenced_file_blocks(response)

        # Phase 1: Pre-apply integrity validation
        if llm_metadata:
            # Import from public contract (execution.public.service re-exports it)
            from polaris.cells.director.execution.public.service import validate_before_apply

            validation_response = _fenced_file_blocks_to_protocol(response) if fenced_blocks else normalized_response
            integrity = validate_before_apply(validation_response, llm_metadata)

            if not integrity.is_valid:
                if integrity.can_continue:
                    # Truncated but can try continuation
                    return [], [f"TRUNCATED: {integrity.errors[0]}"]
                else:
                    # Fail-closed: block the output
                    return [], [f"BLOCKED: {integrity.errors[0]}"]

        if fenced_blocks:
            applied: list[dict] = []
            errors: list[str] = []
            fenced_validation_errors = self._validate_structured_files(
                [{"path": path, "content": content} for path, content in fenced_blocks]
            )
            if fenced_validation_errors:
                return [], fenced_validation_errors
            protocol_remainder = _strip_fenced_file_blocks(response)
            if _has_patch_or_delete_protocol(protocol_remainder):
                protocol_snapshots = self._snapshot_files(self._protocol_paths(protocol_remainder))
                report = apply_protocol_output(
                    protocol_remainder,
                    self.workspace,
                    strict=True,
                    allow_fuzzy_match=False,
                )
                errors.extend(f"{r.operation.path}: {r.error_message}" for r in report.results if not r.success)
                if report.changed_files:
                    protocol_applied = self.collect_workspace_files(
                        report.changed_files, task_id=task_id, operation="modify"
                    )
                    structured_errors = self._validate_structured_files(protocol_applied)
                    if structured_errors:
                        self._restore_snapshots(protocol_snapshots)
                        errors.extend(structured_errors)
                    else:
                        applied.extend(protocol_applied)

            applied.extend(
                self.write_files(
                    [{"path": path, "content": content} for path, content in fenced_blocks],
                    task_id=task_id,
                )
            )
            if applied:
                deduped: list[dict] = []
                seen_paths: set[str] = set()
                for item in applied:
                    path = str(item.get("path") or "").strip()
                    if path and path not in seen_paths:
                        seen_paths.add(path)
                        deduped.append(item)
                return deduped, errors
            return [], errors or ["no_changes"]

        # Phase 2: Parse and apply (strict mode, no fallback)
        operation_snapshots = self._snapshot_files(self._protocol_paths(normalized_response))
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
                changed_files = self.collect_workspace_files(report.changed_files, task_id=task_id, operation="modify")
                structured_errors = self._validate_structured_files(changed_files)
                if structured_errors:
                    self._restore_snapshots(operation_snapshots)
                    return [], [*errors, *structured_errors]
                return (
                    changed_files,
                    errors,
                )

            fenced_only_response = _fenced_file_blocks_to_protocol(response)
            if fenced_only_response and fenced_only_response != normalized_response:
                fenced_report = apply_protocol_output(
                    fenced_only_response,
                    self.workspace,
                    strict=True,
                    allow_fuzzy_match=False,
                )
                fenced_errors = [
                    f"{r.operation.path}: {r.error_message}" for r in fenced_report.results if not r.success
                ]
                if fenced_report.changed_files:
                    fenced_changed_files = self.collect_workspace_files(
                        fenced_report.changed_files, task_id=task_id, operation="modify"
                    )
                    structured_errors = self._validate_structured_files(fenced_changed_files)
                    if structured_errors:
                        self._restore_snapshots(operation_snapshots)
                        return [], [*errors, *fenced_errors, *structured_errors]
                    return (
                        fenced_changed_files,
                        [*errors, *fenced_errors],
                    )
                errors.extend(fenced_errors)
            return [], errors

        if not report.changed_files:
            return [], ["no_changes"]

        changed_files = self.collect_workspace_files(report.changed_files, task_id=task_id, operation="modify")
        structured_errors = self._validate_structured_files(changed_files)
        if structured_errors:
            self._restore_snapshots(operation_snapshots)
            return [], structured_errors

        return (
            changed_files,
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
