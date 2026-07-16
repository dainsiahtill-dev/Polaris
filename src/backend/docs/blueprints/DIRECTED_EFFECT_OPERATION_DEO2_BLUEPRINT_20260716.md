# Directed Effect Operation DEO-2 Blueprint

**Task:** `DEO-2-KERNEL-BATCH-ADMISSION`  
**Status:** design locked; implementation pending  
**Date:** 2026-07-16  
**Scheduling:** DEO-2, DEO-3, DEO-4, pre-bench, and Bench remain
`not_schedulable`. This blueprint authorizes no Bench run and no target-project
change.

## 1. Decision

DEO-2 establishes one pre-effect authority path for every mutation-capable tool
invocation:

```text
canonical ToolBatch
  -> kernel mutation inventory
  -> TaskRuntime parent admission
  -> durable inventory seal
  -> all INTENT_COMMITTED admissions
  -> durable inventory-ready proof
  -> per-call EFFECT_STARTED claim
  -> claim-bound typed grant
  -> director.runtime policy validation
  -> DirectorToolExecutor effect
  -> KernelOne effect receipt with non-authoritative DEO linkage
  -> BatchReceipt
```

The unit of authority is one actual mutation invocation, never an executor
instance, repair plan, source tool, callback, file, or whole batch. Every
`ToolEffectType.WRITE` and `ToolEffectType.ASYNC` invocation is an inventory
member. Unknown tools that default to write remain mutation-capable. Read-only
invocations do not create DEOs.

`write_file`, `edit_file`, `delete_file`, and `execute_command`/`run_command`
each require their own operation. A deterministic repair operation becomes one
synthetic `ToolInvocation` per actual effect call. Model arguments, ordinary
metadata, Job Tokens, and Director policy verdicts cannot mint or widen a DEO.

## 2. Rejected Alternatives

### 2.1 Adapter just-in-time admission

Rejected. Giving `roles.adapters` an admission callback would let the adapter
choose the inventory after planning and would make adapter behavior part of the
authority decision. A callback can request kernel execution of an already
frozen plan, but it cannot call TaskRuntime admission or create a grant.

### 2.2 Constructor-scoped or repair-plan-scoped token

Rejected. An executor is reusable and a repair plan may emit several effects.
One shared token would allow cross-call reuse, collapse idempotency, and violate
one-effect/one-operation evidence.

### 2.3 Move `DirectorToolExecutor` into `roles.kernel`

Rejected. This would mix role-turn ownership with Director tool semantics and
would create pressure for `roles.kernel -> director.runtime -> roles.adapters`
imports. Physical execution remains a composition-layer implementation detail;
authority stays in TaskRuntime and orchestration stays in `roles.kernel`.

## 3. Cell Ownership and Dependency Direction

| Concern | Owner | Required dependency direction |
| --- | --- | --- |
| Parent, sealed inventory, operation facts, CAS, claim grant source | `runtime.task_runtime` | no dependency on roles or Director cells |
| Canonical batch inventory, admission orchestration, claim timing | `roles.kernel` | depends on `runtime.task_runtime` public contracts/services |
| Pure Director effect-policy validation and normalized effect descriptor | `director.runtime` | may depend on TaskRuntime grant contracts; must not import roles/adapters |
| Composition and physical `DirectorToolExecutor` implementation | `roles.adapters` | consumes roles.kernel port, director.runtime public policy, and TaskRuntime grant type |
| Effect receipt primitive and receipt hash/link fields | KernelOne | must not import TaskRuntime or roles adapters |
| Projection only | Run Ledger | never grants or mutates authority |

No new cycle is permitted. In particular:

- `director.runtime` must not import `roles.adapters` or `roles.kernel`.
- `roles.kernel` must not import `roles.adapters` internals.
- `roles.adapters` may depend on both public boundaries because it is the
  composition layer, but it cannot import TaskRuntime admission commands.
- TaskRuntime remains the only durable DEO writer.

## 4. Durable Inventory Contract

Parent admission alone is insufficient. A crash after admitting only part of a
batch would otherwise make “not planned” indistinguishable from “planned but
not admitted.” DEO-2 therefore adds a durable, immutable inventory seal before
any effect claim.

### 4.1 Inventory member

`DirectedEffectInventoryMemberV1` is frozen and canonical. It contains:

- ordinal and canonical `tool_call_id`;
- server-derived `effect_id`;
- normalized tool name and `ToolEffectType`;
- redacted intended-effect fingerprint;
- Director policy verdict hash;
- expected KernelOne receipt-binding hash;
- execution mode;
- optional deterministic contingency kind (`forward` or `rollback`);
- no raw prompt, secret, arbitrary tool argument, or writable path authority.

`effect_id` is server-derived from the parent binding, call id, normalized
effect fingerprint, and schema version. Exact retry inputs reproduce it. A new
logical call produces a new id. Model output cannot provide it.

### 4.2 Seal and ready proof

`SealDirectedEffectInventoryCommandV1` appends one guarded
`parent_inventory_sealed` registry fact containing the ordered canonical member
descriptors, member count, and inventory hash. Limits remain bounded by the v1
parent target: at most 64 operations per parent.

Seal uses the parent registry as guarded-append target and the enrolled
operation stream as guard. The operation head must be exactly zero. A non-empty
operation stream before seal is a typed conflict, never a baseline to absorb,
so no legacy or concurrently injected child fact can become an inventory member
after the fact.

After the seal, `roles.kernel` idempotently admits every member through the
existing `AdmitDirectedEffectOperationCommandV1`. No member may be added,
removed, reordered, or semantically changed after sealing.

`FinalizeDirectedEffectInventoryAdmissionCommandV1` targets the parent
registry while guarding the operation-stream head. TaskRuntime strictly proves
that the admitted `INTENT_COMMITTED` set exactly equals the seal, then appends
`parent_inventory_ready`. Claims require this ready fact. Partial admission can
be safely retried, but can never dispatch an effect.

The public read model reports sealed count, admitted count, missing member ids,
unexpected member ids, inventory hash, and readiness. It is diagnostic; only
the guarded `parent_inventory_ready` fact permits claim processing.

## 5. Claim-Bound Grant

`INTENT_COMMITTED` is not execution permission. Immediately before each effect,
`roles.kernel` calls the existing `claim_directed_effect`. TaskRuntime returns a
frozen `DirectedEffectClaimGrantV1` only when the guarded transition durably
reaches `EFFECT_STARTED` and the operation still matches the ready inventory.
Only the original in-flight claim call may receive the grant after its guarded
append is confirmed, including same-call append reconciliation. An exact claim
replay returns idempotent evidence but never reissues an executable grant. If a
process loses the original grant after durability, the operation remains
`EFFECT_STARTED` for DEO-3 recovery; callers must not redispatch it.

The grant contains the complete attempt, parent binding, operation identity,
inventory hash, intended-effect fingerprint, policy verdict hash, expected
receipt-binding hash, operation version, claim event/head evidence, and a
canonical grant hash. It contains no mutation method and is not a second SSoT.

The grant travels only through typed, out-of-band execution context:

- it is not added to `ToolInvocation.arguments`;
- it is not supplied by the model;
- it is not a constructor-wide token;
- it is frozen and deeply detached;
- `ToolBatchRuntime` selects it by exact `call_id`;
- a grant cannot be reused for another call, tool, workspace, fingerprint, or
  effect id.

`ToolExecutionContext` remains batch context. A separate frozen per-call
`DirectedEffectExecutionContextV1` carries the grant into the executor port.
Mutation execution through the legacy two-argument callable is removed or
fails closed; read-only compatibility cannot become a mutation fallback.

## 6. Director Policy and Physical Execution

`director.runtime.public` adds one pure policy boundary with two explicit
phases. Preflight accepts the normalized effect request without a grant and
returns the canonical policy descriptor/hash used by the inventory seal.
Execution validation accepts that same normalized request plus
`DirectedEffectClaimGrantV1`; it must reproduce the preflight descriptor/hash
and return a typed execution verdict. This avoids making a claim grant a
precondition for the policy hash that the grant itself binds. Execution
validation checks:

- exact workspace, task, attempt, parent, call, effect, and operation identity;
- exact normalized tool and effect fingerprint;
- exact policy verdict and expected receipt-binding hashes;
- path/command scope derived from the existing Job Token and Director policy;
- claim state/version/head evidence and grant hash shape.

It does not admit, claim, query a writable authority source, write, execute
commands, import adapters, or issue a success receipt. TaskRuntime's guarded
claim is the durable authorization event; the policy adapter only validates the
complete detached evidence and exact request binding. `roles.kernel` additionally
maintains a process-local single-dispatch fence for each returned grant. That
fence is defense in depth only: it is never persisted, never used for recovery,
and never permits redispatch after restart. An already-started operation is
always handed to DEO-3 reconciliation instead.

`DirectorToolExecutor` may be constructed once at the composition root and
reused. Every mutation method requires a keyword-only per-call effect context
with no default. Missing or mismatched context returns a typed blocked result
before filesystem or process services are called. Read-only methods do not
accept or manufacture a mutation grant.

The physical result keeps existing Director policy evidence and KernelOne
effect receipt. DEO-2 adds only linkage fields:

- `operation_id`;
- `effect_id` and `tool_call_id`;
- parent binding id and inventory hash;
- claim grant hash/ref;
- expected receipt-binding hash.

These fields prove correlation, not receipt durability or DEO closure.

## 7. Canonical Batch Data Flow

1. `roles.runtime` validates the typed TaskRuntime execution attempt before it
   builds `RoleTurnRequest`. `roles.kernel` reconstructs only that validated
   canonical record; arbitrary metadata cannot replace it.
2. Tool alias/argument normalization, allow-list, path guard, mutation guard,
   and Director effect-policy preflight run before inventory sealing.
3. Blocked mutation calls produce typed lifecycle failures and are excluded
   from the executable inventory. If normalization or policy is ambiguous, the
   mutation portion of the batch is rejected with zero effects.
4. `roles.kernel` derives the surviving mutation inventory from the canonical
   `ToolBatch`. Read-only calls retain existing scheduling.
5. If the inventory is empty, no DEO parent or operation is created.
6. Otherwise kernel admits the parent for the canonical turn/batch, explicitly
   enrolls its streams, seals the complete inventory, admits every intent, and
   finalizes inventory readiness.
7. `ToolBatchRuntime` processes mutation members serially. Before each dispatch,
   kernel claims exactly that member and injects its claim grant by call id.
8. The injected `CellToolExecutorPort` implementation validates the typed
   context through `director.runtime.public`, then invokes
   `DirectorToolExecutor`.
9. The KernelOne result produces an effect receipt. `BatchReceipt` includes the
   effect receipt plus non-authoritative DEO linkage.
10. Any receipt persistence, `RECEIPT_COMMITTED`, recovery, parent close, or
    terminal admission remains blocked until DEO-3.

## 8. Deterministic Repair Boundary

Repair planning remains authoritative in `director.runtime`; repair execution
authority does not.

The public planning result must expose an execution-grade, immutable effect
projection without leaking private `RepairOperation` classes. The projection
lists the exact `write_file`, `edit_file`, or `delete_file` calls that a single
repair round may perform, plus deterministic rollback contingencies known
before execution.

`roles.adapters` may return a typed deferred execution request for that frozen
plan through a kernel-owned port. The port must not execute recursively from
inside an adapter, tool executor, or active mutation dispatch. At the next
canonical kernel scheduling boundary, `roles.kernel` verifies the plan id/hash
against `director.runtime`, converts each actual effect and each known rollback
contingency into a synthetic `ToolInvocation`, seals/admit/readies the complete
inventory, and dispatches only through `ToolBatchRuntime`. No adapter callback
may synchronously open a nested batch.

Unused rollback contingencies are durably aborted from `INTENT_COMMITTED` with
`contingency_not_activated`. A rollback that is activated must be claimed and
executed as its own DEO. A failure after a forward claim that leaves effect
outcome ambiguous is not blindly rolled back; it remains `EFFECT_STARTED` for
DEO-3 reconciliation.

Claim and abort transitions both require the durable inventory-ready proof.
Cancellation during partial admission therefore never aborts a subset and
creates an impossible ready set: kernel first idempotently completes every
sealed admission and the ready fact, then aborts each unclaimed member with a
typed cancellation reason.

Current repair convergence can discover new operations in later verifier
rounds. Those operations cannot be added to an immutable sealed inventory.
Therefore DEO-2 permits one planned repair round only. Requesting a second
commit round fails closed with `deo_multi_round_repair_requires_receipt_close`
before any second-round effect. DEO-3 may enable a new canonical repair
turn/batch only after the previous batch receipt is durably committed and its
parent is eligible to close. Hidden batches, `count_towards_batch_limit=False`,
and callback writes are forbidden.

## 9. Crash, Cancellation, and Error Semantics

| Failure point | DEO-2 outcome |
| --- | --- |
| Before inventory seal | no operation authority and no effect |
| After seal, before all intents | retry missing admissions; claim forbidden |
| After all intents, before ready fact | retry guarded readiness finalize; claim forbidden |
| Before per-call claim, after ready | intent may abort; no effect |
| After claim, before executor | remains `EFFECT_STARTED`; no blind abort or retry |
| After effect, before/without usable receipt | remains `EFFECT_STARTED`; DEO-3 recovery owns outcome |
| Cancellation before claim | abort only the unclaimed intent |
| Cancellation after claim | record cancellation evidence; do not infer no effect |
| Grant mismatch or missing grant | typed blocked result; executor spy must prove zero effect |
| Policy drift after claim | typed blocked result; started operation remains for recovery |
| Async submit | claim before submit; pending receipt links to DEO; closure deferred |

No timer polling, in-memory authority registry, long-held command lock, target
project mutation, deterministic legacy fallback, or second receipt journal is
allowed.

## 10. Thirty-Eight-Surface Migration

Current production inventory is exact:

- 24 in `post_execution_repair_bridge.py`;
- 8 in `materialization_quality_callback_ports.py`;
- 3 in `quality_gate.py`;
- 2 in `execute_method_repair_bridge.py`;
- 1 in `execution.py`.

It comprises 31 `executor_factory=DirectorToolExecutor` injections and 7 direct
constructions. DEO-2 removes the repair executor-factory seam, replaces direct
callback writes with the kernel-owned planned-effect port, and keeps at most one
composition-root physical executor. That executor is not an authority object;
every mutation call still requires a per-call claim grant.

The exit metric is zero **unbound mutation surfaces**, not merely zero textual
constructors. Architecture tests inspect constructors, factories, mutation
method calls, callbacks, private patch helpers, injected ports, and production
bootstrap wiring. Test-only doubles must implement the same fail-closed grant
contract.

## 11. Verification and Fences

Required proof:

1. Exact mutation inventory equals the sealed TaskRuntime inventory; read-only
   calls never appear in it.
2. No claim succeeds before the ready fact; partial admission is replay-safe.
3. Every dispatched mutation has exactly one `EFFECT_STARTED` grant with exact
   call/effect/fingerprint identity.
4. Missing, forged, stale, cross-workspace, cross-tool, cross-call, or
   cross-fingerprint grants produce zero filesystem/process effects.
5. Unknown/default-write and async tools enter the inventory.
6. One repair effect equals one synthetic invocation and one DEO; rollback is
   separately admitted; second-round commit is blocked.
7. All batch receipts link operation, claim grant, and KernelOne effect receipt
   identities without claiming durable closure.
8. The 38-surface AST/call-shape inventory reports zero unbound mutations.
9. Import graph has no new cycle and `director.runtime` never imports roles
   adapters/kernel internals.
10. TaskRuntime DEO-1A/B/C durability, readiness, settlement pre-barrier, and
    strict-stream tests remain green.

Focused tests must cover TaskRuntime contracts/repository, kernel inventory and
ToolBatchRuntime, director.runtime policy, adapter executor denial, repair
single-round behavior, KernelOne linkage, architecture fences, and concurrency.
Then run Ruff, format check, mypy on production surfaces, compileall, diff check,
the full TaskRuntime suite, roles.kernel targeted suite, Director adapter/runtime
targeted suites, KernelOne tool execution suite, and catalog hard-fail gate.

## 12. Multi-Dimensional Audit Matrix

| Dimension | DEO-2 requirement |
| --- | --- |
| Architecture | one authority route; zero adapter admission; zero dependency cycles |
| Role identity | typed TaskRuntime attempt must match Director role/run/task/workspace |
| Final provider request | no DEO/grant field may originate in provider messages/tools/arguments |
| Tool chain | alias and args normalize before seal; claim grant injected out of band |
| Context hygiene | no prompt/history retry can mint or replay a new effect id |
| Runtime events | tool lifecycle events carry DEO linkage but never become authority |
| UI projection | no new UI authority or Bench state; linkage is backend evidence only |
| Artifact gates | real zero-effect denial tests plus lint/type/architecture gates |
| Model health | model choice cannot weaken inventory, policy, or claim requirements |
| Convergence | second repair round blocked until DEO-3 closes prior batch safely |

## 13. DEO-2 / DEO-3 Boundary

DEO-2 owns inventory seal, intent admission, admission-ready proof, effect claim,
typed grant, Director policy validation, missing-grant denial, repair single-round
normalization, 38-surface closure, and non-authoritative receipt linkage.

DEO-3 alone owns KernelOne durable effect-receipt commit, receipt hash CAS into
`RECEIPT_COMMITTED`, crash reconciliation, `RECOVERY_PENDING`, dead letter,
ambiguous-effect handling, later repair batch enablement, parent close,
heartbeat/reclaim fencing, and terminal TaskRuntime admission.

The existence of a `BatchReceipt`, effect-receipt dictionary, grant, policy
verdict, runtime event, Run Ledger row, or passing unit test cannot be
interpreted as receipt closure or settlement.

## 14. Stop Conditions

Stop implementation and keep DEO-2 open if any of these occur:

- the inventory cannot be durably sealed before claim;
- a mutation can reach a two-argument/raw executor path;
- a repair callback can write without a synthetic invocation;
- a grant must be placed in model arguments or caller-controlled metadata;
- `director.runtime` must import `roles.adapters` or `roles.kernel` internals;
- a second repair round executes before prior receipt/parent closure;
- any DEO-3 state transition is required to claim DEO-2 complete;
- any target-project code change or Bench run appears necessary.

Until every exit proof is current and independently reviewed, DEO-2 remains
`p0_open`, downstream buckets remain `not_schedulable`, and Bench remains
`not_schedulable`.
