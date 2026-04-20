"""Normalizer for repo_read_head and repo_read_tail tools.

Refactored (2026-04-05): Added to handle lines->n conversion.
Parameter aliases are now handled by SchemaDrivenNormalizer via contracts.py.

Complex transformations (not expressible as arg_aliases):
- lines->n conversion (GPT style parameter)
"""

from __future__ import annotations

from typing import Any

from ._shared import _coerce_int


def normalize_repo_read_head_tail_args(tool_args: dict[str, Any]) -> dict[str, Any]:
    """Normalize repo_read_head and repo_read_tail arguments.

    This normalizer ONLY handles the lines->n conversion:
    - GPT style: lines -> n

    Path aliases are handled by SchemaDrivenNormalizer via contracts.py arg_aliases.
    """
    normalized = dict(tool_args)

    # Handle lines->n conversion (GPT style parameter)
    if "lines" in normalized and "n" not in normalized:
        lines_val = _coerce_int(normalized.get("lines"))
        if lines_val is not None and lines_val > 0:
            normalized["n"] = lines_val

    # Remove lines alias
    normalized.pop("lines", None)

    # Coerce n to int (handles string "100" -> 100)
    if "n" in normalized:
        n_val = _coerce_int(normalized.get("n"))
        if n_val is not None and n_val > 0:
            normalized["n"] = n_val

    # Set defaults
    normalized.setdefault("n", 50)

    return normalized
