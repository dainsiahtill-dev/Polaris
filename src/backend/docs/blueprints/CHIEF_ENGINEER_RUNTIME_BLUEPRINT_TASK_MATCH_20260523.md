# Chief Engineer Runtime Blueprint Task Match

Date: 2026-05-23
Classification: pattern

## Problem

The Chief Engineer desktop can list persisted runtime blueprints from `/v2/chief-engineer/blueprints`, but its candidate task list only checks whether each PM task carries inline `blueprint_id`, `blueprint_path`, or `runtime_blueprint_path` fields.

This creates a false negative: a task can already have a valid persisted Chief Engineer blueprint in `runtime/blueprints`, while the UI still shows it under `待生成蓝图`.

## Architecture

```text
PM task rows
    |
    | inline blueprint fields
    v
ChiefEngineerWorkspace evidence model
    ^
    | persisted blueprint summaries with raw.task_id
    |
/v2/chief-engineer/blueprints -> runtime/blueprints/*
```

The desktop evidence model must treat runtime blueprint summaries as first-class task evidence. Matching should use:

1. PM task inline blueprint fields.
2. Persisted blueprint `raw.task_id` from the backend summary payload.
3. Fallback blueprint id only when no task id exists.

## Scope

- Frontend only for this slice.
- No backend schema change: `ChiefEngineerBlueprintSummaryV1.raw` already carries the persisted payload.
- No new route: existing `/v2/chief-engineer/blueprints` is sufficient.

## Implementation Plan

1. Preserve `raw.task_id` when building runtime blueprint evidence.
2. Build a set of task ids that already have runtime or inline blueprint evidence.
3. Exclude those tasks from `待生成蓝图`.
4. Add a regression test where a PM task has no inline blueprint fields, but `/v2/chief-engineer/blueprints` returns a runtime blueprint with `raw.task_id` matching the task.

## Verification

- `npm run test -- ChiefEngineerWorkspace chiefEngineerService`
- `npm run typecheck`
- `npm run lint`
