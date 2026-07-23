# DEO-3 Durable Receipt Settlement Design

## Status and authority

This design implements the already locked DEO-3 section of
`DIRECTED_EFFECT_OPERATION_V1_BLUEPRINT_20260714.md`. The user authorized
autonomous execution without another review pause. DEO-3 is the only active
bucket. DEO-4, pre-bench, Provider calls, and Bench remain `not_schedulable`.

## Problem

DEO-2 closes every unbound mutation surface, but the canonical physical port
still returns a non-durable acknowledgement. A successful external effect can
therefore leave its operation at `EFFECT_STARTED`; TaskRuntime correctly blocks
terminal settlement, yet no durable receipt or finite recovery path exists.
The existing bare `parent_closed` schema also does not bind a close to the
requested terminal outcome.

DEO-3 must establish this single fact chain:

```text
physical effect
  -> hash-bound physical receipt
  -> TaskRuntime RECEIPT_COMMITTED CAS
  -> pending_terminal_intent
  -> child CLOSED_BY_PARENT CAS
  -> outcome-bound parent close CAS
  -> TaskExecutionSession terminal write
  -> Run Ledger read-only projection
```

## Considered approaches

### A. Add a separate receipt database

Rejected. It creates a second durable authority and cannot atomically fence the
existing operation stream without rebuilding the same guarded-CAS protocol.

### B. Use a cross-stream transaction

Rejected. The locked blueprint requires one target append guarded by the other
stream head. FactStream deliberately has no caller-owned multi-stream
transaction.

### C. Reuse guarded single-target CAS with an outcome-bound terminal intent

Chosen. Receipt and child transitions target the operation stream and guard the
OPEN parent head. Parent close targets the registry stream and guards the exact
operation head. Session locks serialize heartbeat, stale reclaim, pending intent,
parent close, and terminal session persistence without becoming FactStream
locks.

## Public contracts

TaskRuntime adds three non-terminal operation commands:

- `CommitDirectedEffectReceiptCommandV1`: exact attempt, parent binding,
  operation identity fields, expected version/sequence, receipt ref/hash,
  expected receipt-binding hash, and `succeeded|failed` outcome.
- `MarkDirectedEffectRecoveryPendingCommandV1`: exact operation identity,
  expected version/sequence, reason, and hash-bound recovery evidence.
- `DeadLetterDirectedEffectOperationCommandV1`: exact recovery-pending
  operation, expected version/sequence, reason, and hash-bound resolution
  evidence.

Receipt refs and evidence refs are identifiers, not filesystem authority.
Hashes are canonical lowercase SHA-256. The physical receipt remains
non-authoritative; the TaskRuntime transition is the durable authority binding.

No public parent-close command is added. `SettleTaskRuntimeExecutionAttemptCommandV1`
remains the only terminal entry.

## Operation protocol

The existing reducer gains strict schema-v3 transition descriptors while
remaining able to read schema-v2 DEO-1/2 events. Legal transitions remain:

```text
EFFECT_STARTED -> RECEIPT_COMMITTED
EFFECT_STARTED -> RECOVERY_PENDING
RECOVERY_PENDING -> RECEIPT_COMMITTED
RECOVERY_PENDING -> DEAD_LETTER
RECEIPT_COMMITTED -> CLOSED_BY_PARENT
```

Each transition keeps the original intended-effect, policy, and expected
receipt-binding hashes. Receipt commit additionally proves:

- command receipt-binding hash equals the durable expected binding;
- receipt hash is canonical and bound to one receipt ref;
- receipt outcome is explicit;
- exact replay returns one idempotent result and never reissues physical work.

`EFFECT_STARTED` is ambiguous after a crash. Recovery may reconcile a known
receipt or move once to `RECOVERY_PENDING`; it may later commit the receipt or
dead-letter with explicit evidence. No path re-executes the physical mutation.

## Terminal settlement

While holding the existing local session lock and cooperative session-file
lock, TaskRuntime:

1. Validates the exact active attempt and lease.
2. Builds and persists `pending_terminal_intent` containing schema, identity
   hash, outcome, summary hash, metadata hash, and canonical intent hash.
3. Strictly reconstructs the sealed/ready inventory and all child operations.
4. Rejects `INTENT_COMMITTED`, `EFFECT_STARTED`, or `RECOVERY_PENDING`.
5. Rejects `completed` when any committed receipt outcome is `failed` or any
   child is `DEAD_LETTER`.
6. Transitions every eligible `RECEIPT_COMMITTED` child to
   `CLOSED_BY_PARENT`, binding the same terminal-intent hash and outcome.
7. Appends one schema-v2 outcome-bound parent-close fact to the registry while
   guarding the exact operation head.
8. Strictly re-reads the close proof, then marks and persists the session
   terminal.

`ABORTED` children need no receipt. `DEAD_LETTER` is terminal evidence of an
unresolved effect and is allowed only for `failed|suspended` settlement. A
historical schema-v1 bare close remains readable but cannot authorize terminal
settlement.

Crash recovery is command replay:

- crash after intent write: replay resumes child closure;
- crash after any child close: replay skips exact closed children;
- crash after parent close: replay verifies the exact outcome-bound close;
- crash after session terminal write: existing terminal replay stays
  idempotent;
- different outcome, summary, metadata, receipt, or evidence is a typed
  semantic conflict.

## Heartbeat and reclaim fence

An active session with `pending_terminal_intent` rejects heartbeat renewal and
stale-session reclaim. Reclaim cannot turn a pending terminal attempt into a
separate suspended outcome. The only legal continuation is exact settlement
replay. A settled session already rejects heartbeat through the existing
inactive-session fence.

## Mutation-port integration

The one canonical Director mutation port commits its physical receipt through
TaskRuntime before returning `executed`. If physical execution or receipt
commit is ambiguous, it records `RECOVERY_PENDING` when possible and returns a
typed non-success result. It never reports a durable receipt unless the strict
TaskRuntime projection proves `RECEIPT_COMMITTED` for the same operation and
receipt hash.

## Run Ledger projection

Run Ledger remains read-only. Tool result projection consumes the TaskRuntime
receipt-commit evidence already embedded by the mutation port:

- no receipt fact: required modality is missing;
- receipt fact with `outcome=failed`: required modality is present but failed;
- successful receipt fact: modality present and successful;
- recovery/dead-letter stays visible and blocks success.

Run Ledger never closes a parent, settles a session, or repairs TaskRuntime.

## Failure taxonomy

- wrong receipt binding/hash/ref: `receipt_binding_conflict` or
  `receipt_evidence_conflict`, zero new fact;
- ambiguous external effect: `recovery_pending`, no blind retry;
- exhausted recovery: `dead_lettered`, explicit evidence required;
- open/unresolved child at settlement: `settlement_directed_effect_unresolved`;
- failed receipt/dead letter with completed outcome:
  `settlement_effect_outcome_conflict`;
- different pending intent: `settlement_terminal_intent_conflict`;
- bare historical close: `settlement_parent_close_proof_required`;
- guarded drift/exhaustion: existing strict CAS taxonomy, zero false success.

## Verification

TDD covers strict contracts, reducer compatibility, receipt commit and exact
replay, failure/recovery/dead-letter, child-versus-parent races, outcome-bound
close, crash seams at each ordering boundary, heartbeat/reclaim races, mutation
port integration, and Run Ledger missing-versus-failed projection. Closure also
requires full TaskRuntime, roles.kernel, roles.adapters, Director Runtime,
Run Ledger, KernelOne guarded FactStream/FS, architecture, Ruff, format, mypy,
compileall, YAML/catalog, and diff gates plus independent specification and
quality/security review. Provider and Bench counts remain zero.

## Self-review

- No cross-stream transaction or second receipt authority exists.
- `settle_execution_attempt` remains the sole terminal entry.
- No replay path repeats a physical side effect.
- Historical events remain readable but cannot authorize new terminal state.
- DEO-4 removal work and Bench scheduling remain outside this bucket.
