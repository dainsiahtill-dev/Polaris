# ADR-0099: Project Outcome Projection and Goal Runtime Ownership

- 状态: Accepted (GR0 unbound projection boundary)
- 日期: 2026-08-02
- 关联: ADR-0096 (Bench internal-only); ADR-0097 (execution fact authority);
  Run Ledger; TaskRuntime; resident.autonomy (existing Goal owner)

> GR0 is accepted only for the unbound pure-projection boundary. Authoritative
> owner-fact binding remains a separate GR1 decision and implementation bucket.

## 背景

Long-running autonomous delivery needs a stable project outcome view. Historical
paths collapse delivery, chain progress, QA, TaskBoundary, TaskRuntime, and
Run Ledger evidence into one status token. That erases independent evidence:

- disk presence is not verification
- missing required modalities are not the same as failed required modalities
- control-plane/chain failure can co-exist with independently verified delivery
- observers need advisory disposition without becoming schedulers
- untyped flat evidence tuples allow green axes without per-axis proof refs

Ownership must stay modular: fact owners keep write authority; projection only
reduces typed facts into a read-only outcome contract.

## 决策

### 1. `runtime.projection` owns only the pure `ProjectOutcomeV1` reducer

`ProjectOutcomeQueryV1` → `query_project_outcome` → `ProjectOutcomeV1` is a
deterministic, side-effect-free reduction. It must not open files, call the
network, spawn processes, read the environment, use wall-clock time, or invoke
an LLM. It must not execute commands, persist state, or schedule goals.

This reducer is **not** the owner-fact gathering adapter. Public inputs are
untrusted caller claims. This bucket alone cannot establish an authoritative
platform outcome: it emits at most `completion_candidate=True` while
`authority_bound=False` and `completed_verified=False`. Future adapter wiring
must read sole owners directly instead of trusting supplied refs.

### 2. Independent axes are mandatory

Outcome contracts expose independent axes rather than a single success token:

- delivery: unknown / missing / present_unverified / verified
- chain: not_started / active / incomplete / completed / control_plane_failed
- qa: not_run / pending / failed / passed
- task_boundary: unknown / failed / passed
- task_runtime: not_converged / converged
- run_ledger: not_closed / closed
- missing_required_modalities (disjoint from failed_required_modalities)

### 3. Per-axis evidence refs are required for an unbound candidate

`ProjectOutcomeEvidenceRefsV1` carries one normalized (sorted, deduplicated)
tuple per required axis: delivery, chain, qa, task_boundary, task_runtime,
run_ledger. `completion_candidate` requires every axis to pass **and** every
corresponding evidence-ref tuple to be non-empty. Missing per-axis refs appear
as deterministic blocking axes `evidence_refs.<axis>`. Evidence refs are
untrusted structural claims: arbitrary non-empty strings can never produce
`completed_verified=True`.

### 4. Task counts participate in completion

Completion requires `task_count > 0` and `completed_task_count == task_count`.
`completed_task_count > task_count` is a typed validation failure. Counts must
be exact `int` (bool, str, float, and other coercions rejected).

### 5. Fact owners remain sole writers

| Concern | Sole fact owner |
|---------|-----------------|
| Task rows / convergence | `runtime.task_runtime` |
| Release / evidence / receipts | `control_plane.run_ledger` |
| Durable Goal state | `resident.autonomy` (existing owner; FSM hardening remains future work) |
| Activity execution chain | `factory.pipeline` |
| Background process lifecycle | `runtime.execution_broker` |

### 6. Advisory disposition is not authority

`recommended_disposition` is a typed enum for UI/AGI advisory consumption only.
It cannot authorize writes, register rules, or drive Goal scheduling.

### 7. Bench vocabulary stays out of product contracts

No Bench terms in production `ProjectOutcome` contracts, reducer code, or
focused tests. Bench remains internal test mode per ADR-0096.

### 8. Validation is fail-closed and typed

Empty run identity, non-exact-int counts, completed > total, overlapping
missing/failed modalities, and raw non-enum axis values raise
`ProjectOutcomeValidationV1Error` with stable `error_code`. Invalid inputs
never produce `completed_verified`. The public service rejects non-exact query
types, run identity rejects non-string coercion, and `ProjectOutcomeV1`
revalidates all candidate/disposition/blocker invariants on direct construction.

## 后果

### 正面

- Modular autonomy can grow Goal/scheduling cells without rewriting outcome math
- Caller-supplied refs cannot greenwash an authoritative completion
- Missing vs failed modalities stay auditable and distinct
- Control-plane failure no longer overwrites delivery proof

### 负面

- Callers may only obtain an unbound completion candidate in GR0
- Transport/adapter layers still need a later owner-fact gathering path

## 验收

- Focused tests cover unbound candidacy, authoritative completion rejection,
  direct-result invariant enforcement, per-axis evidence, task counts, typed
  validation, modality overlap, dedup normalization, raw-string enum rejection,
  and canonical public package imports
- ruff / mypy / focused pytest gates pass on allowed projection files
- Independent review is CLEAR; authoritative owner-fact binding remains GR1
- No Factory Bench, Provider, or cross-Cell internal imports
