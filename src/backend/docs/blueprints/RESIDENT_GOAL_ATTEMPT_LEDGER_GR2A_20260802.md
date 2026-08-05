# GR2A — Resident Goal Attempt Ledger

Status: `accepted`

## Scope

`resident.autonomy` owns a durable, strict Goal/Attempt state machine. Attempt
history lives at
`<workspace>/.polaris/meta/resident/goals/<goal_id>/attempts.v1.jsonl`. Public services do
not call providers, models, Factory, Director, QA, Projection, or the execution
broker.

## Goal lifecycle

Allowed transitions: `PENDING -> APPROVED|REJECTED`, `APPROVED -> MATERIALIZED`,
`MATERIALIZED -> ARCHIVED`, and `REJECTED -> ARCHIVED`. Same-state transition is
idempotent. `ARCHIVED` is terminal. CAS mismatch fails with
`goal_revision_conflict`; illegal movement fails with `invalid_goal_transition`.
Unknown persisted states fail closed.

## Execution and attempts

Execution states are `READY`, `ACTIVE`, `RETRY_ELIGIBLE`,
`BLOCKED_NO_PROGRESS`, `AWAITING_OUTCOME_BINDING`, `EXHAUSTED`, and `CANCELLED`.
`COMPLETED_VERIFIED` is reserved and rejected in GR2A. Attempt statuses are
`ACTIVE`, `SUCCEEDED`, `FAILED`, `CANCELLED`, and `BLOCKED_NO_PROGRESS`.

Goal lifecycle mutation holds `goals.json.lock` across strict load, CAS,
transition, and atomic write, including compatibility calls that omit an
explicit expected revision. Start, observe, and settle commands append hash-chained, revisioned UTF-8 JSONL
records with `fsync`. Idempotency keys bind semantic payloads: exact replay
returns the prior receipt; changed payload fails closed. No-progress streak is
durable, resets when fingerprint changes, and blocks at its configured limit.
Failed attempts become retry eligible until budget exhaustion. A succeeded
attempt awaits an independent outcome binding.

## Read boundary

`QueryResidentGoalExecutionV1` performs no directory creation, append, session
creation, effect, or provider call. Missing stream projects `READY`; corrupt
encoding, JSON, schema, revision, or hash fails with
`goal_attempt_stream_corrupt`.
