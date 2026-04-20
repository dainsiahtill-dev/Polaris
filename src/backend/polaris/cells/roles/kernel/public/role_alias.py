"""Polaris role alias public contracts.

This module lives in the roles Cell and contains all Polaris-specific
role identification logic that must not leak into KernelOne.

Role alias mapping resolves legacy / alternative role identifiers to their
canonical forms.  For example, ``"docs"`` maps to ``"architect"`` because
the Architect role was previously called the "docs" role.
"""

from __future__ import annotations

#: Canonical role alias table for Polaris.
#: Maps legacy or alternative role identifiers to their canonical role_id.
#: Keys are lowercase; values are canonical role_ids registered in RoleProfileRegistry.
ROLE_ALIASES: dict[str, str] = {
    "docs": "architect",
    "auditor": "qa",
}


def normalize_role_alias(role: str) -> str:
    """Resolve a role identifier to its canonical form.

    Applies ROLE_ALIASES mapping first, then lowercases and strips whitespace.
    If no alias matches, returns the input (already lowercased and stripped).

    Args:
        role: A role identifier (may be canonical, aliased, or arbitrary).

    Returns:
        The canonical role string (lowercase, stripped).
    """
    token = str(role or "").strip().lower()
    return ROLE_ALIASES.get(token, token)


__all__ = [
    "ROLE_ALIASES",
    "normalize_role_alias",
]
