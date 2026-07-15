# Runtime Task Runtime

## Purpose

Own task lifecycle transitions for runtime taskboard state, execution-attempt
authority, and the DEO-1B guarded operation aggregate.

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
- queries: `ListRuntimeTasksQueryV1`, `GetRuntimeTaskQueryV1`, `ValidateTaskRuntimeExecutionAttemptQueryV1`, `GetDirectedEffectParentRegistryQueryV1`, `GetDirectedEffectOperationQueryV1`
- events: `RuntimeTaskLifecycleEventV1`, `TaskRuntimeExecutionFactV1`
- results: `RuntimeTaskResultV1`, `RuntimeTaskFactoryRunBindingResultV1`, `ExpiredFactoryRunSessionFenceResultV1`, `OwnerReworkExecutionPreparationResultV1`, `TaskRuntimeExecutionAttemptValidationVerdictV1`, `TaskRuntimeExecutionAttemptAuthorityOpenVerdictV1`, `TaskRuntimeExecutionAttemptHeartbeatVerdictV1`, `TaskRuntimeExecutionAttemptSettlementVerdictV1`, `DirectedEffectStreamEnrollmentResultV1`, `DirectedEffectOperationResultV1`, `DirectedEffectParentRegistryResultV1`, `ObservableTaskRowsProjectionV1`
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

## Verification

- `polaris/cells/runtime/task_runtime/tests/test_service.py`
- `polaris/cells/runtime/task_runtime/tests/test_directed_effect_operation.py`
- `polaris/cells/runtime/task_runtime/tests/test_directed_effect_operation_concurrency.py`
- `polaris/cells/runtime/task_runtime/tests/test_directed_effect_operation_guarded_fence.py`
- `polaris/cells/runtime/task_runtime/tests/test_execution_attempt_settlement.py`
- `polaris/tests/test_runtime_projection_snapshot_tasks.py`

## Current Governance Status

DEO-1B is closed. The main TaskRuntime rerun recorded `457 passed`, `0`
failures, and one `nats-py` environment warning; the independent A-L audit
passed. DEO-1C is pending as a read-only readiness/fence bucket. DEO-2,
DEO-3, and DEO-4 remain unschedulable; no end-to-end bench is authorized.
