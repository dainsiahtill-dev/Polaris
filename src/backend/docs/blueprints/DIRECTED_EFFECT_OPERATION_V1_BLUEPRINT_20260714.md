# Directed Effect Operation v1 Blueprint

**Task:** `DEO-1C-CLOSURE-SYNC`
**Status:** DEO-1A, DEO-1B, and DEO-1C are closed by the 2026-07-15 closure
records in Sections 10.1, 10.2, and 10.4. The DEO-1 durable fact foundation is
therefore closed. Directed Effect Operation v1 remains `p0_open`: DEO-2 design
is locked by
`DIRECTED_EFFECT_OPERATION_DEO2_BLUEPRINT_20260716.md`, implementation is
pending, DEO-2/3/4 remain `not_schedulable`, and DEO-3 remains the highest-risk
P0 child/terminal close, receipt, and recovery path.
Bench remains `not_schedulable`; no Bench was run. DEO-1B made only the limited
settlement semantic change defined below: every active-to-inactive writer
passes a fail-closed parent-registry pre-barrier, but 1B does not write
`parent_closed` or implement DEO-3 receipt eligibility or recovery.
**Scope:** durable mutation authority and effect-receipt closure only. This is
not a child transaction, a new batch model, a new commit point, or a new SSoT.

## 1. Decision and Evidence

The audited inventory found **38 `DirectorToolExecutor` construction or
injection surfaces**. Those surfaces can reach mutation-capable tools outside a
single durable operation/receipt protocol. They are therefore a P0 mutation
authority and receipt-bypass risk, not a cosmetic dependency-injection cleanup.

Codegraph evidence reviewed on 2026-07-14:

- `TaskRuntime` persists terminal execution facts with a stable
  `transition_id` and derived idempotency key; its public attempt settlement is
  the owner of terminal admission.
- `roles.kernel.internal.ToolBatchRuntime` owns one role turn's batch result
  and converts tool results into batch receipts, including missing-effect-
  receipt handling.
- `DirectorToolExecutor` remains in `roles.adapters` and exposes write, edit,
  delete, and command paths. Its construction/injection fan-out is outside the
  `roles.kernel` turn owner.
- The Run Ledger settlement barrier is a read-only projection. `closed` and
  `passed` are intentionally distinct and cannot grant mutation authority.

WS2-B1 is final for this revision: the TaskRuntime public-authority suite has
**373 passed**, and the independent review reported **zero findings**. This
evidence closes only the B1 authority-handle work. It does not close DEO-1,
authorize a mutation path, or weaken the DEO-2/3/4 gates.

The design rejects a long-held `begin_mutation` lock. A local `RLock` is not
cross-process authority, and a file lock must never span an arbitrary tool
command, especially the maximum 300-second command timeout. Durable state plus
short compare-and-swap (CAS) transitions is the linearization mechanism.

## 2. Authority Topology

```text
TaskRuntime: operation facts, CAS, terminal admission, parent-close barrier
        |
        +-- roles.kernel: sole turn and ToolBatchRuntime owner
        |       |
        |       +-- DirectorToolExecutor via director.runtime policy adapter
        |               |
        |               v
        |        KernelOne effect-receipt primitives
        |
        v
Run Ledger: read-only projection of closed facts and receipts
```

Ownership is strict:

| Concern | Owner | Forbidden owner |
| --- | --- | --- |
| Durable DEO facts, CAS, parent barrier, terminal admission | `TaskRuntime` | `roles.adapters`, `roles.kernel`, Run Ledger |
| One turn and one batch lifecycle | `roles.kernel` | Director adapter, CLI, Factory |
| Tool semantics and policy adaptation | `director.runtime` | `roles.adapters` as an authority owner |
| Receipt persistence/hash primitive | KernelOne | Director adapter private journal |
| Evidence projection/query | Run Ledger | any mutation path |

`DirectorToolExecutor` becomes an invoked implementation detail. It cannot
create, close, recover, or certify a DEO. The protocol does not create a child
transaction: the enclosing TaskRuntime operation is the only durable operation,
the enclosing `ToolBatchRuntime` is the only batch, and the existing terminal
fact remains the only commit/settlement truth.

## 3. Protocol Contract

A Directed Effect Operation (DEO) is identified by immutable
`operation_id`, `workspace`, `task_id`, `execution_attempt_id`, `turn_id`,
`batch_id`, `tool_call_id`, and `effect_id`. The idempotency key is derived from
that tuple plus the intended effect fingerprint. A retry must reuse all of these
identities; a logically new tool call must mint a new `effect_id`.

Required durable states:

```text
ABSENT
  -> INTENT_COMMITTED
  -> EFFECT_STARTED
  -> RECEIPT_COMMITTED
  -> CLOSED_BY_PARENT

INTENT_COMMITTED -> ABORTED
EFFECT_STARTED -> RECOVERY_PENDING -> RECEIPT_COMMITTED | DEAD_LETTER
```

No other forward transition is legal. `ABORTED`, `CLOSED_BY_PARENT`, and
`DEAD_LETTER` are terminal. `RECEIPT_COMMITTED` is not a parent settlement
verdict; it only proves the individual effect receipt is durable.

Each durable record includes the state, operation identity, parent attempt and
batch identities, policy verdict/hash, intended-effect fingerprint, expected
receipt schema/hash, actor, timestamp, previous-state version, CAS version, and
the reason/evidence reference for a non-happy-path transition. Payloads that
cannot be safely replayed store a redacted canonical fingerprint, not arbitrary
tool arguments or prompt text.

### Linearization Points

1. **Intent admission:** TaskRuntime CAS `ABSENT -> INTENT_COMMITTED` after
   binding the active attempt, turn, and batch. This is the only point at which
   a mutation becomes authorized.
2. **External-effect claim:** TaskRuntime CAS
   `INTENT_COMMITTED -> EFFECT_STARTED`. The process performs no side effect
   before this record is durable.
3. **Receipt commit:** KernelOne atomically persists the effect receipt; the
   receipt reference/hash is then CAS-bound to the DEO as
   `EFFECT_STARTED -> RECEIPT_COMMITTED`.
4. **Parent close:** TaskRuntime verifies that the parent batch is terminal,
   every expected DEO is in a terminal receipt/abort/dead-letter state, and the
   Run Ledger query has no open applicable obligation. It CASes only eligible
   receipts to `CLOSED_BY_PARENT` before admitting terminal task settlement.

The fourth point is a TaskRuntime barrier, not a Run Ledger write. A Run Ledger
projection may lag and cannot be used to reconstruct an authorization decision.

## 4. Crash, Cancellation, Idempotency, and Recovery

| Boundary | Required result | Recovery rule |
| --- | --- | --- |
| Before intent CAS | `ABSENT`; no effect | Retry may create intent once. |
| After intent, before effect claim | `INTENT_COMMITTED` | Cancel may CAS to `ABORTED`; otherwise resume only after parent/attempt validation. |
| After effect claim, before external call result | `EFFECT_STARTED` | Never infer absence of effect; classify `RECOVERY_PENDING` and reconcile idempotently. |
| After external effect, before receipt | `RECOVERY_PENDING` | Re-run only if the effect's idempotency key is accepted by the tool; otherwise read/reconcile and write one receipt or dead letter. |
| After receipt, before parent close | `RECEIPT_COMMITTED` | Replay parent barrier and close once. |
| During cancellation | Intent may abort only before `EFFECT_STARTED` | After start, cancellation records a request and recovery closes through receipt/dead letter. |

Recovery is finite and event/replay driven. It scans durable non-terminal DEOs
for the scoped active attempt, claims each record with CAS, and writes a durable
outcome. It must not use timer polling, an in-memory registry, a cross-process
long lock, or a second receipt journal. A stale execution attempt, changed
fencing identity, missing parent batch, receipt hash mismatch, or ambiguous
external effect fails closed into `RECOVERY_PENDING` or `DEAD_LETTER` with a
typed evidence reference.

Counterexamples that must remain impossible:

- A duplicate `DirectorToolExecutor` may not call a write tool merely because
  it has a workspace path; it needs a durable `EFFECT_STARTED` DEO claim.
- A process restart may not repeat a non-idempotent command based only on a
  missing local receipt.
- A terminal task may not be admitted while a batch effect is merely
  `EFFECT_STARTED` or `RECOVERY_PENDING`.
- A `closed` Run Ledger projection may not authorize a later mutation.
- A 300-second command may not hold a file lock or local mutex as authority.

## 5. Migration Buckets

### DEO-1: Durable Fact Foundation

DEO-1 is strictly ordered as **DEO-1A -> DEO-1B -> DEO-1C**. A later sub-bucket
may consume only the earlier public contract; it must not recreate a private
stream, aggregate, parser, snapshot authority, or readiness rule.

#### DEO-1A: Strict FactStream Substrate

**Entry gate:** WS2-B1 final evidence is present: **373 passed** and
independent review **zero findings**. The legacy `create` seam remains an
explicit DEO-4/WS2-B4 deletion item.

**Write set:**
`src/backend/polaris/kernelone/events/sourcing/file_store.py`,
`src/backend/polaris/kernelone/events/sourcing/models.py`,
`src/backend/polaris/kernelone/events/sourcing/__init__.py`, the KernelOne
JSONL durability primitive it consumes, and directly paired KernelOne sourcing
tests. No TaskRuntime aggregate, settlement, UI, Run Ledger, or Director write
belongs in 1A.

**Contract:** KernelOne exposes one common durability capability with
`durability=buffered|flush|fsync`. Existing callers retain their current default
behavior. FactStream public append/query selects strict validation with
`strict_integrity`; the selection is public and is not a DEO-only private
parser. DEO streams select `strict_integrity` and `durability=fsync`; other
callers are not silently upgraded or behaviorally changed.

Each parent batch derives one independent, irreversible stream token. The token
is bound to its immutable parent identity and cannot be reassigned, reset, or
reused for another batch. All replay, idempotency, and head queries are scoped
to that token, never to a workspace-wide historical scan. A snapshot is a
rebuildable projection/cache of that one stream; it is not an authorization
artifact and cannot mint a claim, satisfy a CAS, close a parent, or replace the
stream.

**Strict parser rules:**

1. A torn tail is only a malformed, non-newline-terminated final physical
   record. Strict read returns typed torn-tail evidence and blocks append/replay
   until an explicit locked repair/quarantine procedure validates the contiguous
   prefix; it never silently treats the tail as a committed event.
2. Invalid JSON, envelope/hash/sequence failure, or a malformed record before
   that final physical line is middle corruption. It is a fail-closed stream
   corruption: no skip, truncate, replay, or new append may proceed.
3. An unknown schema/event version is fail-closed in `strict_integrity` mode,
   with its stream, sequence, schema/version, and parser evidence recorded.
   Non-strict legacy reads retain their documented compatibility behavior; they
   must not be used to authorize DEO work.

**Performance target, not a hard CI deadline:** normal parent batches are
`M <= 64` operations and `<= 320` events; snapshot/replay triggers at `512`
events or `2 MiB`, reads at most the last `32` events after a valid snapshot,
and targets p95 `<100 ms`, p99 `<500 ms` with four processes and `<8 MiB`
incremental working memory. The benchmark must be configurable and recorded as
non-hard-CI evidence so slow development machines cannot cause a false gate
failure.

**Exit gate:** strict and legacy compatibility tests prove the durability
selection, strict parser outcomes, stream-token isolation, no workspace-wide
scan for a parent batch, and snapshot non-authority. The performance harness
publishes its configuration and measurements without becoming a brittle CI
threshold.

#### DEO-1B: TaskRuntime Guarded Aggregate, Enrollment, Settlement Pre-Barrier, and In-Memory Projection

**Status:** `closed`; Section 10.2 is the authoritative completion record.
DEO-1A supplies the guarded append and dynamic stream-enrollment ports.
DEO-1B consumed those public ports without reopening, recreating, or modifying
DEO-1A, KernelOne, or FactStream. DEO-1C is now pending. The following remains
the final implementation contract that was satisfied by DEO-1B.

**Audited current gap:** the current repository has all six defects below.

1. `_mutate` validates a parent binding and then directly calls
   `append_fact_event`, leaving a parent-open TOCTOU between validation and the
   durable operation append.
2. Dynamic enrollment is reachable only through test helpers; the production
   TaskRuntime public maintenance contract is absent.
3. Re-prepare/retry has no numeric upper bound.
4. Existing concurrency tests can go false-green because they do not execute
   the complete production sequence and can manually enroll or monkeypatch an
   append seam.
5. `_persist_snapshot` and `_snapshot_path` use `Path`/`os.replace` for a
   write-only disk side effect. No authorization path reads that disk snapshot.
6. Canonical settle and the other identified active-to-inactive writers do not
   yet share one strict parent-registry pre-barrier under the cooperative
   session-file lock.

The 1A guarded primitive already supports an absent target. DEO-1B must use
that capability for a first operation event and must not reopen DEO-1A or add
target-file bootstrap logic.

**Exact implementation write scope:**

- `src/backend/polaris/cells/runtime/task_runtime/public/contracts.py`
- `src/backend/polaris/cells/runtime/task_runtime/public/service.py`
- `src/backend/polaris/cells/runtime/task_runtime/public/__init__.py`
- `src/backend/polaris/cells/runtime/task_runtime/internal/directed_effect_operation.py`
- `src/backend/polaris/cells/runtime/task_runtime/internal/service.py`
- `src/backend/polaris/cells/runtime/task_runtime/tests/test_service.py`
- directly paired TaskRuntime public, internal, concurrency, and architecture
  tests
- after behavior is stable only: TaskRuntime `cell.yaml`, `README.agent.md`,
  generated/context packs, and the global catalog governance synchronization

No other write is authorized. In particular, do not modify KernelOne,
FactStream, roles, Factory settlement, Run Ledger, UI, delivery wiring, KFS, or
AtomicCache. Do not add a private lock, second enrollment mechanism, stream
bootstrap path, or snapshot cache authority.

**Public maintenance contracts:** TaskRuntime exposes exactly these two explicit
maintenance commands and service methods:

```text
EnrollDirectedEffectParentRegistryStreamCommandV1(execution_attempt)
  -> enroll_directed_effect_parent_registry_stream

EnrollDirectedEffectOperationStreamCommandV1(execution_attempt, parent_binding)
  -> enroll_directed_effect_operation_stream
```

Both return `DirectedEffectStreamEnrollmentResultV1`. Its receipt is
observability evidence only and is not authorization, a parent binding, an
operation admission, or an alternative fact source. These commands are
TaskRuntime maintenance-only public APIs. Business repositories/services and
all child business operations must never call them implicitly, lazily, or as a
fallback. Missing enrollment fails closed with the typed 1A failure; it never
creates or enrolls an authority object as a side effect.

Enrollment is non-authoritative maintenance and does not acquire the
TaskRuntime session lock pair. It cannot admit a parent or make an inactive
attempt active. An architecture fence must prove that parent admission is the
sole production writer of parent-registry facts; maintenance enrollment writes
no parent-registry fact.

Both commands carry the complete
`TaskRuntimeExecutionAttemptIdentityV1 execution_attempt`; accepting only an
attempt id, registry identity, task id, mutable handle, or binding-derived
identity is forbidden. The operation command additionally carries the complete
`DirectedEffectParentBindingV1 parent_binding`.

`enroll_directed_effect_parent_registry_stream` first calls
`validate_execution_attempt`, then derives the registry identity and stream
token only from that validated complete attempt. A non-valid verdict returns
before FactStream enrollment and cannot produce a success receipt.

`enroll_directed_effect_operation_stream` must execute this fail-closed order
before invoking the FactStream enrollment port:

1. Call `validate_execution_attempt` with the command's complete
   `execution_attempt`; any non-valid verdict returns immediately.
2. Derive the expected parent-registry identity from that validated attempt,
   never from the caller's binding.
3. Strict-read and fully rebuild that registry stream.
4. Find the durable binding by `parent_binding.binding_id`; absence is failure.
5. Compare canonical workspace and task id across command, execution attempt,
   derived registry identity, rebuilt registry, supplied binding, and durable
   binding. Compare the complete execution-attempt-derived registry identity
   and every canonical `DirectedEffectParentBindingV1` field against the durable
   binding, including registry/operation stream tokens, versions/sequences,
   hashes, admission key, correlation, actor, and source event identity.

Any validation, lookup, canonicalization, identity, or field mismatch returns a
typed failure without calling FactStream enrollment and without producing a
success receipt. Binding hash equality alone is insufficient. The parent
registry may be closed when this maintenance attestation runs; enrollment does
not authorize a child transition, and the later guarded business operation must
still apply its own replay/state/fence checks.

The only permitted lifecycle ordering is:

```text
registry enrollment
  -> admit parent
  -> operation enrollment(parent_binding)
  -> child get / admit / claim / abort
```

DEO-2 later orchestrates these maintenance calls. DEO-1B provides only their
TaskRuntime public port and the fail-closed business behavior; it does not add
kernel, Director, adapter, or service orchestration.

**Parent admission linearization:** `TaskRuntimeService` owns the bounded lock
pair and acquires it in the fixed order `local session RLock -> cooperative
session-file lock`; parent admission then acquires the FactStream locks. Under
the caller-held session lock pair, the service strictly rereads and validates
the attempt from the locked session state, then calls a repository method that
assumes that validation has already succeeded. The repository method performs
the strict registry read/reduction and guarded registry append only. It must not
call public `validate_execution_attempt`, recursively reacquire either session
lock, or import/call back into `TaskRuntimeService`.

The local `RLock` is a per-service optimization only. The cooperative
session-file lock supplies cross-instance and cross-process correctness. While
the lock chain is held there is no tool, network, TaskBoard, Run Ledger, or
projection call. Locks are not authority or a second SSoT; the strict registry
fact is the admission truth.

**Active-to-inactive settlement pre-barrier:** DEO-1B deliberately changes the
existing settle semantics without closing the parent. The canonical
`settle_execution_attempt` path and every other writer that can move an active
session or row to inactive must acquire the same service-owned session lock
pair, strictly reread the parent registry while the cooperative lock remains
held, apply the rules below, and only then write session/row/fact inactive or
terminal state:

| Strict pre-barrier state | DEO-1B verdict |
| --- | --- |
| Exact unenrolled/nonexistent registry, or enrolled strict-empty registry | Allow the inactive/terminal write while still holding the session-file lock. A later parent admission must fail its locked active-attempt validation. |
| `OPEN` | Return `settlement_parent_close_required`; write no session, row, or fact inactive/terminal state. |
| `CLOSED` without the future DEO-3 outcome-bound close proof | Fail closed; a bare close fact is insufficient. |
| Corruption, strict-read failure, identity mismatch, unknown state, or ambiguous result | Fail closed with typed evidence and no inactive/terminal write. |

The audited active-to-inactive inventory is: canonical settle, stale fencing,
bulk cancellation suspension, rework failure, dedupe cancellation,
role-adapter failure, and reopen suspension. Every path must consume the same
pre-barrier or reject the active DEO session. Reset remains exempt only because
its existing contract rejects active sessions; if reset ever accepts one, it
enters this inventory immediately.

**Guarded child mutation:** every child mutation, including a first event to an
absent target, uses this loop:

```text
guarded prepare(target=operation stream, guard=parent registry stream)
  -> outside-lock strict rebuild/replay/authorization
  -> guarded commit(one canonical operation event)
```

The outside-lock decision reconstructs complete strict target and guard facts;
it revalidates parent binding, operation identity, state, execution fence, and
all semantic continuity. Exact idempotent replay is selected before requiring
the parent to remain `OPEN`, so a completed historical retry returns its stable
receipt even when the parent has later closed. Only a non-replay transition
requires the current parent `OPEN` state. `expected_seq` applies only to the
target operation head plus one; `expected_version` applies only to the operation
aggregate version. Neither is a parent-registry version or an authorization
substitute.

There are at most three prepare/commit attempts: the initial attempt plus at
most two re-prepares. Only `target_snapshot_drift` and
`guard_snapshot_drift` retry, and each retry repeats the full strict
rebuild/replay/authorization process. All other prepare, decision, commit, or
identity failures return immediately. On exhaustion return
`guarded_reprepare_exhausted` with evidence containing `attempts_total=3`,
`reprepare_count=2`, the ordered `drift_codes`, last target and guard heads,
operation identity, and parent binding id.

**Receipt and ambiguous-write reconciliation:** a successful guarded consumer
must validate the complete `GuardedFactAppendedV1` receipt, not a partial
projection: `event_id`, `workspace`, `stream`, `storage_path`, `appended_at`,
`appended_seq`, and `semantic_digest`. `semantic_digest` is not reimplemented
from KernelOne private canonicalization; TaskRuntime verifies it only through
the FactStream public exact-replay receipt together with strict rebuilt facts.
For an ambiguous append/fsync result, strict rebuild first locates and validates
an exact durable event and its full public receipt before attempt-liveness or
inactive-attempt handling. Thus an exact completed replay cannot be hidden by a
later inactive attempt. If no exact durable event exists, return the causal
typed failure; `append_write_failed` stays explicitly mapped and may not become
an untyped reconciliation success.

**Settlement boundary:** DEO-1B owns guarded child consumption and the limited
fail-closed inactive-write pre-barrier above. It does not write
`parent_closed`, bind a close to outcome/receipt eligibility, or recover a
pending close. The remaining settle-versus-child TOCTOU is DEO-3 P0 work and
keeps Bench `not_schedulable`. DEO-3 retains
`SettleTaskRuntimeExecutionAttemptCommandV1` and public
`settle_execution_attempt` as the only terminal entry point. Its internal path
must durably record a pending terminal intent, guarded-close the parent
registry while guarding the operation head, validate receipt eligibility and
the outcome-bound close proof, and only then persist the session terminal fact.
That DEO-3 path upgrades DEO-1B's `settlement_parent_close_required` blocker to
a recoverable successful terminal transition. No specialized settle entry
point, alternate terminal write, or pre-barrier bypass may be added.

**Operation schema, normalization, and replay:** new operation writers emit
`DIRECTED_EFFECT_OPERATION_SCHEMA_V2`. A v2 payload is the v1 semantic field
set with `recorded_at` removed. The parser validates schema-specific exact
field sets before reduction: v1 still requires a non-empty, timezone-aware
`recorded_at`; v2 forbids `recorded_at`; unknown schema versions and any
missing or extra field fail closed. The outer FactStream envelope timestamp is
storage metadata only and never enters semantic identity. Parent-registry schema
and parsing remain v1 and are not changed by this bucket.

After schema validation, both parsers produce the same schema-neutral internal
`NormalizedDirectedEffectTransitionV1`. Command comparison produces a matching
schema-neutral `NormalizedDirectedEffectReplayDescriptorV1`. These normalized
forms contain only domain-significant operation identity, transition kind/state,
parent binding identity, execution fence, actor/reason/effect semantics, and
other canonical command semantics required to distinguish legal replay from a
changed command. They do not contain `schema_version`, `recorded_at`, envelope
timestamp, event id, event sequence, aggregate/previous version, expected/head
sequence, append-attempt id, or other CAS/storage volatility.

New append idempotency keys are derived only from the normalized semantic
identity, so equivalent v1 and v2 transitions have the same semantic identity.
The strict aggregate reducer still validates stored CAS/version continuity;
normalization removes volatility only from replay/idempotency comparison and
never weakens stream integrity.

TaskRuntime must detect a matching historical v1 transition during the
outside-lock rebuild and return that record's original receipt directly before
calling `append_if_guarded_snapshot`. It must never submit a synthesized v2
event for an already committed v1 replay. A matching replay descriptor with
changed normalized transition semantics fails
`idempotency_semantic_conflict`; it cannot append under a new schema-derived
key. Ambiguous commit/fsync outcomes re-enter strict rebuild and use the same
normalized replay rule to return the one original receipt or the exact typed
conflict, never a duplicate append.

**Projection rule:** delete `_persist_snapshot`, `_snapshot_path`, and every
disk snapshot write side effect. Retain only in-memory `_project_snapshot`,
created from each complete strict stream rebuild. It is never read from disk,
never authorizes a transition, and never replaces strict replay. Do not replace
the deleted writes with KFS, AtomicCache, or another persistence layer.

**Required acceptance gates (A-L):**

| Gate | Required proof |
| --- | --- |
| A | Both public maintenance commands carry and validate the complete `execution_attempt`; registry identity is attempt-derived; operation enrollment also carries `parent_binding`; service methods, typed result, and receipt non-authority are public. |
| B | Unenrolled registry or operation stream fails closed, and every denied maintenance/business path proves FactStream enrollment was not called. |
| C | Operation enrollment validates the attempt first, derives registry identity from it, strict-rebuilds the registry, looks up `binding_id`, compares canonical workspace/task/attempt and every durable binding field, and rejects every mismatch before enrollment. |
| D | The required sequence is explicit registry enrollment -> parent admission -> operation enrollment(binding) -> child operation; no business repository/service performs implicit, lazy, or fallback enrollment. |
| E | Child mutation uses guarded prepare/commit and has no `append_fact_event` bypass. |
| F | Exact v1/v2 parser rules pass and both schemas normalize to the same transition/replay descriptor; unknown/missing/extra fields fail closed. |
| G | Historical v1 exact replay returns its original receipt before guarded commit; no synthesized v2 append is attempted; schema-neutral idempotency excludes schema, timestamp, and CAS volatility. |
| H | Changed normalized semantics fail with `idempotency_semantic_conflict`; ambiguous append/fsync reconciliation strict-replays to one original receipt with no duplicate. |
| I | Only target/guard snapshot drift retries; exactly three total attempts exhaust to `guarded_reprepare_exhausted` with required evidence. |
| J | Exact replay is returned before the parent-OPEN check; a non-replay child after parent close fails with no append; no disk snapshot write remains and projection is in-memory/rebuild-derived. |
| K | Real thread/process parent-admit-versus-public-settle tests use the complete explicit enrollment fixture and production public calls, never an AST-only proof. The only permitted outcomes are: parent admission linearizes first, leaves `OPEN`, and public settle returns `settlement_parent_close_required` without any inactive/terminal write; or public settle linearizes the allowed empty/nonexistent-registry transition first and parent admission fails locked active-attempt validation. This proves admission/pre-barrier ordering only, not the DEO-3 child-versus-terminal close/recovery fence. |
| L | **Hard gate:** public/internal import and call-shape fences prove TaskRuntime-only guarded consumption, parent admission as the sole production parent-registry fact writer, all seven identified active-to-inactive paths use the common pre-barrier, reset rejects active sessions, and the repository helper neither imports/calls back into `TaskRuntimeService` nor validates/reacquires the caller-held session locks. No KernelOne/FactStream source change or DEO-2/DEO-3 behavior is added, and TaskRuntime `cell.yaml`, README, context pack, and global catalog are synchronized. |

For gate K, the fixture must call the two TaskRuntime public maintenance methods;
it may not manually call FactStream enrollment. It must exercise the existing
public `settle_execution_attempt` path against parent admission in real threads
and processes. If deterministic scheduling requires a hook, only a module-owned
barrier seam may pause the interleaving; it cannot replace, mock, or monkeypatch
enrollment, guarded prepare/commit, strict read, public append, or settle. The
admission-first branch must assert the exact blocker and absence of every
session/row/fact inactive write; the settle-first branch must assert admission's
locked rejection. Separate tests must exhaustively cover all seven identified
active-to-inactive paths and the reset exemption. The unresolved
settle-versus-child close/recovery test belongs to DEO-3 and cannot be simulated
by writing `parent_closed` facts or by adding a DEO-3 path in DEO-1B.

Gate L is not cleanup after implementation; it is a required hard exit gate.
DEO-1B is complete because the Section 10.2 evidence records A-L as green. The
fence does not weaken the DEO-4 repository-wide legacy-removal obligation.

**Complexity:** each prepare or commit scan/rebuild is `O(T + G)` time and
`O(T + G)` transient memory for target and guard facts. The bounded policy makes
the mutation path at most three such attempts; in-memory projection remains the
same order. No disk snapshot I/O, workspace-wide scan, or unbounded retry is
permitted.

**Explicit deferrals:** DEO-1B must not add DEO-1C readiness, DEO-2 token or
orchestration, DEO-3 pending terminal intent, guarded parent close,
outcome-bound close proof, receipt eligibility/recovery, DEO-4 legacy removal,
or any KernelOne/FactStream modification. The limited inactive-write
pre-barrier is in DEO-1B scope and is not a DEO-3 implementation.
The completed DEO-1B evidence includes reconciliation order, full seven-field
receipt validation, `append_write_failed` mapping, the active-to-inactive
inventory, and real thread/process Gate K. These facts do not implement the
DEO-3 close/recovery fence.

#### DEO-1C: Readiness Query and Fence

**Entry gate:** DEO-1B aggregate/CAS/snapshot evidence is green.

**Write set:** TaskRuntime public read-only readiness contracts/query, the
minimal TaskRuntime projection/fence implementation, and directly paired
TaskRuntime architecture/read-query tests. This bucket must not write
settlement, receipt closure, terminal admission, roles, Factory, UI, Run Ledger,
or delivery code.

**Contract:** expose a typed, read-only readiness result for one parent batch
from strict stream facts and the rebuilt aggregate. Its policy field is fixed to
`enforcement="not_enabled"`. The result is diagnostic/fencing evidence only;
it does not authorize effects, settle an attempt, close a batch, or mutate any
projection.

**Exit gate:** a mechanical dependency fence proves 1C has no call/import/data
path into settle, terminal admission, UI, or Run Ledger. Readiness queries are
workspace and parent-token scoped, fail closed on strict-stream ambiguity, and
cannot be mistaken for a parent-close or receipt verdict.

**Deferral:** DEO-3, not DEO-1C, is the first bucket permitted to change
readiness enforcement from `not_enabled` and use outcome-bound close proof to
admit a successful terminal transition. DEO-1B's direct registry pre-barrier is
independent of this read-only readiness projection and can only block.

### DEO-2: Kernel-Owned Batch Admission and Director Adapter

**Detailed design:**
`DIRECTED_EFFECT_OPERATION_DEO2_BLUEPRINT_20260716.md` is the controlling DEO-2
specification. It locks durable inventory seal/readiness, per-call
`EFFECT_STARTED` claim grants, two-phase pure Director policy validation,
deferred synthetic repair effects, the single-round repair barrier, and the
DEO-2/DEO-3 ownership boundary. This summary must not be used to weaken those
requirements.

**Entry gate:** DEO-1A, DEO-1B, and DEO-1C exit evidence exists and
`roles.kernel` has an approved public DEO service dependency.

**Work:** establish the expected operation inventory, then make `roles.kernel`
create/admit every inventory member from the canonical turn and
`ToolBatchRuntime`; route Director tool semantics through a
`director.runtime` policy adapter. Convert every one of the 38 known executor
construction/injection surfaces to consume an already-admitted operation token
or fail closed.

**Exit gate:** mechanical inventory test equals zero unbound mutation-capable
surfaces; all batch receipts link to their DEO and KernelOne receipt; adapters
cannot create DEOs; direct executor writes without a token fail closed.

**Risk and counterexample:** moving construction without moving authority would
leave a policy adapter able to mint a token. The fence must reject adapter-owned
DEO creation and `DirectorToolExecutor` mutation entry without a kernel-owned
operation reference.

### DEO-3: Receipt Closure, Recovery, and Terminal Admission

**Entry gate:** DEO-2 has complete fan-out inventory evidence and no unbound
mutation entry point.

**Work:** bind KernelOne receipt commits to DEO CAS transitions; add finite
replay/reconciliation for `EFFECT_STARTED` and `RECOVERY_PENDING`; make
TaskRuntime terminal admission consume the parent close barrier. The existing
`SettleTaskRuntimeExecutionAttemptCommandV1` / public
`settle_execution_attempt` remains the only terminal entry. Internally it must
persist `pending_terminal_intent`, guarded-close the parent registry with the
operation head as guard, prove receipt eligibility and bind the close to the
settlement outcome, then persist the session terminal fact. This upgrades the
DEO-1B `settlement_parent_close_required` blocker into successful terminal
settlement. DEO-3 also owns close recovery, receipt/recovery evidence, and the
heartbeat-versus-reclaim fence. Project only facts/receipts to Run Ledger.

**Exit gate:** crash/cancel matrix above passes; one effect yields one receipt;
replay does not duplicate side effects; pending terminal intent, guarded parent
close, and session terminalization are ordered and recoverable; terminal
admission rejects open DEOs; heartbeat/reclaim cannot cross a terminal fence;
Run Ledger differentiates missing from failed evidence without authority write.

**Risk and counterexample:** treating receipt absence as proof of no effect can
duplicate a mutation. Recovery tests must simulate crash after an external
effect and prove reconciliation or dead letter, never blind re-execution.

### DEO-4: Legacy Removal and Architecture Fence

**Entry gate:** DEO-3 recovery proof and repository-wide caller inventory are
green.

**Work:** remove the old `create` seam and all compatibility bypasses, including
unscoped executor construction, raw/direct receipt closure, and remaining
admission, guarded-child, or terminal-settlement side paths. Extend
AST/import/call-shape fences for TaskRuntime, roles.kernel, director.runtime,
roles.adapters, KernelOne, and Run Ledger.

**Exit gate:** zero legacy seams; all 38 former surfaces either use the canonical
path or are deleted; no new batch/commit/SSoT; full targeted quality matrix
passes with no exemption.

**Risk and counterexample:** a test-only compatibility path can become a
production injection path. The fence must cover non-test imports and production
bootstrap/CLI wiring, and tests must assert that bypass constructors raise a
typed failure.

## 9. Guarded FactStream Append Amendment (2026-07-14, Historical and Superseded)

**Historical status (superseded by Section 10.1):** DEO-1A was reopened as a
**pending guarded-append extension** with second-review High blockers. The
previous 121-pass DEO-1A evidence remained valid only for the already-reviewed
strict/durability substrate; it did not prove the multi-stream admission
property. At that time DEO-1B was blocked pending the substrate, its TaskRuntime
consumer, and multi-process race tests. Reported `91/464` and hygiene-green
observations were not DEO-1B closure evidence.

Independent review found a High TOCTOU: a child can validate its parent as OPEN,
then the parent can close, and then the child can durably append an operation
fact. Returning `parent_closed` after that durable mutation is unacceptable.
The repair is the generic guarded single-target append defined in
`GUARDED_FACT_APPEND_V1_BLUEPRINT_20260714.md`; it is not a reservation journal,
a long-held lock, or a multi-stream transaction.

One immutable parent binding has exactly one shared `operation_stream_token`.
All child operation facts use that token's operation stream. A child transition
targets the operation stream and guards the parent registry head. DEO-3 parent
close targets the registry stream and guards the operation-stream head. Each
successful request appends exactly one target fact with `fsync`; it never appends
a guard fact or attempts a cross-stream commit.

The KernelOne primitive owns stable central-key locking, descriptor-only strict
scan/head validation, continuity-proof verification, idempotency matching, and
the one target append. It exposes `read_guarded_fact_snapshot`, which acquires a
reusable `LockedRegularFileSetV1` / `StreamLeaseSet`, strictly scans through held
descriptors, returns deeply immutable facts plus a proof, and releases locks.
TaskRuntime then reduces and authorizes those facts outside FactStream locks. It
passes the unchanged proof and its canonical semantic event to
`append_if_guarded_snapshot`; that call locks again, strictly rescans, returns an
exact idempotent receipt before drift checks, recomputes both proofs, and fsyncs
one target append only when both proofs still match. KernelOne must not import
TaskRuntime or execute caller-supplied domain work while holding FactStream locks.

The normative proof uses a non-authenticating `continuity_digest`, validated
before any proof-controlled resolution. It binds canonical workspace, both
stream references, storage identity, strict format/schema revision, exact heads,
and full canonical digests of immutable
facts. Commit derives heads only from that proof, not independent caller heads.
The semantic digest excludes generated volatility (`recorded_at`, `event_id`,
`seq`, append timestamp, and occurrence time). Same-key/different-semantic
events are typed conflicts. v1 rejects identical target and guard streams. Drift,
proof tampering, strict corruption, and lock failure write nothing; TaskRuntime
alone performs bounded re-prepare/re-authorization, with no internal retry.

Steady-state physical I/O uses a platform-owned persistent lock authority outside
mutable workspace/runtime stream directories and isolated by storage identity.
`anchor.lock` is separate from `realm/` and persistently binds storage identity,
realm device/inode, and authority format revision. Explicit bootstrap/provision
or offline maintenance alone may hold anchor `LOCK_EX`; ordinary acquire opens
the existing authority, holds anchor `LOCK_SH`, validates the binding, then takes
canonical per-stream `LOCK_EX` locks under a monotonic deadline. Missing or
mismatched authority fails closed. Acquire never creates, rebinds, repairs, or
rotates authority; v1 has no online rotation or lock upgrade/downgrade.

Anchor `LOCK_SH` remains held through strict scans, descriptor append/create,
file fsync, parent-directory fsync, final identity validation, and receipt
verdict. Pre/post checks without that continuously held anchor are insufficient.
All `JsonlEventStore` FactStream writers move to this authority in one cutover,
so legacy and guarded FactStream APIs cannot retain a permanent dual-lock split.

Traversal is root-relative from a trusted runtime/root directory descriptor.
Directory components use `O_DIRECTORY|O_NOFOLLOW|O_NONBLOCK|O_CLOEXEC`; leaves
use `O_NOFOLLOW|O_NONBLOCK|O_CLOEXEC`, regular-file/link/device/inode checks, and
descriptor-only reads/appends/fsync. No absent target is created until both
strict snapshots match. First create uses parent-relative `O_CREAT|O_EXCL`, file
fsync, then parent-directory fsync; existing append uses the held `O_APPEND`
descriptor. Special files, symlinks, hard links, and root/parent identity drift
fail closed.

The guarantee assumes an owned local filesystem and cooperating Polaris writers.
Portable APIs cannot make ancestor names immutable against a privileged or
same-UID actor that concurrently renames ancestors while ignoring locks; detected
drift and unsupported environments fail closed. Windows v1 remains unavailable
without an equivalent reparse-safe handle backend. Central lock enrollment and
workspace/runtime physical identity are projections/capabilities, not a second
SSoT.

`LockedRegularFileSetV1` owns a lifecycle mutex and
`ACTIVE -> CLOSING -> CLOSED`. Every lease I/O operation shares the mutex.
`close()` idempotently transitions state, atomically detaches all owned fds, and
closes only that detached batch, preventing close-versus-I/O races and accidental
closure of a reused fd number. The anchor is released last and never spans
TaskRuntime domain/tool work.

If durability completed but final authority validation drifts, there is no
success receipt and no rollback. The typed result is
`post_fsync_authority_reconciliation_required`; strict replay is mandatory.

DEO-3 is the only bucket permitted to consume the parent-close commit form for
terminal admission and receipt closure. DEO-4 must remove or fence every legacy
direct append/CAS path that could bypass the guarded port and allowlist only
TaskRuntime as the DEO guarded-commit consumer. That allowlist is architecture
control, not a security boundary or replacement for FactStream proof checks.
Cell manifest and context-pack reconciliation remains a separate DEO-1B
governance item; it neither implements nor proves guarded append. Bench status
remains `not schedulable`; no calendar or success claim is authorized.

The historical second review recorded Blocker/High implementation gaps for central lock
authority/provision separation, descriptor-relative path safety, delayed target
creation, lifecycle-safe close, taxonomy/public parity, scoped all-writer
cutover, cross-process race proof, registry publication safety, and runtime-root
binding/receipt completeness. Historical evidence was **328 focused passed** and
**611 passed, 3 external baseline failures broad**; those superseded counts did
not close DEO-1A. A separate Medium finding remains out of guarded-substrate
scope: TaskRuntime snapshot persistence bypasses KFS, so the full KernelOne
release gate is **393 passed, 1 skipped, 1 failed**. The three filesystem
baseline failures are external to DEO-1A. This docs task does not fix or
reclassify any of them.

Scope is only FactStream/`JsonlEventStore` sourcing and public call paths.
`kernelone/events/io_events.py`, `kernelone/fs/jsonl/*`, generic `.seq.lock`, and
unrelated JSONL writers are not this bucket. FactStream `cell.yaml`,
`README.agent.md`, and generated context-pack synchronization is a separate
governance item after code stabilizes, not part of this documentation change.

## 6. Acceptance Matrix

| Proof | DEO-1A | DEO-1B | DEO-1C | DEO-2 | DEO-3 | DEO-4 |
| --- | --- | --- | --- | --- | --- |
| Strict FactStream durability/parser/token isolation | required | regression | regression | regression | regression | regression |
| Platform anchor authority, descriptor I/O, scoped writer cutover | required | regression | regression | regression | regression | fence |
| Lifecycle-safe close and public failure-taxonomy parity | required | regression | regression | regression | regression | fence |
| Aggregate CAS and snapshot non-authority | n/a | required | regression | regression | regression | regression |
| Readiness query/fence, `enforcement="not_enabled"` | n/a | n/a | required | regression | changed only here | regression |
| 38-surface construction/injection inventory | baseline | baseline | baseline | zero unbound | zero unbound | zero legacy |
| Kernel batch and Director policy boundary | design | design | fence | required | regression | AST/import fence |
| KernelOne receipt hash/idempotency | contract | aggregate bind | query only | linked receipt | crash/replay | regression |
| TaskRuntime parent barrier/terminal admission | not enabled | not enabled | not enabled | regression | required | repository fence |
| Crash and cancellation recovery | parser only | aggregate only | n/a | harness | required | regression |
| Run Ledger read-only projection | excluded | excluded | excluded | regression | required | required |
| Ruff, format, mypy, focused tests | required | required | required | required | required | required |

The implementing bucket must publish exact commands, counts, receipt hashes,
and relevant codegraph call paths. Passing a unit test does not authorize
declaring the P0 closed without the corresponding matrix row and boundary
evidence.

## 7. Bench Policy and ETA

**Do not run a bench** while any of these conditions holds: DEO-1A through
DEO-4 lacks its exit evidence; any mutation-capable executor surface is unbound;
an `EFFECT_STARTED` or `RECOVERY_PENDING` operation lacks recovery evidence;
the old `create` seam remains outside its explicit DEO-4 deletion plan; or the
architecture fence cannot prove the owner boundaries. A bench before then would
measure an intentionally open P0 path and cannot be used as acceptance evidence.

There is no calendar-date bench ETA. The only credible ETA is gate-based:
after DEO-4 reports its repository inventory, crash/cancel matrix, targeted
quality gates, and independent review evidence, schedule one fresh isolated
bench under the prescribed isolated-instance command. The schedule record must
name the exact commit/worktree fingerprint, command, owner, timeout, and work
directory. Until those preconditions are evidenced, the truthful bench ETA is
`not schedulable`.

## 8. Superseded WS2 Findings

WS2-B1 is complete: TaskRuntime public authority work has **373 passed**, with
an independent review reporting **zero findings**. The old `create` seam is not
evidence of closure and is intentionally deferred to WS2-B4/DEO-4 deletion.

The former claim that WS2-B2 was closed is rejected by review. Its actual status
is **P0 open** because the 38 `DirectorToolExecutor` construction/injection
surfaces bypass the intended mutation and receipt authority topology.

The former Phase-B recovery finding is superseded and closed for this scope:
production `app_factory` lifecycle wiring and the Factory settlement-consumer
crash tests already provide the bounded settlement replay producer. DEO recovery
is different: it governs effect receipt closure before parent terminal admission
and must not reimplement Factory Phase-B settlement recovery.

## 9. DEO-1A Bootstrap and Dynamic Enrollment Boundary (2026-07-15)

**Historical status and ETA (superseded):** `pending`. The second-round DEO-1A items were formal
entrypoint delegation, static stream catalog enrollment, dynamic enrollment
port, authority final-validation/ELOOP mapping, registry publication safety,
runtime-root identity binding and enrollment receipts, strict public taxonomy
parity, shutdown exception precedence, Cell-surface governance, and the
`directed_effect_operation` type-contract gap. This is a gate-based pending
state, not a calendar estimate.

The platform owns one authority provision service. Every formal production
entrypoint must delegate to it before its first FactStream I/O: HTTP lifespan,
Director and role CLI startup, and Factory direct-runtime startup are adapters,
not independent authority implementations. The service is not restricted to an
HTTP caller. Its receipt/evidence binds storage identity, runtime-root
device/inode, authority binding, canonical enrolled key set, operation kind,
idempotent created-versus-already-present verdict, format revision, root/anchor/
realm identities, and final validation after the durability boundary.

Provision and enrollment are explicit maintenance operations under anchor
`LOCK_EX`. Exact repeated requests are idempotent; concurrent calls serialize;
partial, unsafe, or mismatched state fails closed with the exact authority or
stream-lock code. Ordinary acquire is never permitted to provision, enroll,
create, repair, rebind, rotate, or substitute authority. Static platform streams
are enrolled by startup. Dynamic DEO streams are enrolled by the TaskRuntime/DEO
aggregate owner before first business I/O, using the same public maintenance
port. DEO-1A supplies that port only; DEO-1B consumes guarded append and must
not implement a second TaskRuntime lock or enrollment substrate.

Provision validates anchor regularity, `st_nlink == 1`, descriptor identity,
runtime-root device/inode, and the canonical realm binding both before and after
durable work. Same logical root path with a different device/inode fails
`lock_anchor_binding_mismatch`; it is never rebound. `ELOOP` maps to
`lock_anchor_invalid` for anchor open, `lock_realm_binding_mismatch` for realm
open, `stream_identity_drift` for root/ancestor traversal, and
`unsafe_stream_object` for a leaf. After file fsync, final drift of root,
ancestor, parent, leaf, anchor, or realm returns only
`post_fsync_authority_reconciliation_required`, never a success receipt.

Public strict corruption has one stable category,
`strict_stream_corruption`, with mandatory typed evidence preserving
`torn_tail` or `sequence_violation` where applicable. Generic
`guarded_snapshot_prepare_failed` and `guarded_append_failed` fallbacks are
prohibited. Known typed failures preserve their exact code; unknown internal
programming failures retain their exception chain and have no success receipt,
rather than being compressed into a generic public code. `kernelone/events/io_events.py` and `kernelone/fs/jsonl/*`
`.seq.lock` are outside this FactStream bucket. The three known broad filesystem
baseline failures are external ledger items and do not become DEO-1A defects.

The historical DEO-1A exit review required inventory of all production entrypoints and dynamic stream
paths, optional NATS `OSError` policy, startup cleanup failure boundary that
does not mask an application-body error, registry publication safety, FactStream
root export/manifest/README/context-pack/global-graph consistency, and the
guarded physical I/O evidence. `directed_effect_operation` historically had 35
mypy errors and was a DEO-1A type-contract gap. The production guarded-append
consumer is DEO-1B, so that consumer wiring is not counted as uncompleted
DEO-1A substrate/bootstrap/enrollment work. The historical audit snapshot was
**328 focused passed** and **611 passed, 3 external baseline failures**; it is
superseded, not current closure evidence.

## 10. DEO-1A Second-Round Independent Audit (2026-07-15, Historical and Superseded)

The process-local FactStream registry must serialize initialization and publish
only a fully constructed instance. A failed initialization must leave no partial
singleton accessible. Existing authority binding is physical: a runtime-root
path that resolves to a different device/inode must fail
`lock_anchor_binding_mismatch`, not be silently accepted by path equality.

Enrollment may report success only after final authority/root/realm/anchor proof
following `fsync`. The typed maintenance receipt must contain
`created`/`already_present`, format revision, runtime-root/anchor/realm
identities, canonical stream lock keys, and `final_validation=true`. Final drift
returns `post_fsync_authority_reconciliation_required` and no success receipt.

Formal Director/role CLI startup delegates to the same bootstrap service as HTTP
and Factory. Ordinary TaskRuntime and adapter I/O never lazily bootstraps. On
lifespan shutdown, settlement `OSError` is logged as cleanup evidence, remaining
cleanup continues, and an existing application-body exception remains the
exception observed by the caller. DEO-1A must add a governance gate for
FactStream exports, manifest, README, generated context pack, and global graph
catalog. These Blocker/High items keep DEO-1A pending; the DEO-1B production
guarded-append consumer remains explicitly out of this exit criterion.

### 10.1 Current DEO-1A Closure Record (2026-07-15)

This record supersedes the pending status, process-singleton proposal, `328`
focused count, `611` broad count, and `35` mypy-error claim in Sections 9-10.
DEO-1A is closed, and the guarded-append consumer remains intentionally outside
its scope as DEO-1B work.

The final review set reports no Blocker or High finding. The prior missing
64-process-bootstrap concern was withdrawn: the Cell-external integration test
uses a parent-held `LOCK_EX` with one release to 64 independent children calling
`bootstrap_fact_stream_workspace`, proving provision and enrollment each yield
one `created` and 63 `already_present` receipts, a common storage identity, and
per-lock-key proof. Stateless bootstrap has no process singleton/cache; ordinary
FactStream I/O does not bootstrap or enroll implicitly.

Current verification is: focused unified `342 passed in 21.77s`; broad `1533
passed, 3 failed, 1 skipped, 1 xfailed, 13 warnings in 67.29s`; independent
reviews `109 passed`, `95 passed + 5 passed`, and governance review `185 passed`;
Ruff, compileall, and diff check green; catalog hard-fail exit 0 with
`issue_count=0`, `blocker_count=0`, `high_count=0`, `new_issue_count=0`, and
`mismatch_count=0`; targeted mypy over 21
production files including `app_factory.py` and `directed_effect_operation.py`
reports `0 issues`. `FS-BASELINE-001..003` remain external/open filesystem
baselines, so this is not a whole-repository-green claim. DEO-1B was pending at
this point. Section 10.2 supersedes that status: DEO-1B is closed, DEO-1C is
pending, DEO-1 remains pending until 1C exits, and Bench remains
`not_schedulable` while DEO-2/3/4 are unfinished.

### 10.2 Current DEO-1B Closure Record (2026-07-15)

This record supersedes DEO-1B's pending wording and all earlier provisional
DEO-1B counts. DEO-1B is closed. It does not close DEO-1, authorize DEO-1C
enforcement, implement DEO-2/3/4, or authorize an end-to-end Bench.

The final main TaskRuntime rerun is `457 passed`, `0 failures`, with one
`nats-py` environment warning. Ruff, mypy, compileall, and `git diff --check`
are green. The independent final audit records every required A-L gate as
PASS.

The production evidence includes the bounded three-attempt drift-exhaustion
path, real thread and process parent-admission-versus-public-settle sequences
using the complete public enrollment flow, and 23 durable-binding mismatch
cases verified with the enrollment spy. The concurrency proof establishes the
DEO-1B admission/pre-barrier ordering only. It does not prove or simulate the
DEO-3 child-versus-terminal close/recovery fence.

The Cell manifest, README, generated context pack, and global catalog now name
the finalized TaskRuntime public contracts and its sole `events.fact_stream`
dependency. DEO-1C is therefore unblocked to `pending`, with its existing
read-only `enforcement="not_enabled"` boundary unchanged. DEO-2, DEO-3, and
DEO-4 remain unschedulable. Bench remains `not_schedulable` until their exit
evidence, the 38-surface closure, crash/cancel evidence, targeted gates, and
independent audit are complete.

### 10.3 DEO-1C Blueprint Materialization (2026-07-15)

**Status:** `implementing`. This record is the audited minimal design before
implementation. It preserves the 1A/1B closure evidence above and changes no
DEO-2, DEO-3, DEO-4, or Bench scheduling state.

#### Public Contract

The public surface adds exactly one read-only query and one result family:

```text
GetDirectedEffectParentReadinessQueryV1(
  workspace,
  task_id,
  execution_attempt: TaskRuntimeExecutionAttemptIdentityV1,
  parent_binding: DirectedEffectParentBindingV1,
) -> get_directed_effect_parent_readiness

DirectedEffectParentReadinessProjectionV1(
  schema_version,
  workspace,
  task_id,
  execution_attempt,
  parent_binding_id,
  parent_registry_stream_token,
  parent_registry_source_head_seq,
  operation_stream_token,
  operation_source_head_seq,
  operation_count,
  state_counts: tuple[DirectedEffectParentReadinessStateCountV1, ...],
  enforcement: Literal["not_enabled"],
)

DirectedEffectParentReadinessResultV1(
  ok,
  code: "readiness_observed" | DirectedEffectOperationCodeV1,
  projection: DirectedEffectParentReadinessProjectionV1 | None,
  evidence,
)
```

`GetDirectedEffectParentReadinessQueryV1` carries the complete execution
attempt and the complete parent binding. It uses the existing token, positive
integer, and runtime-identity validation conventions. The public service first
performs the existing attempt validation and then delegates to the repository;
it does not construct `TaskRuntimeService`, take a session lock, enroll a
stream, or invoke any maintenance path.

`DirectedEffectParentReadinessStateCountV1` is a frozen pair of an existing
`DirectedEffectOperationStateV1` and a non-negative count. The projection is
frozen and its `state_counts` is a deterministically ordered tuple, containing
one entry for every existing DEO state, including zero counts. `evidence` is a
deep detached mapping. The identities, stream tokens, source heads, operation
count, state counts, schema version, and enforcement literal are immutable
observations from this query. The result succeeds only when
`code="readiness_observed"` and a projection is present; every other code
requires `ok=false` and no projection.

The sole policy literal is `enforcement="not_enabled"`. Neither the query,
projection, result, evidence, nor public export may carry a ready, eligible,
authorized, receipt, close, or terminal verdict. In particular, no field may
encode a boolean or derived label that a caller can reinterpret as such a
verdict. The public operation result remains unchanged; this is a new parent
batch observation contract, not an overload of child-operation results.

#### Repository Read Algorithm

The repository adds one `get_parent_readiness(query)` method and reuses the
existing read path in this exact order:

1. Reuse `_validated_parent_binding(query, require_open=False)` to validate
   the complete attempt, rebuild the parent registry strictly, locate the
   durable binding, and compare every canonical binding field. A historical
   parent is observable; this does not alter a parent state.
2. Reuse `_read_stream(workspace, binding.operation_stream_token,
   max_events=_MAX_OPERATION_EVENTS, stream_kind="operation")`. It remains
   the only FactStream operation read, with `strict_integrity=True`, the
   existing bounded page/head checks, and existing typed FactStream failures.
3. Refactor the existing `_reduce_operation` into one shared operation-stream
   reducer. Both the current single-operation query/mutation flows and the new
   parent observation call that reducer. It parses each transition once,
   preserves the current schema, transition, version, identity, and semantic
   drift checks, and returns immutable per-operation aggregate facts plus the
   operation stream head. No second parser, partial scan, cached snapshot, or
   workspace-wide history scan is permitted.
4. Derive the ordered state counts and immutable projection in memory. The
   method performs no append, enrollment, persistence, lock acquisition,
   snapshot write, or callback.

The scan is `O(N)` time and `O(N)` transient memory for `N` strictly read
operation events, bounded by `_MAX_OPERATION_EVENTS`. Projection construction
is `O(S)` time and space for the fixed existing state set `S`; no retry loop is
introduced. This is bounded by the existing strict reader and adds no disk I/O
other than the existing FactStream query.

#### Error and Fail-Closed Matrix

| Condition | Result | Side effect |
| --- | --- | --- |
| Wrong public query type | `TypeError` at the public boundary | none |
| Invalid query field or identity object | existing constructor `TypeError` or `ValueError` | none |
| Attempt validation failure | exact existing `DirectedEffectOperationCodeV1`, no projection | none |
| Missing, mismatched, or inconsistent parent binding | exact existing parent-binding code, no projection | none |
| Strict FactStream failure, overload, corruption, or unknown schema | exact existing strict-stream code and evidence, no projection | none |
| Illegal transition, version discontinuity, operation identity conflict, or semantic drift during shared reduction | exact existing reducer code and evidence, no projection | none |
| Empty enrolled operation stream | `readiness_observed` with zero operation count and fixed zero state counts | none |
| Unrecognized programming or storage exception | propagate with its causal chain; do not map to success or a generic fallback | none |

There is no recovery, inference, implicit enrollment, stale-cache fallback,
best-effort result, mutation, or authority grant in this bucket.

#### Exact Six-File Write Set

Only these six implementation/test files are authorized after this blueprint:

1. `src/backend/polaris/cells/runtime/task_runtime/public/contracts.py`
2. `src/backend/polaris/cells/runtime/task_runtime/public/service.py`
3. `src/backend/polaris/cells/runtime/task_runtime/public/__init__.py`
4. `src/backend/polaris/cells/runtime/task_runtime/internal/directed_effect_operation.py`
5. `src/backend/polaris/cells/runtime/task_runtime/public/tests/test_directed_effect_operation.py`
6. `src/backend/polaris/cells/runtime/task_runtime/tests/test_directed_effect_operation_guarded_fence.py`

`src/backend/polaris/cells/runtime/task_runtime/internal/service.py` is
explicitly forbidden. No other source, test, Cell metadata, generated pack,
KernelOne, FactStream, Factory, roles, delivery, UI, or Run Ledger file is in
scope.

#### Mechanical Boundary Fence

The paired fence test must statically inspect imports, calls, exported names,
and result/projection field names. It must prove that the new public service
uses only attempt validation and the repository query, and that the repository
read method reaches only `_validated_parent_binding`, `_read_stream`, the one
shared reducer, and in-memory projection construction.

It must reject a direct or indirect call/import/data path from the new query,
projection, result, or export to settlement, terminal admission, mutation,
Factory, roles, delivery, UI, or Run Ledger. The forbidden call set includes
settle methods, terminal-transition methods, all DEO admit/claim/abort methods,
append/enrollment methods, parent admission, and the active-to-inactive
pre-barrier. The data fence rejects result/projection fields and evidence keys
that introduce authority or settlement semantics. Existing 1B pre-barriers may
continue to block independently, but the 1C observation may not feed them.

#### Stop Conditions and Gates

Stop immediately and retain `implementing` if the required result cannot be
expressed without a new state transition, a parent registry write, a receipt or
outcome dependency, a terminal admission dependency, a service-lock change, or
a seventh implementation/test file. Stop as well if strict reconstruction
cannot reuse `_validated_parent_binding`, `_read_stream`, and the one shared
reducer, or if any mechanical fence finds a prohibited connection. Those are
DEO-3 or later design questions, not a 1C exception path.

Implementation may be marked complete only after all of these exact gates pass
from `src/backend` with UTF-8 text handling:

```bash
python -m pytest polaris/cells/runtime/task_runtime/public/tests/test_directed_effect_operation.py polaris/cells/runtime/task_runtime/tests/test_directed_effect_operation_guarded_fence.py
python -m ruff check polaris/cells/runtime/task_runtime/public/contracts.py polaris/cells/runtime/task_runtime/public/service.py polaris/cells/runtime/task_runtime/public/__init__.py polaris/cells/runtime/task_runtime/internal/directed_effect_operation.py polaris/cells/runtime/task_runtime/public/tests/test_directed_effect_operation.py polaris/cells/runtime/task_runtime/tests/test_directed_effect_operation_guarded_fence.py
python -m mypy polaris/cells/runtime/task_runtime/public/contracts.py polaris/cells/runtime/task_runtime/public/service.py polaris/cells/runtime/task_runtime/public/__init__.py polaris/cells/runtime/task_runtime/internal/directed_effect_operation.py
python -m compileall -q polaris/cells/runtime/task_runtime/public polaris/cells/runtime/task_runtime/internal/directed_effect_operation.py
git diff --check
```

The focused tests must cover valid empty and populated operation streams,
historical-parent observation, every strict-stream and reducer failure mapping,
deep immutability, fixed `not_enabled`, and the full mechanical no-path fence.
They must also prove that no operation fact, parent fact, session row, receipt,
or projection persistence is written. Completion must not reclassify DEO-2,
DEO-3, DEO-4, or Bench.

### 10.4 DEO-1C Closure Record (2026-07-15)

**Status:** `closed`. The Section 10.3 design was implemented and reviewed
without widening its six-file implementation/test scope. With DEO-1A and
DEO-1B already closed, this closes the DEO-1 durable fact foundation only.
Directed Effect Operation v1 remains `p0_open` because DEO-2, DEO-3, and DEO-4
remain pending and `not_schedulable`.

#### Final Public Surface

The finalized public contracts are:

- `GetDirectedEffectParentReadinessQueryV1`
- `DirectedEffectParentReadinessStateCountV1`
- `DirectedEffectParentReadinessProjectionV1`
- `DirectedEffectParentReadinessResultV1`
- `get_directed_effect_parent_readiness`

The service is a read-only strict observation of one parent operation stream.
It supports historical `CLOSED` parents, reuses the shared operation reducer,
preserves exact typed fail-closed diagnostics, and returns deeply immutable,
cycle-safe evidence. Successful evidence uses the exact source-head schema.
The only enforcement value is `enforcement="not_enabled"`. The query has no
mutation, settlement, receipt, terminal-admission, Run Ledger, or UI path and
does not grant readiness or close authority.

#### Fresh Closure Evidence

- Final focused two-file suite: `72 passed`.
- Final full TaskRuntime suite: `482 passed in 64.02s`.
- Root Ruff check: passed.
- Root Ruff format check: `6 files formatted`.
- Mypy over the four production files: `0 issues`.
- Compileall: passed.
- `git diff --check`: passed.
- Independent specification review: `CLEAR` after all High findings were
  closed.
- Independent code-quality review: `APPROVED` after two Important findings
  were closed.

The only remaining code-quality finding is non-blocking Minor canonical state
sequence duplication. It does not change the shared-reducer, typed-failure, or
read-only fence evidence and is not a reason to reopen DEO-1C.

#### Limits and Next Bucket

DEO-2 is next, remains `p0_open` and `not_schedulable`, and was not started by
this closure. DEO-3 remains the highest-risk P0 child/terminal close, receipt,
and recovery bucket; DEO-4 also remains pending and `not_schedulable`. Bench
remains `not_schedulable`, and no Bench evidence is claimed because no Bench
was run.

The targeted Cell manifest, README, context pack, and global catalog are
synchronized by the closure task. `generated/descriptor.pack.json` is not
regenerated because its generator exposes only global generation; descriptor
freshness is therefore not claimed by this closure record.
