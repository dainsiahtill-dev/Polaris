# Execution Fact Authority Blueprint (2026-07-10)

## 1. Problem boundary

The defect is not a missing language repair rule. The running system has typed
contracts at its endpoints but multiple authorities in the middle. This creates
combinatorial drift and prevents Factory from reliably finishing a fresh
project even when the model produces valid code.

Confirmed current facts:

- `TurnOutcome`, `CommitReceipt`, and `SealedTurn` have no production consumers.
- `_commit_turn_to_snapshot()` mutates only an in-memory mapping, may silently
  skip, and its receipt is ignored.
- stream completion synthesizes lifecycle and TaskBoundary evidence separately
  from non-stream completion.
- TaskRuntime already appends CAS-ordered facts, but file rows and sessions
  still participate in read authority.
- Control Plane Run Ledger uses a second append-only file without sequence CAS.

## 2. Target topology

```text
                         KernelOne
              JsonlEventStore + effect receipts
                              |
                              v
Provider -> roles.kernel TransactionKernel -> TurnOutcome
                              |
                              v
                    events.fact_stream (SSoT)
                              |
              +---------------+---------------+
              |               |               |
              v               v               v
        TaskRuntime      Run Ledger/QA    Runtime Projection
        projections      projections       -> WebSocket/UI
              |
              v
        TaskMarket dependency unlock
```

Factory owns scheduling and deadline policy only. It submits cancellation
requests to the execution coordinator; it does not directly terminalize an
active Director session inside the commit barrier.

## 3. Module responsibilities

### KernelOne

- `kernelone.events.sourcing`: append ordering, file lock, CAS, UTF-8 durability.
- `kernelone.effect`: effect declarations and immutable receipts.
- No Polaris task/QA/Factory semantics.

### events.fact_stream

- Public idempotent CAS append API.
- One event id and monotonic sequence per durable fact.
- No business classification or UI projection.

### roles.kernel

- Maintains one typed decision and one typed batch receipt per turn.
- Builds one `TurnOutcome` from typed execution facts.
- Fails closed when durable commit cannot be proven.
- Stream and non-stream call the same committer.

### runtime.task_runtime

- Owns commands and validates state transitions.
- Task row/session files are materialized projections.
- Uses canonical FactStream CAS API; no private CAS loops.

### control_plane.run_ledger and QA

- Read and classify facts after a sequence barrier.
- Preserve `missing evidence` versus `failed evidence`.
- Never repair or infer a missing execution commit from prose.

## 4. State machine

```text
TURN_OPEN
  -> DECISION_RECEIVED
  -> DISPATCHING
  -> EFFECTS_SETTLED
  -> COMMITTING
  -> COMMITTED

Any pre-commit failure -> COMMITTING(failed outcome) -> COMMITTED
cancel request         -> CANCEL_REQUESTED
CANCEL_REQUESTED during DISPATCHING/EFFECTS_SETTLED/COMMITTING is deferred
CANCEL_REQUESTED outside the barrier -> COMMITTING(cancelled outcome)
```

There is no terminal state without a commit receipt. `session_not_active`
before commit is a platform invariant violation, not an implementation defect.

## 5. Implementation waves

### Wave A: establish the authoritative commit

1. Add one idempotent CAS helper to `events.fact_stream`.
2. Activate the existing `TurnOutcome`/`CommitReceipt`/`SealedTurn` contracts.
3. Persist one turn outcome from the shared TransactionKernel controller.
4. Attach the fact event id/seq to stream and non-stream completion projections.
5. Fail closed on commit failure.

Exit condition: parity tests prove both transports produce the same outcome
schema and one fact event.

### Wave B: remove duplicate state authority

1. Replace TaskRuntime private CAS loops with the FactStream API.
2. Make observable task/session reads fact-only.
3. Keep row/session files only as rebuildable command projections.
4. Delete fallback coverage/readiness helpers after cutover.

Exit condition: a workspace can rebuild task status from facts after deleting
projection files.

### Wave C: projection-only Run Ledger and QA

1. Move Control Plane Run Ledger append callers to canonical fact streams.
2. Make barrier reads sequence-based.
3. Route QA classification from typed failure evidence.
4. Delete the second Run Ledger writer.

Exit condition: Run Ledger files are generated views or removed; no product
writer targets `runtime/control_plane/ledger/*` directly.

### Wave D: barrier-aware orchestration

1. Add an execution barrier query to Factory cancellation/deadline handling.
2. Defer suspension while a turn is settling.
3. Refuse new LLM turns when deadline headroom is insufficient.
4. Project platform failures without relabeling downstream symptoms.

Exit condition: cancellation tests prove a received tool batch always settles
to one committed outcome before task suspension.

## 6. Verification strategy

- Unit: FactStream idempotent CAS under concurrent identical and distinct facts.
- Contract: `TurnOutcome` success, failure, handoff, and cancellation shapes.
- Parity: stream and non-stream produce the same commit identity semantics.
- Integration: write effect -> effect receipt -> outcome -> TaskBoundary -> QA.
- Recovery: rebuild task projections from facts only.
- Concurrency: cancellation during dispatch cannot produce `session_not_active`.
- Governance: graph ownership, no direct Run Ledger writers, no second committer.

No Factory Bench run is required to prove an individual implementation wave;
Bench is the later system oracle after component and integration gates are green.

## 7. Complexity and performance

Append is O(1) amortized plus O(n) only for current idempotency lookup. The
first implementation preserves bounded scans; the production target adds an
idempotency index or SQLite/WAL store when run volume makes O(n) material.
Projection rebuild is O(e), where `e` is the number of facts in a run. Snapshots
may reduce replay cost but never become authority.

