"""Identity normalizer for tools that don't need complex transformation.

These tools have simple parameters that don't require:
- Range parameter conversion
- Value range clamping
- Scope pattern normalization
- Boolean coercion
- Workspace path alias resolution

SchemaDrivenNormalizer handles all arg_aliases via contracts.py.
"""

from __future__ import annotations

from typing import Any


def normalize_noop_args(tool_args: dict[str, Any]) -> dict[str, Any]:
    """Identity normalizer - returns args unchanged.

    This is used for tools with simple parameters that don't need
    any complex transformation beyond SchemaDrivenNormalizer's arg_aliases.
    """
    return dict(tool_args)
