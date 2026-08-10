"""Role final-request evidence policy definitions and lookups."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from polaris.kernelone.events.final_request_evidence._constants import (
    _ROLE_FINAL_REQUEST_POLICY_FIELDS,
    _ROLE_FINAL_REQUEST_POLICY_SPECS,
    _ROLE_FINAL_REQUEST_SOURCE_KEYS,
    ROLE_FINAL_REQUEST_POLICY_SCHEMA,
)
from polaris.kernelone.events.final_request_evidence._helpers import (
    _require_role_final_request_string,
    _validate_role_final_request_json,
)


def canonical_role_final_request_json(value: Any) -> str:
    """Return strict UTF-8 canonical JSON for provider-visible role facts."""

    _validate_role_final_request_json(value)
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def canonical_role_final_request_hash(value: Any) -> str:
    """Return SHA-256 of strict role-fact canonical JSON."""

    return hashlib.sha256(canonical_role_final_request_json(value).encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class RoleFinalRequestPolicyV1:
    """Exact ordered evidence policy for one canonical role."""

    schema_version: str
    role: str
    slot_order: tuple[str, ...]
    required_present_slots: tuple[str, ...]

    def __post_init__(self) -> None:
        _require_role_final_request_string("schema_version", self.schema_version)
        _require_role_final_request_string("role", self.role)
        if self.schema_version != ROLE_FINAL_REQUEST_POLICY_SCHEMA:
            raise ValueError("role_final_request_policy_schema_mismatch")
        expected = _ROLE_FINAL_REQUEST_POLICY_SPECS.get(self.role)
        if expected is None:
            raise ValueError(f"role_final_request_policy_unknown_role:{self.role or '<empty>'}")
        if (self.slot_order, self.required_present_slots) != expected:
            raise ValueError("role_final_request_policy_definition_mismatch")

    def _hash_payload(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "role": self.role,
            "slot_order": list(self.slot_order),
            "required_present_slots": list(self.required_present_slots),
        }

    @property
    def policy_hash(self) -> str:
        return canonical_role_final_request_hash(self._hash_payload())

    def to_record(self) -> dict[str, Any]:
        return {**self._hash_payload(), "policy_hash": self.policy_hash}

    @classmethod
    def from_record(cls, record: Mapping[str, Any]) -> RoleFinalRequestPolicyV1:
        if not isinstance(record, Mapping) or frozenset(record) != _ROLE_FINAL_REQUEST_POLICY_FIELDS:
            raise ValueError("role_final_request_policy_fields_mismatch")
        schema_version = _require_role_final_request_string("schema_version", record.get("schema_version"))
        role = _require_role_final_request_string("role", record.get("role"))
        policy_hash = _require_role_final_request_string("policy_hash", record.get("policy_hash"))
        raw_slots = record.get("slot_order")
        raw_required = record.get("required_present_slots")
        if not isinstance(raw_slots, (list, tuple)) or not isinstance(raw_required, (list, tuple)):
            raise ValueError("role_final_request_policy_definition_mismatch")
        if any(not isinstance(item, str) for item in (*raw_slots, *raw_required)):
            raise ValueError("role_final_request_policy_definition_mismatch")
        created = cls(
            schema_version=schema_version,
            role=role,
            slot_order=tuple(raw_slots),
            required_present_slots=tuple(raw_required),
        )
        if policy_hash != created.policy_hash:
            raise ValueError("role_final_request_policy_hash_mismatch")
        return created


_ROLE_FINAL_REQUEST_POLICIES: dict[str, RoleFinalRequestPolicyV1] = {
    role: RoleFinalRequestPolicyV1(
        schema_version=ROLE_FINAL_REQUEST_POLICY_SCHEMA,
        role=role,
        slot_order=spec[0],
        required_present_slots=spec[1],
    )
    for role, spec in _ROLE_FINAL_REQUEST_POLICY_SPECS.items()
}


def role_final_request_policy(role: str) -> RoleFinalRequestPolicyV1:
    """Return exact policy; unknown roles fail closed."""

    normalized = _require_role_final_request_string("role", role).strip()
    policy = _ROLE_FINAL_REQUEST_POLICIES.get(normalized)
    if policy is None:
        raise ValueError(f"role_final_request_policy_unknown_role:{normalized or '<empty>'}")
    return policy


def role_final_request_source_keys(ref_kind: str) -> tuple[str, ...]:
    """Return allowlisted structured source keys for one canonical slot."""

    normalized = _require_role_final_request_string("ref_kind", ref_kind).strip()
    keys = _ROLE_FINAL_REQUEST_SOURCE_KEYS.get(normalized)
    if keys is None:
        raise ValueError(f"role_final_request_source_unknown_slot:{normalized or '<empty>'}")
    return keys
