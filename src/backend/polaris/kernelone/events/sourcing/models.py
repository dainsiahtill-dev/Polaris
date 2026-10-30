"""Event sourcing models shared by runtime event producers/consumers.

This module is intentionally KernelOne-only and business-agnostic.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from polaris.kernelone.utils.time_utils import utc_now_str

_TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+$")
_INTEGRITY_DIGEST_FIELD = "integrity_digest"
_STRICT_DIGEST_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_STRICT_REQUIRED_RECORD_FIELDS = frozenset(
    {
        "schema_version",
        "event_id",
        "stream",
        "event_type",
        "event_version",
        "seq",
        "occurred_at",
        "source",
        "payload",
        "metadata",
        _INTEGRITY_DIGEST_FIELD,
    }
)
_STRICT_OPTIONAL_RECORD_FIELDS = frozenset({"aggregate_id", "correlation_id", "causation_id"})
_STRICT_RECORD_FIELDS = _STRICT_REQUIRED_RECORD_FIELDS | _STRICT_OPTIONAL_RECORD_FIELDS


class EventRecordValidationError(ValueError):
    """A raw event record cannot satisfy the strict envelope contract."""

    def __init__(self, *, field: str, message: str) -> None:
        super().__init__(message)
        self.field = field


class StrictEventRecordError(ValueError):
    """Typed, payload-safe failure produced by the strict raw JSON decoder."""

    def __init__(self, *, reason: str, field: str | None = None) -> None:
        super().__init__("strict event record corruption")
        self.code = "strict_record_corruption"
        self.details: dict[str, str] = {"reason": reason}
        if field is not None:
            self.details["field"] = field


def _strict_object_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    record: dict[str, Any] = {}
    for key, value in pairs:
        if key in record:
            raise StrictEventRecordError(reason="duplicate_key", field=key)
        record[key] = value
    return record


def _reject_non_finite_json_constant(_value: str) -> None:
    raise StrictEventRecordError(reason="non_finite_number")


def _validate_canonical_json_value(value: Any, *, field: str) -> None:
    """Accept only JSON values without normalizing strings or object keys."""

    value_type = type(value)
    if value is None or value_type in (bool, int, str):
        return
    if value_type is float:
        if math.isfinite(value):
            return
        raise StrictEventRecordError(reason="non_finite_number", field=field)
    if value_type is list:
        for item in value:
            _validate_canonical_json_value(item, field=field)
        return
    if value_type is dict:
        for key, item in value.items():
            if type(key) is not str:
                raise StrictEventRecordError(reason="non_string_object_key", field=field)
            _validate_canonical_json_value(item, field=field)
        return
    raise StrictEventRecordError(reason="non_canonical_json_value", field=field)


def _require_strict_positive_int(record: Mapping[str, Any], field: str) -> int:
    value = record[field]
    if type(value) is not int or value < 1:
        raise StrictEventRecordError(reason="invalid_integer", field=field)
    return value


def _require_strict_non_empty_str(record: Mapping[str, Any], field: str) -> str:
    value = record[field]
    if type(value) is not str or not value:
        raise StrictEventRecordError(reason="invalid_string", field=field)
    return value


def _decode_strict_raw_record(raw_record: str) -> dict[str, Any]:
    if type(raw_record) is not str:
        raise StrictEventRecordError(reason="record_not_text")
    try:
        record = json.loads(
            raw_record,
            object_pairs_hook=_strict_object_pairs,
            parse_constant=_reject_non_finite_json_constant,
        )
    except StrictEventRecordError:
        raise
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise StrictEventRecordError(reason="invalid_json") from exc
    if type(record) is not dict:
        raise StrictEventRecordError(reason="record_not_object")
    return record


def decode_strict_event_record(raw_record: str) -> EventEnvelope:
    """Decode one strict JSON event record before constructing its model.

    Strict records preserve Unicode exactly as decoded by JSON. They do not
    normalize strings, coerce scalar types, or replace invalid objects with
    defaults. Duplicate object keys are rejected at every nesting level.
    """

    record = _decode_strict_raw_record(raw_record)
    missing_fields = _STRICT_REQUIRED_RECORD_FIELDS.difference(record)
    if missing_fields:
        raise StrictEventRecordError(reason="missing_required_field", field=sorted(missing_fields)[0])
    unexpected_fields = set(record).difference(_STRICT_RECORD_FIELDS)
    if unexpected_fields:
        raise StrictEventRecordError(reason="unknown_field", field=sorted(unexpected_fields)[0])

    schema_version = _require_strict_positive_int(record, "schema_version")
    event_version = _require_strict_positive_int(record, "event_version")
    seq = _require_strict_positive_int(record, "seq")
    if schema_version != 1:
        raise StrictEventRecordError(reason="unknown_schema_version", field="schema_version")
    if event_version != 1:
        raise StrictEventRecordError(reason="unknown_event_version", field="event_version")

    event_id = _require_strict_non_empty_str(record, "event_id")
    stream = _require_strict_non_empty_str(record, "stream")
    event_type = _require_strict_non_empty_str(record, "event_type")
    occurred_at = _require_strict_non_empty_str(record, "occurred_at")
    source = _require_strict_non_empty_str(record, "source")
    for mapping_field in ("payload", "metadata"):
        if type(record[mapping_field]) is not dict:
            raise StrictEventRecordError(reason="invalid_mapping", field=mapping_field)
        _validate_canonical_json_value(record[mapping_field], field=mapping_field)
    for optional_field in _STRICT_OPTIONAL_RECORD_FIELDS:
        if optional_field in record and record[optional_field] is not None:
            _require_strict_non_empty_str(record, optional_field)

    actual_digest = record[_INTEGRITY_DIGEST_FIELD]
    if type(actual_digest) is not str or not _STRICT_DIGEST_PATTERN.fullmatch(actual_digest):
        raise StrictEventRecordError(reason="invalid_digest", field=_INTEGRITY_DIGEST_FIELD)
    expected_digest = EventEnvelope.integrity_digest_for_record(record)
    if actual_digest != expected_digest:
        raise StrictEventRecordError(reason="integrity_digest_mismatch", field=_INTEGRITY_DIGEST_FIELD)

    return EventEnvelope(
        event_id=event_id,
        stream=stream,
        event_type=event_type,
        event_version=event_version,
        seq=seq,
        occurred_at=occurred_at,
        source=source,
        aggregate_id=record.get("aggregate_id"),
        correlation_id=record.get("correlation_id"),
        causation_id=record.get("causation_id"),
        payload=record["payload"],
        metadata=record["metadata"],
    )


def _require_token(name: str, value: str) -> str:
    token = str(value or "").strip()
    if not token:
        raise ValueError(f"{name} must be a non-empty string")
    if not _TOKEN_PATTERN.fullmatch(token):
        raise ValueError(f"{name} contains invalid characters: {value!r}")
    return token


def _require_optional_token(name: str, value: str | None) -> str | None:
    if value is None:
        return None
    token = str(value).strip()
    if not token:
        return None
    if not _TOKEN_PATTERN.fullmatch(token):
        raise ValueError(f"{name} contains invalid characters: {value!r}")
    return token


def _require_positive(name: str, value: int) -> int:
    coerced = int(value)
    if coerced < 1:
        raise ValueError(f"{name} must be >= 1")
    return coerced


def _require_exact_positive_record_int(name: str, value: Any) -> int:
    """Validate a strict JSON integer without Python's coercive conversions."""

    if type(value) is not int or value < 1:
        raise EventRecordValidationError(
            field=name,
            message=f"{name} must be an exact positive JSON integer",
        )
    return value


def utc_now_iso() -> str:
    return utc_now_str()


def new_event_id() -> str:
    return uuid.uuid4().hex


@dataclass(frozen=True)
class EventEnvelope:
    """Canonical versioned event envelope."""

    event_id: str
    stream: str
    event_type: str
    event_version: int
    seq: int
    occurred_at: str
    source: str
    payload: Mapping[str, Any] = field(default_factory=dict)
    metadata: Mapping[str, Any] = field(default_factory=dict)
    aggregate_id: str | None = None
    correlation_id: str | None = None
    causation_id: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "event_id", _require_token("event_id", self.event_id))
        object.__setattr__(self, "stream", _require_token("stream", self.stream))
        object.__setattr__(self, "event_type", _require_token("event_type", self.event_type))
        object.__setattr__(self, "event_version", _require_positive("event_version", self.event_version))
        object.__setattr__(self, "seq", _require_positive("seq", self.seq))
        object.__setattr__(self, "source", _require_token("source", self.source))

        occurred_at = str(self.occurred_at or "").strip()
        if not occurred_at:
            raise ValueError("occurred_at must be a non-empty string")
        object.__setattr__(self, "occurred_at", occurred_at)

        payload_copy = dict(self.payload or {})
        metadata_copy = dict(self.metadata or {})
        object.__setattr__(self, "payload", payload_copy)
        object.__setattr__(self, "metadata", metadata_copy)
        object.__setattr__(
            self,
            "aggregate_id",
            _require_optional_token("aggregate_id", self.aggregate_id),
        )
        object.__setattr__(
            self,
            "correlation_id",
            _require_optional_token("correlation_id", self.correlation_id),
        )
        object.__setattr__(
            self,
            "causation_id",
            _require_optional_token("causation_id", self.causation_id),
        )

    def to_record(self, *, include_integrity_digest: bool = False) -> dict[str, Any]:
        record = {
            "schema_version": 1,
            "event_id": self.event_id,
            "stream": self.stream,
            "event_type": self.event_type,
            "event_version": self.event_version,
            "seq": self.seq,
            "occurred_at": self.occurred_at,
            "source": self.source,
            "aggregate_id": self.aggregate_id,
            "correlation_id": self.correlation_id,
            "causation_id": self.causation_id,
            "payload": dict(self.payload),
            "metadata": dict(self.metadata),
        }
        if include_integrity_digest:
            record[_INTEGRITY_DIGEST_FIELD] = self.integrity_digest_for_record(record)
        return record

    @staticmethod
    def integrity_digest_for_record(record: Mapping[str, Any]) -> str:
        """Hash the canonical record while excluding its self-referential digest."""

        canonical_record = dict(record)
        canonical_record.pop(_INTEGRITY_DIGEST_FIELD, None)
        encoded = json.dumps(
            canonical_record,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    @classmethod
    def from_record(
        cls,
        record: Mapping[str, Any],
        *,
        strict_integrity: bool = False,
    ) -> EventEnvelope:
        if strict_integrity:
            raise ValueError("strict_integrity requires decode_strict_event_record raw JSON input")
        payload = record.get("payload") if isinstance(record.get("payload"), Mapping) else {}
        metadata = record.get("metadata") if isinstance(record.get("metadata"), Mapping) else {}
        return cls(
            event_id=str(record.get("event_id") or ""),
            stream=str(record.get("stream") or ""),
            event_type=str(record.get("event_type") or ""),
            event_version=(record["event_version"] if strict_integrity else int(record.get("event_version") or 1)),
            seq=record["seq"] if strict_integrity else int(record.get("seq") or 0),
            occurred_at=str(record.get("occurred_at") or ""),
            source=str(record.get("source") or ""),
            aggregate_id=str(record.get("aggregate_id") or "").strip() or None,
            correlation_id=str(record.get("correlation_id") or "").strip() or None,
            causation_id=str(record.get("causation_id") or "").strip() or None,
            payload=dict(payload) if isinstance(payload, dict) else {},
            metadata=dict(metadata) if isinstance(metadata, dict) else {},
        )


@dataclass(frozen=True)
class EventQueryResult:
    """Query result for an event stream slice."""

    stream: str
    storage_path: str
    events: tuple[EventEnvelope, ...]
    total: int
    next_offset: int


class EventSourcingError(RuntimeError):
    """Raised when event sourcing operations fail."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "event_sourcing_error",
        details: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.details = dict(details or {})


class ExpectedSequenceDriftError(EventSourcingError):
    """The requested append sequence does not match the verified stream head."""

    def __init__(self, message: str, *, requested_seq: int, actual_seq: int) -> None:
        super().__init__(
            message,
            code="expected_seq_drift",
            details={"requested_seq": requested_seq, "actual_seq": actual_seq},
        )


class IdempotencyConflictError(EventSourcingError):
    """An idempotency key already identifies a different semantic event."""

    def __init__(self, message: str, *, drift_fields: list[str]) -> None:
        super().__init__(
            message,
            code="idempotency_conflict",
            details={"drift_fields": tuple(drift_fields)},
        )
