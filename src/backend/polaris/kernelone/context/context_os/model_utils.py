"""Model utilities for safe model mutation with validation.

Provides helpers to replace fields on frozen Pydantic models while
preserving type safety and catching invalid field names early.
"""

from __future__ import annotations

from typing import Any, TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)

def validated_replace(model: T, **updates: Any) -> T:
    """Return a copy of *model* with fields replaced by *updates*.

    Unlike ``model_copy(update=...)`` this helper:

    1. **Validates field names** – raises ``ValueError`` if a key does not
       exist on the model.
    2. **Runs Pydantic validators** – the new instance is created through
       the normal constructor so all ``@field_validator`` and
       ``@model_validator`` hooks execute.
    3. **Converts dict → tuple for ``metadata``** – if the model has a
       ``metadata`` field and the supplied value is a ``dict``, it is
       automatically converted to ``tuple[tuple[str, Any], ...]`` (the
       canonical type for ``TranscriptEventV2.metadata``).

    Args:
        model: The frozen (or non-frozen) Pydantic model to copy.
        **updates: Field names and their new values.

    Returns:
        A new model instance of the same type.

    Raises:
        ValueError: If any key in *updates* is not a declared field on
            *model*.
    """
    model_fields = model.__class__.model_fields
    existing = set(model_fields)
    invalid = set(updates) - existing
    if invalid:
        raise ValueError(
            f"Invalid field name(s) for {model.__class__.__name__}: {sorted(invalid)}. "
            f"Valid fields: {sorted(existing)}"
        )

    # Build the full kwargs for reconstruction, applying conversions.
    kwargs: dict[str, Any] = {}
    for name in model_fields:
        if name in updates:
            value = updates[name]
            # Auto-convert dict metadata to tuple-of-tuples (canonical form).
            if name == "metadata" and isinstance(value, dict):
                value = tuple(sorted(value.items()))
            kwargs[name] = value
        else:
            kwargs[name] = getattr(model, name)

    return model.__class__(**kwargs)
