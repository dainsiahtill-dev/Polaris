"""Truncation detection utilities for LLM responses.

This module provides unified truncation detection logic across different LLM providers.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class TruncationDetection:
    """Result of truncation detection."""

    truncated: bool
    reason: str  # "length" | "unclosed_block" | "incomplete_json" | "none"
    finish_reason: str | None
    continuation_supported: bool


def detect_truncation_from_metadata(
    provider_metadata: dict[str, Any],
) -> TruncationDetection:
    """Detect truncation based on provider metadata alone.

    Args:
        provider_metadata: Metadata from the LLM provider response

    Returns:
        TruncationDetection with the result
    """
    # Check for explicit length truncation
    finish_reason = provider_metadata.get("finish_reason")
    if finish_reason == "length":
        return TruncationDetection(
            truncated=True,
            reason="length",
            finish_reason=finish_reason,
            continuation_supported=True,
        )

    # Check Ollama-specific done_reason
    done_reason = provider_metadata.get("done_reason")
    if done_reason == "length":
        return TruncationDetection(
            truncated=True,
            reason="length",
            finish_reason=done_reason,
            continuation_supported=True,
        )

    # Check for incomplete response (Ollama: done=False without done_reason)
    done = provider_metadata.get("done")
    if done is False and done_reason is None:
        return TruncationDetection(
            truncated=True,
            reason="incomplete_response",
            finish_reason=None,
            continuation_supported=True,
        )

    # Normal completion
    return TruncationDetection(
        truncated=False,
        reason="none",
        finish_reason=finish_reason,
        continuation_supported=False,
    )


__all__ = [
    "TruncationDetection",
    "detect_truncation_from_metadata",
]
