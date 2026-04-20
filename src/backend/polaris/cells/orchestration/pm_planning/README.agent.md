# PM Planning

## Purpose

Produce PM task contracts from project goals and docs, enforce planning quality gates, and publish governed planning artifacts.

## Kind

`workflow`

## Public Contracts

- commands: GeneratePmTaskContractCommandV1
- queries: GetPmPlanningStatusQueryV1
- events: PmTaskContractGeneratedEventV1
- results: PmTaskContractResultV1
- errors: PmPlanningErrorV1

## Depends On

- `context.engine`
- `llm.control_plane`
- `policy.permission`
- `policy.workspace_guard`
- `runtime.state_owner`
- `audit.evidence`
- `finops.budget_guard`

## State Ownership

- `runtime/state/pm/*`

## Effects Allowed

- `fs.read:workspace/docs/*`
- `fs.read:runtime/contracts/*`
- `fs.write:runtime/state/pm/*`
- `fs.write:runtime/events/pm.events.jsonl`
- `llm.invoke:pm/*`

## Verification

- `tests/test_pm_task_quality_gate.py`
- `tests/test_pm_orchestration_api.py`
