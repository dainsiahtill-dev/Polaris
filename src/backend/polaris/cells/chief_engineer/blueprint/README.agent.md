# Chief Engineer Blueprint

## Purpose

Generate task-level implementation blueprints and dependency analysis for Director execution without performing code write operations.

## Kind

`capability`

## Public Contracts

- commands: GenerateTaskBlueprintCommandV1
- queries: GetBlueprintStatusQueryV1
- events: TaskBlueprintGeneratedEventV1
- results: TaskBlueprintResultV1
- errors: ChiefEngineerBlueprintErrorV1

## Depends On

- `context.engine`
- `llm.control_plane`
- `policy.permission`
- `policy.workspace_guard`
- `finops.budget_guard`
- `audit.evidence`

## State Ownership

- `runtime/state/blueprints/*`

## Effects Allowed

- `fs.read:workspace/**`
- `fs.write:runtime/state/blueprints/*`
- `fs.write:runtime/events/runtime.events.jsonl`
- `llm.invoke:chief_engineer/*`

## Verification

- `tests/test_chief_engineer_preflight.py`
