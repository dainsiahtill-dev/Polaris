# Events Fact Stream

## Purpose

Provide strict append-only FactStream facts, guarded snapshots, explicit lock
authority maintenance, and bounded per-stream queries.

## Public Surface

- `AppendFactEventCommandV1`
- `AppendIfGuardedSnapshotCommandV1`
- `AppendPayloadFactEventCommandV1`
- `AppendSegmentedFactEventCommandV1`
- `BootstrapFactStreamWorkspaceCommandV1`
- `EnrollFactStreamStreamsCommandV1`
- `EnsureSegmentedFactLedgerCommandV1`
- `FactEventAppendedV1`
- `FactPayloadReferenceV1`
- `FactStreamError`
- `FactStreamHeadV1`
- `FactStreamLockIdentityV1`
- `FactStreamLockKeyEvidenceV1`
- `FactStreamMaintenanceProofV1`
- `FactStreamMaintenanceReceiptV1`
- `FactStreamProvenanceV1`
- `FactStreamQueryResultV1`
- `GuardedFactAppendedV1`
- `GuardedFactEventV1`
- `GuardedFactSnapshotProofV1`
- `GuardedFactSnapshotV1`
- `ProvisionFactStreamLockAuthorityCommandV1`
- `QueryFactEventsV1`
- `QueryFactStreamHeadV1`
- `QueryPayloadFactByIdempotencyKeyV1`
- `QueryPayloadFactEventsV1`
- `QuerySegmentedFactEventsV1`
- `QuerySegmentedFactLedgerHeadV1`
- `ReadGuardedFactSnapshotCommandV1`
- `SegmentedFactEventAppendedV1`
- `SegmentedFactLedgerHeadV1`
- `SegmentedFactLedgerReadyV1`
- `SegmentedFactQueryResultV1`
- `StoreFactPayloadCommandV1`
- `append_fact_event`
- `append_if_guarded_snapshot`
- `append_payload_fact_event`
- `append_segmented_fact_event`
- `bootstrap_fact_stream_workspace`
- `configure_debug_tracing`
- `emit_debug_event`
- `enroll_fact_stream_streams`
- `ensure_segmented_fact_ledger`
- `fact_stream_bootstrap_streams`
- `install_global_debug_hooks`
- `is_debug_tracing_enabled`
- `log_stream_token`
- `provision_fact_stream_lock_authority`
- `query_fact_events`
- `query_fact_stream_head`
- `query_payload_fact_by_idempotency_key`
- `query_payload_fact_events`
- `query_payload_fact_ledger_presence`
- `query_segmented_fact_events`
- `query_segmented_fact_ledger_head`
- `read_guarded_fact_snapshot`
- `sanitize_headers`
- `set_debug_tracing_enabled`
- `store_fact_payload`

## Public Contracts

- commands:
  - `AppendFactEventCommandV1`
  - `AppendIfGuardedSnapshotCommandV1`
  - `AppendPayloadFactEventCommandV1`
  - `AppendSegmentedFactEventCommandV1`
  - `BootstrapFactStreamWorkspaceCommandV1`
  - `EnrollFactStreamStreamsCommandV1`
  - `EnsureSegmentedFactLedgerCommandV1`
  - `ProvisionFactStreamLockAuthorityCommandV1`
  - `StoreFactPayloadCommandV1`
- queries:
  - `QueryFactEventsV1`
  - `QueryFactStreamHeadV1`
  - `QueryPayloadFactByIdempotencyKeyV1`
  - `QueryPayloadFactEventsV1`
  - `QuerySegmentedFactEventsV1`
  - `QuerySegmentedFactLedgerHeadV1`
  - `ReadGuardedFactSnapshotCommandV1`
- events:
  - `FactEventAppendedV1`
  - `GuardedFactEventV1`
  - `SegmentedFactEventAppendedV1`
- results:
  - `FactPayloadReferenceV1`
  - `FactStreamHeadV1`
  - `FactStreamMaintenanceReceiptV1`
  - `FactStreamProvenanceV1`
  - `FactStreamQueryResultV1`
  - `GuardedFactAppendedV1`
  - `GuardedFactSnapshotProofV1`
  - `GuardedFactSnapshotV1`
  - `SegmentedFactLedgerHeadV1`
  - `SegmentedFactLedgerReadyV1`
  - `SegmentedFactQueryResultV1`
- errors:
  - `FactStreamError`

## Authority Bootstrap

`bootstrap_fact_stream_workspace` provisions one workspace authority and enrolls
the static platform stream catalog. Dynamic streams require the explicit
`enroll_fact_stream_streams` maintenance operation before ordinary FactStream
I/O. Reads and appends never provision, enroll, repair, or rotate lock state.

## Dependencies

None. This Cell consumes KernelOne event-sourcing and filesystem capabilities
without depending on another Polaris Cell.

## State Ownership

- `runtime/events/*`

## Effects Allowed

- `fs.read:runtime/events/*`
- `fs.write:runtime/events/*`

## Verification

- `polaris/cells/events/fact_stream/public/tests/test_public_surface.py`
- `polaris/cells/events/fact_stream/public/tests/test_payload_fact_history.py`
- `polaris/cells/events/fact_stream/public/tests/test_payload_fact_exact_lookup.py`
- `polaris/cells/events/fact_stream/public/tests/test_guarded_fact_append.py`
- `polaris/cells/events/fact_stream/public/tests/test_public_contracts.py`
- `polaris/cells/events/fact_stream/public/tests/test_public_service.py`
- `polaris/cells/events/fact_stream/public/tests/test_segmented_contracts.py`
- `polaris/cells/events/fact_stream/public/tests/test_workspace_bootstrap.py`

## Pinned payload facts

`StoreFactPayloadCommandV1` / `store_fact_payload` explicitly prepares canonical
UTF-8 JSON using the existing workspace lock authority. It enrolls the derived
content path, writes complete bytes once, and confirms file and parent-directory
fsync before returning `FactPayloadReferenceV1`. A replay verifies the entire
SHA-256, byte size, declared payload schema and storage identity, then confirms
durability without rewriting bytes. Queries never enroll or repair lock state.

`AppendPayloadFactEventCommandV1` / `append_payload_fact_event` consumes that
reference. It confirms the existing payload again before the existing segmented
store appends a small `kernelone.payload_fact.v1` record. The generic segmented
append API enforces the same validation for approved payload namespaces, so it
cannot bypass payload verification. Small record size and segment limits remain
unchanged; large JSON does not enter the 4 KiB record.

`QueryPayloadFactEventsV1` / `query_payload_fact_events` returns actual segmented
coordinates and `event_hash`, the complete small `storage_record`, and separately
verified `expanded_payload`. The event hash binds the small raw record; the
reference SHA binds canonical expanded bytes. They are distinct hashes, and a
consumer must not pretend that expanded data was the original storage record.
Missing, foreign or corrupt payloads fail closed. Content remains pinned audit
data and does not use ContextOS TTL or cache eviction.

The initial approved namespace is `roles.kernel.turn_outcomes.v3`, initialized
through `EnsureSegmentedFactLedgerCommandV1`. It is separate from the ordinary
bootstrap catalog. `query_payload_fact_ledger_presence`, using
`QuerySegmentedFactLedgerHeadV1`, observes storage existence without initializing
it; this boolean is not integrity, completion, or release evidence.

New role-outcome writers can use V3 in an empty namespace. Existing V2 streams
retain their original bytes, identities, sequence numbers, hashes and references.
This capability does not automatically migrate a live stream or remove its old
lifetime limit. A future legacy cutover requires an explicit stopped-owner
protocol; no dual-write or raw-digest substitution is provided here.

`QueryPayloadFactByIdempotencyKeyV1` / `query_payload_fact_by_idempotency_key`
uses the existing manifest and locator to verify one exact small-record key and
its expanded payload. It does not expand unrelated payloads, initialize storage
or repair state. The same cold-read integrity and hash distinctions apply.
