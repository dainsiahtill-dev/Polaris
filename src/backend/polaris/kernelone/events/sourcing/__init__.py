"""KernelOne event sourcing exports."""

from .file_store import JsonlEventStore, query_stream_events
from .guarded import (
    AppendIfGuardedSnapshotCommandV1,
    GuardedFactAppendedV1,
    GuardedFactEventV1,
    GuardedFactSnapshotProofV1,
    GuardedFactSnapshotV1,
    ReadGuardedFactSnapshotCommandV1,
    append_if_guarded_snapshot,
    read_guarded_fact_snapshot,
)
from .models import (
    EventEnvelope,
    EventQueryResult,
    EventSourcingError,
    ExpectedSequenceDriftError,
    IdempotencyConflictError,
    StrictEventRecordError,
    decode_strict_event_record,
)
from .segmented_file_store import (
    SegmentedEventStoreError,
    SegmentedJsonlEventStore,
    SegmentedLedgerHeadV1,
    SegmentedQueryResultV1,
    SegmentedStoredEventV1,
)

__all__ = [
    "AppendIfGuardedSnapshotCommandV1",
    "EventEnvelope",
    "EventQueryResult",
    "EventSourcingError",
    "ExpectedSequenceDriftError",
    "GuardedFactAppendedV1",
    "GuardedFactEventV1",
    "GuardedFactSnapshotProofV1",
    "GuardedFactSnapshotV1",
    "IdempotencyConflictError",
    "JsonlEventStore",
    "ReadGuardedFactSnapshotCommandV1",
    "SegmentedEventStoreError",
    "SegmentedJsonlEventStore",
    "SegmentedLedgerHeadV1",
    "SegmentedQueryResultV1",
    "SegmentedStoredEventV1",
    "StrictEventRecordError",
    "append_if_guarded_snapshot",
    "decode_strict_event_record",
    "query_stream_events",
    "read_guarded_fact_snapshot",
]
