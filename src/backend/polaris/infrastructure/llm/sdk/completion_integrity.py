"""Completion integrity detection for LLM outputs.

This module provides unified integrity detection to identify truncated outputs
before file writing, enabling fail-closed behavior.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class IntegrityStatus(Enum):
    """Status of LLM output integrity."""

    COMPLETE = "complete"
    TRUNCATED = "truncated"
    NO_CHANGES = "no_changes"
    INVALID = "invalid"


@dataclass
class FileBlockParseState:
    """State of parsed file blocks."""

    blocks: list[dict[str, str]] = field(default_factory=list)
    open_block_path: str | None = None  # Path of unclosed block
    has_unclosed_block: bool = False
    is_no_changes: bool = False


@dataclass
class CompletionIntegrity:
    """Result of completion integrity evaluation."""

    status: IntegrityStatus
    reason: str | None = None
    parse_state: FileBlockParseState | None = None
    truncation_reason: str | None = None
    continuation_supported: bool = False


def _detect_truncated_file_blocks(text: str) -> FileBlockParseState:
    """Detect FILE blocks that are unclosed.

    Returns a FileBlockParseState with information about whether
    the output has unclosed file blocks.
    """
    if not text:
        return FileBlockParseState(is_no_changes=True)

    stripped = text.strip()
    if stripped == "NO_CHANGES":
        return FileBlockParseState(is_no_changes=True)

    blocks: list[dict[str, str]] = []
    current_path = ""
    current_lines: list[str] = []
    in_content = False
    has_unclosed_block = False
    open_block_path: str | None = None

    for line in text.splitlines():
        # Detect FILE: start
        if line.startswith("FILE:"):
            # If there's a previous unclosed block
            if current_path and in_content:
                has_unclosed_block = True
                open_block_path = current_path
            # Start new block
            current_path = line[len("FILE:") :].strip()
            current_lines = []
            in_content = True
            continue

        # Detect END FILE
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

        # Accumulate content
        if current_path and in_content:
            current_lines.append(line)

    # Check if last block is unclosed
    if current_path and in_content:
        has_unclosed_block = True
        open_block_path = current_path

    is_no_changes = len(blocks) == 0 and not has_unclosed_block

    return FileBlockParseState(
        blocks=blocks,
        open_block_path=open_block_path,
        has_unclosed_block=has_unclosed_block,
        is_no_changes=is_no_changes,
    )


def _detect_truncated_json(text: str) -> bool:
    """Detect if JSON appears to be truncated.

    Checks for incomplete JSON structures (missing closing braces/brackets).
    """
    stripped = text.strip()

    # Not JSON if doesn't start with { or [
    if not (stripped.startswith("{") or stripped.startswith("[")):
        return False

    # Try to parse - if it fails and ends with incomplete structure, likely truncated
    import json

    try:
        json.loads(stripped)
        return False  # Valid JSON, not truncated
    except json.JSONDecodeError as e:
        # Check if the error suggests truncation
        error_msg = str(e).lower()
        # Position where parsing failed
        pos = e.pos if hasattr(e, "pos") else 0

        # If the error is about unexpected end, likely truncated
        if "unexpected end" in error_msg:
            return True

        # Check if we're mid-structure at the end
        if pos > 0 and pos <= len(stripped):
            tail = stripped[max(0, pos - 10) : pos + 10]
            # Count open vs close braces
            open_braces = tail.count("{") + tail.count("[")
            close_braces = tail.count("}") + tail.count("]")
            if open_braces > close_braces:
                return True

        return False


def evaluate_completion_integrity(
    raw_text: str,
    provider_metadata: dict[str, Any],
) -> CompletionIntegrity:
    """Evaluate the integrity of LLM completion output.

    This is the main entry point for detecting truncated outputs.
    It checks:
    1. Explicit NO_CHANGES
    2. Provider-reported length truncation
    3. Unclosed file blocks
    4. Incomplete JSON structures

    Args:
        raw_text: The raw LLM output text
        provider_metadata: Metadata from the LLM provider (may contain finish_reason)

    Returns:
        CompletionIntegrity with status and details
    """
    # 1. NO_CHANGES detection
    if not raw_text or raw_text.strip() == "NO_CHANGES":
        return CompletionIntegrity(
            status=IntegrityStatus.NO_CHANGES,
            reason="explicit_no_changes",
            parse_state=None,
            continuation_supported=False,
        )

    # 2. Provider-reported length truncation
    finish_reason = provider_metadata.get("finish_reason")
    if finish_reason == "length":
        return CompletionIntegrity(
            status=IntegrityStatus.TRUNCATED,
            reason="provider_length_limit",
            parse_state=None,
            truncation_reason="length",
            continuation_supported=True,
        )

    # Check for Ollama-specific done_reason
    done_reason = provider_metadata.get("done_reason")
    if done_reason == "length":
        return CompletionIntegrity(
            status=IntegrityStatus.TRUNCATED,
            reason="ollama_length_limit",
            parse_state=None,
            truncation_reason="length",
            continuation_supported=True,
        )

    # 3. FILE block structure integrity detection
    parse_state = _detect_truncated_file_blocks(raw_text)

    if parse_state.is_no_changes:
        return CompletionIntegrity(
            status=IntegrityStatus.NO_CHANGES,
            reason="no_valid_blocks",
            parse_state=parse_state,
            continuation_supported=False,
        )

    if parse_state.has_unclosed_block:
        return CompletionIntegrity(
            status=IntegrityStatus.TRUNCATED,
            reason="unclosed_file_block",
            parse_state=parse_state,
            truncation_reason=f"unclosed_block:{parse_state.open_block_path}",
            continuation_supported=True,
        )

    # 4. JSON structure detection (optional, for JSON outputs)
    if _detect_truncated_json(raw_text):
        return CompletionIntegrity(
            status=IntegrityStatus.TRUNCATED,
            reason="incomplete_json",
            parse_state=parse_state,
            truncation_reason="incomplete_json",
            continuation_supported=True,
        )

    # 5. Default: complete
    return CompletionIntegrity(
        status=IntegrityStatus.COMPLETE,
        reason=None,
        parse_state=parse_state,
        continuation_supported=False,
    )


__all__ = [
    "CompletionIntegrity",
    "FileBlockParseState",
    "IntegrityStatus",
    "evaluate_completion_integrity",
]
