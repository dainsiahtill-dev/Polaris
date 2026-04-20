"""Normalizer for repo_apply_diff tool.

Refactored (2026-04-05): Added to handle boolean coercion for dry_run/strict.
Parameter aliases are now handled by SchemaDrivenNormalizer via contracts.py.

Complex transformations (not expressible as arg_aliases):
- Boolean coercion for dry_run, strict (string "true" -> True)
"""

from __future__ import annotations

from typing import Any

from ._shared import _coerce_bool


def normalize_repo_apply_diff_args(tool_args: dict[str, Any]) -> dict[str, Any]:
    """Normalize repo_apply_diff arguments.

    This normalizer handles boolean coercion:
    - dry_run: string "true"/"false" -> bool True/False
    - strict: string "true"/"false" -> bool True/False

    Path aliases are handled by SchemaDrivenNormalizer via contracts.py arg_aliases.
    """
    normalized = dict(tool_args)

    # Coerce dry_run to bool
    if "dry_run" in normalized:
        bool_val = _coerce_bool(normalized["dry_run"])
        if bool_val is not None:
            normalized["dry_run"] = bool_val

    # Coerce strict to bool
    if "strict" in normalized:
        bool_val = _coerce_bool(normalized["strict"])
        if bool_val is not None:
            normalized["strict"] = bool_val

    return normalized
