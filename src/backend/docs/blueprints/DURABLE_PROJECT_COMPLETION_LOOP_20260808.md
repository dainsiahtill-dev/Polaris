# Durable Project Completion Loop

Status: implementation_active
Owner: Polaris platform maintainers
Date: 2026-08-08
Scope: platform foundation only; generated projects and Factory Bench remain
external test inputs.

## Objective

Polaris must finish one project without a human supervising logs.  Completion
means the generated project has owned artifacts, reproducible environment
preparation, at least one real verifier receipt, and one real entrypoint probe.
It does not mean that an intermediate role returned `ok`, that a task row was
marked resolved, or that a benchmark summary looked green.

The platform must also converge honestly when the first materialization is
incomplete.  Every residual must become one typed diagnostic, one dependency-
ready follow-up task, one bounded attempt, and one receipt-backed revalidation.

## Architectural decision

No new Cell and no second state authority are introduced.

```text
Chief Engineer
  ProjectCompletionContractV1
           |
           | hash-bound compilation
           v
Director TaskExecutionContract / ExecutionEnvelope
           |
           | policy-gated effects + receipts
           v
TaskRuntime + TaskBoundary + Run Ledger + QA + Factory chain
           |                              |
           | owner facts                  | all diagnostics
           +---------------+--------------+
                           v
                  runtime.projection
                  ProjectOutcomeV1
                  sole completion verdict
                           |
                           v
             orchestration.workflow_runtime
             durable convergence cursor only
                           |
                           v
              TaskMarket / TaskRuntime attempts
```

`runtime.projection` is the sole read-side authority that may emit
`completed_verified=True`.  It reads each fact through that fact owner's public
port.  It never writes, schedules, executes a command, or trusts a caller-
supplied success claim.

`orchestration.workflow_runtime` owns only the durable convergence cursor and
transition history.  It does not copy TaskRuntime rows, receipts, Run Ledger
facts, or ProjectOutcome into a second source of truth.

An external supervisor may select which project to run next.  It may not own
same-project residual semantics, fabricate success, or become a Run Ledger
dependency.

## Typed completion contract

The Chief Engineer produces a `ProjectCompletionContractV1` for every runnable
application.  The contract contains:

- exact owned artifact obligations and their semantic roles;
- source-to-runtime entrypoint mappings and executable probes;
- dependency/environment preparation obligations;
- build, test and lint modalities, including explicit required/optional/N/A;
- required test artifacts or an explicit library-only exemption;
- the completion predicate version;
- blueprint/run/workspace identity and a canonical contract hash.

Director compiles this contract into each task execution contract and execution
envelope.  The hash must survive every hop unchanged.  A runnable application
without an entrypoint probe, required verifier, or test obligation is rejected
before provider dispatch.  A library may use N/A only when the CE contract
declares and justifies that product kind.

## Sole outcome authority

The authoritative query binds all six independent axes directly:

1. delivery facts and current-run effect receipts;
2. Factory chain observation;
3. QA/verifier facts;
4. TaskBoundary facts;
5. TaskRuntime convergence facts;
6. Run Ledger closure and evidence policy.

All facts must match workspace, project, run, contract hash and owner identity.
The result may be `completed_verified=True` only when every required axis is
green and every evidence reference comes from the owner adapter.  Caller-
supplied strings remain useful only for the existing unbound candidate API.

Independent states remain visible.  For example, verified disk delivery may
coexist with `task_runtime=not_converged`; the platform reports both and repairs
the control plane.  A TaskBoundary verdict may never overwrite a failed,
pending or in-progress TaskRuntime row.

## Diagnostic and convergence protocol

VerificationGuard evaluates the whole contract and returns every residual in a
stable order.  It does not fail fast.  Each diagnostic contains:

- stable diagnostic id and archetype;
- primary module owner;
- contract obligation and affected target;
- owner evidence refs;
- retry class and allowed next action;
- dependency set;
- deterministic-repair coverage status;
- verifier needed for revalidation.

The workflow runtime then performs this state machine:

```text
READY
  -> ATTEMPT_ACTIVE
  -> SETTLING
  -> OUTCOME_PROJECTED
     -> COMPLETED_VERIFIED
     -> RESIDUALS_CLASSIFIED
        -> NEXT_LEAF_PUBLISHED
        -> ATTEMPT_ACTIVE
        -> MODEL_CEILING | CONTROL_PLANE_BLOCKED | BUDGET_EXHAUSTED
```

Only one dependency-ready leaf is published at a time for a project.  Its
attempt is claimed through TaskMarket/TaskRuntime, executed by the existing
Director authority, settled with effect receipts, and revalidated against the
same completion-contract hash.  Crash recovery resumes from the durable cursor
and re-reads owner facts; it never repeats a committed effect solely because a
workflow checkpoint was lost.

QA and physical-verifier failures are repairing states, not upstream planning
failures.  Missing verifier evidence reruns only that verifier; executable
coverage runs the Director repair kernel; other artifact or verifier failures
atomically reopen the exact owner Director task with the failure receipt and
contract hash attached.  The requeue action is keyed by the convergence action
id and carries a durable TaskMarket receipt, so duplicate wakes cannot consume
another model attempt.  PM and Chief Engineer are never called for ordinary
repair.  Contract contradiction or authority failure stops with a structured
blocker; budget exhaustion becomes `model_ceiling`/`budget_exhausted`, never an
implicit upstream retry and never a fabricated QA pass.

Role-stage recovery follows the same locality rule. A CE output/schema failure
retries `chief_engineer_review` against the committed PM contract and includes
the prior failure in the next final provider request. A Director failure
preserves completed task rows, reopens only unfinished Director work, and
re-enters `director_dispatch`. A QA failure re-enters Director with verifier
receipts. PM is rerun only when its own contract is invalid or explicitly
superseded; downstream failure never restarts the chain from PM. Each local
route has a bounded budget and records an auditable rework history.

## Stage-local recovery assumption register

- Assumption: a settled CE failure leaves its TaskRuntime attempt resumable.
  Evidence: CE failure settlement uses `outcome="suspended"`, whose observable
  task state is pending/resumable.
- Assumption: a failed Director wave can preserve valid work. Evidence:
  TaskRuntime reset supports `preserve_completed=True` and reopens only
  unfinished rows.
- Assumption: failure evidence reaches the retried role. Evidence: router writes
  typed local-rework evidence into Factory metadata; CE portfolio context and
  Director stage context consume that metadata in their next physical request.

Pre-mortem: the most likely unsafe failure is terminal drain deleting TaskRuntime
rows before orchestration chooses local recovery. FactoryRunService therefore
defers drain only for failed CE/Director/QA stages while their synchronous,
bounded local-rework decision is pending. Exhausted retries resume normal
fail-closed terminalization.

Terminal QA-only retry can also begin after the bounded drain has already
completed. Its restoration authority is the frozen TaskRuntime epoch plus the
same-run immutable CE handoff and JobToken. PM targets alone are insufficient:
PM may name a manifest while CE legitimately expands concrete source topology.
Restoration must require exact run/task identity, a generated handoff-ready CE
row, and a run-bound JobToken, then materialize that authority through
`runtime.task_runtime` before claiming repair. It must never infer owner from
disk existence or a verifier path. See ADR-0110.

Verification plan: unit tests prove PM executes once while CE retries; PM/CE
execute once while Director retries; completed Director rows survive; failure
feedback appears in CE retry context; automatic mutation families remain
service-owned; a fresh isolated bench must show stage-local history before this
bucket can close.

## Model ceiling

`model_ceiling` is a structured terminal classification, never substring
matching.  It requires all of the following:

- provider request and required tool surface were valid and readable;
- the completion contract and relevant failure evidence were present;
- execution authority and budget were available;
- no control-plane, environment, provider or deterministic-repair blocker
  remains;
- bounded model attempts for the same diagnostic/contract hash were exhausted;
- the residual semantic class did not improve.

The caller object is only `ModelCeilingCandidateV1`: exact identity plus
ContextOS and owner locators.  A bootstrap adapter queries ContextOS,
roles.kernel, runtime.execution_broker and director.runtime directly; generic
`audit.evidence` receipts are never model-ceiling authority. Workflow-runtime
independently checks request identity/coverage/tool surface, attempt ordinal and
budget, artifact-hash continuity, execution/control/environment/provider/repair
facts, and stable verifier semantics. Missing owner query APIs park as
`CONTROL_PLANE_BLOCKED`. Only workflow-runtime internal authority can seal a
terminal result, and convergence re-queries owners before accepting or replaying
it. Direct construction, `dataclasses.replace`, copy/deepcopy/pickle, malformed
mappings, timeout/control/provider/sandbox failures, and Bench heuristics all
fail closed.

Changing diagnostic class resets neither the global budget nor silently opens a
new module.  It produces a new attribution decision.

## Non-negotiable invariants

1. Owner facts, not disk existence alone, establish delivery.
2. Current-run effect receipt/hash is required for owned mutations.
3. Missing evidence and failed evidence remain disjoint.
4. Aggregate build success cannot hide a required test failure.
5. `Director ok=True` without effects and verifier evidence is not completion.
6. TaskBoundary cannot override TaskRuntime lifecycle.
7. One project has one active convergence cursor and one completion contract
   hash.
8. Workflow checkpoints are cursors, never copied fact authority.
9. Deterministic repair follows the existing Director repair-kernel public
   contract and never invents business stubs.
10. Bench remains an internal probe and never participates in product truth.
11. Every physical role request keeps the final-request context audit required
    by repository governance.
12. `49977/5173` remain reserved for the main instance.

## Delivery buckets

### F0 — Characterization and governance

- Freeze contradiction tests before production edits.
- Record this blueprint, ADR-0100 and VC-20260808.

### F1 — Authoritative ProjectOutcome

- Add direct owner-bound public query/result contracts.
- Bind all remaining axes and remove parallel completion verdicts.
- Preserve the existing unbound pure reducer for advisory callers.

### F2 — Completion contract propagation

- Add CE completion-contract types and canonical hashing.
- Compile/hash-bind into Director contracts/envelopes.
- Add fail-closed preflight validation.

### F3 — Diagnostics and durable convergence

- Return all contract diagnostics from VerificationGuard.
- F3c physical evidence is owner-sealed in `runtime.execution_broker`: artifact
  bytes and verifier input/output are hashed, artifact drift invalidates old
  receipts, and missing evidence remains distinct from failed evidence.
- Verifier dispatch accepts identity only at VerificationGuard. Before spawn,
  broker bootstrap authority re-resolves exact CE command authority plus the
  committed current JobToken/policy. Public runner injection and arbitrary
  caller-selected argv are fail-closed; broker returns effects, never final
  completion verdicts.
- Add workflow-runtime convergence cursor, CAS/replay and one-leaf scheduler.
- Publish/claim/settle through existing TaskMarket/TaskRuntime ports.
- Replace HTTP-router-owned business loops with the workflow service.

### F4 — Structured ceiling and seal

- Replace heuristic ceiling classification.
- Synchronize graph/catalog/descriptors/documentation.
- Verify one fresh isolated project, then three fresh batches spanning at least
  two language families, then march L1-L12 sequentially.

## Acceptance

The first project closes only when one fresh isolated run yields:

- authoritative `ProjectOutcomeV1.completed_verified=True`;
- owned source artifacts with current-run effect receipts;
- environment/dependency command receipt;
- at least one required build/test/lint receipt with no failed modality;
- one successful CLI/Web/API entrypoint receipt;
- readable final provider request snapshots for every physical role call;
- no target-project edit from the platform implementation worktree.

The architecture seals only after three fresh batches across at least two
language families reveal no new general root cause.  L1-L12 then proceeds one
project at a time; failure stays on the same project until its general platform
root cause is closed or a structured model ceiling is reached.
