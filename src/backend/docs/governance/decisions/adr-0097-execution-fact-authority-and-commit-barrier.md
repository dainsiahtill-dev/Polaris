---
status: accepted
date: 2026-07-10
---

# ADR-0097: Execution Fact Authority and Commit Barrier

## Context

Polaris already has most of the right typed endpoints, but they are not the
authority used by the running system. `TurnOutcome`, `CommitReceipt`, and
`SealedTurn` exist in `roles.kernel`, while production execution still derives
completion independently in non-stream completion, stream projection,
TaskRuntime rows/sessions, Control Plane Run Ledger events, Factory status, and
QA verdicts. The current `_commit_turn_to_snapshot()` mutates an in-memory
ContextOS mapping and can return `None`; it is not a durable execution commit.

This split explains the recurring Factory failures: a provider decision may be
valid, tools may write files, and a later cancellation or stale projection may
still classify the task as `session_not_active`, `incomplete_materialization`,
or an implementation defect. Each repair fixes one projection without reducing
the number of authorities.

## Decision

### 1. One durable execution fact

The canonical execution write is an immutable `TurnOutcome` fact appended to
`events.fact_stream`, which is backed by KernelOne `JsonlEventStore`.

`roles.kernel` owns creation of the outcome. `events.fact_stream` owns durable,
ordered, idempotent append. No other Cell may create a competing turn outcome.

### 2. One commit barrier

The authoritative turn sequence is:

```text
provider decision
  -> normalized ToolBatch
  -> authorized dispatch
  -> ToolExecutionResult / effect receipt
  -> canonical TurnOutcome
  -> FactStream append (durable commit)
  -> TaskRuntime / Run Ledger / QA / UI projections
```

A turn is not successful until the fact append returns an event id and sequence.
Completion events and status responses are projections of that receipt.

### 3. Cancellation is a request, not an immediate state rewrite

Factory deadline or user cancellation first records `cancel_requested`. If a
turn is between decision receipt and durable commit, cancellation is deferred
until the tool batch settles and the turn outcome commits. The settlement may
be success, failure, or cancelled, but it must always have one durable outcome.

No orchestration layer may invalidate a TaskRuntime session while the commit
barrier is active.

### 4. State and evidence ownership

- `events.fact_stream`: ordered immutable facts and sequence CAS.
- `roles.kernel`: turn transaction semantics and `TurnOutcome` construction.
- `runtime.task_runtime`: task commands and rebuildable task/session projections.
- `control_plane.run_ledger`: evidence/read projection; it is not an independent
  event store after cutover.
- `qa.audit_verdict`: classification over barrier-satisfied evidence only.
- `factory.pipeline`: orchestration and deadlines; it cannot author execution
  success/failure facts.
- `runtime.projection`: read-only transport projection.

### 5. No permanent dual write

Migration may emit compatibility projections only from the canonical fact.
Compatibility writes must have a removal condition, an architecture fence, and
a test proving they cannot influence authority. New dual-write paths are
forbidden.

## Consequences

### Positive

- Stream and non-stream execution share the same success definition.
- A successful write without an effect receipt or durable turn fact cannot be
  projected as success.
- Factory cancellation cannot erase an in-flight tool transaction.
- QA and Bench can attribute platform, orchestration, contract, and artifact
  failures from one evidence chain.
- Task rows and sessions become rebuildable caches instead of competing truth.

### Cost

- Existing ContextOS snapshot commit becomes a projection and must no longer be
  described as the durable execution commit.
- Run Ledger writers must migrate to FactStream-backed projections.
- TaskRuntime file fallbacks must be removed after fact coverage and projection
  parity are proven.

## Invariants

1. Exactly one terminal `TurnOutcome` exists per `(run_id, task_id, turn_id)`.
2. Successful write tools have effect receipts before the turn outcome commits.
3. A completion projection always carries the canonical commit event id/seq.
4. `cancel_requested` cannot invalidate an active commit barrier.
5. QA reads at or beyond the referenced commit sequence.
6. Run Ledger, Factory, Bench, ContextOS, and UI never author terminal execution
   state.

## Supersedes and amends

This ADR preserves ADR-0071's single-turn/single-batch decision and amends its
commit definition: mutating a ContextOS snapshot is a projection, not a durable
execution commit. It also operationalizes ADR-0094's reliable-fact requirement.

