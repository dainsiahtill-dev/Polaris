"""Provider transport redaction for final-request audit evidence."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from polaris.kernelone.events.final_request_evidence._constants import (
    _SECRET_KEY_SUFFIXES,
    _SECRET_KEYS,
)


def redact_provider_transport(value: Any, *, key: str = "") -> Any:
    """Recursively redact transport secrets while preserving unknown semantics."""

    normalized_key = str(key).strip().lower().replace("-", "_")
    if normalized_key in _SECRET_KEYS or normalized_key.endswith(_SECRET_KEY_SUFFIXES):
        return {"redacted": True, "kind": "secret"}
    if value is None or isinstance(value, (bool, int, float, str)):
        if key == "endpoint" and isinstance(value, str) and "?" in value:
            return value.split("?", 1)[0]
        return value
    if isinstance(value, Mapping):
        return {str(item_key): redact_provider_transport(item, key=str(item_key)) for item_key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact_provider_transport(item, key=key) for item in value]
    raise ValueError("provider_config_not_snapshot_safe")
