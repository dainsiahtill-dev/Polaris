# ContextOS/Cognitive Runtime read-only storage boundary

Status: design for implementation, not closure. Owner: factory.cognitive_runtime application facade and existing infrastructure store; KernelOne owns DB path policy.
Classification: structural. User authorized foundation/ContextOS hardening.

Dynamic diagnosis: three real handoff reads on absent /ws attempt canonical
writability probes, then SQLite schema/WAL/writer setup. DB policy catches layout
failure and falls back to /ws/runtime. Isolated boundary capture proves these
effects without creating /ws. The tests have no stale mocks.

```text
query -> existing read-only roots -> existing database -> mode=ro connection
      missing namespace/database -> unavailable/None, no initialization
command -> existing managed layout -> schema/write queue -> committed evidence
managed path resolution failure -> explicit DB error, never raw-path fallback
```

Reuse existing resolve_existing_storage_roots_read_only; do not probe writes or
refresh layout caches in a read. Preserve injected-store workspace fence. Existing
WAL committed data must stay visible; do not use immutable=1 to ignore a live WAL.
Read-only SQL must not run schema DDL, WAL-setting pragmas or start a writer.
SQLite-native locking/shared-memory bookkeeping is distinct from application-owned
schema/namespace writes and needs explicit tests/boundary reporting.

The ordinary read_only SQLite URI is documented at https://www.sqlite.org/uri.html;
WAL constraints: https://www.sqlite.org/wal.html#read_only_databases.

Keep explicit operator-selected absolute DB paths and ordinary relative DB
contracts; only invalid/traversing or rejected managed logical paths lose unsafe
fallback. No new Cell or storage owner; no target code changes, grant/QA relaxation
or alternate realtime path.

Verification: missing read zero mkdir/schema/writer; existing handoff/receipt and
fresh WAL visibility; readonly write refusal; workspace isolation; SQLite and LanceDB
managed rejection no bare fallback. Then owner/public ContextOS adapter suites and
existing DB/store regressions. Full provider/project closure remains separate.
