"""Shared private helpers and projection-schema constants for director.runtime public contracts."""

from __future__ import annotations

from typing import Any, Mapping


def _require_non_empty(name: str, value: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError(f"{name} must be a non-empty string")
    return normalized


def _to_dict_copy(value: Mapping[str, Any] | None) -> dict[str, Any]:
    return dict(value or {})


def _to_tuple_str(value: tuple[str, ...] | list[str] | None) -> tuple[str, ...]:
    return tuple(str(item) for item in (value or ()) if str(item or "").strip())


def _default_repairer_module_name(language: str) -> str:
    normalized_language = "".join(
        char if char.isalnum() or char == "_" else "_" for char in str(language or "unknown").lower()
    ).strip("_")
    return f"polaris.cells.director.runtime.internal.repair_kernel.{normalized_language or 'unknown'}_runtime"


_ADAPTER_RECEIPT_PROJECTION_SCHEMA_VERSION = "director.repair_adapter_receipt_projection.v1"
_DEFAULT_ADAPTER_RECEIPT_AUTHORITY = "non_authoritative_adapter_projection"
_DEFAULT_ADAPTER_RECEIPT_MIGRATION_BLOCKER = "adapter_projection_not_authoritative_receipt"
_CALLBACK_RECEIPT_PROJECTION_SCHEMA_VERSION = _ADAPTER_RECEIPT_PROJECTION_SCHEMA_VERSION
_DEFAULT_CALLBACK_RECEIPT_AUTHORITY = _DEFAULT_ADAPTER_RECEIPT_AUTHORITY
_ALLOWED_CALLBACK_RECEIPT_AUTHORITIES = {
    _DEFAULT_CALLBACK_RECEIPT_AUTHORITY,
    "non_authoritative_callback_receipt_projection",
    "non_authoritative_callback_projection",
    "non_authoritative_adapter_projection",
}
_DEFAULT_CALLBACK_RECEIPT_MIGRATION_BLOCKER = _DEFAULT_ADAPTER_RECEIPT_MIGRATION_BLOCKER


def _optional_non_empty_str(value: Any) -> str | None:
    normalized = str(value or "").strip()
    return normalized or None


def _optional_non_negative_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return None


def _optional_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _to_tuple_str_from_any(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, (str, bytes, bytearray)):
        items = (value,)
    else:
        try:
            items = tuple(value)
        except TypeError:
            items = (value,)
    return tuple(str(item) for item in items if str(item or "").strip())


def _to_tuple_mapping_from_any(value: Any) -> tuple[Mapping[str, Any], ...]:
    if value is None:
        return ()
    if isinstance(value, Mapping):
        items = (value,)
    else:
        try:
            items = tuple(value)
        except TypeError:
            return ()
    return tuple(_to_dict_copy(item) for item in items if isinstance(item, Mapping))


def _strict_bool_claim(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    normalized = str(value or "").strip().lower()
    return normalized in {"true", "1", "yes", "on"}
