# Resident Autonomy Cell

## Objective
Provide long-running resident autonomy capability, including decision trace,
goal governance, evidence bundling, and improvement loop execution.
GR2A adds strict durable Goal transitions and an independent Attempt ledger;
Attempt success never establishes `COMPLETED_VERIFIED`.

## Boundaries
- Owns resident autonomy runtime internals under `internal/**`.
- Owns resident delivery endpoint `polaris/delivery/http/v2/resident.py`.
- Exposes cross-cell access only via `public/contracts.py`.

## State Ownership
- `runtime/state/resident/*`
- `runtime/resident/*`
- `workspace/meta/resident/goals.json`
- `workspace/meta/resident/goals/<goal_id>/attempts.v1.jsonl`

## Allowed Effects
- `fs.read:workspace/**`
- `fs.read:runtime/**`
- `fs.write:runtime/state/resident/*`
- `fs.write:runtime/events/runtime.events.jsonl`
- `fs.write:workspace/meta/resident/**`
- `process.spawn:resident/*`
- `ws.outbound:resident/*`

## Public Contracts
- `RunResidentCycleCommandV1`
- `RecordResidentEvidenceCommandV1`
- `QueryResidentStatusV1`
- `ResidentCycleCompletedEventV1`
- `ResidentAutonomyResultV1`
- `ResidentAutonomyError`
- `StartResidentGoalAttemptCommandV1`
- `ObserveResidentGoalAttemptCommandV1`
- `SettleResidentGoalAttemptCommandV1`
- `ArchiveResidentGoalCommandV1`
- `QueryResidentGoalExecutionV1`
- `ResidentGoalAttemptReceiptV1`
- `ResidentGoalExecutionV1`
- `ResidentGoalLifecycleErrorV1`

## Goal/Attempt invariants
- Existing `goals.json` remains sole Goal lifecycle state source.
- Attempt stream uses strict UTF-8, contiguous revisions, hash chaining, and `fsync`.
- Query of missing stream is zero-write and returns `READY`.
- `materialization_artifacts.pm_run`, titles, and caller progress claims never derive Attempt state.
- One workspace/goal may have one `ACTIVE` attempt; terminal states are immutable.
