"""Typing helpers that keep runtime validation alive under static narrowing.

Polaris validators deliberately re-check annotated fields at runtime (raw
dicts/strings arrive from persistence and HTTP boundaries). Mypy's narrowing
marks those guards unreachable; calling isinstance through an opaque helper
preserves the runtime check without the false narrowing.
"""

from __future__ import annotations

from typing import Any


def opaque_isinstance(value: object, expected: type[Any] | tuple[type[Any], ...]) -> bool:
    """isinstance() whose result mypy cannot use to narrow ``value``."""
    return isinstance(value, expected)


def opaque_not_isinstance(value: object, expected: type[Any] | tuple[type[Any], ...]) -> bool:
    """Negated opaque_isinstance for coercion guards."""
    return not isinstance(value, expected)
