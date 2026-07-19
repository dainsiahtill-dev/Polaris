"""Strict, bounded hash-chain contracts for authoritative Factory events.

This module is intentionally pure.  Filesystem locking and durability belong to
``FactoryStore``; this module owns canonical admission/event DTOs plus complete
prefix validation so appenders and readers share one fail-closed grammar.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final

from polaris.kernelone.events.final_request_evidence import (
    canonical_role_final_request_hash,
    canonical_role_final_request_json,
)

FACTORY_EVENT_CHAIN_SCHEMA: Final[str] = "factory.event_chain.v1"
FACTORY_EVENT_CHAIN_ZERO_HASH: Final[str] = "0" * 64
FACTORY_RUN_ADMITTED_SCHEMA: Final[str] = "factory.run_admitted.v1"
FACTORY_EVENT_CHAIN_MAX_RECORDS: Final[int] = 4096
FACTORY_EVENT_CHAIN_MAX_BYTES: Final[int] = 8 * 1024 * 1024

_CHAIN_HASH_DOMAIN: Final[str] = "polaris.factory.event_chain.v1"
_CHAIN_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "chain_schema_version",
        "chain_sequence",
        "chain_previous_hash",
        "chain_event_hash",
    }
)
_SHA256_RE: Final[re.Pattern[str]] = re.compile(r"[0-9a-f]{64}")


class FactoryEventChainError(RuntimeError):
    """Fail-closed event-chain validation or capacity error."""

    def __init__(self, code: str, message: str, *, details: Mapping[str, object] | None = None) -> None:
        super().__init__(message)
        self.code = str(code)
        self.details = dict(details or {})


def _fail(code: str, message: str, **details: object) -> FactoryEventChainError:
    return FactoryEventChainError(code, message, details=details)


def _require_text(field_name: str, value: object) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field_name}_must_be_non_empty_string")
    return value


@dataclass(frozen=True, slots=True)
class FactoryRunAdmissionV1:
    """Detached immutable intent admitted before mutable ``run.json`` exists."""

    factory_run_id: str
    created_at: str
    name: str
    description: str | None

    def __post_init__(self) -> None:
        _require_text("factory_run_id", self.factory_run_id)
        _require_text("created_at", self.created_at)
        _require_text("name", self.name)
        if self.description is not None and not isinstance(self.description, str):
            raise ValueError("description_must_be_string_or_none")

    def to_payload(self) -> dict[str, str | None]:
        """Return the exact four-field authority payload; metadata cannot enter."""

        return {
            "factory_run_id": self.factory_run_id,
            "created_at": self.created_at,
            "name": self.name,
            "description": self.description,
        }


def build_factory_run_admitted_event(admission: FactoryRunAdmissionV1) -> dict[str, Any]:
    """Build the detached admission event before chain fields are assigned."""

    if not isinstance(admission, FactoryRunAdmissionV1):
        raise TypeError("FactoryRunAdmissionV1 required")
    payload = admission.to_payload()
    return {
        "type": "factory_run_admitted",
        "schema_version": FACTORY_RUN_ADMITTED_SCHEMA,
        "payload": payload,
        "canonical_sha256": canonical_role_final_request_hash(payload),
    }


def _event_hash(record_without_hash: Mapping[str, Any]) -> str:
    try:
        return canonical_role_final_request_hash(
            {
                "domain": _CHAIN_HASH_DOMAIN,
                "event": dict(record_without_hash),
            }
        )
    except ValueError as exc:
        raise _fail("factory_event_chain_noncanonical_json", "event contains non-canonical JSON") from exc


def encode_factory_event_record(record: Mapping[str, Any]) -> bytes:
    """Encode one record as canonical UTF-8 JSONL, including its final newline."""

    if not isinstance(record, Mapping):
        raise TypeError("factory event record must be a mapping")
    try:
        return (canonical_role_final_request_json(dict(record)) + "\n").encode("utf-8")
    except ValueError as exc:
        raise _fail("factory_event_chain_noncanonical_json", "event contains non-canonical JSON") from exc


def _validate_record(
    record: Mapping[str, Any],
    *,
    run_id: str,
    expected_sequence: int,
    expected_previous_hash: str,
    seen_event_ids: set[str],
) -> dict[str, Any]:
    normalized = dict(record)
    if normalized.get("chain_schema_version") != FACTORY_EVENT_CHAIN_SCHEMA:
        raise _fail(
            "factory_event_chain_schema_mismatch",
            "Factory event chain schema is missing or unsupported",
            sequence=expected_sequence,
        )
    sequence = normalized.get("chain_sequence")
    if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence != expected_sequence:
        raise _fail(
            "factory_event_chain_sequence_mismatch",
            "Factory event chain sequence is not continuous",
            expected=expected_sequence,
            actual=sequence,
        )
    previous_hash = normalized.get("chain_previous_hash")
    if not isinstance(previous_hash, str) or not _SHA256_RE.fullmatch(previous_hash):
        raise _fail("factory_event_chain_previous_hash_invalid", "Factory previous hash is not lowercase SHA-256")
    if previous_hash != expected_previous_hash:
        raise _fail(
            "factory_event_chain_previous_hash_mismatch",
            "Factory previous hash does not match the validated prefix head",
            sequence=expected_sequence,
        )
    event_hash = normalized.get("chain_event_hash")
    if not isinstance(event_hash, str) or not _SHA256_RE.fullmatch(event_hash):
        raise _fail("factory_event_chain_event_hash_invalid", "Factory event hash is not lowercase SHA-256")
    if normalized.get("run_id") != run_id:
        raise _fail(
            "factory_event_chain_run_mismatch",
            "Factory event belongs to a different run",
            sequence=expected_sequence,
        )
    event_id = normalized.get("event_id")
    if not isinstance(event_id, str) or not event_id:
        raise _fail("factory_event_chain_event_id_invalid", "Factory event id must be a non-empty string")
    if event_id in seen_event_ids:
        raise _fail(
            "factory_event_chain_duplicate_event_id",
            "Factory event id is duplicated in the authoritative prefix",
            event_id=event_id,
        )
    expected_hash = _event_hash({key: value for key, value in normalized.items() if key != "chain_event_hash"})
    if event_hash != expected_hash:
        raise _fail(
            "factory_event_chain_hash_mismatch",
            "Factory event hash does not match canonical record content",
            sequence=expected_sequence,
        )
    seen_event_ids.add(event_id)
    return normalized


def _validate_admission_genesis(record: Mapping[str, Any], *, run_id: str) -> None:
    if record.get("type") != "factory_run_admitted" or record.get("schema_version") != FACTORY_RUN_ADMITTED_SCHEMA:
        raise _fail(
            "factory_event_chain_genesis_invalid",
            "Sequence one must be the immutable Factory run admission fact",
        )
    payload = record.get("payload")
    if not isinstance(payload, Mapping) or set(payload) != {
        "factory_run_id",
        "created_at",
        "name",
        "description",
    }:
        raise _fail(
            "factory_event_chain_admission_payload_invalid",
            "Factory admission payload must contain exactly four frozen fields",
        )
    admission = FactoryRunAdmissionV1(
        factory_run_id=payload.get("factory_run_id"),  # type: ignore[arg-type]
        created_at=payload.get("created_at"),  # type: ignore[arg-type]
        name=payload.get("name"),  # type: ignore[arg-type]
        description=payload.get("description"),  # type: ignore[arg-type]
    )
    if admission.factory_run_id != run_id:
        raise _fail("factory_event_chain_run_mismatch", "Admission payload belongs to a different run")
    if record.get("canonical_sha256") != canonical_role_final_request_hash(admission.to_payload()):
        raise _fail(
            "factory_event_chain_admission_hash_mismatch",
            "Factory admission payload hash does not match its exact detached content",
        )


def validate_factory_event_chain(
    records: Sequence[Mapping[str, Any]],
    *,
    run_id: str,
) -> tuple[dict[str, Any], ...]:
    """Validate one complete in-memory prefix without skipping any record."""

    _require_text("run_id", run_id)
    if len(records) > FACTORY_EVENT_CHAIN_MAX_RECORDS:
        raise _fail(
            "factory_event_chain_record_limit_exceeded",
            "Factory event chain exceeds the record bound",
            limit=FACTORY_EVENT_CHAIN_MAX_RECORDS,
        )
    validated: list[dict[str, Any]] = []
    seen_event_ids: set[str] = set()
    previous_hash = FACTORY_EVENT_CHAIN_ZERO_HASH
    encoded_size = 0
    for sequence, record in enumerate(records, start=1):
        if not isinstance(record, Mapping):
            raise _fail(
                "factory_event_chain_record_not_object",
                "Factory event record must be a JSON object",
                sequence=sequence,
            )
        normalized = _validate_record(
            record,
            run_id=run_id,
            expected_sequence=sequence,
            expected_previous_hash=previous_hash,
            seen_event_ids=seen_event_ids,
        )
        if sequence == 1:
            try:
                _validate_admission_genesis(normalized, run_id=run_id)
            except (TypeError, ValueError) as exc:
                raise _fail(
                    "factory_event_chain_admission_payload_invalid",
                    "Factory admission payload has invalid field types",
                ) from exc
        elif normalized.get("type") == "factory_run_admitted":
            raise _fail(
                "factory_event_chain_duplicate_admission",
                "Factory event chain must contain exactly one run admission at sequence one",
                sequence=sequence,
            )
        encoded_size += len(encode_factory_event_record(normalized))
        if encoded_size > FACTORY_EVENT_CHAIN_MAX_BYTES:
            raise _fail(
                "factory_event_chain_byte_limit_exceeded",
                "Factory event chain exceeds the byte bound",
                limit=FACTORY_EVENT_CHAIN_MAX_BYTES,
            )
        validated.append(normalized)
        previous_hash = str(normalized["chain_event_hash"])
    return tuple(validated)


def _reject_json_constant(token: str) -> object:
    raise ValueError(f"non_finite_json_constant:{token}")


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate_json_key:{key}")
        result[key] = value
    return result


def decode_factory_event_chain(raw: bytes, *, run_id: str) -> tuple[dict[str, Any], ...]:
    """Decode and validate exact stored UTF-8 JSONL bytes as one strict prefix."""

    if not isinstance(raw, bytes):
        raise TypeError("factory event chain bytes required")
    if len(raw) > FACTORY_EVENT_CHAIN_MAX_BYTES:
        raise _fail(
            "factory_event_chain_byte_limit_exceeded",
            "Factory event chain exceeds the byte bound",
            limit=FACTORY_EVENT_CHAIN_MAX_BYTES,
        )
    if not raw:
        return ()
    if not raw.endswith(b"\n"):
        raise _fail("factory_event_chain_half_record", "Factory event chain lacks a final newline")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise _fail("factory_event_chain_invalid_utf8", "Factory event chain is not valid UTF-8") from exc
    lines = text.splitlines()
    if len(lines) > FACTORY_EVENT_CHAIN_MAX_RECORDS:
        raise _fail(
            "factory_event_chain_record_limit_exceeded",
            "Factory event chain exceeds the record bound",
            limit=FACTORY_EVENT_CHAIN_MAX_RECORDS,
        )
    records: list[dict[str, Any]] = []
    for line_number, line in enumerate(lines, start=1):
        if not line:
            raise _fail(
                "factory_event_chain_blank_record",
                "Factory event chain contains a blank record",
                line=line_number,
            )
        try:
            value = json.loads(
                line,
                object_pairs_hook=_strict_object,
                parse_constant=_reject_json_constant,
            )
        except (json.JSONDecodeError, ValueError) as exc:
            raise _fail(
                "factory_event_chain_invalid_json",
                "Factory event chain contains invalid strict JSON",
                line=line_number,
            ) from exc
        if not isinstance(value, dict):
            raise _fail(
                "factory_event_chain_record_not_object",
                "Factory event record must be a JSON object",
                line=line_number,
            )
        records.append(value)
    if records and not any(field in records[0] for field in _CHAIN_FIELDS):
        raise _fail(
            "factory_event_chain_legacy_ineligible",
            "Unchained Factory events are readable only through the compatibility reader",
        )
    return validate_factory_event_chain(records, run_id=run_id)


def build_next_factory_event_record(
    prefix: Sequence[Mapping[str, Any]],
    *,
    run_id: str,
    event: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate the complete prefix, then assign the sole valid next CAS record."""

    validated = validate_factory_event_chain(prefix, run_id=run_id)
    if not isinstance(event, Mapping):
        raise TypeError("factory event must be a mapping")
    event_payload = dict(event)
    forged_fields = sorted(_CHAIN_FIELDS.intersection(event_payload))
    if forged_fields:
        raise _fail(
            "factory_event_chain_fields_preassigned",
            "Caller may not preassign authoritative chain fields",
            fields=forged_fields,
        )
    if event_payload.get("run_id") != run_id:
        raise _fail("factory_event_chain_run_mismatch", "Factory event belongs to a different run")
    event_id = event_payload.get("event_id")
    if not isinstance(event_id, str) or not event_id:
        raise _fail("factory_event_chain_event_id_invalid", "Factory event id must be a non-empty string")
    if any(record.get("event_id") == event_id for record in validated):
        raise _fail(
            "factory_event_chain_duplicate_event_id",
            "Factory event id is duplicated in the authoritative prefix",
            event_id=event_id,
        )
    sequence = len(validated) + 1
    if sequence > FACTORY_EVENT_CHAIN_MAX_RECORDS:
        raise _fail(
            "factory_event_chain_record_limit_exceeded",
            "Factory event chain exceeds the record bound",
            limit=FACTORY_EVENT_CHAIN_MAX_RECORDS,
        )
    previous_hash = validated[-1]["chain_event_hash"] if validated else FACTORY_EVENT_CHAIN_ZERO_HASH
    record_without_hash = {
        **event_payload,
        "chain_schema_version": FACTORY_EVENT_CHAIN_SCHEMA,
        "chain_sequence": sequence,
        "chain_previous_hash": previous_hash,
    }
    record = {**record_without_hash, "chain_event_hash": _event_hash(record_without_hash)}
    validate_factory_event_chain((*validated, record), run_id=run_id)
    return record


__all__ = [
    "FACTORY_EVENT_CHAIN_MAX_BYTES",
    "FACTORY_EVENT_CHAIN_MAX_RECORDS",
    "FACTORY_EVENT_CHAIN_SCHEMA",
    "FACTORY_EVENT_CHAIN_ZERO_HASH",
    "FACTORY_RUN_ADMITTED_SCHEMA",
    "FactoryEventChainError",
    "FactoryRunAdmissionV1",
    "build_factory_run_admitted_event",
    "build_next_factory_event_record",
    "decode_factory_event_chain",
    "encode_factory_event_record",
    "validate_factory_event_chain",
]
