"""Durable Factory settlement journal backed by the canonical FactStream."""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from itertools import pairwise
from pathlib import Path
from types import MappingProxyType
from typing import Any, Protocol

from polaris.cells.events.fact_stream.public.contracts import (
    AppendFactEventCommandV1,
    FactEventAppendedV1,
    FactStreamError,
    FactStreamQueryResultV1,
    QueryFactEventsV1,
)

FACTORY_SETTLEMENT_STREAM = "factory.settlement"
FACTORY_SETTLEMENT_SOURCE = "factory.pipeline.settlement_consumer"
_JOURNAL_SCHEMA = "factory.settlement.journal/1"
_FACT_STREAM_ENVELOPE_VERSION = 1
_PAGE_SIZE = 1000


class SettlementJournalValidationError(RuntimeError):
    """Raised when durable journal state cannot be trusted."""

    def __init__(self, message: str, *, code: str) -> None:
        normalized_message = str(message or "").strip()
        normalized_code = str(code or "").strip()
        if not normalized_message or not normalized_code:
            raise ValueError("journal validation errors require message and code")
        super().__init__(normalized_message)
        self.code = normalized_code


class FactStreamPort(Protocol):
    """Narrow FactStream dependency used by the settlement core."""

    def query(self, query: QueryFactEventsV1, /) -> FactStreamQueryResultV1:
        """Return one immutable page from a FactStream."""

    def append(self, command: AppendFactEventCommandV1, /) -> FactEventAppendedV1:
        """Append one immutable event, honoring idempotency and CAS."""


class SettlementJournalStatus(StrEnum):
    """Persisted settlement state names."""

    PENDING = "pending"
    APPLIED = "applied"
    DEAD_LETTER = "dead_letter"
    CHECKPOINT = "checkpoint"


class SettlementPendingPhase(StrEnum):
    """Reason a durable settlement remains pending."""

    WAITING_BARRIER = "waiting_barrier"
    APPLYING = "applying"
    WAITING_RETRY = "waiting_retry"


@dataclass(frozen=True, slots=True)
class SettlementIdentity:
    """Fenced idempotency identity for one source fact."""

    workspace: str
    source_fact_event_id: str
    factory_run_id: str
    workspace_fencing_token: int

    def __post_init__(self) -> None:
        for field_name in ("workspace", "source_fact_event_id", "factory_run_id"):
            value = str(getattr(self, field_name) or "").strip()
            if not value:
                raise ValueError(f"{field_name} must be a non-empty string")
            object.__setattr__(self, field_name, value)
        token = self.workspace_fencing_token
        if isinstance(token, bool) or not isinstance(token, int) or token < 1:
            raise ValueError("workspace_fencing_token must be an int >= 1")

    @property
    def digest(self) -> str:
        """Return the stable journal key digest."""

        encoded = json.dumps(
            {
                "factory_run_id": self.factory_run_id,
                "source_fact_event_id": self.source_fact_event_id,
                "workspace": self.workspace,
                "workspace_fencing_token": self.workspace_fencing_token,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def to_payload(self) -> dict[str, Any]:
        """Project the identity into journal payload fields."""

        return {
            "workspace": self.workspace,
            "source_fact_event_id": self.source_fact_event_id,
            "factory_run_id": self.factory_run_id,
            "workspace_fencing_token": self.workspace_fencing_token,
            "settlement_key": self.digest,
        }


@dataclass(frozen=True, slots=True)
class SettlementJournalRecord:
    """Validated projection of one journal fact."""

    event_id: str
    event_seq: int
    status: SettlementJournalStatus
    settlement_key: str
    payload: Mapping[str, Any]

    @property
    def pending_phase(self) -> SettlementPendingPhase | None:
        """Return the pending phase when this is a pending record."""

        if self.status is not SettlementJournalStatus.PENDING:
            return None
        try:
            return SettlementPendingPhase(str(self.payload.get("phase") or ""))
        except ValueError:
            return None


@dataclass(frozen=True, slots=True)
class SettlementClaim:
    """A successfully persisted CAS claim."""

    claim_id: str
    journal_event_id: str
    journal_event_seq: int
    recovery: bool


@dataclass(frozen=True, slots=True)
class _CheckpointChainEntry:
    """One validated checkpoint and its durable source cursor."""

    record: SettlementJournalRecord
    next_source_offset: int


@dataclass(slots=True)
class SettlementJournalReplaySnapshot:
    """One replay-scoped, read-only FactStream projection with mutable indexes.

    The source events and initial journal records are immutable FactStream
    observations.  Index heads are updated only from successful append receipts
    during the same lifecycle-locked replay, so normal replay work never
    re-queries the journal for a state or cursor lookup.
    """

    source_stream: str
    source_events: Mapping[int, Mapping[str, Any]]
    _records: list[SettlementJournalRecord]
    _current_seq: int
    _states: dict[str, SettlementJournalRecord]
    _terminals: dict[str, SettlementJournalRecord]
    _checkpoint_chains: dict[str, list[_CheckpointChainEntry]]
    _event_ids: set[str]

    @classmethod
    def create(
        cls,
        *,
        source_stream: str,
        source_events: Mapping[int, Mapping[str, Any]],
        records: Sequence[SettlementJournalRecord],
        current_seq: int,
        checkpoint_chain: Sequence[_CheckpointChainEntry],
    ) -> SettlementJournalReplaySnapshot:
        states: dict[str, SettlementJournalRecord] = {}
        terminals: dict[str, SettlementJournalRecord] = {}
        for record in records:
            states[record.settlement_key] = record
            if record.status in {SettlementJournalStatus.APPLIED, SettlementJournalStatus.DEAD_LETTER}:
                terminals[record.settlement_key] = record
        return cls(
            source_stream=source_stream,
            source_events=MappingProxyType(dict(source_events)),
            _records=list(records),
            _current_seq=current_seq,
            _states=states,
            _terminals=terminals,
            _checkpoint_chains={source_stream: list(checkpoint_chain)},
            _event_ids={record.event_id for record in records},
        )

    @property
    def current_seq(self) -> int:
        """Return the journal head observed or appended in this replay."""

        return self._current_seq

    @property
    def records(self) -> tuple[SettlementJournalRecord, ...]:
        """Return the replay-local journal records in event sequence order."""

        return tuple(self._records)

    def state_for(self, identity: SettlementIdentity) -> SettlementJournalRecord | None:
        """Return terminal state preferentially, otherwise the latest state."""

        return self._terminals.get(identity.digest) or self._states.get(identity.digest)

    def latest_checkpoint_offset(self, *, source_stream: str) -> int:
        """Return the contiguous checkpoint head already validated for replay."""

        chain = self._checkpoint_chains.get(source_stream)
        if chain is None:
            raise SettlementJournalValidationError(
                "replay snapshot does not cover the requested source stream",
                code="replay_snapshot_source_stream_mismatch",
            )
        return chain[-1].next_source_offset if chain else 0

    def checkpoint_chain(self, *, source_stream: str) -> tuple[_CheckpointChainEntry, ...]:
        """Return the validated checkpoint chain for one replay source stream."""

        return tuple(self._checkpoint_chains.get(source_stream, ()))

    def record_append(
        self,
        *,
        status: SettlementJournalStatus,
        payload: Mapping[str, Any],
        appended: FactEventAppendedV1,
    ) -> None:
        """Advance local indexes from one successful FactStream append receipt."""

        event_id = str(appended.event_id or "").strip()
        event_seq = int(appended.appended_seq or 0)
        if not event_id or event_seq < 1:
            raise SettlementJournalValidationError(
                "journal append receipt is missing its durable identity",
                code="invalid_journal_append_receipt",
            )
        if event_id in self._event_ids:
            return
        if event_seq != self._current_seq + 1:
            raise SettlementJournalValidationError(
                "journal append receipt would create a replay snapshot sequence gap",
                code="replay_snapshot_non_contiguous_append",
            )
        record = SettlementJournalRecord(
            event_id=event_id,
            event_seq=event_seq,
            status=status,
            settlement_key=str(payload.get("settlement_key") or "").strip(),
            payload=dict(payload),
        )
        self._records.append(record)
        self._event_ids.add(event_id)
        self._current_seq = max(self._current_seq, event_seq)
        self._states[record.settlement_key] = record
        if status in {SettlementJournalStatus.APPLIED, SettlementJournalStatus.DEAD_LETTER}:
            self._terminals[record.settlement_key] = record
        if status is SettlementJournalStatus.CHECKPOINT:
            stream = str(payload.get("source_stream") or "").strip()
            chain = self._checkpoint_chains.get(stream)
            if chain is None:
                raise SettlementJournalValidationError(
                    "replay snapshot append used an unknown source stream",
                    code="replay_snapshot_source_stream_mismatch",
                )
            chain.append(
                _CheckpointChainEntry(
                    record=record,
                    next_source_offset=_strict_int(
                        payload.get("next_source_offset"),
                        field_name="next_source_offset",
                        minimum=1,
                    ),
                )
            )

    def replace(
        self,
        *,
        records: Sequence[SettlementJournalRecord],
        current_seq: int,
        checkpoint_chain: Sequence[_CheckpointChainEntry],
    ) -> None:
        """Install one bounded CAS-drift reload of the derived projection."""

        replacement = self.create(
            source_stream=self.source_stream,
            source_events=self.source_events,
            records=records,
            current_seq=current_seq,
            checkpoint_chain=checkpoint_chain,
        )
        self._records = replacement._records
        self._current_seq = replacement._current_seq
        self._states = replacement._states
        self._terminals = replacement._terminals
        self._checkpoint_chains = replacement._checkpoint_chains
        self._event_ids = replacement._event_ids


def _canonical_workspace(value: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError("workspace must be a non-empty string")
    return os.path.normcase(str(Path(normalized).expanduser().resolve(strict=False)))


def _strict_int(
    value: object,
    *,
    field_name: str,
    minimum: int,
) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise SettlementJournalValidationError(
            f"journal {field_name} must be an int >= {minimum}",
            code=f"invalid_journal_{field_name}",
        )
    return value


def _stable_digest(value: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        dict(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _barrier_audit_payload(
    *,
    barrier_hash: str,
    evidence_refs: Sequence[str],
) -> dict[str, Any]:
    """Return deterministic, JSON-safe Run Ledger barrier audit fields."""

    if not isinstance(barrier_hash, str) or not barrier_hash.strip():
        raise ValueError("barrier_hash must be a non-empty string")
    if isinstance(evidence_refs, (str, bytes, bytearray)):
        raise TypeError("evidence_refs must be a sequence of strings")

    normalized_refs: set[str] = set()
    for raw_ref in evidence_refs:
        if not isinstance(raw_ref, str):
            raise TypeError("evidence_refs must contain only strings")
        reference = raw_ref.strip()
        if reference:
            normalized_refs.add(reference)
    return {
        "barrier_hash": barrier_hash.strip(),
        "evidence_refs": sorted(normalized_refs),
    }


class FactorySettlementJournal:
    """Append and query settlement state in ``factory.settlement``.

    The journal owns no files. FactStream provides append durability,
    idempotency, and optimistic sequence allocation.
    """

    def __init__(self, *, workspace: str, fact_stream: FactStreamPort) -> None:
        self._workspace = _canonical_workspace(workspace)
        self._fact_stream = fact_stream

    @property
    def workspace(self) -> str:
        return self._workspace

    def records(self) -> tuple[SettlementJournalRecord, ...]:
        """Read all journal records or fail closed on the first invalid fact."""

        records, _ = self._read_records()
        return records

    def open_replay_snapshot(
        self,
        *,
        source_stream: str,
        source_events: Mapping[int, Mapping[str, Any]],
    ) -> SettlementJournalReplaySnapshot:
        """Read and index the journal once for a lifecycle-locked replay."""

        stream = str(source_stream or "").strip()
        if not stream:
            raise ValueError("source_stream must be a non-empty string")
        records, current_seq = self._read_records()
        chain = self._validate_checkpoint_chain(
            records=records,
            source_stream=stream,
            source_events=source_events,
        )
        return SettlementJournalReplaySnapshot.create(
            source_stream=stream,
            source_events=source_events,
            records=records,
            current_seq=current_seq,
            checkpoint_chain=chain,
        )

    def state_for(
        self,
        identity: SettlementIdentity,
        *,
        snapshot: SettlementJournalReplaySnapshot | None = None,
    ) -> SettlementJournalRecord | None:
        """Return terminal state preferentially, otherwise the latest pending state."""

        if snapshot is not None:
            return snapshot.state_for(identity)
        matching = [record for record in self.records() if record.settlement_key == identity.digest]
        terminal = [
            record
            for record in matching
            if record.status in {SettlementJournalStatus.APPLIED, SettlementJournalStatus.DEAD_LETTER}
        ]
        if terminal:
            return terminal[-1]
        return matching[-1] if matching else None

    def latest_checkpoint_offset(
        self,
        *,
        source_stream: str,
        snapshot: SettlementJournalReplaySnapshot | None = None,
    ) -> int:
        """Return the durable, contiguous source cursor for one stream."""

        expected_stream = str(source_stream or "").strip()
        if not expected_stream:
            raise ValueError("source_stream must be a non-empty string")
        if snapshot is not None:
            return snapshot.latest_checkpoint_offset(source_stream=expected_stream)

        records, _ = self._read_records()
        chain = self._validate_checkpoint_chain(
            records=records,
            source_stream=expected_stream,
        )
        return chain[-1].next_source_offset if chain else 0

    def append_waiting_barrier(
        self,
        identity: SettlementIdentity,
        *,
        source_fact_seq: int,
        barrier_hash: str,
        evidence_refs: Sequence[str],
        snapshot: SettlementJournalReplaySnapshot | None = None,
    ) -> FactEventAppendedV1:
        """Persist an idempotent pending barrier state."""

        payload = self._base_payload(
            identity,
            source_fact_seq=source_fact_seq,
            barrier_hash=barrier_hash,
            evidence_refs=evidence_refs,
        )
        payload["phase"] = SettlementPendingPhase.WAITING_BARRIER.value
        expected_seq = snapshot.current_seq + 1 if snapshot is not None else None
        try:
            return self._append(
                status=SettlementJournalStatus.PENDING,
                payload=payload,
                identity=identity,
                idempotency_suffix="pending:waiting_barrier",
                expected_seq=expected_seq,
                snapshot=snapshot,
            )
        except FactStreamError as exc:
            if exc.code not in {"expected_seq_drift", "idempotency_conflict"}:
                raise

            # Two replay workers may open snapshots before either appends the
            # stable WAITING_BARRIER fact.  The first append wins; the second
            # then sees the same idempotency key with a newer live barrier
            # projection.  That is a stale-snapshot race, not semantic drift.
            # Reload once and reuse only an exact pending state for this
            # settlement identity.  Terminal/other-phase conflicts remain
            # fail-closed.
            if snapshot is not None:
                self._reload_replay_snapshot_after_drift(snapshot)
            existing = self.state_for(identity, snapshot=snapshot)
            if (
                existing is not None
                and existing.status is SettlementJournalStatus.PENDING
                and existing.pending_phase is SettlementPendingPhase.WAITING_BARRIER
            ):
                return self._append(
                    status=SettlementJournalStatus.PENDING,
                    payload=existing.payload,
                    identity=identity,
                    idempotency_suffix="pending:waiting_barrier",
                )
            if exc.code != "expected_seq_drift" or snapshot is None:
                raise
            # Another settlement identity won the CAS.  Retry once from the
            # reloaded head; a second race remains a typed retryable drift for
            # the caller rather than silently skipping journal records.
            return self._append(
                status=SettlementJournalStatus.PENDING,
                payload=payload,
                identity=identity,
                idempotency_suffix="pending:waiting_barrier",
                expected_seq=snapshot.current_seq + 1,
                snapshot=snapshot,
            )

    def append_waiting_retry(
        self,
        identity: SettlementIdentity,
        *,
        source_fact_seq: int,
        claim_id: str,
        error_code: str,
        barrier_hash: str,
        evidence_refs: Sequence[str],
        snapshot: SettlementJournalReplaySnapshot | None = None,
    ) -> FactEventAppendedV1:
        """Persist a retryable outcome without acknowledging the wake signal."""

        normalized_code = str(error_code or "settlement_retryable_error").strip()
        normalized_claim_id = str(claim_id or "").strip()
        if not normalized_claim_id:
            raise ValueError("claim_id must be a non-empty string")
        payload = self._base_payload(
            identity,
            source_fact_seq=source_fact_seq,
            barrier_hash=barrier_hash,
            evidence_refs=evidence_refs,
        )
        payload.update(
            {
                "phase": SettlementPendingPhase.WAITING_RETRY.value,
                "claim_id": normalized_claim_id,
                "error_code": normalized_code,
            }
        )
        return self._append(
            status=SettlementJournalStatus.PENDING,
            payload=payload,
            identity=identity,
            idempotency_suffix=f"pending:waiting_retry:{normalized_code}:{normalized_claim_id}",
            snapshot=snapshot,
        )

    def try_claim(
        self,
        identity: SettlementIdentity,
        *,
        source_fact_seq: int,
        recovery: bool,
        barrier_hash: str,
        evidence_refs: Sequence[str],
        snapshot: SettlementJournalReplaySnapshot | None = None,
    ) -> SettlementClaim | None:
        """CAS-append an applying claim, returning ``None`` on contention."""

        current_seq = snapshot.current_seq if snapshot is not None else self._read_records()[1]
        claim_id = uuid.uuid4().hex
        payload = self._base_payload(
            identity,
            source_fact_seq=source_fact_seq,
            barrier_hash=barrier_hash,
            evidence_refs=evidence_refs,
        )
        payload.update(
            {
                "phase": SettlementPendingPhase.APPLYING.value,
                "claim_id": claim_id,
                "recovery": bool(recovery),
            }
        )
        try:
            appended = self._append(
                status=SettlementJournalStatus.PENDING,
                payload=payload,
                identity=identity,
                idempotency_suffix=f"pending:claim:{claim_id}",
                expected_seq=current_seq + 1,
                snapshot=snapshot,
            )
        except FactStreamError as exc:
            if exc.code == "expected_seq_drift":
                if snapshot is not None:
                    self._reload_replay_snapshot_after_drift(snapshot)
                return None
            raise
        return SettlementClaim(
            claim_id=claim_id,
            journal_event_id=appended.event_id,
            journal_event_seq=int(appended.appended_seq or 0),
            recovery=bool(recovery),
        )

    def append_applied(
        self,
        identity: SettlementIdentity,
        *,
        source_fact_seq: int,
        barrier_hash: str,
        evidence_refs: Sequence[str],
        snapshot: SettlementJournalReplaySnapshot | None = None,
    ) -> FactEventAppendedV1:
        """Persist the terminal applied state before transport acknowledgement."""

        return self._append(
            status=SettlementJournalStatus.APPLIED,
            payload=self._base_payload(
                identity,
                source_fact_seq=source_fact_seq,
                barrier_hash=barrier_hash,
                evidence_refs=evidence_refs,
            ),
            identity=identity,
            idempotency_suffix="applied",
            snapshot=snapshot,
        )

    def append_dead_letter(
        self,
        identity: SettlementIdentity,
        *,
        source_fact_seq: int,
        reason_code: str,
        barrier_hash: str,
        evidence_refs: Sequence[str],
        snapshot: SettlementJournalReplaySnapshot | None = None,
    ) -> FactEventAppendedV1:
        """Persist a terminal dead letter for a validated identity."""

        normalized_reason = str(reason_code or "invalid_settlement_fact").strip()
        payload = self._base_payload(
            identity,
            source_fact_seq=source_fact_seq,
            barrier_hash=barrier_hash,
            evidence_refs=evidence_refs,
        )
        payload["reason_code"] = normalized_reason
        return self._append(
            status=SettlementJournalStatus.DEAD_LETTER,
            payload=payload,
            identity=identity,
            idempotency_suffix=f"dead_letter:{normalized_reason}",
            snapshot=snapshot,
        )

    def append_invalid_dead_letter(
        self,
        *,
        source_event: Mapping[str, Any],
        source_fact_event_id: str,
        source_fact_seq: int,
        reason_code: str,
        snapshot: SettlementJournalReplaySnapshot | None = None,
    ) -> FactEventAppendedV1:
        """Persist an invalid source fact without inventing a valid fenced identity."""

        fingerprint = _stable_digest(source_event)
        event_id = str(source_fact_event_id or "").strip() or f"invalid-{fingerprint[:24]}"
        reason = str(reason_code or "invalid_settlement_fact").strip()
        settlement_key = _stable_digest(
            {
                "source_fact_event_id": event_id,
                "source_fingerprint": fingerprint,
                "workspace": self._workspace,
            }
        )
        payload = {
            "schema_version": _JOURNAL_SCHEMA,
            "workspace": self._workspace,
            "source_fact_event_id": event_id,
            "source_fact_seq": max(0, int(source_fact_seq)),
            "source_fingerprint": fingerprint,
            "factory_run_id": "",
            "workspace_fencing_token": 0,
            "settlement_key": settlement_key,
            "reason_code": reason,
            "barrier_hash": "",
            "evidence_refs": [],
        }
        appended = self._fact_stream.append(
            AppendFactEventCommandV1(
                workspace=self._workspace,
                stream=FACTORY_SETTLEMENT_STREAM,
                event_type=SettlementJournalStatus.DEAD_LETTER.value,
                payload=payload,
                source=FACTORY_SETTLEMENT_SOURCE,
                correlation_id=event_id,
                idempotency_key=f"factory-settlement:{settlement_key}:dead_letter:{reason}",
                expected_seq=(snapshot.current_seq + 1 if snapshot is not None else None),
            )
        )
        if snapshot is not None:
            snapshot.record_append(
                status=SettlementJournalStatus.DEAD_LETTER,
                payload=payload,
                appended=appended,
            )
        return appended

    def append_checkpoint(
        self,
        *,
        source_stream: str,
        source_fact_event_id: str,
        source_fact_seq: int,
        next_source_offset: int,
        settlement_key: str,
        snapshot: SettlementJournalReplaySnapshot | None = None,
        source_event: Mapping[str, Any] | None = None,
    ) -> FactEventAppendedV1:
        """Persist a source cursor only after an ACK-safe terminal decision."""

        stream = str(source_stream or "").strip()
        event_id = str(source_fact_event_id or "").strip()
        key = str(settlement_key or "").strip()
        if not stream or not event_id or not key:
            raise ValueError("checkpoint requires source_stream, source_fact_event_id, and settlement_key")
        if isinstance(next_source_offset, bool) or not isinstance(next_source_offset, int) or next_source_offset < 1:
            raise ValueError("next_source_offset must be >= 1")
        if isinstance(source_fact_seq, bool) or not isinstance(source_fact_seq, int) or source_fact_seq < 0:
            raise ValueError("source_fact_seq must be an int >= 0")
        if snapshot is not None:
            records = snapshot.records
            current_seq = snapshot.current_seq
            chain = snapshot.checkpoint_chain(source_stream=stream)
        else:
            records, current_seq = self._read_records()
            chain = self._validate_checkpoint_chain(records=records, source_stream=stream)
        existing = self._checkpoint_at_offset(chain, next_source_offset=next_source_offset)
        if existing is not None:
            if not self._checkpoint_matches_request(
                record=existing.record,
                source_stream=stream,
                source_fact_event_id=event_id,
                source_fact_seq=source_fact_seq,
                settlement_key=key,
            ):
                if chain and next_source_offset <= chain[-1].next_source_offset:
                    raise SettlementJournalValidationError(
                        "checkpoint next_source_offset cannot move the durable cursor backward",
                        code="checkpoint_offset_not_contiguous",
                    )
                raise SettlementJournalValidationError(
                    "checkpoint offset is already bound to another source decision",
                    code="checkpoint_offset_reused",
                )
            return self._append_checkpoint_event(
                payload=existing.record.payload,
                source_fact_event_id=event_id,
                settlement_key=key,
                expected_seq=existing.record.event_seq,
            )

        previous = chain[-1] if chain else None
        expected_offset = (previous.next_source_offset if previous is not None else 0) + 1
        if next_source_offset != expected_offset:
            raise SettlementJournalValidationError(
                "checkpoint next_source_offset must advance exactly one source fact",
                code="checkpoint_offset_not_contiguous",
            )

        if source_event is None:
            self._verify_checkpoint_source(
                source_stream=stream,
                source_fact_event_id=event_id,
                source_fact_seq=source_fact_seq,
                next_source_offset=next_source_offset,
            )
        else:
            self._verify_checkpoint_source_event(
                source_event=source_event,
                source_fact_event_id=event_id,
                source_fact_seq=source_fact_seq,
            )
        decision_provenance = self._checkpoint_decision_provenance(
            records=records,
            source_fact_event_id=event_id,
            source_fact_seq=source_fact_seq,
            settlement_key=key,
        )
        expected_seq = current_seq + 1
        payload = {
            "schema_version": _JOURNAL_SCHEMA,
            "workspace": self._workspace,
            "source_stream": stream,
            "source_fact_event_id": event_id,
            "source_fact_seq": source_fact_seq,
            "next_source_offset": int(next_source_offset),
            "settlement_key": key,
            "previous_checkpoint_event_id": previous.record.event_id if previous else "",
            "previous_checkpoint_event_seq": previous.record.event_seq if previous else 0,
            "previous_next_source_offset": previous.next_source_offset if previous else 0,
            "journal_expected_seq": expected_seq,
            **decision_provenance,
        }
        try:
            appended = self._append_checkpoint_event(
                payload=payload,
                source_fact_event_id=event_id,
                settlement_key=key,
                expected_seq=expected_seq,
            )
            if snapshot is not None:
                snapshot.record_append(
                    status=SettlementJournalStatus.CHECKPOINT,
                    payload=payload,
                    appended=appended,
                )
            return appended
        except FactStreamError as exc:
            if exc.code != "expected_seq_drift":
                raise
            return self._recover_checkpoint_append_after_drift(
                source_stream=stream,
                source_fact_event_id=event_id,
                source_fact_seq=source_fact_seq,
                next_source_offset=next_source_offset,
                settlement_key=key,
                snapshot=snapshot,
            )

    def _base_payload(
        self,
        identity: SettlementIdentity,
        *,
        source_fact_seq: int,
        barrier_hash: str,
        evidence_refs: Sequence[str],
    ) -> dict[str, Any]:
        payload = identity.to_payload()
        payload.update(
            {
                "schema_version": _JOURNAL_SCHEMA,
                "source_fact_seq": max(0, int(source_fact_seq)),
            }
        )
        payload.update(
            _barrier_audit_payload(
                barrier_hash=barrier_hash,
                evidence_refs=evidence_refs,
            )
        )
        return payload

    def _append(
        self,
        *,
        status: SettlementJournalStatus,
        payload: Mapping[str, Any],
        identity: SettlementIdentity,
        idempotency_suffix: str,
        expected_seq: int | None = None,
        snapshot: SettlementJournalReplaySnapshot | None = None,
    ) -> FactEventAppendedV1:
        effective_expected_seq = expected_seq
        if effective_expected_seq is None and snapshot is not None:
            effective_expected_seq = snapshot.current_seq + 1
        appended = self._fact_stream.append(
            AppendFactEventCommandV1(
                workspace=self._workspace,
                stream=FACTORY_SETTLEMENT_STREAM,
                event_type=status.value,
                payload=payload,
                source=FACTORY_SETTLEMENT_SOURCE,
                run_id=identity.factory_run_id,
                correlation_id=identity.source_fact_event_id,
                idempotency_key=(f"factory-settlement:{identity.digest}:{idempotency_suffix}"),
                expected_seq=effective_expected_seq,
            )
        )
        if snapshot is not None:
            snapshot.record_append(status=status, payload=payload, appended=appended)
        return appended

    def _append_checkpoint_event(
        self,
        *,
        payload: Mapping[str, Any],
        source_fact_event_id: str,
        settlement_key: str,
        expected_seq: int,
    ) -> FactEventAppendedV1:
        """Append one checkpoint through the FactStream CAS and idempotency gate."""

        return self._fact_stream.append(
            AppendFactEventCommandV1(
                workspace=self._workspace,
                stream=FACTORY_SETTLEMENT_STREAM,
                event_type=SettlementJournalStatus.CHECKPOINT.value,
                payload=payload,
                source=FACTORY_SETTLEMENT_SOURCE,
                correlation_id=source_fact_event_id,
                idempotency_key=(
                    f"factory-settlement:{settlement_key}:checkpoint:{int(payload['next_source_offset'])}"
                ),
                expected_seq=expected_seq,
            )
        )

    def _recover_checkpoint_append_after_drift(
        self,
        *,
        source_stream: str,
        source_fact_event_id: str,
        source_fact_seq: int,
        next_source_offset: int,
        settlement_key: str,
        snapshot: SettlementJournalReplaySnapshot | None = None,
    ) -> FactEventAppendedV1:
        """Return a concurrent idempotent checkpoint or fail closed on a real conflict."""

        records, current_seq, chain = self._read_checkpoint_drift_state(
            source_stream=source_stream,
            source_events=snapshot.source_events if snapshot is not None else None,
        )
        if snapshot is not None:
            snapshot.replace(
                records=records,
                current_seq=current_seq,
                checkpoint_chain=chain,
            )
        existing = self._checkpoint_at_offset(chain, next_source_offset=next_source_offset)
        if existing is None or not self._checkpoint_matches_request(
            record=existing.record,
            source_stream=source_stream,
            source_fact_event_id=source_fact_event_id,
            source_fact_seq=source_fact_seq,
            settlement_key=settlement_key,
        ):
            raise SettlementJournalValidationError(
                "checkpoint CAS drift was not an idempotent concurrent append",
                code="checkpoint_cas_drift",
            )
        return self._append_checkpoint_event(
            payload=existing.record.payload,
            source_fact_event_id=source_fact_event_id,
            settlement_key=settlement_key,
            expected_seq=existing.record.event_seq,
        )

    def _reload_replay_snapshot_after_drift(
        self,
        snapshot: SettlementJournalReplaySnapshot,
    ) -> None:
        """Perform one bounded journal reload after a FactStream CAS drift."""

        records, current_seq, chain = self._read_checkpoint_drift_state(
            source_stream=snapshot.source_stream,
            source_events=snapshot.source_events,
        )
        snapshot.replace(
            records=records,
            current_seq=current_seq,
            checkpoint_chain=chain,
        )

    def _read_checkpoint_drift_state(
        self,
        *,
        source_stream: str,
        source_events: Mapping[int, Mapping[str, Any]] | None,
    ) -> tuple[tuple[SettlementJournalRecord, ...], int, tuple[_CheckpointChainEntry, ...]]:
        """Reload and validate FactStream journal facts for a CAS-drift decision."""

        records, current_seq = self._read_records()
        chain = self._validate_checkpoint_chain(
            records=records,
            source_stream=source_stream,
            source_events=source_events,
        )
        return records, current_seq, chain

    def _read_records(self) -> tuple[tuple[SettlementJournalRecord, ...], int]:
        records: list[SettlementJournalRecord] = []
        offset = 0
        current_seq = 0
        while True:
            page = self._fact_stream.query(
                QueryFactEventsV1(
                    workspace=self._workspace,
                    stream=FACTORY_SETTLEMENT_STREAM,
                    limit=_PAGE_SIZE,
                    offset=offset,
                )
            )
            if _canonical_workspace(page.workspace) != self._workspace:
                raise SettlementJournalValidationError(
                    "journal query returned a foreign workspace",
                    code="journal_query_workspace_mismatch",
                )
            if page.stream != FACTORY_SETTLEMENT_STREAM:
                raise SettlementJournalValidationError(
                    "journal query returned a foreign stream",
                    code="journal_query_stream_mismatch",
                )
            for event in page.events:
                record = self._record_from_event(event)
                records.append(record)
                current_seq = max(current_seq, record.event_seq)
            if page.next_offset == 0:
                records.sort(key=lambda record: record.event_seq)
                if any(current.event_seq <= previous.event_seq for previous, current in pairwise(records)):
                    raise SettlementJournalValidationError(
                        "journal events must have strictly increasing sequences",
                        code="invalid_journal_event_sequence",
                    )
                return tuple(records), current_seq
            if page.next_offset <= offset:
                raise FactStreamError(
                    "factory.settlement query returned a non-advancing cursor",
                    code="non_advancing_cursor",
                    details={"offset": offset, "next_offset": page.next_offset},
                )
            offset = page.next_offset

    def _record_from_event(self, event: Mapping[str, Any]) -> SettlementJournalRecord:
        schema_version = event.get("schema_version")
        if (
            isinstance(schema_version, bool)
            or not isinstance(schema_version, int)
            or schema_version != _FACT_STREAM_ENVELOPE_VERSION
        ):
            raise SettlementJournalValidationError(
                "journal envelope schema_version is missing or unsupported",
                code="unsupported_journal_envelope_schema",
            )
        event_version = event.get("event_version")
        if (
            isinstance(event_version, bool)
            or not isinstance(event_version, int)
            or event_version != _FACT_STREAM_ENVELOPE_VERSION
        ):
            raise SettlementJournalValidationError(
                "journal envelope event_version is missing or unsupported",
                code="unsupported_journal_event_version",
            )
        if str(event.get("stream") or "").strip() != FACTORY_SETTLEMENT_STREAM:
            raise SettlementJournalValidationError(
                "journal event belongs to a foreign stream",
                code="invalid_journal_stream",
            )
        if str(event.get("source") or "").strip() != FACTORY_SETTLEMENT_SOURCE:
            raise SettlementJournalValidationError(
                "journal event has an untrusted source",
                code="invalid_journal_source",
            )

        event_type = str(event.get("event_type") or "").strip()
        try:
            status = SettlementJournalStatus(event_type)
        except ValueError as exc:
            raise SettlementJournalValidationError(
                f"unknown settlement journal status: {event_type or '<missing>'}",
                code="unknown_journal_status",
            ) from exc

        payload_raw = event.get("payload")
        if not isinstance(payload_raw, Mapping):
            raise SettlementJournalValidationError(
                "journal payload must be a mapping",
                code="invalid_journal_payload",
            )
        payload = dict(payload_raw)
        if payload.get("schema_version") != _JOURNAL_SCHEMA:
            raise SettlementJournalValidationError(
                "settlement journal payload schema is unsupported",
                code="unsupported_journal_schema",
            )
        payload_workspace = str(payload.get("workspace") or "").strip()
        if not payload_workspace or _canonical_workspace(payload_workspace) != self._workspace:
            raise SettlementJournalValidationError(
                "journal payload workspace does not match the journal",
                code="journal_workspace_mismatch",
            )

        event_id = str(event.get("event_id") or "").strip()
        if not event_id:
            raise SettlementJournalValidationError(
                "journal event_id is missing",
                code="invalid_journal_event_id",
            )
        event_seq = _strict_int(
            event.get("seq"),
            field_name="event_seq",
            minimum=1,
        )
        settlement_key = str(payload.get("settlement_key") or "").strip()
        source_fact_event_id = str(payload.get("source_fact_event_id") or "").strip()
        if not settlement_key or not source_fact_event_id:
            raise SettlementJournalValidationError(
                "journal payload lacks settlement or source identity",
                code="invalid_journal_identity",
            )
        _strict_int(
            payload.get("source_fact_seq"),
            field_name="source_fact_seq",
            minimum=0,
        )
        self._validate_status_payload(status=status, payload=payload)
        return SettlementJournalRecord(
            event_id=event_id,
            event_seq=event_seq,
            status=status,
            settlement_key=settlement_key,
            payload=payload,
        )

    def _validate_status_payload(
        self,
        *,
        status: SettlementJournalStatus,
        payload: Mapping[str, Any],
    ) -> None:
        if status is SettlementJournalStatus.CHECKPOINT:
            if not str(payload.get("source_stream") or "").strip():
                raise SettlementJournalValidationError(
                    "checkpoint source_stream is missing",
                    code="invalid_checkpoint_source_stream",
                )
            _strict_int(
                payload.get("next_source_offset"),
                field_name="next_source_offset",
                minimum=1,
            )
            previous_event_id = payload.get("previous_checkpoint_event_id")
            if not isinstance(previous_event_id, str):
                raise SettlementJournalValidationError(
                    "checkpoint previous_checkpoint_event_id must be a string",
                    code="invalid_checkpoint_previous_provenance",
                )
            _strict_int(
                payload.get("previous_checkpoint_event_seq"),
                field_name="previous_checkpoint_event_seq",
                minimum=0,
            )
            _strict_int(
                payload.get("previous_next_source_offset"),
                field_name="previous_next_source_offset",
                minimum=0,
            )
            _strict_int(
                payload.get("journal_expected_seq"),
                field_name="journal_expected_seq",
                minimum=1,
            )
            decision_kind = str(payload.get("decision_kind") or "").strip()
            decision_event_id = payload.get("decision_event_id")
            decision_event_seq = payload.get("decision_event_seq")
            decision_status = str(payload.get("decision_status") or "").strip()
            if decision_kind == "journal_terminal":
                if not isinstance(decision_event_id, str) or not decision_event_id.strip():
                    raise SettlementJournalValidationError(
                        "checkpoint terminal decision event_id is missing",
                        code="invalid_checkpoint_decision_provenance",
                    )
                _strict_int(
                    decision_event_seq,
                    field_name="decision_event_seq",
                    minimum=1,
                )
                if decision_status not in {
                    SettlementJournalStatus.APPLIED.value,
                    SettlementJournalStatus.DEAD_LETTER.value,
                }:
                    raise SettlementJournalValidationError(
                        "checkpoint terminal decision status is invalid",
                        code="invalid_checkpoint_decision_provenance",
                    )
            elif decision_kind == "ignored":
                if (
                    decision_event_id != ""
                    or decision_event_seq != 0
                    or decision_status != "ignored"
                    or not str(payload.get("settlement_key") or "").startswith("ignored:")
                ):
                    raise SettlementJournalValidationError(
                        "checkpoint ignored decision provenance is invalid",
                        code="invalid_checkpoint_decision_provenance",
                    )
            else:
                raise SettlementJournalValidationError(
                    "checkpoint decision provenance kind is unsupported",
                    code="invalid_checkpoint_decision_provenance",
                )
            return

        factory_run_id = str(payload.get("factory_run_id") or "").strip()
        fencing_token = payload.get("workspace_fencing_token")
        invalid_dead_letter = bool(
            status is SettlementJournalStatus.DEAD_LETTER
            and str(payload.get("source_fingerprint") or "").strip()
            and not factory_run_id
            and fencing_token == 0
        )
        if not invalid_dead_letter:
            if not factory_run_id:
                raise SettlementJournalValidationError(
                    "journal factory_run_id is missing",
                    code="invalid_journal_factory_run_id",
                )
            _strict_int(
                fencing_token,
                field_name="workspace_fencing_token",
                minimum=1,
            )
        if status is SettlementJournalStatus.PENDING:
            phase = str(payload.get("phase") or "").strip()
            try:
                SettlementPendingPhase(phase)
            except ValueError as exc:
                raise SettlementJournalValidationError(
                    f"unknown settlement pending phase: {phase or '<missing>'}",
                    code="unknown_journal_pending_phase",
                ) from exc

        barrier_hash = payload.get("barrier_hash")
        evidence_refs = payload.get("evidence_refs")
        if not isinstance(barrier_hash, str):
            raise SettlementJournalValidationError(
                "journal barrier_hash must be a string",
                code="invalid_journal_barrier_hash",
            )
        if not isinstance(evidence_refs, Sequence) or isinstance(
            evidence_refs,
            (str, bytes, bytearray),
        ):
            raise SettlementJournalValidationError(
                "journal evidence_refs must be a sequence of strings",
                code="invalid_journal_evidence_refs",
            )
        if any(not isinstance(reference, str) for reference in evidence_refs):
            raise SettlementJournalValidationError(
                "journal evidence_refs must contain only strings",
                code="invalid_journal_evidence_refs",
            )

    def _checkpoint_at_offset(
        self,
        chain: Sequence[_CheckpointChainEntry],
        *,
        next_source_offset: int,
    ) -> _CheckpointChainEntry | None:
        """Return the checkpoint for one source offset, if already committed."""

        return next(
            (entry for entry in chain if entry.next_source_offset == next_source_offset),
            None,
        )

    def _checkpoint_matches_request(
        self,
        *,
        record: SettlementJournalRecord,
        source_stream: str,
        source_fact_event_id: str,
        source_fact_seq: int,
        settlement_key: str,
    ) -> bool:
        """Check whether a committed cursor is the same idempotent request."""

        payload = record.payload
        return (
            str(payload.get("source_stream") or "").strip() == source_stream
            and str(payload.get("source_fact_event_id") or "").strip() == source_fact_event_id
            and payload.get("source_fact_seq") == source_fact_seq
            and str(payload.get("settlement_key") or "").strip() == settlement_key
        )

    def _checkpoint_decision_provenance(
        self,
        *,
        records: Sequence[SettlementJournalRecord],
        source_fact_event_id: str,
        source_fact_seq: int,
        settlement_key: str,
    ) -> dict[str, object]:
        """Bind a checkpoint to its terminal decision or explicit ignored outcome."""

        terminal_records = [
            record
            for record in records
            if record.status in {SettlementJournalStatus.APPLIED, SettlementJournalStatus.DEAD_LETTER}
            and str(record.payload.get("source_fact_event_id") or "").strip() == source_fact_event_id
            and record.payload.get("source_fact_seq") == source_fact_seq
        ]
        if terminal_records:
            terminal = terminal_records[-1]
            return {
                "decision_kind": "journal_terminal",
                "decision_event_id": terminal.event_id,
                "decision_event_seq": terminal.event_seq,
                "decision_status": terminal.status.value,
            }
        if settlement_key.startswith("ignored:"):
            return {
                "decision_kind": "ignored",
                "decision_event_id": "",
                "decision_event_seq": 0,
                "decision_status": "ignored",
            }
        raise SettlementJournalValidationError(
            "checkpoint lacks a durable terminal decision provenance",
            code="checkpoint_terminal_decision_missing",
        )

    def _validate_checkpoint_chain(
        self,
        *,
        records: Sequence[SettlementJournalRecord],
        source_stream: str,
        source_events: Mapping[int, Mapping[str, Any]] | None = None,
    ) -> tuple[_CheckpointChainEntry, ...]:
        """Validate one source cursor chain in linear journal time and space."""

        by_event_id = {record.event_id: record for record in records}
        chain: list[_CheckpointChainEntry] = []
        previous_event_id = ""
        previous_event_seq = 0
        previous_next_offset = 0
        for record in records:
            if record.status is not SettlementJournalStatus.CHECKPOINT:
                continue
            payload = record.payload
            recorded_stream = str(payload.get("source_stream") or "").strip()
            if recorded_stream != source_stream:
                raise SettlementJournalValidationError(
                    "checkpoint source stream does not match recovery stream",
                    code="checkpoint_source_stream_mismatch",
                )
            next_source_offset = _strict_int(
                payload.get("next_source_offset"),
                field_name="next_source_offset",
                minimum=1,
            )
            if (
                payload.get("previous_checkpoint_event_id") != previous_event_id
                or payload.get("previous_checkpoint_event_seq") != previous_event_seq
                or payload.get("previous_next_source_offset") != previous_next_offset
            ):
                raise SettlementJournalValidationError(
                    "checkpoint previous provenance does not match the durable chain",
                    code="checkpoint_previous_provenance_mismatch",
                )
            if next_source_offset != previous_next_offset + 1:
                raise SettlementJournalValidationError(
                    "checkpoint offsets must advance by exactly one",
                    code="checkpoint_offset_not_contiguous",
                )
            if payload.get("journal_expected_seq") != record.event_seq:
                raise SettlementJournalValidationError(
                    "checkpoint event sequence does not prove the requested CAS position",
                    code="checkpoint_expected_seq_mismatch",
                )
            self._verify_checkpoint_decision_provenance(
                checkpoint=record,
                records_by_event_id=by_event_id,
            )
            entry = _CheckpointChainEntry(
                record=record,
                next_source_offset=next_source_offset,
            )
            chain.append(entry)
            previous_event_id = record.event_id
            previous_event_seq = record.event_seq
            previous_next_offset = next_source_offset
        self._verify_checkpoint_sources(
            chain=chain,
            source_stream=source_stream,
            source_events=source_events,
        )
        return tuple(chain)

    def _verify_checkpoint_sources(
        self,
        *,
        chain: Sequence[_CheckpointChainEntry],
        source_stream: str,
        source_events: Mapping[int, Mapping[str, Any]] | None = None,
    ) -> None:
        """Verify all checkpoint source facts in one forward source-stream scan."""

        if not chain:
            return
        if source_events is not None:
            for entry in chain:
                source_event = source_events.get(entry.next_source_offset)
                if source_event is None:
                    raise SettlementJournalValidationError(
                        "checkpoint offset does not resolve to exactly one source fact",
                        code="checkpoint_source_fact_mismatch",
                    )
                self._verify_checkpoint_source_event(
                    source_event=source_event,
                    source_fact_event_id=str(entry.record.payload.get("source_fact_event_id") or "").strip(),
                    source_fact_seq=_strict_int(
                        entry.record.payload.get("source_fact_seq"),
                        field_name="source_fact_seq",
                        minimum=0,
                    ),
                )
            return
        expected_sources = {
            entry.next_source_offset: (
                str(entry.record.payload.get("source_fact_event_id") or "").strip(),
                _strict_int(
                    entry.record.payload.get("source_fact_seq"),
                    field_name="source_fact_seq",
                    minimum=0,
                ),
            )
            for entry in chain
        }
        offset = 0
        while expected_sources:
            page = self._fact_stream.query(
                QueryFactEventsV1(
                    workspace=self._workspace,
                    stream=source_stream,
                    limit=_PAGE_SIZE,
                    offset=offset,
                )
            )
            if _canonical_workspace(page.workspace) != self._workspace or page.stream != source_stream:
                raise SettlementJournalValidationError(
                    "checkpoint source query crossed its workspace or stream boundary",
                    code="checkpoint_source_query_mismatch",
                )
            for index, source_event in enumerate(page.events, start=1):
                expected = expected_sources.pop(offset + index, None)
                if expected is None:
                    continue
                source_event_id = str(source_event.get("event_id") or "").strip()
                source_event_seq = source_event.get("seq")
                if (
                    source_event_id != expected[0]
                    or isinstance(source_event_seq, bool)
                    or not isinstance(source_event_seq, int)
                    or source_event_seq != expected[1]
                ):
                    raise SettlementJournalValidationError(
                        "checkpoint source fact identity does not match its claimed offset",
                        code="checkpoint_source_fact_mismatch",
                    )
            if not expected_sources:
                return
            if page.next_offset == 0:
                break
            if page.next_offset <= offset:
                raise FactStreamError(
                    "checkpoint source query returned a non-advancing cursor",
                    code="non_advancing_cursor",
                    details={"offset": offset, "next_offset": page.next_offset},
                )
            offset = page.next_offset
        raise SettlementJournalValidationError(
            "checkpoint offset does not resolve to exactly one source fact",
            code="checkpoint_source_fact_mismatch",
        )

    def _verify_checkpoint_decision_provenance(
        self,
        *,
        checkpoint: SettlementJournalRecord,
        records_by_event_id: Mapping[str, SettlementJournalRecord],
    ) -> None:
        """Ensure checkpoint decision provenance names a prior terminal journal fact."""

        payload = checkpoint.payload
        decision_kind = str(payload.get("decision_kind") or "").strip()
        if decision_kind == "ignored":
            return
        decision_event_id = str(payload.get("decision_event_id") or "").strip()
        decision = records_by_event_id.get(decision_event_id)
        if (
            decision is None
            or decision.event_seq != payload.get("decision_event_seq")
            or decision.event_seq >= checkpoint.event_seq
            or decision.status.value != payload.get("decision_status")
            or decision.status not in {SettlementJournalStatus.APPLIED, SettlementJournalStatus.DEAD_LETTER}
            or str(decision.payload.get("source_fact_event_id") or "").strip()
            != str(payload.get("source_fact_event_id") or "").strip()
            or decision.payload.get("source_fact_seq") != payload.get("source_fact_seq")
        ):
            raise SettlementJournalValidationError(
                "checkpoint decision provenance does not name a matching prior terminal fact",
                code="checkpoint_decision_provenance_mismatch",
            )

    def _verify_checkpoint_source(
        self,
        *,
        source_stream: str,
        source_fact_event_id: str,
        source_fact_seq: int,
        next_source_offset: int,
    ) -> None:
        page = self._fact_stream.query(
            QueryFactEventsV1(
                workspace=self._workspace,
                stream=source_stream,
                limit=1,
                offset=next_source_offset - 1,
            )
        )
        if _canonical_workspace(page.workspace) != self._workspace or page.stream != source_stream:
            raise SettlementJournalValidationError(
                "checkpoint source query crossed its workspace or stream boundary",
                code="checkpoint_source_query_mismatch",
            )
        if len(page.events) != 1:
            raise SettlementJournalValidationError(
                "checkpoint offset does not resolve to exactly one source fact",
                code="checkpoint_source_fact_mismatch",
            )
        self._verify_checkpoint_source_event(
            source_event=page.events[0],
            source_fact_event_id=source_fact_event_id,
            source_fact_seq=source_fact_seq,
        )

    @staticmethod
    def _verify_checkpoint_source_event(
        *,
        source_event: Mapping[str, Any],
        source_fact_event_id: str,
        source_fact_seq: int,
    ) -> None:
        """Validate one source fact already obtained from a durable page."""

        source_event_id = str(source_event.get("event_id") or "").strip()
        source_event_seq = source_event.get("seq")
        if (
            source_event_id != source_fact_event_id
            or isinstance(source_event_seq, bool)
            or not isinstance(source_event_seq, int)
            or source_event_seq != source_fact_seq
        ):
            raise SettlementJournalValidationError(
                "checkpoint source fact identity does not match its claimed offset",
                code="checkpoint_source_fact_mismatch",
            )


__all__ = [
    "FACTORY_SETTLEMENT_SOURCE",
    "FACTORY_SETTLEMENT_STREAM",
    "FactStreamPort",
    "FactorySettlementJournal",
    "SettlementClaim",
    "SettlementIdentity",
    "SettlementJournalRecord",
    "SettlementJournalReplaySnapshot",
    "SettlementJournalStatus",
    "SettlementJournalValidationError",
    "SettlementPendingPhase",
]
