"""Canonical task-token normalizer — single source of truth (§9.5).

PM taskboard, CE blueprints, and the orchestration layer use different task-id
formats (numeric ``1``, prefixed ``TASK-1`` / ``task_1``, orchestration
``task-0-director``). Cross-role lookups must compare equal regardless of
prefix, so §9.5 mandates one canonical normalizer. Every cell funnels through
this; the legacy per-cell wrappers delegate here.
"""

from __future__ import annotations

from typing import Any

__all__ = ["normalize_task_token"]


def normalize_task_token(value: Any) -> str:
    """Normalize a task identifier token for comparison.

    Strips whitespace, lowercases, and removes any ``task-`` / ``task_`` prefix
    (repeated prefixes like ``task-task-1`` collapse to ``1``) so that
    ``"TASK-1"``, ``"task_1"``, and ``"1"`` all compare equal. Returns ``""``
    for empty/None input.

    The numeric suffix (e.g. ``0-director`` from orchestration IDs) is
    preserved verbatim — only the ``task[-_]`` prefix is stripped.
    """

    token = str(value or "").strip().lower()
    if not token:
        return ""
    while token.startswith(("task-", "task_")):
        token = token[5:]
    return token
