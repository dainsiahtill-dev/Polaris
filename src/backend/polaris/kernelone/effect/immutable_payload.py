"""Pure immutable effect payloads and their lossless typed UTF-8 codec."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, TypeAlias


def _require_token(name: str, value: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a string")
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{name} must be a non-empty string")
    return normalized


@dataclass(frozen=True, slots=True)
class DirectedEffectImmutableMapV1:
    """Explicit immutable map value with recursively canonicalized items."""

    items: DirectedEffectImmutableItemsV1

    def __post_init__(self) -> None:
        normalized = _normalize_directed_effect_immutable_items("map items", self.items)
        object.__setattr__(self, "items", tuple(sorted(normalized, key=lambda item: item[0])))


@dataclass(frozen=True, slots=True)
class DirectedEffectImmutableSequenceV1:
    """Explicit immutable ordered sequence value."""

    items: tuple[DirectedEffectImmutableValueV1, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.items, tuple):
            raise TypeError("sequence items must be an immutable tuple")
        object.__setattr__(self, "items", tuple(_require_immutable_value(item) for item in self.items))


DirectedEffectImmutableValueV1: TypeAlias = (
    None
    | bool
    | int
    | float
    | str
    | tuple["DirectedEffectImmutableValueV1", ...]
    | DirectedEffectImmutableMapV1
    | DirectedEffectImmutableSequenceV1
)


DirectedEffectImmutableItemsV1: TypeAlias = tuple[tuple[str, DirectedEffectImmutableValueV1], ...]


def _require_immutable_value(value: object) -> DirectedEffectImmutableValueV1:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, DirectedEffectImmutableMapV1 | DirectedEffectImmutableSequenceV1):
        return value
    if isinstance(value, Mapping):
        raise TypeError("mutable mappings are not permitted in DEO contracts")
    if isinstance(value, tuple):
        return tuple(_require_immutable_value(item) for item in value)
    raise TypeError("DEO contract payload values must be immutable scalars or tuples")


def _normalize_directed_effect_immutable_items(
    name: str,
    value: DirectedEffectImmutableItemsV1,
) -> DirectedEffectImmutableItemsV1:
    if isinstance(value, Mapping) or not isinstance(value, tuple):
        raise TypeError(f"{name} must be a tuple of immutable key/value pairs")
    normalized: list[tuple[str, DirectedEffectImmutableValueV1]] = []
    for item in value:
        if not isinstance(item, tuple) or len(item) != 2:
            raise TypeError(f"{name} entries must be two-item tuples")
        key, item_value = item
        normalized.append((_require_token(f"{name} key", key), _require_immutable_value(item_value)))
    result = tuple(normalized)
    if len({key for key, _ in result}) != len(result):
        raise ValueError(f"{name} must not contain duplicate keys")
    return result


def require_directed_effect_immutable_items(
    name: str,
    value: DirectedEffectImmutableItemsV1,
) -> DirectedEffectImmutableItemsV1:
    """Validate one sorted immutable key/value payload without coercion."""
    result = _normalize_directed_effect_immutable_items(name, value)
    if result != tuple(sorted(result, key=lambda item: item[0])):
        raise ValueError(f"{name} must be sorted by key")
    return result


def hash_directed_effect_arguments(value: DirectedEffectImmutableItemsV1) -> str:
    """Return the SHA-256 of one canonical immutable arguments payload."""
    normalized = _normalize_directed_effect_immutable_items("normalized_arguments", value)
    canonical = (
        "map",
        tuple(
            (key, _canonicalize_directed_effect_value(item_value))
            for key, item_value in sorted(normalized, key=lambda item: item[0])
        ),
    )
    encoded = json.dumps(
        canonical,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _canonicalize_directed_effect_value(value: DirectedEffectImmutableValueV1) -> object:
    if value is None:
        return ("null",)
    if type(value) is bool:
        return ("bool", value)
    if isinstance(value, int):
        return ("int", value)
    if isinstance(value, float):
        return ("float", value)
    if isinstance(value, str):
        return ("string", value)
    if isinstance(value, DirectedEffectImmutableMapV1):
        return (
            "map",
            tuple((key, _canonicalize_directed_effect_value(item)) for key, item in value.items),
        )
    if isinstance(value, DirectedEffectImmutableSequenceV1):
        return ("sequence", tuple(_canonicalize_directed_effect_value(item) for item in value.items))
    return ("sequence", tuple(_canonicalize_directed_effect_value(item) for item in value))


def freeze_directed_effect_arguments(arguments: dict[str, Any]) -> tuple[tuple[str, Any], ...]:
    """Freeze decoded dict/list arguments without changing their outer order."""

    def freeze(value: Any) -> Any:
        if isinstance(value, dict):
            return DirectedEffectImmutableMapV1(tuple((key, freeze(item)) for key, item in value.items()))
        if isinstance(value, list):
            return DirectedEffectImmutableSequenceV1(tuple(freeze(item) for item in value))
        return value

    return tuple((key, freeze(value)) for key, value in arguments.items())
