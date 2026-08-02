# Project Goal Runtime Modular Architecture

Date: 2026-08-02  
Status: GR0_PROJECT_OUTCOME_V1 implemented and independently accepted  
Scope: modular long-running autonomy ownership; this bucket delivers only the
read-only `ProjectOutcomeV1` projection reducer.

## Problem

Polaris needs durable multi-goal autonomy without collapsing project health into
a single success/failure token. Disk artifacts, chain progress, QA verdict,
TaskBoundary, TaskRuntime convergence, and Run Ledger evidence must remain
independent axes. Missing required modalities must never be reported as failed
modalities. Control-plane failure must never erase a separately proven delivery
state. `completed_verified` must fail closed.

## Ownership split

| Cell | Owns | Must not own |
|------|------|--------------|
| `resident.autonomy` | Existing durable Goal state; long-running FSM/scheduling hardening is future work | Outcome reduction semantics |
| `runtime.projection` | Pure read-only `ProjectOutcomeV1` reducer | Writes, scheduling, command execution |
| `runtime.execution_broker` | Background activity lifecycle (future use) | Project outcome truth |
| `factory.pipeline` | PM → Chief Engineer → Director → QA activity executor | Project outcome SSoT |
| `runtime.task_runtime` | Task runtime facts | Outcome projection writes |
| `control_plane.run_ledger` | Run Ledger / evidence / release facts | Outcome projection writes |

Bench remains internal test-only and never becomes product state.

## This bucket (GR0)

`runtime.projection` exposes:

- Query: `ProjectOutcomeQueryV1`
- Result: `ProjectOutcomeV1`
- Evidence refs: `ProjectOutcomeEvidenceRefsV1` (one normalized tuple per axis)
- Error: `ProjectOutcomeValidationV1Error`
- Service: `query_project_outcome`
- Internal pure reducer: `internal/project_outcome.py`

### Authority boundary (critical)

This bucket is an **unbound derived projection only**:

- Public query inputs and evidence refs are caller-supplied claims; GR0 never
  treats them as owner facts.
- The reducer is **not** the owner-fact gathering adapter.
- This bucket can emit only `completion_candidate=True`; `authority_bound` and
  `completed_verified` remain false by construction.
- Future adapter wiring is required to gather owner facts into
  an authority-bound path without trusting caller-supplied refs.
- No persistence, no scheduling, no command execution, no second SSoT.

### Independent axes

- **delivery**: `unknown` / `missing` / `present_unverified` / `verified`
- **chain**: `not_started` / `active` / `incomplete` / `completed` / `control_plane_failed`
- **qa**: `not_run` / `pending` / `failed` / `passed`
- **task_boundary**: `unknown` / `failed` / `passed`
- **task_runtime**: `not_converged` / `converged`
- **run_ledger**: `not_closed` / `closed`
- **missing_required_modalities** vs **failed_required_modalities** (distinct, disjoint)

### Completion rule

`completion_candidate=True` only when all of the following caller claims hold:

1. delivery == verified
2. chain == completed
3. qa == passed
4. task_boundary == passed
5. task_runtime == converged
6. run_ledger == closed
7. missing_required_modalities empty
8. failed_required_modalities empty
9. every `ProjectOutcomeEvidenceRefsV1` axis tuple non-empty
10. task_count > 0 and completed_task_count == task_count

Disk artifacts alone (`present_unverified` / `missing`) cannot pass. Unknown
axes cannot pass. Empty per-axis evidence refs block as
`evidence_refs.<axis>`. Chain/control-plane failure does not rewrite delivery.
These checks do not authenticate refs. GR0 always returns
`authority_bound=False` and `completed_verified=False`; only the future
owner-fact adapter may establish authoritative completion.

### Advisory disposition

`recommended_disposition` is a deterministic enum for observers only. It must
not execute commands, schedule work, or become a second authority over Goal or
Run Ledger facts. A fully green unbound claim yields
`await_authority_binding`, never `complete`.

### Purity

The reducer is pure and deterministic:

- no filesystem, database, network, subprocess, environment, clock, or LLM
- typed enums/dataclasses only; no free-text parsing or substring heuristics
- token normalization is sorted and deduplicated
- counts must be exact `int` (bool/str/float rejected)
- evidence refs/reasons preserved structurally, not re-authored as truth
- public results revalidate reducer invariants and reject direct contradictory
  construction

## Out of scope for this bucket

- Goal FSM / scheduler hardening in `resident.autonomy` (existing Goal owner)
- Background activity lifecycle in `runtime.execution_broker`
- Factory pipeline changes
- Task runtime or Run Ledger implementation changes
- Owner-fact gathering adapter (future)
- Bench schemas, reports, or product vocabulary
- HTTP/WS transport wiring (can consume the reducer later)

## Verification

See `docs/governance/templates/verification-cards/vc-20260802-project-outcome-v1.yaml`
and focused tests in `polaris/cells/runtime/projection/tests/test_project_outcome.py`.
