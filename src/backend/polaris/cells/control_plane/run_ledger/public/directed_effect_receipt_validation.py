"""Pure validation for authoritative TaskRuntime-directed effect receipts."""

from __future__ import annotations

import hashlib
import json
import math
from typing import Any, Mapping

DIRECTED_EFFECT_RECEIPT_V2_SCHEMA = "roles.adapters.director_physical_effect_receipt.v2"

_DIRECTED_EFFECT_RECEIPT_PAYLOAD_KEYS: tuple[str, ...] = (
    "arguments_hash",
    "authoritative",
    "batch_id",
    "claim_grant_hash",
    "context_id",
    "durable",
    "effect_call_id",
    "effect_operation_id",
    "normalized_tool_name",
    "operation_id",
    "parent_close_eligible",
    "physical_result_hash",
    "plan_hash",
    "policy_evidence_hash",
    "repair_binding_hash",
    "repair_contingency_kind",
    "repair_request_hash",
    "receipt_binding_hash",
    "receipt_outcome",
    "schema_version",
    "target_state_hash",
    "tool_call_id",
)
_DIRECTED_EFFECT_RECEIPT_ALLOWED_KEYS = frozenset(
    (*_DIRECTED_EFFECT_RECEIPT_PAYLOAD_KEYS, "receipt_hash", "receipt_id", "_task_runtime_receipt_commit")
)
_REQUIRED_IDENTIFIER_FIELDS = (
    "batch_id",
    "context_id",
    "normalized_tool_name",
    "operation_id",
    "schema_version",
    "tool_call_id",
)
_NULLABLE_IDENTIFIER_FIELDS = (
    "effect_call_id",
    "effect_operation_id",
    "repair_contingency_kind",
)
_REQUIRED_HASH_FIELDS = (
    "arguments_hash",
    "claim_grant_hash",
    "physical_result_hash",
    "policy_evidence_hash",
    "receipt_binding_hash",
    "target_state_hash",
)
_NULLABLE_HASH_FIELDS = (
    "plan_hash",
    "repair_binding_hash",
    "repair_request_hash",
)


def _is_lower_sha256(value: object) -> bool:
    return type(value) is str and len(value) == 64 and all(character in "0123456789abcdef" for character in value)


def _is_exact_nonempty_string(value: object) -> bool:
    return type(value) is str and bool(value) and value == value.strip()


def _receipt_payload_field_errors(receipt: Mapping[str, Any], *, prefix: str) -> list[str]:
    errors: list[str] = []
    for field in _REQUIRED_IDENTIFIER_FIELDS:
        if not _is_exact_nonempty_string(receipt.get(field)):
            errors.append(f"{prefix}:invalid_{field}")
    for field in _NULLABLE_IDENTIFIER_FIELDS:
        value = receipt.get(field)
        if value is not None and not _is_exact_nonempty_string(value):
            errors.append(f"{prefix}:invalid_{field}")
    for field in _REQUIRED_HASH_FIELDS:
        if not _is_lower_sha256(receipt.get(field)):
            errors.append(f"{prefix}:invalid_{field}")
    for field in _NULLABLE_HASH_FIELDS:
        value = receipt.get(field)
        if value is not None and not _is_lower_sha256(value):
            errors.append(f"{prefix}:invalid_{field}")
    if receipt.get("receipt_outcome") not in {"succeeded", "failed"}:
        errors.append(f"{prefix}:invalid_receipt_outcome")
    for flag in ("authoritative", "durable", "parent_close_eligible"):
        if receipt.get(flag) is not True:
            errors.append(f"{prefix}:{flag}_not_true")
    return errors


def directed_effect_receipt_payload_hash(receipt: Mapping[str, Any]) -> str | None:
    """Recompute the schema-v2 immutable payload hash without trusting its digest."""

    if any(key not in receipt for key in _DIRECTED_EFFECT_RECEIPT_PAYLOAD_KEYS):
        return None
    if _receipt_payload_field_errors(receipt, prefix="receipt"):
        return None
    canonical_items: list[tuple[str, tuple[object, ...]]] = []
    for key in _DIRECTED_EFFECT_RECEIPT_PAYLOAD_KEYS:
        value = receipt[key]
        if value is None:
            canonical_value: tuple[object, ...] = ("null",)
        elif type(value) is bool:
            canonical_value = ("bool", value)
        elif isinstance(value, int) and not isinstance(value, bool):
            canonical_value = ("int", value)
        elif isinstance(value, float):
            if not math.isfinite(value):
                return None
            canonical_value = ("float", value)
        elif isinstance(value, str):
            canonical_value = ("string", value)
        else:
            return None
        canonical_items.append((key, canonical_value))
    encoded = json.dumps(
        ("map", tuple(sorted(canonical_items))),
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _receipt_hash_errors(receipt: Mapping[str, Any], *, prefix: str) -> list[str]:
    errors: list[str] = []
    claimed_hash = receipt.get("receipt_hash")
    receipt_id = receipt.get("receipt_id")
    if not _is_lower_sha256(claimed_hash):
        return [f"{prefix}:invalid_receipt_hash"]
    assert isinstance(claimed_hash, str)
    recomputed_hash = directed_effect_receipt_payload_hash(receipt)
    if recomputed_hash is None:
        errors.append(f"{prefix}:invalid_receipt_payload")
    elif claimed_hash != recomputed_hash:
        errors.append(f"{prefix}:receipt_payload_hash_mismatch")
    elif receipt_id != f"director-physical-effect-{claimed_hash[:24]}":
        errors.append(f"{prefix}:receipt_id_hash_mismatch")
    return errors


def _receipt_shape_errors(receipt: Mapping[str, Any], *, prefix: str) -> list[str]:
    errors = _receipt_payload_field_errors(receipt, prefix=prefix)
    if not _is_exact_nonempty_string(receipt.get("receipt_id")):
        errors.append(f"{prefix}:invalid_receipt_id")
    return errors


def _receipt_commit_errors(
    receipt: Mapping[str, Any],
    commit: Mapping[str, Any] | None,
    *,
    prefix: str,
) -> list[str]:
    if commit is None:
        return [f"{prefix}:missing_task_runtime_commit"]
    errors: list[str] = []
    expected_values = {
        "operation_id": receipt.get("operation_id"),
        "receipt_ref": receipt.get("receipt_id"),
        "receipt_hash": receipt.get("receipt_hash"),
        "receipt_binding_hash": receipt.get("receipt_binding_hash"),
        "receipt_outcome": receipt.get("receipt_outcome"),
    }
    error_names = {
        "operation_id": "task_runtime_operation_mismatch",
        "receipt_ref": "task_runtime_receipt_ref_mismatch",
        "receipt_hash": "task_runtime_receipt_hash_mismatch",
        "receipt_binding_hash": "task_runtime_receipt_binding_hash_mismatch",
        "receipt_outcome": "task_runtime_receipt_outcome_mismatch",
    }
    if type(commit.get("code")) is not str or commit.get("code") not in {"receipt_committed", "idempotent_replay"}:
        errors.append(f"{prefix}:invalid_task_runtime_code")
    if type(commit.get("state")) is not str or commit.get("state") != "RECEIPT_COMMITTED":
        errors.append(f"{prefix}:invalid_task_runtime_state")
    if not _is_exact_nonempty_string(commit.get("event_id")):
        errors.append(f"{prefix}:invalid_task_runtime_event_id")
    version = commit.get("version")
    if isinstance(version, bool) or not isinstance(version, int) or version <= 0:
        errors.append(f"{prefix}:invalid_task_runtime_version")
    for field, expected in expected_values.items():
        actual = commit.get(field)
        if type(actual) is not str or type(expected) is not str or actual != expected:
            errors.append(f"{prefix}:{error_names[field]}")
    return errors


def directed_effect_receipt_v2_errors(
    receipt: Mapping[str, Any],
    commit: Mapping[str, Any] | None,
    *,
    prefix: str,
) -> tuple[str, ...] | None:
    """Return authoritative binding errors, or ``None`` for non-v2 legacy data."""

    schema_version = receipt.get("schema_version")
    if type(schema_version) is str and schema_version.strip() == DIRECTED_EFFECT_RECEIPT_V2_SCHEMA:
        if schema_version != DIRECTED_EFFECT_RECEIPT_V2_SCHEMA:
            return (f"{prefix}:invalid_schema_version",)
    elif schema_version != DIRECTED_EFFECT_RECEIPT_V2_SCHEMA:
        return None
    unexpected_fields = sorted(set(receipt) - _DIRECTED_EFFECT_RECEIPT_ALLOWED_KEYS)
    if unexpected_fields:
        return (f"{prefix}:unexpected_receipt_fields:{','.join(unexpected_fields)}",)
    errors = _receipt_shape_errors(receipt, prefix=prefix)
    errors.extend(_receipt_hash_errors(receipt, prefix=prefix))
    errors.extend(_receipt_commit_errors(receipt, commit, prefix=prefix))
    return tuple(errors)
