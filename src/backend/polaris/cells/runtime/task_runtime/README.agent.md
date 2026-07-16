# Runtime Task Runtime

## Purpose

Own task lifecycle transitions for runtime taskboard state, execution-attempt
authority, and the closed DEO-1 durable fact foundation: the guarded operation
aggregate plus read-only parent-operation readiness observation.

## Implementation

- public service entrypoint: `polaris/cells/runtime/task_runtime/public/service.py`
- primary runtime implementation: `polaris/cells/runtime/task_runtime/internal/service.py`
- execution-attempt/session implementation: `polaris/cells/runtime/task_runtime/internal/execution_session.py`
- directed-effect aggregate: `polaris/cells/runtime/task_runtime/internal/directed_effect_operation.py`

## Kind

`workflow`

## Public Contracts

- commands: `CreateRuntimeTaskCommandV1`, `UpdateRuntimeTaskCommandV1`, `ReopenRuntimeTaskCommandV1`, `BindRuntimeTaskToFactoryRunCommandV1`, `FenceExpiredFactoryRunSessionsCommandV1`, `PrepareOwnerReworkExecutionCommandV1`
- execution commands: `OpenTaskRuntimeExecutionAttemptAuthorityCommandV1`, `HeartbeatTaskRuntimeExecutionAttemptCommandV1`, `SettleTaskRuntimeExecutionAttemptCommandV1`
- DEO-1B commands: `EnrollDirectedEffectParentRegistryStreamCommandV1`, `EnrollDirectedEffectOperationStreamCommandV1`, `AdmitDirectedEffectParentCommandV1`, `AdmitDirectedEffectOperationCommandV1`, `ClaimDirectedEffectCommandV1`, `AbortDirectedEffectOperationCommandV1`
- queries: `ListRuntimeTasksQueryV1`, `GetRuntimeTaskQueryV1`, `ValidateTaskRuntimeExecutionAttemptQueryV1`, `GetDirectedEffectParentRegistryQueryV1`, `GetDirectedEffectOperationQueryV1`, `GetDirectedEffectParentReadinessQueryV1`
- DEO-1C read service: `get_directed_effect_parent_readiness`
- events: `RuntimeTaskLifecycleEventV1`, `TaskRuntimeExecutionFactV1`
- results: `RuntimeTaskResultV1`, `RuntimeTaskFactoryRunBindingResultV1`, `ExpiredFactoryRunSessionFenceResultV1`, `OwnerReworkExecutionPreparationResultV1`, `TaskRuntimeExecutionAttemptValidationVerdictV1`, `TaskRuntimeExecutionAttemptAuthorityOpenVerdictV1`, `TaskRuntimeExecutionAttemptHeartbeatVerdictV1`, `TaskRuntimeExecutionAttemptSettlementVerdictV1`, `DirectedEffectStreamEnrollmentResultV1`, `DirectedEffectOperationResultV1`, `DirectedEffectParentRegistryResultV1`, `DirectedEffectParentReadinessStateCountV1`, `DirectedEffectParentReadinessProjectionV1`, `DirectedEffectParentReadinessResultV1`, `ObservableTaskRowsProjectionV1`
- errors: `RuntimeTaskRuntimeError`

## Depends On

- `events.fact_stream`

## State Ownership

- `runtime/tasks/*`
- `runtime/tasks/sessions/*`

## Effects Allowed

- `fs.read:runtime/tasks/*`
- `fs.write:runtime/tasks/*`
- `fs.read:runtime/tasks/sessions/*`
- `fs.write:runtime/tasks/sessions/*`
- `fs.write:runtime/events/taskboard.terminal.events.jsonl`
- `fs.write:runtime/events/task_runtime.execution.jsonl`
- `ws.outbound:runtime/*`

## Invariants

- task status writes must flow through one runtime service entry
- task execution must be claim-first and lease-backed before Director starts work
- interrupted work must surface as resumable runtime state instead of silently disappearing
- cross-role task lifecycle updates are append-only auditable
- all text reads/writes use explicit UTF-8
- DEO-1B enrollment receipts are observability evidence only; enrollment never
  admits a parent or authorizes a child operation
- active-to-inactive writes fail closed while a durable parent registry is OPEN;
  DEO-1B does not close a parent or admit a terminal outcome
- DEO-1C readiness is a read-only strict observation of one parent operation
  stream, including historical `CLOSED` parents; it reuses the shared reducer
  and preserves exact typed fail-closed diagnostics
- readiness evidence is deeply immutable and cycle-safe; successful evidence
  uses the exact source-head schema and always reports
  `enforcement="not_enabled"`
- the readiness query has no mutation, settlement, receipt, terminal-admission,
  Run Ledger, or UI path and grants no close or terminal authority

## Verification

- `polaris/cells/runtime/task_runtime/tests/test_service.py`
- `polaris/cells/runtime/task_runtime/tests/test_directed_effect_operation.py`
- `polaris/cells/runtime/task_runtime/tests/test_directed_effect_operation_concurrency.py`
- `polaris/cells/runtime/task_runtime/tests/test_directed_effect_operation_guarded_fence.py`
- `polaris/cells/runtime/task_runtime/tests/test_execution_attempt_settlement.py`
- `polaris/tests/test_runtime_projection_snapshot_tasks.py`

## Current Governance Status

DEO-1A, DEO-1B, and DEO-1C are closed, so the DEO-1 durable fact foundation is
closed. DEO-1C closure evidence records `72 passed` in the final focused
two-file suite and `482 passed in 64.02s` in the final full TaskRuntime suite.
Root gates record Ruff check passed, Ruff format check `6 files formatted`,
mypy over four production files with `0 issues`, compileall passed, and
`git diff --check` passed. Independent specification review was `CLEAR` after
all High findings closed; independent code-quality review was `APPROVED` after
two Important findings closed. Canonical state sequence duplication remains a
non-blocking Minor.

Directed Effect Operation v1 remains `p0_open`. DEO-2 is next but has not
started and remains `not_schedulable`; DEO-3 remains the highest-risk P0
child/terminal close, receipt, and recovery path; DEO-4 remains pending and
`not_schedulable`. Bench remains `not_schedulable`, and no Bench was run.
