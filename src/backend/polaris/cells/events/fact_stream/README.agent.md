# Events Fact Stream

## Purpose

Provide strict append-only FactStream facts, guarded snapshots, explicit lock
authority maintenance, and bounded per-stream queries.

## Public Surface

- `AppendFactEventCommandV1`
- `AppendIfGuardedSnapshotCommandV1`
- `AppendSegmentedFactEventCommandV1`
- `BootstrapFactStreamWorkspaceCommandV1`
- `EnrollFactStreamStreamsCommandV1`
- `EnsureSegmentedFactLedgerCommandV1`
- `FactEventAppendedV1`
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
- `QuerySegmentedFactEventsV1`
- `QuerySegmentedFactLedgerHeadV1`
- `ReadGuardedFactSnapshotCommandV1`
- `SegmentedFactEventAppendedV1`
- `SegmentedFactLedgerHeadV1`
- `SegmentedFactLedgerReadyV1`
- `SegmentedFactQueryResultV1`
- `append_fact_event`
- `append_if_guarded_snapshot`
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
- `query_segmented_fact_events`
- `query_segmented_fact_ledger_head`
- `read_guarded_fact_snapshot`
- `sanitize_headers`
- `set_debug_tracing_enabled`

## Public Contracts

- commands:
  - `AppendFactEventCommandV1`
  - `AppendIfGuardedSnapshotCommandV1`
  - `AppendSegmentedFactEventCommandV1`
  - `BootstrapFactStreamWorkspaceCommandV1`
  - `EnrollFactStreamStreamsCommandV1`
  - `EnsureSegmentedFactLedgerCommandV1`
  - `ProvisionFactStreamLockAuthorityCommandV1`
- queries:
  - `QueryFactEventsV1`
  - `QueryFactStreamHeadV1`
  - `QuerySegmentedFactEventsV1`
  - `QuerySegmentedFactLedgerHeadV1`
  - `ReadGuardedFactSnapshotCommandV1`
- events:
  - `FactEventAppendedV1`
  - `GuardedFactEventV1`
  - `SegmentedFactEventAppendedV1`
- results:
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

- `polaris/cells/events/fact_stream/public/tests/test_guarded_fact_append.py`
- `polaris/cells/events/fact_stream/public/tests/test_public_contracts.py`
- `polaris/cells/events/fact_stream/public/tests/test_public_service.py`
- `polaris/cells/events/fact_stream/public/tests/test_segmented_contracts.py`
- `polaris/cells/events/fact_stream/public/tests/test_workspace_bootstrap.py`
