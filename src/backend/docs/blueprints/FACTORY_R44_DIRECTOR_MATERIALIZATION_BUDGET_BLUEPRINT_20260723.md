# Factory R44 — Director Materialization Budget Blueprint

Status: `R44A_CLOSED`

## Problem

Fresh isolated Bench R43 reached the real Director and produced authoritative
write receipts, but the second materialization wave received a 69-second stage
lease (64-second execution + 5-second settlement).  Its physical `write_file`
completed, while the task could not finish all declared targets before the
Factory barrier.  Factory then reconciled the in-flight child as
`director.execution_barrier_timeout`, blocked its dependent task, and failed
the run.

Two deadline projections disagree:

- `_director_dispatch_timeout_seconds` reserves only the minimum downstream
  quality/QA start budget while multiple owner tasks still require
  materialization.
- `resolve_director_dispatch_admission` always reserves the full configured QA
  allowance and applies the first-materialization minimum only to the first
  ever wave.

The same run therefore looks schedulable to one projection and starved to the
canonical admission projection.

## R44A scope

One bucket only: make the typed deadline admission the single budget truth.

Required invariants:

1. Keep the existing first-materialization versus follow-up execution minimum;
   R43 does not prove that policy is wrong.
2. While more than one materialization wave remains, reserve the typed minimum
   quality/QA start budget, not the full allowance.
3. The final materialization wave retains the full downstream reserve.  No QA
   or safety budget may be silently consumed.
4. Future Director waves remain explicitly reserved; budget conservation stays
   fail-closed.
5. Admission evidence exposes the selected QA reserve and reserve mode.
6. No target-project edits, deterministic business-code fallback, or bypass of
   TaskRuntime / effect-receipt / QA authority.

## TDD proof

- RED: reproduce the R43-shaped later materialization wave and assert it uses
  the existing follow-up execution minimum plus the typed minimum QA reserve.
- RED: prove a final materialization wave retains full QA reserve.
- GREEN: focused deadline-policy and Factory characterization tests.
- Gate: Factory Pipeline suite, Ruff, format, mypy, compileall, diff audit.

## Deferred buckets

- R44B: Director physical tool schema and final-request audit contract.
- R44C: PM artifact-profile misclassification for Go CLI projects.
- Bench remains `not_schedulable` until all R44 buckets and pre-bench gates
  close.

## R44A closure evidence

- RED: two R43-shaped policy tests failed on the missing typed minimum reserve.
- Focused deadline policy: `13 passed`.
- Factory deadline characterization: `4 passed`.
- Factory Pipeline: `1240 passed`, two pre-existing fork deprecation warnings.
- Ruff, format, mypy, compileall, YAML parse, and scoped diff check: pass.
