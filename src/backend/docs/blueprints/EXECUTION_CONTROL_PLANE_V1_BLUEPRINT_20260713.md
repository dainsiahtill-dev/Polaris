# Execution Control Plane v1 Blueprint

## 1. Objective

Polaris must finish a fresh project through multiple Director turns, tool
batches, timeout or cancellation boundaries, task settlement, QA, and final
verification without deriving state from prompt text or process-local memory.

The acceptance condition is an isolated bench run whose authoritative state is
`COMPLETED_VERIFIED`. Component tests are necessary but not sufficient.

## 2. Authority Topology

```text
Provider request and response
        |
        v
ToolCallLifecycleReceipt + effect receipts
        |
        v
TaskRuntime execution FactStream       runtime.v2 / NATS
        |                              (wake-up only)
        +-------------------------------> Factory settlement consumer
                                            |
                                            v
                                  Factory settlement journal
                                            |
                                            v
                                  Run Ledger settlement barrier
                                            |
                                            v
                                  Factory lease release
                                            |
                                            v
                          TaskBoundary / QA / bench read projections
```

Authority rules:

1. TaskRuntime execution FactStream is the source of truth for child execution
   terminal transitions.
2. Tool lifecycle and effect receipts are the source of truth for materialized
   effects. A provider response is never an effect.
3. Run Ledger owns evidence closure. Existing failed evidence is distinct from
   missing evidence.
4. Factory admission owns workspace authority and fencing tokens.
5. NATS and runtime.v2 are delivery accelerators, not authority. Lost delivery
   is repaired by replaying FactStream.
6. TaskBoard, session JSON, API status, UI, QA, and bench are projections only.

## 3. Terminal Transition Contract

Each TaskRuntime terminal transition has one persisted `transition_id` and one
FactStream `event_id`. Retries reuse both through an idempotency key derived from
workspace, factory run, task, session, and transition id. Non-terminal facts
remain unique.

The terminal fact commit happens before runtime.v2 publication. Publication
uses the committed fact event id. Duplicate wakes are harmless; missing wakes
are recovered by startup replay or the next wake.

## 4. Durable Settlement Consumer

The Factory consumer is workspace-scoped and has one lifecycle owner per backend
instance. It performs these steps:

1. On startup, read the settlement checkpoint and replay unseen TaskRuntime
   execution facts.
2. On each runtime.v2 wake, replay all unseen facts rather than trusting message
   payload completeness.
3. For each terminal fact, append a `pending` settlement journal record using a
   deterministic idempotency key.
4. Query TaskRuntime child-session settlement and the Run Ledger settlement
   barrier.
5. Under the Factory run lock and fencing claim, either release the lease and
   append `applied`, retain `pending` with blockers, or append `deadletter` for
   non-retryable contract violations.
6. Advance checkpoint only after a durable journal outcome exists.

No polling loop, elapsed-time completion inference, or process-local task map is
allowed. The temporary `_terminal_settlement_tasks` monitor must be deleted at
cutover.

## 5. Run Ledger Settlement Barrier

The no-wait barrier returns a typed snapshot containing:

- `closed`
- barrier hash
- missing required modalities
- failed required modalities
- open tool lifecycle count
- open effect receipt count
- evidence references
- blockers

`closed` means all required evidence is present and all lifecycle/effect records
are terminal. It does not mean QA passed. Failed required evidence remains a
closed, failed fact and must reach QA classification without being relabelled as
missing evidence.

Factory cannot reset TaskRuntime records or release workspace authority while
the barrier is open.

## 6. Deadline and Cancellation

Factory uses two barriers:

- Admission barrier: do not start a new LLM turn when remaining deadline cannot
  cover the resolved turn budget.
- Dispatch barrier: once tool dispatch starts, cancellation cannot invalidate
  the session until the batch produces effect receipts or an explicit barrier
  timeout fact.

Cancellation, timeout, and settlement are persisted facts. They are not inferred
from missing files or wall-clock observations in bench.

## 7. Failure Taxonomy

Required classifications:

- `execution_control_plane/tool_dispatch_failed`
- `execution_control_plane/required_tool_text_fallback_not_dispatched`
- `task_boundary/dependency_not_unlocked`
- `task_boundary/incomplete_materialization`
- `task_boundary/missing_entrypoint_target`
- `implementation_defect/compiler_or_test_failure`
- `model_provider/model_provider_timeout`

QA and bench preserve `failure_class`, `responsible_layer`, and evidence refs.
Downstream missing tests or entrypoints cannot overwrite an upstream platform or
dependency blocker.

## 8. Cutover Invariants

1. One terminal transition produces one authoritative FactStream event.
2. Duplicate event delivery causes no duplicate release or reset.
3. A stale fencing token cannot settle a newer run.
4. Backend restart replays pending settlement without process-local state.
5. No Factory code writes or repairs target-project business files.
6. No language repair handles missing targets, blocked dependencies, or dropped
   tool dispatch.
7. GET/status/list APIs remain mutation-free.
8. Public Cell APIs are used across Cell boundaries; internal modules never leak
   into bootstrap, delivery, QA, or bench.

## 9. Verification Sequence

1. Contract, idempotency, fencing, restart replay, and barrier unit tests.
2. Factory, TaskRuntime, Run Ledger, QA, and architecture boundary tests.
3. Ruff, format check, and strict mypy for changed modules.
4. Fresh isolated L1-01 bench from an empty work directory.
5. Evidence audit from final provider request through QA verdict.

The migration is complete only when the isolated project reaches
`COMPLETED_VERIFIED`. A failure must identify the exact broken edge in the
authority topology above.

## 10. WS2 Settlement Closure Audit (2026-07-14)

**Status: open.** This is a settlement-closure implementation blueprint, not a
completion claim. The authority, completion rule, and isolated-run requirement
in this document remain unchanged.

### 10.1 Audit Record

The following targeted gates were verified green on 2026-07-14:

- TaskRuntime: 348
- Roles Runtime: 682
- Factory CE: 20
- architecture fence: 141
- Director closure: 59
- Ruff, format, mypy, and `git diff --check`

These results establish a regression baseline only. They do not replace a
production caller audit or prove that a terminal Phase A decision will cause
Phase B settlement after a crash.

Independent production-path audit findings:

| Severity | Finding | Decision |
| --- | --- | --- |
| High | The PM delivery CLI TaskBoard path still calls deleted `complete_execution`, `fail_execution`, and `suspend_execution` transitions. | Migrate its caller before any recovery work. |
| High | `roles.adapters` Director execution and `roles.kernel` transaction-factory heartbeats pass only `session_id`; they do not atomically propagate `renewed_identity`. | Establish one canonical heartbeat identity before relying on it for settlement safety. |
| High | After a Phase A terminal winner is durable, a crash has no production pending-settlement recovery producer that automatically replays Phase B. | Add bounded startup/maintenance recovery and prove four crash points. |
| Medium | Existing fences do not mechanically prove typed caller use, public-signature conformance, or `renewed_identity` propagation for every heartbeat. | Extend the architecture fence in the owning implementation buckets. |
| Low | FactStream idempotency append is O(N). | Track as a later performance ledger item; it is not a current correctness blocker. |

### 10.2 Authority and Invariants

1. TaskRuntime terminal facts, the Factory settlement journal, and the Run
   Ledger barrier remain the only settlement authorities. TaskBoard, PM CLI,
   Director metadata, session JSON, UI, and bench remain projections.
2. A caller may request a transition only through the current TaskRuntime public
   contract. Retired TaskBoard mutators and compatibility fallbacks are not
   valid production paths.
3. The canonical execution-attempt identity includes the renewed lease identity,
   not only its `session_id`. A successful heartbeat must atomically replace the
   identity used by subsequent guard, transition, and settlement calls.
4. Phase A records the terminal winner durably. Phase B applies the settlement
   side effect under the same idempotency, run-lock, and fencing rules. Any
   durable Phase A state that lacks a terminal Phase B outcome is recoverable
   work, never evidence of completion.
5. Recovery is bounded and event/replay driven: startup and explicit maintenance
   run one finite replay over durable pending work. It must not introduce a
   timer fetch loop, polling fallback, or a second settlement authority.
6. A failed migration, malformed identity, stale fence, unresolved barrier, or
   recovery ambiguity fails closed and leaves durable evidence for retry or
   dead-letter classification. It must not mutate TaskBoard state as a fallback.

### 10.3 Ordered, Mutually Exclusive Write Buckets

Only one bucket may own code changes at a time. A bucket may modify only its
listed write scope plus its directly paired tests and architecture fence. Later
buckets consume the prior bucket's public contract; they must not reopen or
duplicate an earlier implementation.

#### WS2-A: PM Caller Migration and Integration Fence

**Write scope:**
`src/backend/polaris/delivery/cli/pm/engine/taskboard.py::_finalize_taskboard_runtime_entry`,
the PM delivery CLI integration boundary, and
`src/backend/polaris/tests/architecture/test_task_runtime_taskboard_boundary_fence.py`.

**Input contract:** a PM TaskBoard outcome must contain the TaskRuntime public
transition input required for the current execution attempt. The migrated caller
may not synthesize a direct TaskBoard terminal write.

**Output contract:** exactly one public TaskRuntime transition request with a
typed outcome and durable transition evidence; PM receives only the returned
projection/result.

**Failure semantics:** missing attempt identity, unavailable public service, or
rejected transition is fail-closed, records the integration failure, and does
not call a retired method or alter the TaskBoard terminal state.

**Acceptance commands:**

```bash
PYTHONPATH=src/backend python -m pytest src/backend/polaris/tests/architecture/test_task_runtime_taskboard_boundary_fence.py src/backend/polaris/cells/runtime/task_runtime/tests/test_service.py -q
ruff check src/backend/polaris/delivery/cli/pm/engine/taskboard.py src/backend/polaris/tests/architecture/test_task_runtime_taskboard_boundary_fence.py
ruff format --check src/backend/polaris/delivery/cli/pm/engine/taskboard.py src/backend/polaris/tests/architecture/test_task_runtime_taskboard_boundary_fence.py
mypy src/backend/polaris/delivery/cli/pm/engine/taskboard.py
```

**Rollback boundary:** revert only the migrated caller and its paired fence
assertions. Do not restore deleted TaskBoard mutators or create a PM-to-Director
bypass.

#### WS2-B: Canonical Execution-Attempt Authority Handle

**Reviewer revision, 2026-07-14:** the existing
`CanonicalTaskRuntimeExecutionAttemptHolder` is a useful local guard, but it is
owned by `roles.kernel.internal`, is separately used by Director and transaction
factory code, and does not serialize terminal settlement with its heartbeat
renewal. It is therefore not the cross-Cell execution-attempt authority. The
single long-term design is a TaskRuntime-public
`TaskRuntimeExecutionAttemptLeaseHandleV1` (exact name subject to the public
contract review). It is a mutable, process-local derived capability/projection
of a claimed attempt, not durable SSoT. Durable identity, lease, terminal state,
and replay truth remain TaskRuntime facts. Every heartbeat and settlement must
return through TaskRuntime public validation.

The handle owns one bounded lock for one attempt. Under that same lock,
`heartbeat()` validates the current identity through TaskRuntime public,
requires a successful typed verdict with `renewed_identity`, verifies unchanged
attempt binding, and atomically replaces the handle identity. `settle()` uses
the same lock, snapshots that current identity, requests the terminal transition
through TaskRuntime public, and closes the handle only after a terminal winner
or a typed already-terminal replay verdict. A closed handle rejects later
heartbeat and replay requests with typed verdicts; callers must not infer success
from a missing exception, stale metadata, or a raw `session_id`.

WS2-B is four strictly ordered, mutually exclusive sub-buckets. A later bucket
must consume the earlier public contract without recreating its own holder,
renewal path, lock, or terminal protocol.

##### WS2-B1: TaskRuntime Public Authority Handle

**Write scope:**
`src/backend/polaris/cells/runtime/task_runtime/public/contracts.py`,
`src/backend/polaris/cells/runtime/task_runtime/public/service.py`, the
TaskRuntime internal implementation needed solely by that public contract, and
TaskRuntime public/internal concurrency, failure, and state-machine tests.

**Contract:** TaskRuntime public creates the handle only from a validated
`TaskRuntimeExecutionAttemptIdentityV1`. The public API exposes the current
identity and typed `heartbeat` and `settle` operations. It retains exactly one
bounded per-handle lock across an in-flight heartbeat or terminal settlement;
successful heartbeat replaces the identity atomically before lock release. The
handle is explicitly non-serializable/non-durable and cannot be treated as a new
source of truth.

**Failure semantics:** malformed identity, workspace/attempt/fence mismatch,
missing `renewed_identity`, lock timeout, expired lease, and TaskRuntime
rejection produce a typed non-success verdict and leave the prior identity
unchanged. A completed terminal settlement closes the handle. A later heartbeat
or replay returns a typed closed/terminal-replay verdict and never renews or
reopens the attempt.

**Acceptance:** focused TaskRuntime tests prove concurrent heartbeat versus
terminal settlement has one ordered winner; identity replacement is atomic; lock
timeout and failed heartbeat preserve identity; terminal close rejects
heartbeat/replay; and the handle cannot be persisted or used as durable SSoT.

```bash
PYTHONPATH=src/backend python -m pytest src/backend/polaris/cells/runtime/task_runtime/public/tests src/backend/polaris/cells/runtime/task_runtime/tests/test_service.py src/backend/polaris/cells/runtime/task_runtime/tests/test_execution_attempt_settlement.py -q
ruff check src/backend/polaris/cells/runtime/task_runtime/public src/backend/polaris/cells/runtime/task_runtime/internal/service.py
ruff format --check src/backend/polaris/cells/runtime/task_runtime/public src/backend/polaris/cells/runtime/task_runtime/internal/service.py
mypy src/backend/polaris/cells/runtime/task_runtime/public src/backend/polaris/cells/runtime/task_runtime/internal/service.py
```

**Rollback boundary:** revert only the new TaskRuntime public handle and its
paired tests. Do not restore a public `session_id`-only heartbeat or move durable
attempt state outside TaskRuntime.

##### WS2-B2: Director and Kernel Consumer Migration

**Write scope:**
`src/backend/polaris/cells/roles/adapters/internal/director/execute_method.py`,
`src/backend/polaris/cells/roles/kernel/internal/kernel/transaction_factory.py`,
their direct tests, and only the TaskRuntime public imports/contracts required to
consume B1.

**Contract:** Director claims or receives one TaskRuntime-public handle and
passes that same handle to the transaction guard. Tool guarding, background
heartbeat, and terminal settlement operate through the handle; neither consumer
owns an identity holder, lock, raw heartbeat call, or terminal identity snapshot.

**Failure semantics:** missing handle, stale/closed handle, non-success
heartbeat verdict, or terminal-race verdict blocks guarded tool execution and
returns the typed TaskRuntime reason. No fallback may reconstruct an identity,
call `heartbeat_execution`, or make a direct `roles.kernel.internal` authority
import from `roles.adapters`.

**Acceptance:** Director/tool-guard tests prove a terminal settle racing a
background heartbeat has one serialized outcome, no post-close guarded tool is
executed, and the transaction factory sees the atomically renewed identity.

```bash
PYTHONPATH=src/backend python -m pytest src/backend/polaris/cells/roles/kernel/internal/kernel/tests/test_transaction_turn_identity.py src/backend/polaris/cells/roles/adapters/tests/test_director_adapter_pure.py src/backend/polaris/cells/runtime/task_runtime/tests/test_execution_attempt_settlement.py -q
ruff check src/backend/polaris/cells/roles/adapters/internal/director/execute_method.py src/backend/polaris/cells/roles/kernel/internal/kernel/transaction_factory.py
ruff format --check src/backend/polaris/cells/roles/adapters/internal/director/execute_method.py src/backend/polaris/cells/roles/kernel/internal/kernel/transaction_factory.py
mypy src/backend/polaris/cells/roles/adapters/internal/director/execute_method.py src/backend/polaris/cells/roles/kernel/internal/kernel/transaction_factory.py
```

**Rollback boundary:** revert the two migrated consumers and their direct tests
as one bucket, retaining B1. Do not reintroduce a roles.kernel-owned canonical
holder or a direct adapter-to-kernel internal dependency.

##### WS2-B3: roles.runtime and CLI Entry-Point Migration

**Write scope:**
`src/backend/polaris/cells/roles/runtime/public/service.py`,
`src/backend/polaris/cells/roles/runtime/public/cli_runner.py`, their public
contracts/tests, and no unrelated role or delivery Cell.

**Contract:** `ExecuteRoleTask` and CLI non-stream execution claim one
TaskRuntime-public handle, use it for all renewal and terminal settlement, and
discard it after the typed terminal result. Streaming execution must either use
the same handle throughout its task lifetime or reject task execution fail-closed
before provider/tool work with an explicit typed
`execution_attempt_handle_required` verdict.

**Failure semantics:** task execution without a valid handle, stream ownership
that cannot retain the handle, heartbeat failure, or terminal settlement failure
is non-success and is projected with the exact typed reason. The CLI must not
settle with the original claimed identity after a renewal and must not silently
convert a handle failure into a role result.

**Acceptance:** public service and CLI tests cover non-stream claim -> run ->
heartbeat -> settle with renewed identity, exception settlement, and both stream
branches (same-handle success or explicit fail-closed rejection).

```bash
PYTHONPATH=src/backend python -m pytest src/backend/polaris/cells/roles/runtime/public/tests src/backend/polaris/cells/roles/runtime/tests src/backend/polaris/tests/test_director_cli_task_runtime_projection.py -q
ruff check src/backend/polaris/cells/roles/runtime/public/service.py src/backend/polaris/cells/roles/runtime/public/cli_runner.py
ruff format --check src/backend/polaris/cells/roles/runtime/public/service.py src/backend/polaris/cells/roles/runtime/public/cli_runner.py
mypy src/backend/polaris/cells/roles/runtime/public/service.py src/backend/polaris/cells/roles/runtime/public/cli_runner.py
```

**Rollback boundary:** revert only roles.runtime/CLI consumers and their tests,
retaining B1 and B2. Do not restore a caller-owned stale identity or leave a
stream path with unmodelled execution authority.

##### WS2-B4: Legacy Removal and Cross-Repository Fences

**Write scope:** remove legacy external `heartbeat_execution` usage and its
compatibility surface from TaskRuntime; add/update the architecture AST/import
fence under `src/backend/polaris/tests/architecture/`; update only directly
paired TaskRuntime and caller tests.

**Contract:** all external heartbeat and settlement consumers use the
TaskRuntime-public handle. The mechanical fence inspects imports and typed call
shapes across the repository: no `roles.adapters` import may target
`polaris.cells.roles.kernel.internal`; no external raw heartbeat,
`session_id`-only identity, private holder, or bypassed terminal settle is
permitted.

**Failure semantics:** forbidden import, legacy call shape, missing public
handle, or unsupported stream ownership fails the architecture gate closed with
the violating file/symbol. There is no compatibility fallback.

**Acceptance:** AST/import fence tests enumerate all affected caller families,
including Director, transaction factory, roles.runtime service, CLI non-stream,
and stream. A repository-wide targeted suite proves no legacy symbols or
cross-Cell internal imports survive.

```bash
PYTHONPATH=src/backend python -m pytest src/backend/polaris/tests/architecture/test_task_runtime_taskboard_boundary_fence.py src/backend/polaris/tests/architecture -k 'task_runtime or execution_attempt or roles_cell_governance' -q
ruff check src/backend/polaris/cells/runtime/task_runtime src/backend/polaris/cells/roles/adapters src/backend/polaris/cells/roles/kernel src/backend/polaris/cells/roles/runtime
ruff format --check src/backend/polaris/cells/runtime/task_runtime src/backend/polaris/cells/roles/adapters src/backend/polaris/cells/roles/kernel src/backend/polaris/cells/roles/runtime
mypy src/backend/polaris/cells/runtime/task_runtime src/backend/polaris/cells/roles/adapters src/backend/polaris/cells/roles/kernel src/backend/polaris/cells/roles/runtime
```

**Rollback boundary:** revert only the legacy-removal/fence bucket after B1-B3
are verified. Do not restore legacy APIs, session-only callers, or forbidden
cross-Cell internal imports merely to preserve an uncovered entry point.

#### WS2-C: Bounded Recovery for Pending Settlement

**Write scope:**
`src/backend/polaris/cells/factory/pipeline/internal/factory_settlement_consumer.py::start`,
`FactorySettlementConsumer._replay_locked`,
`FactorySettlementConsumer._decide_and_apply`,
the Factory settlement journal/runtime lifecycle owner, and the paired Factory
settlement tests. Application wiring may consume only the Factory public
lifecycle entrypoint.

**Input contract:** a durable Phase A terminal winner and journal state whose
Phase B outcome is absent, pending, or interrupted, bound to its workspace, run,
idempotency key, barrier hash, and fencing token.

**Output contract:** one finite startup or explicit-maintenance replay either
applies Phase B exactly once, records a retryable pending outcome, or records a
typed dead letter. Checkpoint advancement occurs only after that durable outcome.

**Failure semantics:** stale fencing, a closed-invalid barrier, or ambiguous
ownership does not release authority. Recovery retains pending evidence or
dead-letters a non-retryable contract violation and reports the exact blocker.

**Required four crash-point tests:**

1. crash before Phase A persistence: no replayed settlement side effect;
2. crash after Phase A persistence and before the Phase B claim: startup replay
   claims and applies exactly once;
3. crash after the Phase B claim and before its terminal journal outcome:
   recovery replays the pending claim without duplicate release;
4. crash after Phase B outcome persistence and before checkpoint advancement:
   replay is duplicate-safe and advances the checkpoint only after the durable
   outcome is observed.

**Acceptance commands:**

```bash
PYTHONPATH=src/backend python -m pytest src/backend/polaris/cells/factory/pipeline/tests/test_factory_settlement_consumer.py src/backend/polaris/cells/factory/pipeline/tests/test_factory_settlement_runtime.py src/backend/polaris/delivery/tests/test_app_factory_settlement_lifespan.py -q
ruff check src/backend/polaris/cells/factory/pipeline/internal/factory_settlement_consumer.py src/backend/polaris/cells/factory/pipeline/internal/factory_settlement_runtime.py
ruff format --check src/backend/polaris/cells/factory/pipeline/internal/factory_settlement_consumer.py src/backend/polaris/cells/factory/pipeline/internal/factory_settlement_runtime.py
mypy src/backend/polaris/cells/factory/pipeline/internal/factory_settlement_consumer.py src/backend/polaris/cells/factory/pipeline/internal/factory_settlement_runtime.py
```

**Rollback boundary:** revert the recovery producer and its four crash-point
tests as a single bucket. Do not remove existing durable Phase A facts, journal
rows, or checkpoints, and do not replace replay with a timer-based monitor.

### 10.4 Mechanical Proof and Next Action

The WS2-A/B fence must mechanically reject: direct or retired TaskBoard terminal
calls, external `session_id`-only heartbeat calls, absent typed
`renewed_identity` propagation, and imports that bypass the owning public Cell
contract. It must inspect typed caller shape and public signature, not only
string names.

**Next action:** implement WS2-A at
`delivery/cli/pm/engine/taskboard.py::_finalize_taskboard_runtime_entry` and
extend `test_task_runtime_taskboard_boundary_fence.py` to prove that all PM
delivery callers use the current TaskRuntime public transition contract. Do not
use a temporary prompt, bench-only observation, or audit text as a production
source of truth. WS2-B starts only after WS2-A passes; WS2-C starts only after
WS2-B passes.

## 11. Directed Effect Operation v1 Revision (2026-07-14)

`DIRECTED_EFFECT_OPERATION_V1_BLUEPRINT_20260714.md` is the controlling
blueprint for mutation authority and effect-receipt closure. It does not replace
this ECP's TaskRuntime terminal facts, Factory settlement journal, or Run Ledger
barrier; it makes their ownership boundary explicit.

Corrections to the WS2 narrative above:

1. WS2-B1 is complete with TaskRuntime authority evidence of **373 passed** and
   an independent review with **zero findings**. Its old `create` compatibility
   seam remains intentionally open only until WS2-B4/DEO-4 removes it.
2. The historical statement that WS2-B2 was closed is rejected. The audited 38
   `DirectorToolExecutor` construction/injection surfaces are a P0 mutation and
   receipt bypass; B2 is P0 open under DEO-2, not a local holder migration.
3. WS2-C's former "Phase-B recovery missing" finding is superseded/closed for
   this scope. Production `app_factory` lifecycle wiring and Factory
   settlement-consumer crash tests already supply bounded Phase-B replay. Do
   not duplicate it in DEO work.
4. DEO-1 is now the ordered `DEO-1A -> DEO-1B -> DEO-1C` foundation:
   - **1A** adds the KernelOne FactStream public `strict_integrity` selection
     and common `durability=buffered|flush|fsync` capability. DEO selects
     `fsync`; legacy defaults remain unchanged. Each parent batch gets an
     independent irreversible stream token, so replay never scans workspace
     history; snapshots are rebuildable projections, never authorization.
   - **1B** adds the TaskRuntime aggregate, aggregate-version re-read/CAS, and
     rebuildable snapshot. `expected_seq` is only stream-head CAS;
     `expected_version` must re-read the aggregate and validate semantic
     preconditions to reject ABA and semantic drift.
   - **1C** adds a read-only readiness query and architecture fence with
     `enforcement="not_enabled"`. It cannot connect to settle, UI, or Run
     Ledger. DEO-3 is the first bucket allowed to make readiness block terminal
     admission.
5. Strict FactStream parsing treats only a malformed final unterminated record
   as typed torn-tail recovery work; middle corruption and unknown schema in
   strict mode fail closed. DEO-1A performance targets are `M <= 64`, normal
   `<= 320` events, snapshot threshold `512` events or `2 MiB`, tail `<= 32`,
   p95 `<100 ms`, p99 `<500 ms` with four processes, and `<8 MiB` incremental
   memory. They are configurable benchmark evidence, not fragile hard-CI gates.
6. No bench is admissible until all DEO-1A..4 exit gates are evidenced. There is
   no calendar ETA: the only credible ETA is `not schedulable` until the DEO-4
   inventory, crash/cancel evidence, quality gates, and independent audit are
   complete.
