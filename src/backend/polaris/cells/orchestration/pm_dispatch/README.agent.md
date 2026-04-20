# PM Dispatch

## Purpose

Dispatch PM contracts into executable task iterations, drive task assignment and lifecycle transitions, and record dispatch outcomes.

## Kind

`workflow`

## Public Contracts

- commands: DispatchPmTasksCommandV1, ResumePmIterationCommandV1
- queries: GetPmDispatchStatusQueryV1
- events: PmTaskDispatchedEventV1, PmIterationAdvancedEventV1
- results: PmDispatchResultV1
- errors: PmDispatchErrorV1

## Depends On

- `orchestration.pm_planning`
- `orchestration.workflow_runtime`
- `director.execution`
- `qa.audit_verdict`
- `runtime.state_owner`
- `audit.evidence`
- `policy.permission`
- `policy.workspace_guard`

## State Ownership

- `runtime/state/dispatch/*`

## Effects Allowed

- `fs.read:runtime/contracts/*`
- `fs.write:runtime/state/dispatch/*`
- `fs.write:runtime/events/runtime.events.jsonl`
- `ws.outbound:runtime/*`

## Verification

- `tests/test_dispatch_pipeline_engine_dispatch.py`
- `tests/test_orchestration_command_service.py`
