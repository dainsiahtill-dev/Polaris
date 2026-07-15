"""Generic two-stream optimistic guarded FactStream append.

This module is deliberately platform-neutral.  It serializes physical JSONL
facts only; callers reduce and authorize a prepared snapshot after locks are
released.  The protocol never imports or invokes TaskRuntime/domain code.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, cast

from polaris.kernelone.fs.locked_regular_file import (
    LockedRegularFileError,
    LockedRegularFileSetV1,
    StreamLeaseV1,
)

from .file_store import JsonlEventStore, _EventSemanticEnvelope, normalize_strict_stream_failure
from .models import EventEnvelope, EventSourcingError

_STRICT_FORMAT_REVISION = "polaris.strict-event-jsonl.v1"
_GUARDED_FS_CAPABILITY_UNAVAILABLE_CODE = "guarded_fs_capability_unavailable"


def _canonical_json(value: object) -> bytes:
    """Return deterministic UTF-8 JSON bytes or reject non-JSON values."""

    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ValueError("value must be JSON-compatible") from exc


def _canonical_object(value: Mapping[str, Any], *, field_name: str) -> dict[str, Any]:
    """Copy and validate one public JSON object without retaining caller state."""

    if not isinstance(value, Mapping):
        raise ValueError(f"{field_name} must be a JSON-compatible mapping")
    try:
        copied = json.loads(_canonical_json(dict(value)).decode("utf-8"))
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError(f"{field_name} must be a JSON-compatible mapping") from exc
    if not isinstance(copied, dict):
        raise ValueError(f"{field_name} must be a JSON-compatible mapping")
    return copied


def _freeze_json(value: Any) -> object:
    """Recursively freeze a validated JSON value for snapshot consumption."""

    if isinstance(value, dict):
        return MappingProxyType({str(key): _freeze_json(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze_json(item) for item in value)
    return value


def _thaw_json(value: object) -> Any:
    """Return a detached ordinary JSON value from an immutable snapshot value."""

    if isinstance(value, Mapping):
        return {str(key): _thaw_json(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw_json(item) for item in value]
    return value


def _require_token(field_name: str, value: object) -> str:
    """Normalize one public non-empty text field."""

    token = str(value or "").strip()
    if not token:
        raise ValueError(f"{field_name} must be a non-empty string")
    return token


def _optional_token(value: object) -> str | None:
    """Normalize an optional stream envelope identifier."""

    token = str(value or "").strip()
    return token or None


@dataclass(frozen=True, slots=True)
class ReadGuardedFactSnapshotCommandV1:
    """Prepare a strict, immutable target-and-guard physical snapshot."""

    workspace: str
    target_stream: str
    guard_stream: str
    strict_integrity: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace", _require_token("workspace", self.workspace))
        object.__setattr__(self, "target_stream", _require_token("target_stream", self.target_stream))
        object.__setattr__(self, "guard_stream", _require_token("guard_stream", self.guard_stream))
        if self.strict_integrity is not True:
            raise ValueError("guarded snapshots require strict_integrity=True")


@dataclass(frozen=True, slots=True)
class GuardedFactSnapshotProofV1:
    """Non-authoritative continuity witness for one prepared physical stream pair.

    ``continuity_digest`` only detects accidental or unsynchronized changes to
    this DTO.  It is not a MAC, credential, authorization decision, or trust
    boundary.  Commit authority comes from locked strict fact revalidation.
    """

    workspace: str
    target_stream: str
    guard_stream: str
    target_storage_path: str
    guard_storage_path: str
    strict_format_revision: str
    target_head_seq: int
    guard_head_seq: int
    target_facts_digest: str
    guard_facts_digest: str
    continuity_digest: str

    def __post_init__(self) -> None:
        for field_name in (
            "workspace",
            "target_stream",
            "guard_stream",
            "target_storage_path",
            "guard_storage_path",
            "strict_format_revision",
            "target_facts_digest",
            "guard_facts_digest",
            "continuity_digest",
        ):
            object.__setattr__(self, field_name, _require_token(field_name, getattr(self, field_name)))
        for field_name in ("target_head_seq", "guard_head_seq"):
            value = getattr(self, field_name)
            if type(value) is not int or value < 0:
                raise ValueError(f"{field_name} must be an int >= 0")


@dataclass(frozen=True, slots=True)
class GuardedFactSnapshotV1:
    """Complete immutable facts and proof returned by guarded prepare."""

    workspace: str
    target_stream: str
    guard_stream: str
    target_facts: tuple[Mapping[str, object], ...]
    guard_facts: tuple[Mapping[str, object], ...]
    target_facts_digest: str
    guard_facts_digest: str
    proof: GuardedFactSnapshotProofV1

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace", _require_token("workspace", self.workspace))
        object.__setattr__(self, "target_stream", _require_token("target_stream", self.target_stream))
        object.__setattr__(self, "guard_stream", _require_token("guard_stream", self.guard_stream))
        target_facts = tuple(_freeze_json(_thaw_json(fact)) for fact in self.target_facts)
        guard_facts = tuple(_freeze_json(_thaw_json(fact)) for fact in self.guard_facts)
        if not all(isinstance(fact, Mapping) for fact in target_facts + guard_facts):
            raise ValueError("snapshot facts must be JSON objects")
        object.__setattr__(self, "target_facts", target_facts)
        object.__setattr__(self, "guard_facts", guard_facts)
        object.__setattr__(self, "target_facts_digest", _require_token("target_facts_digest", self.target_facts_digest))
        object.__setattr__(self, "guard_facts_digest", _require_token("guard_facts_digest", self.guard_facts_digest))
        if not isinstance(self.proof, GuardedFactSnapshotProofV1):
            raise ValueError("proof must be GuardedFactSnapshotProofV1")

    def target_records(self) -> tuple[dict[str, Any], ...]:
        """Return detached mutable target records for caller-side reduction."""

        return tuple(_thaw_json(fact) for fact in self.target_facts)

    def guard_records(self) -> tuple[dict[str, Any], ...]:
        """Return detached mutable guard records for caller-side reduction."""

        return tuple(_thaw_json(fact) for fact in self.guard_facts)


@dataclass(frozen=True, slots=True)
class GuardedFactEventV1:
    """Caller-defined semantic fields for the single guarded target append.

    The semantic digest includes every value supplied here, including payload
    and metadata keys named ``recorded_at`` or similar.  Only generated outer
    envelope fields are excluded: ``event_id``, ``seq``, ``occurred_at``,
    ``recorded_at``, and append/occurrence timestamps.  Those fields are not
    accepted by this DTO and are generated by the JSONL store at commit time.
    """

    event_type: str
    source: str
    payload: Mapping[str, Any]
    event_version: int = 1
    aggregate_id: str | None = None
    correlation_id: str | None = None
    causation_id: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "event_type", _require_token("event_type", self.event_type))
        object.__setattr__(self, "source", _require_token("source", self.source))
        if type(self.event_version) is not int or self.event_version < 1:
            raise ValueError("event_version must be a positive integer")
        payload = _canonical_object(self.payload, field_name="payload")
        if not payload:
            raise ValueError("payload must not be empty")
        metadata = _canonical_object(self.metadata, field_name="metadata")
        object.__setattr__(self, "payload", _freeze_json(payload))
        object.__setattr__(self, "metadata", _freeze_json(metadata))
        object.__setattr__(self, "aggregate_id", _optional_token(self.aggregate_id))
        object.__setattr__(self, "correlation_id", _optional_token(self.correlation_id))
        object.__setattr__(self, "causation_id", _optional_token(self.causation_id))


@dataclass(frozen=True, slots=True)
class AppendIfGuardedSnapshotCommandV1:
    """Commit one target event only if a strict prepared proof still matches."""

    snapshot_proof: GuardedFactSnapshotProofV1
    event: GuardedFactEventV1
    idempotency_key: str
    durability: str = "fsync"
    strict_integrity: bool = True

    def __post_init__(self) -> None:
        if not isinstance(self.snapshot_proof, GuardedFactSnapshotProofV1):
            raise ValueError("snapshot_proof must be GuardedFactSnapshotProofV1")
        if not isinstance(self.event, GuardedFactEventV1):
            raise ValueError("event must be GuardedFactEventV1")
        object.__setattr__(self, "idempotency_key", _require_token("idempotency_key", self.idempotency_key))
        if self.durability != "fsync":
            raise ValueError("guarded append requires durability='fsync'")
        if self.strict_integrity is not True:
            raise ValueError("guarded append requires strict_integrity=True")


@dataclass(frozen=True, slots=True)
class GuardedFactAppendedV1:
    """Durable receipt for one guarded target event, including exact replay."""

    event_id: str
    workspace: str
    stream: str
    storage_path: str
    appended_at: str
    appended_seq: int
    semantic_digest: str

    def __post_init__(self) -> None:
        for field_name in (
            "event_id",
            "workspace",
            "stream",
            "storage_path",
            "appended_at",
            "semantic_digest",
        ):
            object.__setattr__(self, field_name, _require_token(field_name, getattr(self, field_name)))
        if type(self.appended_seq) is not int or self.appended_seq < 1:
            raise ValueError("appended_seq must be a positive integer")


@dataclass(frozen=True, slots=True)
class _StreamLocation:
    """Canonical logical location for one guarded stream."""

    stream: str
    logical_path: str


def read_guarded_fact_snapshot(command: ReadGuardedFactSnapshotCommandV1) -> GuardedFactSnapshotV1:
    """Return an immutable strict snapshot after briefly locking both streams."""

    try:
        store = JsonlEventStore(command.workspace)
        target, guard = _resolve_distinct_streams(
            store=store,
            target_stream=command.target_stream,
            guard_stream=command.guard_stream,
        )
        with _locked_streams(store=store, target=target, guard=guard) as leases:
            target_lease = leases.lease(target.logical_path)
            guard_lease = leases.lease(guard.logical_path)
            target_lease.open_existing(writable=False)
            guard_lease.open_existing(writable=False)
            target_records = _strict_records(store=store, location=target, lease=target_lease)
            guard_records = _strict_records(store=store, location=guard, lease=guard_lease)
            return _snapshot_from_records(
                workspace=store.workspace,
                target=target,
                guard=guard,
                target_records=target_records,
                guard_records=guard_records,
            )
    except EventSourcingError:
        raise
    except LockedRegularFileError as exc:
        raise EventSourcingError(
            "guarded snapshot preparation observed stream identity drift",
            code=exc.code,
            details=dict(exc.details),
        ) from exc
    except OSError as exc:
        raise _guarded_fs_capability_failure(operation="snapshot_prepare", cause=exc) from exc


def append_if_guarded_snapshot(command: AppendIfGuardedSnapshotCommandV1) -> GuardedFactAppendedV1:
    """Revalidate a strict proof and append exactly one fsync target event.

    Idempotent replay is intentionally evaluated before proof-drift rejection:
    a confirmed prior target event returns its original receipt after either
    target or guard has advanced.
    """

    proof = command.snapshot_proof
    try:
        _validate_proof_structure(proof)
        store, target, guard = _resolve_proof_locations(proof)
        semantic = _semantic_from_guarded_event(
            store=store,
            target=target,
            event=command.event,
            idempotency_key=command.idempotency_key,
        )
        semantic_digest = _semantic_digest(semantic)
        with _locked_streams(store=store, target=target, guard=guard) as leases:
            target_lease = leases.lease(target.logical_path)
            guard_lease = leases.lease(guard.logical_path)
            target_lease.open_existing(writable=True)
            guard_lease.open_existing(writable=False)
            target_records = _strict_records(store=store, location=target, lease=target_lease)
            guard_records = _strict_records(store=store, location=guard, lease=guard_lease)
            existing = _find_idempotent_event(target_records, command.idempotency_key)
            if existing is not None:
                existing_digest = _semantic_digest(store._semantic_from_event(existing))
                if existing_digest != semantic_digest:
                    raise EventSourcingError(
                        "guarded append idempotency key identifies different semantic content",
                        code="idempotency_semantic_conflict",
                        details={
                            "idempotency_key": command.idempotency_key,
                            "existing_semantic_digest": existing_digest,
                            "requested_semantic_digest": semantic_digest,
                        },
                    )
                return _receipt_from_event(
                    workspace=store.workspace,
                    location=target,
                    event=existing,
                    semantic_digest=semantic_digest,
                )

            current = _snapshot_from_records(
                workspace=store.workspace,
                target=target,
                guard=guard,
                target_records=target_records,
                guard_records=guard_records,
            )
            _assert_proof_matches(proof=proof, current=current.proof)
            appended = _append_fsync_descriptor(
                store=store,
                location=target,
                lease=target_lease,
                semantic=semantic,
                strict_records=target_records,
            )
            return _receipt_from_event(
                workspace=store.workspace,
                location=target,
                event=appended,
                semantic_digest=semantic_digest,
            )
    except EventSourcingError:
        raise
    except LockedRegularFileError as exc:
        raise EventSourcingError(
            "guarded append observed stream identity drift",
            code=exc.code,
            details=dict(exc.details),
        ) from exc
    except OSError as exc:
        raise _guarded_fs_capability_failure(operation="append", cause=exc) from exc


def _guarded_fs_capability_failure(*, operation: str, cause: OSError) -> EventSourcingError:
    """Return the normative failure for an unavailable guarded filesystem path."""

    return EventSourcingError(
        "guarded filesystem capability is unavailable",
        code=_GUARDED_FS_CAPABILITY_UNAVAILABLE_CODE,
        details={"operation": operation, "cause_type": type(cause).__name__},
    )


def _resolve_distinct_streams(
    *,
    store: JsonlEventStore,
    target_stream: str,
    guard_stream: str,
) -> tuple[_StreamLocation, _StreamLocation]:
    """Canonicalize two stream references and reject aliasing before locking."""

    target = _stream_location(store, target_stream)
    guard = _stream_location(store, guard_stream)
    if target.logical_path == guard.logical_path:
        raise EventSourcingError(
            "guarded target and guard resolve to the same stream path",
            code="same_target_and_guard_stream",
            details={"target_stream": target.stream, "guard_stream": guard.stream},
        )
    return target, guard


def _stream_location(store: JsonlEventStore, stream: str) -> _StreamLocation:
    """Return one validated stream and its canonical physical path."""

    normalized_stream = store._normalize_stream(stream)
    logical_path = store.stream_logical_path(normalized_stream)
    return _StreamLocation(
        stream=normalized_stream,
        logical_path=logical_path,
    )


def _resolve_proof_locations(
    proof: GuardedFactSnapshotProofV1,
) -> tuple[JsonlEventStore, _StreamLocation, _StreamLocation]:
    """Use only proof bindings to derive the commit workspace and stream paths."""

    try:
        store = JsonlEventStore(proof.workspace)
        target, guard = _resolve_distinct_streams(
            store=store,
            target_stream=proof.target_stream,
            guard_stream=proof.guard_stream,
        )
    except EventSourcingError as exc:
        if exc.code == "stream_identity_drift":
            raise
        raise EventSourcingError(
            "guarded snapshot proof has unusable workspace or stream bindings",
            code="snapshot_proof_invalid",
        ) from exc
    except ValueError as exc:
        raise EventSourcingError(
            "guarded snapshot proof has unusable workspace or stream bindings",
            code="snapshot_proof_invalid",
        ) from exc
    if (
        proof.workspace != store.workspace
        or proof.target_storage_path != target.logical_path
        or proof.guard_storage_path != guard.logical_path
    ):
        raise EventSourcingError(
            "guarded snapshot proof has non-canonical stream bindings",
            code="snapshot_proof_invalid",
        )
    return store, target, guard


def _locked_streams(
    *,
    store: JsonlEventStore,
    target: _StreamLocation,
    guard: _StreamLocation,
) -> LockedRegularFileSetV1:
    """Acquire shared persistent locks and descriptor leases for both streams."""

    try:
        return LockedRegularFileSetV1.acquire(
            runtime_root=store.storage_identity.runtime_root,
            storage_identity_token=store.storage_identity.token,
            logical_paths=(target.logical_path, guard.logical_path),
        )
    except LockedRegularFileError as exc:
        raise EventSourcingError(
            "guarded stream lock or descriptor capability failed",
            code=exc.code,
            details=dict(exc.details),
        ) from exc


def _strict_records(
    *,
    store: JsonlEventStore,
    location: _StreamLocation,
    lease: StreamLeaseV1,
) -> list[EventEnvelope]:
    """Strictly scan one descriptor-bound stream without logical-path I/O."""

    if not lease.exists:
        return []
    try:
        content_bytes = lease.read_bytes()
        if len(content_bytes) > store._strict_max_bytes:
            raise EventSourcingError(
                "strict event stream exceeds the configured byte limit",
                code="strict_scan_limit_exceeded",
                details={"stream": location.stream, "storage_path": location.logical_path},
            )
        return store._parse_strict_records(
            content=content_bytes.decode("utf-8"),
            storage_path=location.logical_path,
            stream=location.stream,
            byte_length=len(content_bytes),
        )
    except EventSourcingError as exc:
        normalized = normalize_strict_stream_failure(
            exc,
            stream=location.stream,
            storage_path=location.logical_path,
        )
        if normalized is exc:
            raise
        raise normalized from exc
    except LockedRegularFileError as exc:
        raise EventSourcingError(
            "guarded stream identity changed during strict descriptor read",
            code=exc.code,
            details={"stream": location.stream, **dict(exc.details)},
        ) from exc
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        strict_failure = EventSourcingError(
            "strict descriptor read failed",
            code="stream_read_failed",
            details={
                "stream": location.stream,
                "storage_path": location.logical_path,
                "cause_type": type(exc).__name__,
            },
        )
        raise normalize_strict_stream_failure(
            strict_failure,
            stream=location.stream,
            storage_path=location.logical_path,
        ) from exc


def _append_fsync_descriptor(
    *,
    store: JsonlEventStore,
    location: _StreamLocation,
    lease: StreamLeaseV1,
    semantic: _EventSemanticEnvelope,
    strict_records: list[EventEnvelope],
) -> EventEnvelope:
    """Append and fsync one strict target fact through its held descriptor."""

    if len(strict_records) >= store._strict_max_records:
        raise EventSourcingError(
            "strict event stream append exceeds the configured record limit",
            code="strict_stream_corruption",
        )
    next_seq = (strict_records[-1].seq if strict_records else 0) + 1
    envelope = store._build_envelope(semantic=semantic, seq=next_seq)
    encoded = _canonical_json(envelope.to_record(include_integrity_digest=True)) + b"\n"
    try:
        current_size = len(lease.read_bytes()) if lease.exists else 0
        if current_size + len(encoded) > store._strict_max_bytes:
            raise EventSourcingError(
                "strict event stream append exceeds the configured byte limit",
                code="strict_stream_corruption",
            )
        lease.append_bytes(encoded, fsync_file=True, fsync_parent_on_create=True)
    except EventSourcingError:
        raise
    except LockedRegularFileError as exc:
        raise EventSourcingError(
            "guarded target append durability failed",
            code=exc.code,
            details={"stream": location.stream, "storage_path": location.logical_path, **dict(exc.details)},
        ) from exc
    except OSError as exc:
        raise EventSourcingError(
            "guarded target append write failed",
            code="append_write_failed",
            details={"stream": location.stream, "storage_path": location.logical_path},
        ) from exc
    return envelope


def _snapshot_from_records(
    *,
    workspace: str,
    target: _StreamLocation,
    guard: _StreamLocation,
    target_records: list[EventEnvelope],
    guard_records: list[EventEnvelope],
) -> GuardedFactSnapshotV1:
    """Freeze exact strict records and bind them into a canonical proof."""

    target_facts = tuple(
        cast(Mapping[str, object], _freeze_json(record.to_record(include_integrity_digest=True)))
        for record in target_records
    )
    guard_facts = tuple(
        cast(Mapping[str, object], _freeze_json(record.to_record(include_integrity_digest=True)))
        for record in guard_records
    )
    target_digest = _facts_digest(target_facts)
    guard_digest = _facts_digest(guard_facts)
    proof = _build_proof(
        workspace=workspace,
        target=target,
        guard=guard,
        target_head_seq=target_records[-1].seq if target_records else 0,
        guard_head_seq=guard_records[-1].seq if guard_records else 0,
        target_facts_digest=target_digest,
        guard_facts_digest=guard_digest,
    )
    return GuardedFactSnapshotV1(
        workspace=workspace,
        target_stream=target.stream,
        guard_stream=guard.stream,
        target_facts=target_facts,
        guard_facts=guard_facts,
        target_facts_digest=target_digest,
        guard_facts_digest=guard_digest,
        proof=proof,
    )


def _facts_digest(facts: tuple[Mapping[str, object], ...]) -> str:
    """Hash all exact canonical strict facts in sequence order."""

    return hashlib.sha256(_canonical_json([_thaw_json(fact) for fact in facts])).hexdigest()


def _proof_binding(proof: GuardedFactSnapshotProofV1) -> dict[str, object]:
    """Return continuity-bound proof fields excluding the derived digest."""

    return {
        "workspace": proof.workspace,
        "target_stream": proof.target_stream,
        "guard_stream": proof.guard_stream,
        "target_storage_path": proof.target_storage_path,
        "guard_storage_path": proof.guard_storage_path,
        "strict_format_revision": proof.strict_format_revision,
        "target_head_seq": proof.target_head_seq,
        "guard_head_seq": proof.guard_head_seq,
        "target_facts_digest": proof.target_facts_digest,
        "guard_facts_digest": proof.guard_facts_digest,
    }


def _build_proof(
    *,
    workspace: str,
    target: _StreamLocation,
    guard: _StreamLocation,
    target_head_seq: int,
    guard_head_seq: int,
    target_facts_digest: str,
    guard_facts_digest: str,
) -> GuardedFactSnapshotProofV1:
    """Build one deterministic, non-authenticating snapshot continuity witness."""

    provisional = GuardedFactSnapshotProofV1(
        workspace=workspace,
        target_stream=target.stream,
        guard_stream=guard.stream,
        target_storage_path=target.logical_path,
        guard_storage_path=guard.logical_path,
        strict_format_revision=_STRICT_FORMAT_REVISION,
        target_head_seq=target_head_seq,
        guard_head_seq=guard_head_seq,
        target_facts_digest=target_facts_digest,
        guard_facts_digest=guard_facts_digest,
        continuity_digest="pending",
    )
    digest = hashlib.sha256(_canonical_json(_proof_binding(provisional))).hexdigest()
    return GuardedFactSnapshotProofV1(
        workspace=provisional.workspace,
        target_stream=provisional.target_stream,
        guard_stream=provisional.guard_stream,
        target_storage_path=provisional.target_storage_path,
        guard_storage_path=provisional.guard_storage_path,
        strict_format_revision=provisional.strict_format_revision,
        target_head_seq=provisional.target_head_seq,
        guard_head_seq=provisional.guard_head_seq,
        target_facts_digest=provisional.target_facts_digest,
        guard_facts_digest=provisional.guard_facts_digest,
        continuity_digest=digest,
    )


def _validate_proof_structure(proof: GuardedFactSnapshotProofV1) -> None:
    """Validate proof shape and continuity before consuming any bound location."""

    if not isinstance(proof, GuardedFactSnapshotProofV1):
        raise EventSourcingError("guarded snapshot proof type is invalid", code="snapshot_proof_invalid")
    if proof.strict_format_revision != _STRICT_FORMAT_REVISION:
        raise EventSourcingError(
            "guarded snapshot proof has an unsupported strict format revision",
            code="snapshot_proof_invalid",
        )
    if any(type(value) is not int or value < 0 for value in (proof.target_head_seq, proof.guard_head_seq)):
        raise EventSourcingError("guarded snapshot proof head is invalid", code="snapshot_proof_invalid")
    expected_digest = hashlib.sha256(_canonical_json(_proof_binding(proof))).hexdigest()
    if proof.continuity_digest != expected_digest:
        raise EventSourcingError(
            "guarded snapshot proof continuity digest does not match its binding",
            code="snapshot_proof_tampered",
        )


def _assert_proof_matches(
    *,
    proof: GuardedFactSnapshotProofV1,
    current: GuardedFactSnapshotProofV1,
) -> None:
    """Classify exact locked target and guard drift independently."""

    target_fields = (
        "workspace",
        "target_stream",
        "target_storage_path",
        "strict_format_revision",
        "target_head_seq",
        "target_facts_digest",
    )
    if any(getattr(proof, field_name) != getattr(current, field_name) for field_name in target_fields):
        raise EventSourcingError(
            "guarded target snapshot changed after prepare",
            code="target_snapshot_drift",
        )
    guard_fields = (
        "workspace",
        "guard_stream",
        "guard_storage_path",
        "strict_format_revision",
        "guard_head_seq",
        "guard_facts_digest",
    )
    if any(getattr(proof, field_name) != getattr(current, field_name) for field_name in guard_fields):
        raise EventSourcingError(
            "guarded guard snapshot changed after prepare",
            code="guard_snapshot_drift",
        )


def _semantic_from_guarded_event(
    *,
    store: JsonlEventStore,
    target: _StreamLocation,
    event: GuardedFactEventV1,
    idempotency_key: str,
) -> _EventSemanticEnvelope:
    """Canonicalize caller semantics and inject the command idempotency key."""

    metadata = _thaw_json(event.metadata)
    existing_key = str(metadata.get("idempotency_key") or "").strip()
    if existing_key and existing_key != idempotency_key:
        raise EventSourcingError(
            "guarded event metadata idempotency key conflicts with the command",
            code="idempotency_semantic_conflict",
            details={"command_idempotency_key": idempotency_key, "metadata_idempotency_key": existing_key},
        )
    metadata["idempotency_key"] = idempotency_key

    return store._build_semantic_envelope(
        stream=target.stream,
        event_type=event.event_type,
        source=event.source,
        payload=_thaw_json(event.payload),
        event_version=event.event_version,
        aggregate_id=event.aggregate_id,
        correlation_id=event.correlation_id,
        causation_id=event.causation_id,
        metadata=metadata,
    )


def _semantic_digest(semantic: _EventSemanticEnvelope) -> str:
    """Hash every caller semantic field, excluding only generated envelope fields."""

    record = {
        "stream": semantic.stream,
        "event_type": semantic.event_type,
        "event_version": semantic.event_version,
        "source": semantic.source,
        "aggregate_id": semantic.aggregate_id,
        "correlation_id": semantic.correlation_id,
        "causation_id": semantic.causation_id,
        "payload": semantic.payload,
        "metadata": semantic.metadata,
    }
    return hashlib.sha256(_canonical_json(record)).hexdigest()


def _find_idempotent_event(records: list[EventEnvelope], idempotency_key: str) -> EventEnvelope | None:
    """Return the exact strict target event for one idempotency key."""

    for record in records:
        if str(record.metadata.get("idempotency_key") or "").strip() == idempotency_key:
            return record
    return None


def _receipt_from_event(
    *,
    workspace: str,
    location: _StreamLocation,
    event: EventEnvelope,
    semantic_digest: str,
) -> GuardedFactAppendedV1:
    """Project the stable receipt returned for both commit and exact replay."""

    return GuardedFactAppendedV1(
        event_id=event.event_id,
        workspace=workspace,
        stream=location.stream,
        storage_path=location.logical_path,
        appended_at=event.occurred_at,
        appended_seq=event.seq,
        semantic_digest=semantic_digest,
    )


__all__ = [
    "AppendIfGuardedSnapshotCommandV1",
    "GuardedFactAppendedV1",
    "GuardedFactEventV1",
    "GuardedFactSnapshotProofV1",
    "GuardedFactSnapshotV1",
    "ReadGuardedFactSnapshotCommandV1",
    "append_if_guarded_snapshot",
    "read_guarded_fact_snapshot",
]
