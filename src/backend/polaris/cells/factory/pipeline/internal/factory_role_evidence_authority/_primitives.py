"""Primitive validators, errors, and stream identity helpers."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, TypeVar

from ._constants import (
    _AUTHORITY_STREAM_PREFIX,
    _HASH_LENGTH,
    _LOCATOR_PATTERN,
)

_T = TypeVar("_T")


class FactoryRoleEvidenceAuthorityError(RuntimeError):
    """Stable fail-closed A009B1 authority error."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(slots=True)
class _FactoryRoleEvidenceGrantState:
    """Factory-private capability registry row; never serialized or projected."""

    grant_nonce: str
    role: str
    attempt_budget: int
    execution_authority_hash: str
    controlled_child_run_id: str = ""
    request_freeze_ids: set[str] = field(default_factory=set)
    revoked: bool = False


def _text(field_name: str, value: object, *, allow_empty: bool = False) -> str:
    if type(value) is not str:
        raise TypeError(f"{field_name}_type_invalid")
    normalized = value.strip()
    if not allow_empty and not normalized:
        raise ValueError(f"{field_name}_missing")
    return normalized


def _locator(field_name: str, value: object, *, allow_empty: bool = False) -> str:
    normalized = _text(field_name, value, allow_empty=allow_empty)
    if not normalized and allow_empty:
        return ""
    if _LOCATOR_PATTERN.fullmatch(normalized) is None:
        raise ValueError(f"{field_name}_locator_invalid")
    return normalized


def factory_role_evidence_authority_stream(factory_run_id: str) -> str:
    """Return the one durable cutoff stream identity for a Factory run."""

    normalized_run_id = _locator("factory_run_id", factory_run_id)
    run_hash = hashlib.sha256(normalized_run_id.encode("utf-8")).hexdigest()
    return f"{_AUTHORITY_STREAM_PREFIX}{run_hash}"


def _hash64(field_name: str, value: object) -> str:
    if type(value) is not str:
        raise TypeError(f"{field_name}_type_invalid")
    if len(value) != _HASH_LENGTH or any(char not in "0123456789abcdef" for char in value):
        raise ValueError(f"{field_name}_invalid")
    return value


def _non_negative_int(field_name: str, value: object) -> int:
    if type(value) is not int:
        raise TypeError(f"{field_name}_type_invalid")
    if value < 0:
        raise ValueError(f"{field_name}_invalid")
    return value


def _positive_int(field_name: str, value: object) -> int:
    result = _non_negative_int(field_name, value)
    if result == 0:
        raise ValueError(f"{field_name}_invalid")
    return result


def _exact_mapping(record: object, expected_fields: frozenset[str], *, code: str) -> Mapping[str, Any]:
    if type(record) is not dict or any(type(key) is not str for key in record) or frozenset(record) != expected_fields:
        raise ValueError(code)
    return record
