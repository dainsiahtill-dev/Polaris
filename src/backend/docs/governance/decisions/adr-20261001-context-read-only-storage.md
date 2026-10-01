# Context reads must not initialize writable storage

Status: accepted for implementation within the approved unattended plan.

Decision: separate Cognitive Runtime query-store acquisition and SQL read connections
from write-store initialization. Missing handoffs/receipts are unavailable; they do
not authorize directory creation, schema migration or writer start. Preserve WAL
visibility and explicit custom DB contracts. Reject managed logical-path resolution
errors instead of downgrading to unmanaged workspace-relative paths.

Why: exact live test replay observed /ws/.polaris/runtime probes and /ws/runtime DB
fallback on a read. This violates CQRS, canonical runtime identity and privacy bounds.

Costs: readonly existing-database connection/schema errors must be surfaced; no silent
repair/migration. SQLite-native WAL/shm locking can still occur in mode=ro and is not
claimed as absolute zero filesystem activity. Full arbitrary adversarial DB-path
replacement is not certified by this change.

Implementation/proof: POLARIS_CONTEXT_READ_ONLY_STORAGE_20261001.md and matching
VC-20261001-CONTEXT-READ-ONLY-STORAGE, followed by query/DB/store/WAL regressions.
