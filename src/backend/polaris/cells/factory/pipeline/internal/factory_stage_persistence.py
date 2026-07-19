"""Strict Factory stage persistence transaction contracts and reducer.

This module is deliberately pure.  It validates the authoritative event-chain
projection and the current mutable pointer, but performs no file or event I/O.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping

from .factory_run_models import FactoryRunStatus

_INTENT_SCHEMA = "factory.stage_persistence_intent.v1"
_MARKER_SCHEMA = "factory.stage_persistence_committed.v1"
_POINTER_SCHEMA = "factory.last_stage_commit.v1"
_QUARANTINE_SCHEMA = "factory.run_quarantined.v1"

_HASH_HEX = frozenset("0123456789abcdef")
_MAX_IDENTITY_UTF8_BYTES = 256
_MAX_CHECKPOINT_REF_UTF8_BYTES = 1024
_MAX_CHECKPOINT_FILENAME_UTF8_BYTES = 512
_CHECKPOINT_FILENAME_PATTERN = re.compile(
    r"^(?P<status>[a-z][a-z0-9_-]{0,63})_"
    r"(?P<timestamp>\d{4}-\d{2}-\d{2}T\d{2}_\d{2}_\d{2}"
    r"(?:\.\d{1,9})?(?:Z|[+-]\d{2}_\d{2}))\.json$"
)
_EVENT_ENVELOPE_FIELDS = frozenset(
    {
        "type",
        "run_id",
        "event_id",
        "timestamp",
        "chain_schema_version",
        "chain_sequence",
        "chain_previous_hash",
        "chain_event_hash",
    }
)
_INTENT_FIELDS = frozenset(
    {
        "schema_version",
        "factory_run_id",
        "stage",
        "stage_result_canonical_sha256",
        "checkpoint_ref",
        "persistence_intent_sha256",
    }
)
_MARKER_PAYLOAD_FIELDS = frozenset(
    {
        "schema_version",
        "factory_run_id",
        "stage",
        "stage_completed_event_id",
        "stage_completed_chain_sequence",
        "stage_completed_chain_event_hash",
        "persistence_intent_sha256",
        "run_snapshot_canonical_sha256",
        "checkpoint_ref",
        "checkpoint_canonical_sha256",
    }
)
_POINTER_FIELDS = frozenset(
    {
        "schema_version",
        "stage",
        "stage_completed_event_id",
        "stage_completed_chain_sequence",
        "stage_completed_chain_event_hash",
        "persistence_intent_sha256",
        "checkpoint_ref",
    }
)
_QUARANTINE_PAYLOAD_FIELDS = frozenset(
    {
        "schema_version",
        "factory_run_id",
        "stage",
        "failed_step",
        "stage_completed_event_id",
        "stage_completed_chain_sequence",
        "stage_completed_chain_event_hash",
        "persistence_intent_sha256",
        "error_type",
        "error_message",
    }
)
_QUARANTINE_FAILED_STEPS = frozenset({"save_run", "checkpoint", "commit_marker", "cancelled_before_commit_ack"})


class FactoryStagePersistenceError(RuntimeError):
    """Typed fail-closed Factory stage-persistence error."""

    def __init__(self, code: str, message: str, *, details: Mapping[str, object] | None = None) -> None:
        self.code = code
        self.details = dict(details or {})
        super().__init__(f"{code}: {message}")


def _fail(code: str, message: str, **details: object) -> FactoryStagePersistenceError:
    return FactoryStagePersistenceError(code, message, details=details)


def _canonical_bytes(value: object) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise _fail("canonical_json_invalid", "Value is not strict canonical JSON") from exc


def _canonical_document_hash(domain: str, document: object) -> str:
    """Hash every frozen document with one exact domain-separated shape."""

    return hashlib.sha256(_canonical_bytes({"domain": domain, "document": document})).hexdigest()


def canonical_stage_result_sha256(stage_result: Mapping[str, Any]) -> str:
    if not isinstance(stage_result, Mapping):
        raise _fail("stage_result_invalid", "Stage result must be an object")
    return _canonical_document_hash("polaris.factory.stage_result.v1", dict(stage_result))


def canonical_run_snapshot_sha256(run_snapshot: Mapping[str, Any]) -> str:
    if not isinstance(run_snapshot, Mapping):
        raise _fail("run_snapshot_invalid", "Run snapshot must be an object")
    return _canonical_document_hash("polaris.factory.run_snapshot.v1", dict(run_snapshot))


def canonical_checkpoint_sha256(checkpoint: Mapping[str, Any]) -> str:
    if not isinstance(checkpoint, Mapping):
        raise _fail("checkpoint_invalid", "Checkpoint must be an object")
    return _canonical_document_hash("polaris.factory.run_checkpoint.v1", dict(checkpoint))


def _exact_object(record: object, fields: frozenset[str], *, code: str) -> dict[str, Any]:
    if not isinstance(record, Mapping):
        raise _fail(code, "Record must be an object")
    value = dict(record)
    if set(value) != fields:
        raise _fail(
            code,
            "Record fields do not match the frozen schema",
            missing=sorted(fields.difference(value)),
            extra=sorted(set(value).difference(fields)),
        )
    return value


def _payload_object(record: object, fields: frozenset[str], *, code: str) -> dict[str, Any]:
    if not isinstance(record, Mapping):
        raise _fail(code, "Event record must be an object")
    value = dict(record)
    payload = {key: item for key, item in value.items() if key not in _EVENT_ENVELOPE_FIELDS}
    if set(payload) != fields:
        raise _fail(
            code,
            "Event payload fields do not match the frozen schema",
            missing=sorted(fields.difference(payload)),
            extra=sorted(set(payload).difference(fields)),
        )
    return value


def _text(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip() or "\x00" in value:
        raise _fail("field_invalid", f"{field} must be a non-empty exact string", field=field)
    return value


def _safe_identity(value: object, *, field: str) -> str:
    if type(value) is not str:
        raise _fail("field_invalid", f"{field} must be an exact string", field=field)
    normalized = unicodedata.normalize("NFC", value)
    if (
        not normalized
        or normalized != value
        or normalized != normalized.strip()
        or len(normalized.encode("utf-8")) > _MAX_IDENTITY_UTF8_BYTES
        or normalized in {".", ".."}
        or any(character in normalized for character in ("/", "\\", "\x00"))
        or any(ord(character) < 0x20 or ord(character) == 0x7F for character in normalized)
    ):
        raise _fail("field_invalid", f"{field} must be a safe bounded identity", field=field)
    return normalized


def _checkpoint_ref(value: object, *, factory_run_id: str) -> str:
    run_id = _safe_identity(factory_run_id, field="factory_run_id")
    if type(value) is not str:
        raise _fail("checkpoint_ref_invalid", "checkpoint_ref must be an exact string")
    normalized = unicodedata.normalize("NFC", value)
    prefix = f"runtime/{run_id}/checkpoints/"
    filename = normalized[len(prefix) :] if normalized.startswith(prefix) else ""
    match = _CHECKPOINT_FILENAME_PATTERN.fullmatch(filename)
    if (
        normalized != value
        or normalized != normalized.strip()
        or len(normalized.encode("utf-8")) > _MAX_CHECKPOINT_REF_UTF8_BYTES
        or not filename
        or len(filename.encode("utf-8")) > _MAX_CHECKPOINT_FILENAME_UTF8_BYTES
        or filename in {".", "..", ".json"}
        or match is None
        or any(character in filename for character in ("/", "\\", "\x00"))
        or any(ord(character) < 0x20 or ord(character) == 0x7F for character in normalized)
    ):
        raise _fail(
            "checkpoint_ref_invalid",
            "checkpoint_ref must be the exact canonical current-run immutable checkpoint ref",
            factory_run_id=run_id,
        )
    assert match is not None
    try:
        FactoryRunStatus(match.group("status"))
    except ValueError as exc:
        raise _fail(
            "checkpoint_ref_invalid",
            "checkpoint_ref status must be an internal FactoryRunStatus",
            factory_run_id=run_id,
        ) from exc
    timestamp = match.group("timestamp")
    try:
        datetime.fromisoformat(timestamp.replace("_", ":").replace("Z", "+00:00"))
    except ValueError as exc:
        raise _fail(
            "checkpoint_ref_invalid",
            "checkpoint_ref timestamp must be a bounded ISO-8601 instant",
            factory_run_id=run_id,
        ) from exc
    return normalized


def _hash(value: object, *, field: str) -> str:
    if not isinstance(value, str) or len(value) != 64 or any(char not in _HASH_HEX for char in value):
        raise _fail("field_invalid", f"{field} must be a lowercase SHA-256", field=field)
    return value


def _positive_int(value: object, *, field: str, code: str = "field_invalid") -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise _fail(code, f"{field} must be a positive exact integer", field=field)
    return value


@dataclass(frozen=True, slots=True)
class FactoryStagePersistenceIntentV1:
    factory_run_id: str
    stage: str
    stage_result_canonical_sha256: str
    checkpoint_ref: str
    persistence_intent_sha256: str
    schema_version: str = _INTENT_SCHEMA

    def _hash_payload(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "factory_run_id": self.factory_run_id,
            "stage": self.stage,
            "stage_result_canonical_sha256": self.stage_result_canonical_sha256,
            "checkpoint_ref": self.checkpoint_ref,
        }

    def to_record(self) -> dict[str, object]:
        return {**self._hash_payload(), "persistence_intent_sha256": self.persistence_intent_sha256}

    @classmethod
    def create(
        cls,
        *,
        factory_run_id: str,
        stage: str,
        stage_result_canonical_sha256: str,
        checkpoint_ref: str,
    ) -> FactoryStagePersistenceIntentV1:
        run_id = _safe_identity(factory_run_id, field="factory_run_id")
        stage_value = _text(stage, field="stage")
        result_hash = _hash(stage_result_canonical_sha256, field="stage_result_canonical_sha256")
        checkpoint = _checkpoint_ref(checkpoint_ref, factory_run_id=run_id)
        payload = {
            "schema_version": _INTENT_SCHEMA,
            "factory_run_id": run_id,
            "stage": stage_value,
            "stage_result_canonical_sha256": result_hash,
            "checkpoint_ref": checkpoint,
        }
        intent_hash = _canonical_document_hash("polaris.factory.stage_persistence_intent.v1", payload)
        return cls(run_id, stage_value, result_hash, checkpoint, intent_hash)

    @classmethod
    def from_record(cls, record: object) -> FactoryStagePersistenceIntentV1:
        value = _exact_object(record, _INTENT_FIELDS, code="intent_fields_invalid")
        if value["schema_version"] != _INTENT_SCHEMA:
            raise _fail("intent_schema_invalid", "Persistence intent schema is invalid")
        parsed = cls.create(
            factory_run_id=_safe_identity(value["factory_run_id"], field="factory_run_id"),
            stage=_text(value["stage"], field="stage"),
            stage_result_canonical_sha256=_hash(
                value["stage_result_canonical_sha256"], field="stage_result_canonical_sha256"
            ),
            checkpoint_ref=value["checkpoint_ref"],
        )
        observed = _hash(value["persistence_intent_sha256"], field="persistence_intent_sha256")
        if observed != parsed.persistence_intent_sha256:
            raise _fail("intent_hash_mismatch", "Persistence intent hash does not bind its exact fields")
        return parsed


def build_stage_persistence_intent(
    *,
    factory_run_id: str,
    stage: str,
    stage_result: Mapping[str, Any],
    checkpoint_ref: str,
) -> FactoryStagePersistenceIntentV1:
    return FactoryStagePersistenceIntentV1.create(
        factory_run_id=factory_run_id,
        stage=stage,
        stage_result_canonical_sha256=canonical_stage_result_sha256(stage_result),
        checkpoint_ref=checkpoint_ref,
    )


@dataclass(frozen=True, slots=True)
class FactoryStagePersistenceCommittedV1:
    factory_run_id: str
    stage: str
    stage_completed_event_id: str
    stage_completed_chain_sequence: int
    stage_completed_chain_event_hash: str
    persistence_intent_sha256: str
    run_snapshot_canonical_sha256: str
    checkpoint_ref: str
    checkpoint_canonical_sha256: str
    marker_event_id: str
    marker_chain_sequence: int
    marker_chain_event_hash: str

    def to_event_payload(self) -> dict[str, object]:
        return {
            "type": "factory_stage_persistence_committed",
            "schema_version": _MARKER_SCHEMA,
            "factory_run_id": self.factory_run_id,
            "stage": self.stage,
            "stage_completed_event_id": self.stage_completed_event_id,
            "stage_completed_chain_sequence": self.stage_completed_chain_sequence,
            "stage_completed_chain_event_hash": self.stage_completed_chain_event_hash,
            "persistence_intent_sha256": self.persistence_intent_sha256,
            "run_snapshot_canonical_sha256": self.run_snapshot_canonical_sha256,
            "checkpoint_ref": self.checkpoint_ref,
            "checkpoint_canonical_sha256": self.checkpoint_canonical_sha256,
        }

    @classmethod
    def from_record(cls, record: object) -> FactoryStagePersistenceCommittedV1:
        value = _payload_object(record, _MARKER_PAYLOAD_FIELDS, code="marker_fields_invalid")
        if value.get("type") != "factory_stage_persistence_committed" or value["schema_version"] != _MARKER_SCHEMA:
            raise _fail("marker_schema_invalid", "Commit marker type or schema is invalid")
        try:
            stage_sequence = _positive_int(
                value["stage_completed_chain_sequence"],
                field="stage_completed_chain_sequence",
                code="marker_field_invalid",
            )
            marker_sequence = _positive_int(
                value.get("chain_sequence"), field="chain_sequence", code="marker_field_invalid"
            )
            run_id = _safe_identity(value["factory_run_id"], field="factory_run_id")
            return cls(
                factory_run_id=run_id,
                stage=_text(value["stage"], field="stage"),
                stage_completed_event_id=_text(value["stage_completed_event_id"], field="stage_completed_event_id"),
                stage_completed_chain_sequence=stage_sequence,
                stage_completed_chain_event_hash=_hash(
                    value["stage_completed_chain_event_hash"], field="stage_completed_chain_event_hash"
                ),
                persistence_intent_sha256=_hash(value["persistence_intent_sha256"], field="persistence_intent_sha256"),
                run_snapshot_canonical_sha256=_hash(
                    value["run_snapshot_canonical_sha256"], field="run_snapshot_canonical_sha256"
                ),
                checkpoint_ref=_checkpoint_ref(value["checkpoint_ref"], factory_run_id=run_id),
                checkpoint_canonical_sha256=_hash(
                    value["checkpoint_canonical_sha256"], field="checkpoint_canonical_sha256"
                ),
                marker_event_id=_text(value.get("event_id"), field="event_id"),
                marker_chain_sequence=marker_sequence,
                marker_chain_event_hash=_hash(value.get("chain_event_hash"), field="chain_event_hash"),
            )
        except FactoryStagePersistenceError as exc:
            if exc.code in {"marker_field_invalid", "checkpoint_ref_invalid"}:
                raise
            raise _fail("marker_field_invalid", "Commit marker contains an invalid exact field") from exc


@dataclass(frozen=True, slots=True)
class FactoryLastStageCommitV1:
    stage: str
    stage_completed_event_id: str
    stage_completed_chain_sequence: int
    stage_completed_chain_event_hash: str
    persistence_intent_sha256: str
    checkpoint_ref: str
    schema_version: str = _POINTER_SCHEMA

    def to_record(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "stage": self.stage,
            "stage_completed_event_id": self.stage_completed_event_id,
            "stage_completed_chain_sequence": self.stage_completed_chain_sequence,
            "stage_completed_chain_event_hash": self.stage_completed_chain_event_hash,
            "persistence_intent_sha256": self.persistence_intent_sha256,
            "checkpoint_ref": self.checkpoint_ref,
        }

    @classmethod
    def from_commit(cls, commit: FactoryStagePersistenceCommittedV1) -> FactoryLastStageCommitV1:
        return cls(
            stage=commit.stage,
            stage_completed_event_id=commit.stage_completed_event_id,
            stage_completed_chain_sequence=commit.stage_completed_chain_sequence,
            stage_completed_chain_event_hash=commit.stage_completed_chain_event_hash,
            persistence_intent_sha256=commit.persistence_intent_sha256,
            checkpoint_ref=commit.checkpoint_ref,
        )

    @classmethod
    def from_record(cls, record: object, *, factory_run_id: str) -> FactoryLastStageCommitV1:
        value = _exact_object(record, _POINTER_FIELDS, code="current_pointer_fields_invalid")
        if value["schema_version"] != _POINTER_SCHEMA:
            raise _fail("current_pointer_schema_invalid", "Current stage commit pointer schema is invalid")
        return cls(
            stage=_text(value["stage"], field="stage"),
            stage_completed_event_id=_text(value["stage_completed_event_id"], field="stage_completed_event_id"),
            stage_completed_chain_sequence=_positive_int(
                value["stage_completed_chain_sequence"], field="stage_completed_chain_sequence"
            ),
            stage_completed_chain_event_hash=_hash(
                value["stage_completed_chain_event_hash"], field="stage_completed_chain_event_hash"
            ),
            persistence_intent_sha256=_hash(value["persistence_intent_sha256"], field="persistence_intent_sha256"),
            checkpoint_ref=_checkpoint_ref(value["checkpoint_ref"], factory_run_id=factory_run_id),
        )


@dataclass(frozen=True, slots=True)
class FactoryStagePersistenceStateV1:
    commits: tuple[FactoryStagePersistenceCommittedV1, ...]
    pending_stage_event_id: str | None
    quarantine_event_id: str | None

    @property
    def is_quarantined(self) -> bool:
        return self.pending_stage_event_id is not None or self.quarantine_event_id is not None

    @property
    def latest_commit(self) -> FactoryStagePersistenceCommittedV1 | None:
        return self.commits[-1] if self.commits else None


def _validate_stage_event(
    event: Mapping[str, Any], *, factory_run_id: str
) -> tuple[FactoryStagePersistenceIntentV1, str, int, str]:
    if event.get("run_id") != factory_run_id:
        raise _fail("stage_event_run_id_mismatch", "Stage event run identity is invalid")
    intent = FactoryStagePersistenceIntentV1.from_record(event.get("persistence_intent"))
    result = event.get("result")
    if not isinstance(result, Mapping):
        raise _fail("stage_event_result_invalid", "Stage event result must be an object")
    stage = _text(event.get("stage"), field="stage")
    if intent.factory_run_id != factory_run_id or intent.stage != stage:
        raise _fail("stage_event_intent_identity_mismatch", "Stage event and intent identity differ")
    if canonical_stage_result_sha256(result) != intent.stage_result_canonical_sha256:
        raise _fail("stage_event_result_hash_mismatch", "Stage result does not match persistence intent")
    return (
        intent,
        _text(event.get("event_id"), field="event_id"),
        _positive_int(event.get("chain_sequence"), field="chain_sequence"),
        _hash(event.get("chain_event_hash"), field="chain_event_hash"),
    )


def reduce_factory_stage_persistence(
    events: list[dict[str, Any]] | tuple[dict[str, Any], ...], *, factory_run_id: str
) -> FactoryStagePersistenceStateV1:
    run_id = _text(factory_run_id, field="factory_run_id")
    commits: list[FactoryStagePersistenceCommittedV1] = []
    pending: tuple[FactoryStagePersistenceIntentV1, str, int, str] | None = None
    quarantine_event_id: str | None = None
    for event in events:
        if not isinstance(event, Mapping):
            raise _fail("event_invalid", "Factory event must be an object")
        event_type = event.get("type")
        if event_type == "stage_completed":
            if pending is not None:
                raise _fail("multiple_pending_stage_events", "A second stage event appeared before commit ACK")
            pending = _validate_stage_event(event, factory_run_id=run_id)
        elif event_type == "factory_stage_persistence_committed":
            if pending is None:
                raise _fail("marker_without_pending_stage", "Commit marker has no pending stage event")
            marker = FactoryStagePersistenceCommittedV1.from_record(event)
            intent, event_id, sequence, event_hash = pending
            if (
                marker.factory_run_id != run_id
                or marker.stage != intent.stage
                or marker.stage_completed_event_id != event_id
                or marker.stage_completed_chain_sequence != sequence
                or marker.stage_completed_chain_event_hash != event_hash
                or marker.persistence_intent_sha256 != intent.persistence_intent_sha256
                or marker.checkpoint_ref != intent.checkpoint_ref
                or marker.marker_chain_sequence <= sequence
            ):
                raise _fail("marker_stage_event_mismatch", "Commit marker does not ACK the exact pending stage event")
            commits.append(marker)
            pending = None
        elif event_type == "factory_run_quarantined":
            _payload_object(event, _QUARANTINE_PAYLOAD_FIELDS, code="quarantine_fields_invalid")
            if event.get("schema_version") != _QUARANTINE_SCHEMA or event.get("factory_run_id") != run_id:
                raise _fail("quarantine_identity_invalid", "Quarantine identity or schema is invalid")
            if event.get("failed_step") not in _QUARANTINE_FAILED_STEPS:
                raise _fail("quarantine_failed_step_invalid", "Quarantine failed_step is invalid")
            _text(event.get("stage"), field="stage")
            _text(event.get("stage_completed_event_id"), field="stage_completed_event_id")
            _positive_int(event.get("stage_completed_chain_sequence"), field="stage_completed_chain_sequence")
            _hash(event.get("stage_completed_chain_event_hash"), field="stage_completed_chain_event_hash")
            _hash(event.get("persistence_intent_sha256"), field="persistence_intent_sha256")
            error_type = _text(event.get("error_type"), field="error_type")
            error_message = _text(event.get("error_message"), field="error_message")
            if len(error_type.encode("utf-8")) > 256 or len(error_message.encode("utf-8")) > 2048:
                raise _fail(
                    "quarantine_error_bound_exceeded",
                    "Quarantine error fields exceed their exact UTF-8 bounds",
                )
            quarantine_event_id = _text(event.get("event_id"), field="event_id")
    return FactoryStagePersistenceStateV1(
        commits=tuple(commits),
        pending_stage_event_id=pending[1] if pending is not None else None,
        quarantine_event_id=quarantine_event_id,
    )


def validate_current_stage_commit_pointer(
    pointer_record: object,
    latest_commit: FactoryStagePersistenceCommittedV1 | None,
) -> None:
    if latest_commit is None:
        if pointer_record is not None:
            raise _fail("current_pointer_without_commit", "Current run points to a stage with no commit marker")
        return
    pointer = FactoryLastStageCommitV1.from_record(pointer_record, factory_run_id=latest_commit.factory_run_id)
    expected = FactoryLastStageCommitV1.from_commit(latest_commit)
    if pointer != expected:
        raise _fail("current_pointer_mismatch", "Current run pointer does not match the latest commit marker")


def validate_committed_checkpoint_hashes(
    commit: FactoryStagePersistenceCommittedV1,
    checkpoint: Mapping[str, Any],
) -> None:
    """Recompute both frozen hash domains from the same immutable checkpoint."""

    if not isinstance(checkpoint, Mapping):
        raise _fail("factory_stage_checkpoint_invalid", "Committed checkpoint must be an object")
    checkpoint_document = dict(checkpoint)
    if checkpoint_document.get("id") != commit.factory_run_id:
        raise _fail(
            "factory_stage_checkpoint_identity_mismatch",
            "Committed checkpoint belongs to another Factory run",
        )
    if canonical_checkpoint_sha256(checkpoint_document) != commit.checkpoint_canonical_sha256:
        raise _fail(
            "factory_stage_checkpoint_hash_mismatch",
            "Committed checkpoint no longer matches its immutable checkpoint hash",
        )
    if canonical_run_snapshot_sha256(checkpoint_document) != commit.run_snapshot_canonical_sha256:
        raise _fail(
            "factory_stage_run_snapshot_hash_mismatch",
            "Committed checkpoint no longer matches the frozen run-snapshot hash",
        )


def bounded_redacted_error(value: object, *, max_utf8_bytes: int) -> str:
    """Return one control-safe UTF-8 string bounded without splitting code points."""

    text = str(value).replace("\x00", "�")
    text = "".join(character if character in "\t\n\r" or ord(character) >= 0x20 else "�" for character in text)
    raw = text.encode("utf-8")
    if len(raw) <= max_utf8_bytes:
        return text
    return raw[:max_utf8_bytes].decode("utf-8", errors="ignore")
