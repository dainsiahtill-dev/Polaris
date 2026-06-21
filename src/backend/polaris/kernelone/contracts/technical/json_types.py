"""Shared JSON value type alias for KernelOne contracts.

Provides a single canonical ``JsonValue`` recursive type alias that models the
set of values produced by ``json.loads`` / accepted by ``json.dumps``. This
anchors the ACGA 2.0 "contract-first" principle for JSON-coercion helpers so
that public/contract surfaces can replace bare ``Any`` with a precise,
self-documenting type without coupling to any concrete payload schema.

``JsonValue`` is intentionally permissive (it is the full JSON value space),
not a domain schema. Dynamic domain payloads that need free-form keys should
keep using ``dict[str, Any]`` / ``Mapping[str, Any]``; ``JsonValue`` is for
generic JSON serialization/sanitization helpers.
"""

from __future__ import annotations

from typing import TypeAlias

# Recursive JSON value type. ``dict`` keys are always ``str`` in canonical JSON.
JsonValue: TypeAlias = None | bool | int | float | str | list["JsonValue"] | dict[str, "JsonValue"]

# Convenience alias for the common "JSON object" shape.
JsonObject: TypeAlias = dict[str, JsonValue]

__all__ = [
    "JsonObject",
    "JsonValue",
]
