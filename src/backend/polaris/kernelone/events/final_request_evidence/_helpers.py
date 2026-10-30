"""Shared private helpers for final-request evidence projection."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any


def _validate_exact_context_snapshot_hash(value: str) -> str:
    """Lazily consume the canonical validator without creating an import cycle.

    ``context_store_retention`` owns the audit-pin repository and imports this
    event contract. Importing the engine package while this module is still
    initializing makes the canonical Run Ledger public import order-dependent.
    The validator is a pure leaf, so deferring its import until validation keeps
    one hash authority while preserving a cold-import-safe module graph.
    """

    from polaris.kernelone.llm.engine.internal.context_hash import validate_context_hash

    return validate_context_hash(value)


def _as_mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _text(value: Any) -> str:
    token = str(value or "").strip()
    return token


def _first_text(*values: Any) -> str:
    for value in values:
        token = _text(value)
        if token:
            return token
    return ""


def _first_mapping(*values: Any) -> dict[str, Any]:
    for value in values:
        if isinstance(value, Mapping):
            return dict(value)
    return {}


def _string_sequence(value: Any) -> list[str]:
    if not isinstance(value, (list, tuple, set)):
        return []
    return [token for item in value if (token := _text(item))]


def _has_non_empty_text_sequence(value: Any) -> bool:
    return bool(_string_sequence(value))


def _has_non_empty_mapping(value: Any) -> bool:
    return isinstance(value, Mapping) and bool(value)


def _has_structural_field(payload: Mapping[str, Any], keys: tuple[str, ...]) -> bool:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, str) and _text(value):
            return True
        if isinstance(value, (list, tuple, set)) and _string_sequence(value):
            return True
        if _has_non_empty_mapping(value):
            return True
    return False


def _unique_texts(values: Any) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values if isinstance(values, (list, tuple, set)) else []:
        token = _text(value)
        if token and token not in seen:
            seen.add(token)
            result.append(token)
    return result


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [token for item in value if (token := _text(item))]


def _string_tokens(value: Any) -> list[str]:
    if isinstance(value, str):
        raw_items = value.replace(";", ",").split(",")
    elif isinstance(value, (list, tuple, set)):
        raw_items = list(value)
    else:
        return []
    return [token for item in raw_items if (token := _text(item))]


def _bool_value(value: Any, *, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on", "enabled"}:
            return True
        if normalized in {"0", "false", "no", "off", "disabled"}:
            return False
    return default


def _list_value(value: Any) -> list[Any]:
    return list(value) if isinstance(value, list) else []


def _bool_or_none(value: Any) -> bool | None:
    return value if isinstance(value, bool) else None


def _iter_context_mappings(value: Any, *, depth: int = 0) -> list[Mapping[str, Any]]:
    if depth > 5:
        return []
    if isinstance(value, Mapping):
        mappings: list[Mapping[str, Any]] = [value]
        for nested in value.values():
            mappings.extend(_iter_context_mappings(nested, depth=depth + 1))
        return mappings
    if isinstance(value, (list, tuple)):
        nested_mappings: list[Mapping[str, Any]] = []
        for item in value:
            nested_mappings.extend(_iter_context_mappings(item, depth=depth + 1))
        return nested_mappings
    return []


def _stable_hash(value: Any) -> str:
    try:
        payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError):
        payload = str(value)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _require_role_final_request_string(field_name: str, value: Any) -> str:
    """Return an authority string without coercing runtime values."""

    if not isinstance(value, str):
        raise ValueError(f"{field_name}_must_be_string")
    return value


def _validate_role_final_request_json(value: Any, *, path: str = "$") -> None:
    """Reject non-JSON and unstable values without repr/default fallbacks."""

    if value is None or isinstance(value, (bool, int, str)):
        return
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            raise ValueError(f"canonical_json_non_finite_float:{path}")
        return
    if isinstance(value, Mapping):
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError(f"canonical_json_non_string_key:{path}")
            _validate_role_final_request_json(item, path=f"{path}.{key}")
        return
    if isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            _validate_role_final_request_json(item, path=f"{path}[{index}]")
        return
    raise ValueError(f"canonical_json_unsupported_type:{path}:{type(value).__name__}")
