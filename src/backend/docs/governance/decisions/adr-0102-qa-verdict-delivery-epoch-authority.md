# ADR-0102: QA Verdict Authority Is Scoped to a Director Delivery Epoch

- Status: Accepted; implemented and live-verified
- Date: 2026-08-12
- Related: ADR-0097, ADR-0100, ADR-0101

## Context

Append-only QA verdicts survive same-task Director retries. A newer Director run can settle a new `completed_verified` TaskBoundary without deleting history. Treating an older QA verdict as globally current creates split authority: disk, artifact receipts, TaskRuntime, and TaskBoundary describe the new delivery, while QA still judges the old one.

## Decision

1. Canonical QA authority is scoped by `task_id` plus Director `run_id`.
2. Run Ledger retains every QA verdict but marks verdicts from older delivery epochs ineffective when a newer canonical TaskBoundary exists for the same task.
3. Gate projection exposes `task_id` and `run_id` so consumers can audit freshness.
4. Factory derives QA authority only from `effective_gates`; raw `gates` remain history, never authorization.
5. Director-only retry invalidates QA applicability, not PM/CE authority. Quality-only retry commits a fresh QA verdict and reruns only affected verifiers.
6. No timestamp inference, target-path identity guess, or destructive history rewrite is allowed.

## Rejected alternatives

1. Reuse any existing QA verdict: reproduces stale-failure self-lock.
2. Delete old QA events: violates append-only evidence.
3. Restart PM/CE: wastes tokens and discards valid authority.
4. Mark QA optional: hides real verifier failures and weakens evidence.
5. Let Factory patch generated project files: violates meta-platform boundary.

## Consequences

- Same delivery replay stays idempotent.
- New Director delivery must receive new QA judgement.
- Historical QA failures remain auditable without poisoning future epochs.
- Run Ledger and Factory gain regression coverage for epoch freshness.

## Verification

- Run Ledger: `129 passed`.
- Factory execution-control SSoT: `38 passed`.
- QA verdict contracts: `43 passed`.
- L1-02 r48 QA-only retry: Factory `completed`; current QA verdict and TaskBoundary both bind `TASK-2` / `director-421e2fad985b`; no PM/CE/Director restart.
- Physical verifier evidence: build/test/CLI entrypoint exit `0`, Node tests `22/22`, platform acceptance tests `16/16`.
