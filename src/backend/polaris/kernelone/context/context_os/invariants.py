"""State-First Context OS persisted projection invariants.

This module defines machine-enforced ownership boundaries for
`roles.session.context_config["state_first_context_os"]`.

Key rule:
  - persisted Context OS payload is a derived working-memory projection,
    not raw conversation truth.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from polaris.kernelone.errors import ValidationError

_FORBIDDEN_TRUTH_KEYS = frozenset(
    {
        "messages",
        "history",
        "conversation",
        "conversation_messages",
        "raw_messages",
        "raw_history",
        "session_continuity",
        "continuity_pack",
    }
)


class ContextOSInvariantViolation(ValidationError):
    """Raised when a persisted Context OS payload violates ownership invariants."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "CONTEXT_OS_INVARIANT_VIOLATION",
        **kwargs,
    ) -> None:
        super().__init__(
            message,
            code=code,
            field="persisted_context_os_payload",
            constraint="ownership_invariants",
            **kwargs,
        )


def _find_forbidden_keys_recursive(
    data: Any,
    path: str = "",
) -> list[str]:
    """Recursively find forbidden keys in nested structures.

    Args:
        data: The data structure to check (dict, list, tuple, or scalar)
        path: The current path for error reporting

    Returns:
        List of paths to forbidden keys found
    """
    forbidden_found: list[str] = []

    if isinstance(data, Mapping):
        for key, value in data.items():
            current_path = f"{path}.{key}" if path else key
            if key in _FORBIDDEN_TRUTH_KEYS:
                forbidden_found.append(current_path)
            # Recurse into nested structures
            forbidden_found.extend(_find_forbidden_keys_recursive(value, current_path))
    elif isinstance(data, (list, tuple)):
        for idx, item in enumerate(data):
            current_path = f"{path}[{idx}]"
            forbidden_found.extend(_find_forbidden_keys_recursive(item, current_path))

    return forbidden_found


def validate_context_os_persisted_projection(
    payload: Mapping[str, Any] | None,
) -> dict[str, Any] | None:
    """Validate persisted `state_first_context_os` payload.

    This validator enforces ownership constraints by checking for forbidden
    truth keys at all nesting levels, not just the top level.
    """

    if payload is None:
        return None
    if not isinstance(payload, Mapping):
        raise ContextOSInvariantViolation("state_first_context_os must be a mapping payload.")

    normalized = dict(payload)

    # Check for forbidden keys recursively at all nesting levels
    forbidden = sorted(_find_forbidden_keys_recursive(normalized))
    if forbidden:
        raise ContextOSInvariantViolation(
            f"state_first_context_os contains forbidden truth keys: {', '.join(forbidden)}"
        )

    mode = str(normalized.get("mode") or "").strip()
    if mode and not mode.startswith("state_first_context_os"):
        raise ContextOSInvariantViolation("state_first_context_os.mode must use state_first_context_os namespace.")

    return normalized


__all__ = ["ContextOSInvariantViolation", "validate_context_os_persisted_projection"]
