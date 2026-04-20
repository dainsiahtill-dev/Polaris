# Runtime Task Runtime

## Purpose

Own task lifecycle transitions for runtime taskboard state.

## Implementation

- public service entrypoint: `polaris/cells/runtime/task_runtime/public/service.py`
- primary runtime implementation: `polaris/cells/runtime/task_runtime/internal/service.py`

## Kind

`workflow`

## Public Contracts

- commands: `CreateRuntimeTaskCommandV1`, `UpdateRuntimeTaskCommandV1`, `ReopenRuntimeTaskCommandV1`
- queries: `ListRuntimeTasksQueryV1`, `GetRuntimeTaskQueryV1`
- events: `RuntimeTaskLifecycleEventV1`
- results: `RuntimeTaskResultV1`
- errors: `RuntimeTaskRuntimeErrorV1`

## Depends On

- `policy.workspace_guard`
- `audit.evidence`
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

## Verification

- `polaris/cells/runtime/task_runtime/tests/test_service.py`
- `polaris/tests/test_runtime_projection_snapshot_tasks.py`
