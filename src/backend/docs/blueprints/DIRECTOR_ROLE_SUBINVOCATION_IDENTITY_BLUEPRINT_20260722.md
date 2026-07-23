# Director Role Sub-Invocation Identity Blueprint

Status: Active  
Date: 2026-07-22  
Scope: `roles.adapters` orchestration over the existing `roles.runtime` and
`roles.kernel` public execution path.

## 1. Problem

One claimed TaskRuntime execution attempt can legitimately contain several
semantically independent Director RoleRuntime calls:

```text
TaskRuntime execution attempt (parent authority)
  +-- first_call
  +-- no_write_materialization_retry
  +-- empty_write_content_retry
  +-- quality_repair[_N]
```

The parent attempt is an authorization and settlement boundary, not a unique
turn-request identity. Today every fresh RoleRuntime service starts its local
TransactionKernel attempt counter at zero. If all child calls expose only the
same `task_runtime_session_id`, they derive the same transaction invocation and
the same attempt-0 outcome key. The first child commits successfully; a later
child has a different payload under the same key and correctly fails with an
idempotency conflict.

R31 proved this in a fresh isolated L1-04 run. The main Director call wrote five
files and compiled four Go sources. The subsequent semantic quality repair used
the same RoleRuntime session and TaskRuntime execution scope, then failed while
committing its distinct terminal payload under the main call's attempt-0 key.

## 2. Ownership and Invariants

- `runtime.task_runtime` remains the sole owner of execution-attempt authority.
- `roles.kernel` remains the sole owner of transaction invocation/attempt
  derivation and terminal outcome idempotency.
- `roles.adapters` owns the Director orchestration decision that one parent
  execution attempt contains multiple logical role calls.
- No idempotency conflict check is weakened or bypassed.
- No random identity is allowed.
- A replay of the same parent scope and semantic stage derives the same child
  `turn_request_id`.
- Different semantic stages under the same parent scope derive different child
  `turn_request_id` values.
- Conflicting parent execution-scope fields remain fail-closed.
- The process-local TaskRuntime authority object and its command/session
  alignment remain unchanged; child identity changes correlation only, never
  authorization.

## 3. Design

At the existing Director role-dialogue boundary, before constructing the
RoleRuntime command:

1. Read the same first-class execution-scope fields accepted by
   TransactionKernel from the outgoing metadata.
2. Canonicalize `runtime_execution.session_id` as
   `task_runtime_session_id`.
3. Reject disagreement between multiple populated parent identity fields.
4. Derive a deterministic child request identity from:

```text
schema = director.role_subinvocation.v1
parent execution-scope kind
parent execution-scope id
stage_label
```

5. Preserve the parent scope as structured provenance.
6. Remove parent execution-scope keys only from the TransactionKernel metadata
   identity surface and set the derived `turn_request_id` there.
7. Keep TaskRuntime authority, task/run identity, RoleRuntime session identity,
   context evidence, and directed-effect wiring unchanged.

This makes TransactionKernel derive one stable invocation per logical child
call while its existing attempt counter still owns retries inside that child.

## 4. Rejected Alternatives

- Allow same idempotency key with different payload: rejected; destroys replay
  safety.
- Random UUID per repair: rejected; crash replay would duplicate effects.
- Reuse transaction attempt 1 across a new RoleRuntime service: rejected; the
  new service has no authoritative persisted outer retry counter and would
  manufacture attempt state.
- Create a new TaskRuntime execution attempt for every repair: rejected; a
  repair is a child invocation inside the already claimed task attempt, not a
  second task authorization.
- Change `role_runtime_session_id`: rejected; session continuity and invocation
  identity are different concerns.

## 5. Verification

1. Red regression: same parent scope produces identical child ID for replay,
   but `first_call` and `quality_repair` produce different IDs.
2. Parent scope is preserved as structured evidence and removed from the
   outgoing TransactionKernel identity candidates.
3. Conflicting parent identity fields fail closed.
4. Existing timeout/write-boundary tests stay green.
5. Full `roles.adapters`, relevant `roles.kernel`, Factory Pipeline, Ruff,
   format, mypy, compileall, and diff checks pass.
6. Only after a stable source snapshot may one new isolated L1-04 acceptance
   run be authorized. Its PM, CE, Director, repair, QA, and final Provider
   requests must each be audited through readable `context_snapshot_ref` data.

