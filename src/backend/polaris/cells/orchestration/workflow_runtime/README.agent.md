# Workflow Runtime

## Purpose

Own workflow engine runtime, activity and workflow registration, and workflow state persistence for PM, Director and QA orchestration.

## Kind

`capability`

## Public Contracts

- commands: StartWorkflowCommandV1, CancelWorkflowCommandV1
- queries: QueryWorkflowStatusV1, QueryWorkflowEventsV1
- events: WorkflowExecutionStartedEventV1, WorkflowExecutionCompletedEventV1
- results: WorkflowExecutionResultV1
- errors: WorkflowRuntimeErrorV1

## Depends On

- `runtime.state_owner`
- `policy.workspace_guard`
- `policy.permission`
- `audit.evidence`
- `events.fact_stream`

## State Ownership

- `runtime/workflows/*`
- `runtime/state/workflow/*`

## Effects Allowed

- `fs.read:runtime/**`
- `fs.write:runtime/workflows/*`
- `fs.write:runtime/state/workflow/*`
- `fs.write:runtime/events/runtime.events.jsonl`
- `db.read_write:workflow_runtime`
- `process.spawn:workflow/*`

## Verification

- `tests/orchestration/test_workflow_runtime.py`
- `tests/test_embedded_orchestration_dag.py`
