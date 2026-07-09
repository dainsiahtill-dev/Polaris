# Director Continuation Task-Boundary Plan (2026-07-10)

## Scope

This bucket closes the bench feedback that downstream QA/test files can be
missing because an upstream Director task is misrouted after incomplete
materialization. It does not change cross-file interface triage, CE handoff
authority, or deterministic repair rule ownership.

## Blind-Spot Findings

1. `coverage_matched_but_unplannable` is correct for interface discrepancies,
   but not for a missing file explicitly owned by the current task.
2. Treating current-task missing targets as CE design triage blocks the Director
   retry path that should complete the materialization boundary.
3. Scope expansion remains unsafe. Missing files outside the current task must
   remain deferred to ownership handoff or design replanning.

## Design Invariants

- Current-task target files are Director materialization obligations.
- Scope ownership is authoritative. CE/Director continuation metadata may not
  authorize writes outside declared task scope.
- Interface discrepancies remain fail-closed unless a separate interface
  contract authorizes Director retry.
- Continuation evidence must be explicit and machine-readable:
  `task_boundary_director_continuation_allowed`,
  `task_boundary_continuation_reason`, route, target files, and deferred files.

## Implemented Decisions

1. Materialization-quality boundary summaries are annotated after the runtime
   public boundary returns.
2. If a plan probe says `coverage_matched_but_unplannable`, the adapter checks
   whether artifact-quality evidence is specifically a missing target owned by
   the current task.
3. In-scope missing targets get `director_retry_with_missing_target_context`.
4. Out-of-scope missing targets are projected separately as deferred target
   files and do not grant write authority.

## Verification Notes

- Ruff check, Ruff format check, and `py_compile` passed for the touched files.
- `test_director_adapter_pure.py` is currently collection-blocked in this local
  environment by missing optional `numpy` from the benchmark holographic import
  chain. The added tests are still kept as regression coverage for environments
  with the full optional benchmark dependency set installed.
