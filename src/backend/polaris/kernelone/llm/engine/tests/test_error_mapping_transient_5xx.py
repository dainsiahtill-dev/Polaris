"""H (2026-06-16 audit): transient server-side 5xx / provider overload must be
retryable.

Before this fix the platform-retryable branch enumerated only 503/504, so a bare
HTTP 500 / 502 Bad Gateway / Anthropic ``overloaded_error`` (HTTP 529) fell
through to ``UNKNOWN_ERROR`` with ``retryable=False`` — a transient blip became a
permanently dead step. The keywords are distinctive phrases ("internal server
error", "bad gateway", "overloaded") rather than bare "500"/"502" so a token
count like "5000 tokens" cannot be misclassified as a 5xx.
"""

from __future__ import annotations

import pytest
from polaris.kernelone.llm.engine.error_mapping import (
    PlatformRetryCategory,
    map_error_to_category,
)


@pytest.mark.parametrize(
    "message",
    [
        "HTTP 500 Internal Server Error",
        "Internal Server Error",
        "502 Bad Gateway",
        "upstream returned http 502",
        "overloaded_error: the model is currently overloaded",
        "HTTP 529",
        "503 Service Unavailable",
    ],
)
def test_transient_5xx_and_overload_are_retryable(message: str) -> None:
    category, retryable, _hint = map_error_to_category(RuntimeError(message))
    assert category is PlatformRetryCategory.SERVICE_UNAVAILABLE, message
    assert retryable is True, message


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        # The over-match guard: a "5000" token count must NOT become a 5xx.
        ("context length 5000 exceeded the maximum context", "CONTEXT_LENGTH_EXCEEDED"),
        # Regression: gateway timeout / generic timeout / rate limit unaffected.
        ("504 Gateway Timeout", "GATEWAY_TIMEOUT"),
        ("request timed out", "TIMEOUT"),
        ("rate limit exceeded", "RATE_LIMIT"),
    ],
)
def test_no_false_positive_5xx_classification(message: str, expected: str) -> None:
    category, _retryable, _hint = map_error_to_category(RuntimeError(message))
    assert category.name == expected, f"{message} -> {category.name}"
