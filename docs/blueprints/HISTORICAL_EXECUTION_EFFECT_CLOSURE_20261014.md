# Historical execution effect closure

TaskRuntime stores one current session file per task. A later Director attempt
can replace that file after the prior attempt already settled its owned effects.
Run Ledger previously observed only the newest task fact, so a no-effect
preflight hid an older write-bearing attempt and historical queries returned
`session_identity_mismatch`.

`QueryExecutionEffectClosureV1` now authenticates each queried attempt from the
TaskRuntime-owned `task_runtime.execution` FactStream, using its public strict
integrity reader. The query accepts only owner terminal transitions whose
workspace, task, run, session, transition, source and idempotency bindings match.
The persisted terminal intent must match the owner outcome, identity hash and
settlement proof. Owner settlement and observation share the same pure intent
validator; the owner's original DEO preflight remains in place.

For a current attempt, the session file must still be terminal and agree with
the immutable owner proof. For a historical attempt, the current file must name
a successor known to owner claim/terminal facts. Missing files, foreign identity,
active current attempts, malformed intent and unknown owner facts fail closed.
Observation does not recreate sessions, enroll streams, renew leases, move
runtime roots, or write facts.

Write-bearing attempts continue through the existing strict parent registry and
operation reducers. Every committed receipt must remain owner-bound through the
sealed inventory, claim, receipt and terminal parent close. A no-effect attempt
requires an authenticated owner terminal proof and either a strict empty
registry or FactStream-owned physical absence evidence. `stream_lock_missing`
alone is insufficient: a physically present unenrolled registry remains unknown.
Double reads reject changing owner history, session, parent or operation data.

Run Ledger groups canonical task facts by stable execution identity, keeping
every attempt instead of every task. Query identities are discovery keys; the
observer re-reads owner facts and does not accept caller facts as proof.

Operational closure allows a failed run to release resources; it never changes
its missing/failed evidence, QA verdict, failed delivery status or pass result.
The native18 historical query is external audit evidence only. The original
Director has eight persisted commits; two successor preflights have none.
Fresh product delivery remains the coordinator's separate acceptance boundary.

Verification covers two real mutation attempts followed by a no-effect failed
preflight, exact persisted receipt and commit identities, read immutability,
per-attempt discovery, forged identities and lease, semantic source/intent/
transition/proof tampering with valid byte digests, corrupted byte digests,
missing parent close, altered claim binding, physical unenrolled registry files,
and an active successor. No new state owner or execution authority is introduced.
