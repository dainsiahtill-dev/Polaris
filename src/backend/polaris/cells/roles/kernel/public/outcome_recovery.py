"""Recover an authenticated prepared outcome without executing its tools again."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from polaris.cells.events.fact_stream.public import FactPayloadReferenceV1, FactStreamProvenanceV1
from polaris.cells.roles.kernel.public.turn_contracts import CommitReceipt, SealedTurn, TurnOutcome

_STREAMS = {"roles.kernel.turn_outcomes", "roles.kernel.turn_outcomes.v3"}


@dataclass(frozen=True, slots=True)
class PreparedTurnOutcomeReferenceV1:
    """A locator for a real prepared fact; it is not a completion or capability."""

    provenance: FactStreamProvenanceV1
    stream: str
    prepared_event_id: str
    prepared_event_seq: int
    payload_ref: FactPayloadReferenceV1

    def __post_init__(self) -> None:
        if type(self.provenance) is not FactStreamProvenanceV1:
            raise ValueError("prepared provenance must be FactStreamProvenanceV1")
        if self.stream not in _STREAMS:
            raise ValueError("unsupported prepared outcome stream")
        if not isinstance(self.prepared_event_id, str) or not self.prepared_event_id.strip():
            raise ValueError("prepared event id is required")
        if type(self.prepared_event_seq) is not int or self.prepared_event_seq < 1:
            raise ValueError("prepared event sequence must be positive")
        if type(self.payload_ref) is not FactPayloadReferenceV1:
            raise ValueError("prepared payload reference is required")
        for value in (
            self.provenance.run_id,
            self.provenance.task_id,
            self.provenance.turn_id,
            self.provenance.transition_id,
        ):
            if not value:
                raise ValueError("complete prepared outcome scope is required")


@dataclass(frozen=True, slots=True)
class QueryPreparedTurnOutcomeV1:
    provenance: FactStreamProvenanceV1
    stream: str | None = None

    def __post_init__(self) -> None:
        if type(self.provenance) is not FactStreamProvenanceV1 or (
            self.stream is not None and self.stream not in _STREAMS
        ):
            raise ValueError("typed prepared outcome scope and stream are required")
        if any(
            not value
            for value in (
                self.provenance.run_id,
                self.provenance.task_id,
                self.provenance.turn_id,
                self.provenance.transition_id,
            )
        ):
            raise ValueError("complete prepared outcome scope is required")


@dataclass(frozen=True, slots=True)
class ReplayPreparedTurnOutcomeCommandV1:
    prepared: PreparedTurnOutcomeReferenceV1
    expected_head_seq: int
    expected_session_id: str | None = None

    def __post_init__(self) -> None:
        if type(self.prepared) is not PreparedTurnOutcomeReferenceV1:
            raise ValueError("prepared outcome reference must be typed")
        if type(self.expected_head_seq) is not int or self.expected_head_seq < 0:
            raise ValueError("expected head sequence must be non-negative")
        if self.expected_session_id is not None and type(self.expected_session_id) is not str:
            raise ValueError("expected session id must be text or None")


@dataclass(frozen=True, slots=True)
class RecoveredTurnOutcomeV1:
    outcome: TurnOutcome
    receipt: CommitReceipt
    sealed_turn: SealedTurn
    result_projection_json: str = "{}"

    @property
    def result_projection(self) -> dict[str, Any]:
        """Return the original detached result projection, never a guessed kind."""
        return json.loads(self.result_projection_json)


def query_prepared_turn_outcome(query: QueryPreparedTurnOutcomeV1) -> PreparedTurnOutcomeReferenceV1 | None:
    from polaris.cells.roles.kernel.internal.transaction.outcome_commit import find_prepared_turn_outcome

    return find_prepared_turn_outcome(query)


def replay_prepared_turn_outcome(command: ReplayPreparedTurnOutcomeCommandV1) -> RecoveredTurnOutcomeV1:
    from polaris.cells.roles.kernel.internal.transaction.outcome_commit import replay_prepared_outcome

    return replay_prepared_outcome(command)
