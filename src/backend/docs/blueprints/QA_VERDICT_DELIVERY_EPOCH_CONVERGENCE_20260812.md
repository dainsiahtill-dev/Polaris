# QA Verdict Delivery-Epoch Convergence

Status: implemented and live-verified  
Date: 2026-08-12  
Classification: structural

## Problem

Factory can re-execute one Director stage while preserving PM/Chief Engineer authority. A prior QA verdict then remains in the append-only Run Ledger. Current projection and Factory evaluation treat any historical `qa_verdict` as current, even when its `run_id` differs from the latest `completed_verified` TaskBoundary for the same task. A stale FAIL therefore blocks a newly verified delivery epoch and prevents a fresh QA commit.

## Architecture

```text
Director epoch A TaskBoundary + QA FAIL
                    |
Director-only retry |
                    v
Director epoch B TaskBoundary completed_verified
                    |
Run Ledger projects QA applicability by (task_id, Director run_id)
                    |
stale epoch-A QA becomes historical, not effective
                    |
Factory sees qa_verdict_missing and commits fresh epoch-B QA report
                    |
epoch-B QA PASS + current TaskBoundary authorize completion
```

## Module responsibilities

- `control_plane.run_ledger`: preserve all historical gates, but mark a QA verdict effective only for the latest canonical TaskBoundary epoch of its task. Expose `task_id` and `run_id` on gate projections.
- `factory.pipeline`: consume `effective_gates`, never raw historical `gates`, when deriving canonical QA authority.
- `qa.audit_verdict`: keep append-only evidence and final verdict commits unchanged. A new delivery epoch gets a new verdict fact; it does not mutate history.

## Invariants

1. QA PASS/FAIL applies only to the exact `(task_id, Director run_id)` delivery it audited.
2. Replaying the same delivery epoch remains idempotent and may reuse its verdict.
3. A newer TaskBoundary makes an older QA verdict historical; it cannot authorize or block the newer delivery.
4. Missing QA epoch identity remains legacy-compatible; no identity is invented.
5. PM/CE never rerun for this ordinary Director/QA residual.

## Verification

- Unit: stale QA FAIL becomes ineffective after a newer same-task TaskBoundary.
- Unit: fresh QA PASS for the newer epoch is the only effective QA verdict.
- Unit: Factory canonical authority reads `effective_gates` and reports `qa_verdict_missing` before fresh QA commit.
- Regression: same-epoch QA verdict remains reusable.
- Live: retry only the quality stage of L1-02 r48; no PM/CE/Director call; require `COMPLETED_VERIFIED` or a newly isolated downstream defect.

## Closure evidence

- Unit/regression: Run Ledger `129 passed`; Factory SSoT `38 passed`; QA contracts `43 passed` (`210 passed` total).
- Live: `factory_0dcb1e13baa7` received only `retry_phase=qa_gate`; PM, CE, and Director remained completed.
- Fresh QA verdict and latest TaskBoundary both bind `TASK-2` / `director-421e2fad985b`.
- Factory reached `status=completed`, `phase=completed`, `canonical_authorized=true`.
- Physical checks: `npm run build` exit `0`; `npm test` exit `0` with `22/22`; `npm run start` exit `0` with a real CLI report; platform acceptance `16/16`; delivery depth passed.
- Durable archive: `~/.polaris/audit_archives/unattended-completion-20260812/r48/`.
