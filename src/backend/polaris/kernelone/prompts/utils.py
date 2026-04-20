"""Shared utility functions for prompt modules.

Extracted from prompts.py, prompts_game.py, and prompts_generic.py to
eliminate copy-paste duplication of file-block parsing and path helpers.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass

from polaris.kernelone.fs.text_ops import ensure_parent_dir, read_file_safe
from polaris.kernelone.runtime.shared_types import normalize_path, unique_preserve


@dataclass
class FileBlockParseResult:
    """Result of parsing file blocks with state information."""

    blocks: list[dict[str, str]]
    has_unclosed_block: bool
    open_block_path: str | None
    is_no_changes: bool


def extract_between(text: str, start_tag: str, end_tag: str) -> str:
    if not text:
        return ""
    start_idx = text.find(start_tag)
    end_idx = text.find(end_tag)
    if start_idx == -1 or end_idx == -1 or end_idx <= start_idx:
        return ""
    return text[start_idx + len(start_tag) : end_idx].strip()


def parse_files_to_edit(text: str) -> list[str]:
    if not text:
        return []
    lines = text.splitlines()
    in_section = False
    files: list[str] = []
    header_re = re.compile(r"^\s*(?:\d+[\).\]]\s*)?files to edit\b", re.IGNORECASE)
    next_section_re = re.compile(r"^\s*\d+[\).\]]\s*\S")
    for line in lines:
        stripped = line.strip()
        if not in_section:
            if header_re.match(stripped):
                in_section = True
            continue
        if not stripped:
            continue
        if stripped.startswith("##") or stripped.startswith("[OLLAMA_BEGIN]"):
            break
        if next_section_re.match(stripped):
            break
        match = re.match(r"^\s*[-*]\s+(.+)$", line)
        if match:
            path = normalize_path(match.group(1).strip("`"))
            if path:
                files.append(path)
    return unique_preserve(files)


def build_file_context(files: list[str], workspace: str) -> str:
    blocks: list[str] = []
    for path in files:
        full_path = os.path.join(workspace, path)
        content = read_file_safe(full_path)
        header = f"FILE: {path}"
        blocks.append(header)
        blocks.append(content if content else "<EMPTY OR MISSING>")
        blocks.append("END FILE")
    return "\n".join(blocks)


def parse_file_blocks(text: str) -> list[dict[str, str]]:
    """Parse file blocks from text.

    Note: This function returns a list for backward compatibility.
    For integrity checking, use parse_file_blocks_with_state() instead.
    """
    blocks: list[dict[str, str]] = []
    if not text:
        return blocks
    if text.strip() == "NO_CHANGES":
        return blocks
    current_path = ""
    current_lines: list[str] = []
    in_content = False
    for line in text.splitlines():
        # Support FILE: format
        if line.startswith("FILE:"):
            current_path = normalize_path(line[len("FILE:") :].strip())
            current_lines = []
            in_content = True
            continue
        # Support PATCH_FILE format with <SEARCH></SEARCH><REPLACE></REPLACE>
        if line.startswith("PATCH_FILE") and not line.startswith("PATCH_FILE:"):
            # Format: PATCH_FILE filename
            parts = line[len("PATCH_FILE") :].strip().split()
            if parts:
                current_path = normalize_path(parts[0])
                current_lines = []
                in_content = False
                continue
        # Also support PATCH_FILE: format
        if line.startswith("PATCH_FILE:"):
            current_path = normalize_path(line[len("PATCH_FILE:") :].strip())
            current_lines = []
            in_content = False
            continue
        # Detect <REPLACE> tag - content starts after this
        if line.strip() == "<REPLACE>":
            in_content = True
            continue
        # Detect </REPLACE> tag - content ends
        if line.strip() == "</REPLACE>" and current_path:
            in_content = False
            blocks.append(
                {
                    "path": current_path,
                    "content": "\n".join(current_lines).rstrip("\n") + "\n",
                }
            )
            current_path = ""
            current_lines = []
            continue
        # Skip SEARCH tags
        if line.strip() in ("<SEARCH>", "</SEARCH>"):
            continue
        # Skip REPLACE marker (without angle brackets)
        if line.strip() == "REPLACE":
            in_content = True
            continue
        # Skip WITH marker
        if line.strip() == "WITH":
            in_content = True
            continue
        # Skip git diff style markers
        if line.strip() in ("<<<<<<< SEARCH", "=======", ">>>>>>> REPLACE"):
            if line.strip() == "=======":
                in_content = True
            continue
        if line.strip() == "<EMPTY OR MISSING>":
            continue
        if line.strip() == "END FILE" and current_path:
            blocks.append(
                {
                    "path": current_path,
                    "content": "\n".join(current_lines).rstrip("\n") + "\n",
                }
            )
            current_path = ""
            current_lines = []
            in_content = False
            continue
        if line.strip().startswith("```"):
            continue
        if current_path and in_content:
            current_lines.append(line)
    return blocks


def parse_file_blocks_with_state(text: str) -> FileBlockParseResult:
    """Parse file blocks with state information for integrity checking.

    Returns a FileBlockParseResult that includes information about unclosed blocks.
    """
    blocks: list[dict[str, str]] = []

    if not text:
        return FileBlockParseResult(
            blocks=[],
            has_unclosed_block=False,
            open_block_path=None,
            is_no_changes=True,
        )

    if text.strip() == "NO_CHANGES":
        return FileBlockParseResult(
            blocks=[],
            has_unclosed_block=False,
            open_block_path=None,
            is_no_changes=True,
        )

    current_path = ""
    current_lines: list[str] = []
    in_content = False

    for line in text.splitlines():
        # Support FILE: format
        if line.startswith("FILE:"):
            current_path = normalize_path(line[len("FILE:") :].strip())
            current_lines = []
            in_content = True
            continue
        # Support PATCH_FILE format
        if line.startswith("PATCH_FILE") and not line.startswith("PATCH_FILE:"):
            parts = line[len("PATCH_FILE") :].strip().split()
            if parts:
                current_path = normalize_path(parts[0])
                current_lines = []
                in_content = False
                continue
        if line.startswith("PATCH_FILE:"):
            current_path = normalize_path(line[len("PATCH_FILE:") :].strip())
            current_lines = []
            in_content = False
            continue
        # Detect <REPLACE> tag
        if line.strip() == "<REPLACE>":
            in_content = True
            continue
        # Detect </REPLACE> tag
        if line.strip() == "</REPLACE>" and current_path:
            in_content = False
            blocks.append(
                {
                    "path": current_path,
                    "content": "\n".join(current_lines).rstrip("\n") + "\n",
                }
            )
            current_path = ""
            current_lines = []
            continue
        # Skip markers
        if line.strip() in ("<SEARCH>", "</SEARCH>"):
            continue
        if line.strip() == "REPLACE":
            in_content = True
            continue
        if line.strip() == "WITH":
            in_content = True
            continue
        if line.strip() in ("<<<<<<< SEARCH", "=======", ">>>>>>> REPLACE"):
            if line.strip() == "=======":
                in_content = True
            continue
        if line.strip() == "<EMPTY OR MISSING>":
            continue
        if line.strip() == "END FILE" and current_path:
            blocks.append(
                {
                    "path": current_path,
                    "content": "\n".join(current_lines).rstrip("\n") + "\n",
                }
            )
            current_path = ""
            current_lines = []
            in_content = False
            continue
        if line.strip().startswith("```"):
            continue
        if current_path and in_content:
            current_lines.append(line)

    # Check for unclosed block at end of parsing
    has_unclosed = bool(current_path) and in_content

    return FileBlockParseResult(
        blocks=blocks,
        has_unclosed_block=has_unclosed,
        open_block_path=current_path if has_unclosed else None,
        is_no_changes=len(blocks) == 0 and not has_unclosed,
    )


def strip_full_content_markers(content: str) -> str:
    if not content:
        return content
    lines = content.splitlines()
    if not lines:
        return content
    start = 0
    end = len(lines) - 1
    while start <= end and not lines[start].strip():
        start += 1
    while end >= start and not lines[end].strip():
        end -= 1
    if start <= end and lines[start].strip().lower() == "<full file content>":
        lines.pop(start)
        end -= 1
    if start <= end and lines[end].strip().lower() in {
        "</full content>",
        "</full file content>",
    }:
        lines.pop(end)
    sanitized = "\n".join(lines)
    if content.endswith("\n"):
        sanitized += "\n"
    return sanitized


def apply_file_blocks(blocks: list[dict[str, str]], workspace: str) -> list[str]:
    changed: list[str] = []
    for block in blocks:
        path = block.get("path") or ""
        content = block.get("content")
        if not path or content is None:
            continue
        content = strip_full_content_markers(content)
        full_path = os.path.join(workspace, path)
        ensure_parent_dir(full_path)
        with open(full_path, "w", encoding="utf-8") as handle:
            handle.write(content)
        changed.append(path)
    return unique_preserve(changed)
