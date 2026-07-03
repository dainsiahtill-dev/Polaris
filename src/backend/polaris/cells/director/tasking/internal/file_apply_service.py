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
from pathlib import Path
from typing import Any

import tomllib
from polaris.cells.director.tasking.internal.patch_protocol import validate_before_apply
from polaris.kernelone.llm.toolkit.write_policy import validate_tool_write_policy

logger = logging.getLogger(__name__)


_FENCED_FILE_BLOCK_RE = re.compile(
    r"```file:\s*([^\r\n`]+)\r?\n(.*?)```",
    re.DOTALL | re.IGNORECASE,
)
_FENCED_FILE_HEADER_RE = re.compile(r"^```file:\s*([^`\r\n]+?)\s*$", re.IGNORECASE)
_FENCE_CLOSE_RE = re.compile(r"^```\s*$")
_PROTOCOL_PATH_RE = re.compile(r"(?im)^\s*(?:PATCH_FILE|FILE|DELETE(?:_FILE)?)\s*(?::|\s+)\s*([^\r\n]+?)\s*$")
_SOURCE_FILE_SUFFIXES = (
    ".c",
    ".cc",
    ".cpp",
    ".cs",
    ".go",
    ".java",
    ".js",
    ".jsx",
    ".mjs",
    ".py",
    ".rs",
    ".ts",
    ".tsx",
)
_SOURCE_CODE_LINE_RE = re.compile(
    r"^\s*(?:"
    r"import\s|export\s|from\s|const\s|let\s|var\s|function\s|class\s|"
    r"interface\s|type\s|enum\s|def\s|async\s+def\s|package\s|func\s|"
    r"use\s|pub\s|impl\s|struct\s|public\s|private\s|protected\s|"
    r"#include\b|using\s+namespace\b"
    r")"
)
_MARKDOWN_ADVISORY_LINE_RE = re.compile(r"^\s*(?:#{1,6}\s|\*\*[^*\n]+?\*\*|[-*]\s+|>\s+|\d+\.\s+|\|)")


def _is_package_manifest_path(rel_path: str) -> bool:
    normalized = str(rel_path or "").replace("\\", "/").strip("/").lower()
    return normalized == "package.json" or normalized.endswith("/package.json")


def _read_workspace_agents_policy_text(workspace: str, rel_path: str) -> str:
    """Read root and nested AGENTS.md files that apply to a workspace-relative path."""
    workspace_path = Path(workspace).resolve()
    normalized_rel = str(rel_path or "").replace("\\", "/").strip("/")
    candidates = ["AGENTS.md"]
    parent_parts = [part for part in normalized_rel.split("/")[:-1] if part]
    for index in range(1, len(parent_parts) + 1):
        candidates.append("/".join([*parent_parts[:index], "AGENTS.md"]))

    texts: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        if candidate in seen:
            continue
        seen.add(candidate)
        target = (workspace_path / candidate).resolve()
        if workspace_path not in target.parents and target != workspace_path:
            continue
        try:
            if target.is_file():
                texts.append(target.read_text(encoding="utf-8"))
        except (OSError, UnicodeError):
            continue
    return "\n".join(texts)


def _workspace_fs(workspace: str):
    from polaris.kernelone.fs import KernelFileSystem, get_default_adapter

    return KernelFileSystem(str(Path(workspace).resolve()), get_default_adapter())


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


def _source_file_markdown_advisory_error(path: str, content: str) -> str | None:
    """Reject obvious prose reports accidentally routed into source files."""
    normalized = str(path or "").strip().replace("\\", "/").lower()
    if not normalized.endswith(_SOURCE_FILE_SUFFIXES):
        return None

    stripped = str(content or "").lstrip()
    if not stripped:
        return None
    if not stripped.startswith(("#", "**", "- ", "* ", "> ", "|")) and not re.match(r"^\d+\.\s", stripped):
        return None

    probe_lines = [line for line in stripped.splitlines()[:16] if line.strip()]
    if not probe_lines:
        return None
    markdown_lines = sum(1 for line in probe_lines if _MARKDOWN_ADVISORY_LINE_RE.match(line))
    code_lines = sum(1 for line in probe_lines if _SOURCE_CODE_LINE_RE.match(line) or "=>" in line)
    if markdown_lines >= 2 and code_lines == 0:
        return f"{path}: source file content appears to be markdown/advisory text, not code"
    return None


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
        self._last_write_errors: list[str] = []

    # === File Writing ===

    def _validate_director_policy_for_write(
        self,
        *,
        rel_path: str,
        old_content: str,
        new_content: str,
        operation: str,
        allowed_scope_paths: list[str] | tuple[str, ...] | None = None,
    ) -> str:
        """Return a deterministic policy error for a pending file apply write."""
        normalized_rel = str(rel_path or "").replace("\\", "/").strip("/")
        package_write = _is_package_manifest_path(normalized_rel)
        verdict = validate_tool_write_policy(
            changed_files=[normalized_rel] if normalized_rel else [],
            allowed_scope=list(allowed_scope_paths or []),
            agents_md=_read_workspace_agents_policy_text(self.workspace, normalized_rel),
            operation=operation,
            package_before=old_content if package_write else None,
            package_after=new_content if package_write else None,
            require_change=True,
        )
        if verdict.allowed:
            return ""
        reason = "; ".join(verdict.reasons) or "Director write policy denied the write"
        return f"Director write policy denied: {reason}"

    def write_files(
        self,
        files: list[dict],
        task_id: str = "",
        allowed_scope_paths: list[str] | tuple[str, ...] | None = None,
    ) -> list[dict]:
        """Write generated files to workspace with broadcast support.

        Args:
            files: List of file dictionaries with 'path' and 'content' keys
            task_id: Optional task ID for tracking

        Returns:
            List of successfully written file dictionaries
        """
        files_created: list[dict] = []
        self._last_write_errors = []

        # Import here to avoid circular dependencies
        from polaris.kernelone.events.file_event_broadcaster import write_file_with_broadcast
        from polaris.kernelone.llm.toolkit.tool_normalization import normalize_patch_like_write_content

        fs = _workspace_fs(self.workspace)
        for file_info in files:
            file_path = str(file_info.get("path") or "").strip()
            content = str(file_info.get("content") or "")
            if not file_path or not content:
                continue
            try:
                full_path = self._resolve_workspace_path(file_path)
                if full_path is None:
                    self._last_write_errors.append(f"Unsafe file path outside workspace: {file_path}")
                    continue
                old_content = ""
                if os.path.isfile(full_path):
                    try:
                        old_content = fs.workspace_read_text(file_path, encoding="utf-8")
                    except OSError:
                        old_content = ""
                normalized = normalize_patch_like_write_content(
                    file_path,
                    content,
                    existing_content=old_content if os.path.isfile(full_path) else None,
                )
                if normalized.error:
                    self._last_write_errors.append(normalized.error)
                    logger.warning("Skip file '%s': %s", file_path, normalized.error)
                    continue
                content = str(normalized.content or "")
                if not content:
                    self._last_write_errors.append(f"Empty normalized content for file: {file_path}")
                    logger.warning("Skip file '%s': empty normalized content", file_path)
                    continue
                policy_error = self._validate_director_policy_for_write(
                    rel_path=file_path,
                    old_content=old_content,
                    new_content=content,
                    operation="file_apply_service.write_files",
                    allowed_scope_paths=allowed_scope_paths,
                )
                if policy_error:
                    self._last_write_errors.append(policy_error)
                    logger.warning("Skip file '%s': %s", file_path, policy_error)
                    continue
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

    def _validate_applied_files_against_director_policy(
        self,
        files: list[dict],
        *,
        snapshots: dict[str, str | None],
        operation: str,
        allowed_scope_paths: list[str] | tuple[str, ...] | None = None,
    ) -> list[str]:
        """Validate already-applied protocol writes and let caller roll back."""
        errors: list[str] = []
        for file_info in files:
            file_path = str(file_info.get("path") or "").strip()
            if not file_path:
                continue
            old_content = snapshots.get(file_path)
            policy_error = self._validate_director_policy_for_write(
                rel_path=file_path,
                old_content=old_content or "",
                new_content=str(file_info.get("content") or ""),
                operation=operation,
                allowed_scope_paths=allowed_scope_paths,
            )
            if policy_error:
                errors.append(policy_error)
        return errors

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
        fs = _workspace_fs(self.workspace)
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
                snapshots[path] = fs.workspace_read_text(path, encoding="utf-8")
            except OSError:
                snapshots[path] = None
        return snapshots

    def _restore_snapshots(self, snapshots: dict[str, str | None]) -> None:
        """Restore files after a post-apply validation failure."""
        fs = _workspace_fs(self.workspace)
        for path, content in snapshots.items():
            full_path = self._resolve_workspace_path(path)
            if full_path is None:
                continue
            try:
                if content is None:
                    if os.path.exists(full_path):
                        fs.workspace_remove(path, missing_ok=True)
                    continue
                fs.workspace_write_text(path, content, encoding="utf-8")
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
        source_error = _source_file_markdown_advisory_error(path, content)
        if source_error:
            return source_error
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

        fs = _workspace_fs(self.workspace)
        for raw_path in paths:
            path = str(raw_path or "").strip()
            if not path or path in seen:
                continue
            seen.add(path)
            full_path = os.path.join(self.workspace, path)
            if os.path.isfile(full_path):
                try:
                    content = fs.workspace_read_text(path, encoding="utf-8")
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
        allowed_scope_paths: list[str] | tuple[str, ...] | None = None,
    ) -> tuple[list[dict], list[str]]:
        """Apply patch/file operations from LLM response with pre-apply validation.

        Uses the KernelOne protocol package with strict mode (no fallback to full file).

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
                        policy_errors = self._validate_applied_files_against_director_policy(
                            protocol_applied,
                            snapshots=protocol_snapshots,
                            operation="file_apply_service.apply_response_operations",
                            allowed_scope_paths=allowed_scope_paths,
                        )
                        if policy_errors:
                            self._restore_snapshots(protocol_snapshots)
                            errors.extend(policy_errors)
                        else:
                            applied.extend(protocol_applied)

            applied.extend(
                self.write_files(
                    [{"path": path, "content": content} for path, content in fenced_blocks],
                    task_id=task_id,
                    allowed_scope_paths=allowed_scope_paths,
                )
            )
            if self._last_write_errors:
                errors.extend(self._last_write_errors)
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
                policy_errors = self._validate_applied_files_against_director_policy(
                    changed_files,
                    snapshots=operation_snapshots,
                    operation="file_apply_service.apply_response_operations",
                    allowed_scope_paths=allowed_scope_paths,
                )
                if policy_errors:
                    self._restore_snapshots(operation_snapshots)
                    return [], [*errors, *policy_errors]
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
                    policy_errors = self._validate_applied_files_against_director_policy(
                        fenced_changed_files,
                        snapshots=operation_snapshots,
                        operation="file_apply_service.apply_response_operations",
                        allowed_scope_paths=allowed_scope_paths,
                    )
                    if policy_errors:
                        self._restore_snapshots(operation_snapshots)
                        return [], [*errors, *fenced_errors, *policy_errors]
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
        policy_errors = self._validate_applied_files_against_director_policy(
            changed_files,
            snapshots=operation_snapshots,
            operation="file_apply_service.apply_response_operations",
            allowed_scope_paths=allowed_scope_paths,
        )
        if policy_errors:
            self._restore_snapshots(operation_snapshots)
            return [], policy_errors

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
