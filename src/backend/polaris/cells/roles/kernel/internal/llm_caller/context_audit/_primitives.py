from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any

from ..final_request_metrics import canonical_message_chars


def _json_chars(value: Any) -> int:
    if value is None:
        return 0
    try:
        return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")))
    except (TypeError, ValueError):
        return len(str(value))


def _json_canonical(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError):
        return str(value)


def _stable_digest(value: Any) -> str:
    payload = _json_canonical(value).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:24]


def _estimate_tokens_from_chars(char_count: int) -> int:
    return max(0, int(char_count) // 4)


def _message_chars(messages: list[dict[str, Any]]) -> int:
    return canonical_message_chars(messages)


def _message_content_chars(messages: list[dict[str, Any]]) -> int:
    total = 0
    for message in messages:
        if isinstance(message, dict):
            total += len(str(message.get("content") or ""))
    return total


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


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


def _int_value(value: Any, *, default: int = 0) -> int:
    if isinstance(value, bool):
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _string_list(value: Any) -> list[str]:
    if isinstance(value, str):
        raw_items = value.replace(";", ",").split(",")
    elif isinstance(value, (list, tuple, set)):
        raw_items = list(value)
    else:
        return []
    result: list[str] = []
    for item in raw_items:
        token = str(item or "").strip()
        if token:
            result.append(token)
    return result


def _unique_strings(values: Any) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in _string_list(values):
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def _coerce_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _coerce_int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _is_sha256(value: Any) -> bool:
    token = str(value or "").strip()
    return len(token) == 64 and all(character in "0123456789abcdef" for character in token)


def _canonical_actual_sibling_exports_hash(value: Mapping[str, Any]) -> str:
    payload = dict(value)
    payload.pop("snapshot_sha256", None)
    try:
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeError):
        return ""
    return hashlib.sha256(encoded).hexdigest()


def _json_safe(value: Any) -> Any:
    try:
        return json.loads(json.dumps(value, ensure_ascii=False))
    except (TypeError, ValueError):
        return str(value)


def _non_empty_attr(*owners: Any, name: str) -> str:
    for owner in owners:
        if owner is None:
            continue
        value = getattr(owner, name, None)
        if not isinstance(value, str):
            continue
        text = value.strip()
        if text:
            return text
    return ""
