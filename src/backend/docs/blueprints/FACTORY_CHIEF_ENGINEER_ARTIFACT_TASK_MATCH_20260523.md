# Factory Chief Engineer Artifact Task Match

Date: 2026-05-23
Status: Proposed

## Context

Factory mode now exposes PM, Chief Engineer, and Director as separate role layers. The Chief Engineer layer shows blueprint artifacts and also lists PM tasks that still need a blueprint.

The current Factory UI only treats a task as blueprint-ready when the PM task row itself contains `blueprint_id`, `blueprint_path`, or `runtime_blueprint_path`. Runtime blueprint artifacts can already exist through `/v2/factory/runs/{run_id}/artifacts`, but the Factory Chief Engineer layer may still show the matching PM task under "pending blueprint".

## Assumption Register

1. A Chief Engineer artifact can carry task identity either in the artifact JSON payload (`task_id`, `pm_task_id`, or `taskId`) or in the runtime blueprint filename/path.
2. `/v2/factory/runs/{run_id}/artifacts` is a read-only projection endpoint; adding optional metadata to artifact rows does not make it a runtime state owner.
3. Factory UI must prefer explicit artifact `task_id` metadata and only use path/name inference as a fallback.
4. Different PM tasks must not be hidden unless there is a direct task identity match with task fields or artifact metadata.

## Data Flow

```text
Factory stage result
  -> runtime/blueprints/*.json
  -> /v2/factory/runs/{run_id}/artifacts
  -> FactoryWorkspace.buildBlueprintEvidence()
  -> FactoryChiefEngineerLayer pending-task filter
```

## Contract

1. Factory artifact projection may include optional `task_id` when the artifact JSON payload or CE blueprint filename provides one.
2. Factory `BlueprintEvidenceView` must carry the matched `taskId`.
3. Factory Chief Engineer pending-task filtering must exclude PM tasks that already have matching task evidence or matching runtime artifact evidence.
4. Task matching must normalize only case and surrounding whitespace, not perform broad fuzzy matching.

## Verification Plan

- Backend router test: a stage-completed CE blueprint JSON with `task_id` is returned from `/v2/factory/runs/{run_id}/artifacts` with that `task_id`.
- Frontend component test: a PM task with no inline blueprint fields is not listed as pending when a runtime CE artifact carries the matching `task_id`.
- Frontend component test: existing CE artifact rendering remains intact.
- Run targeted Python ruff/format/mypy/pytest for Factory router/schema changes.
- Run targeted Vitest/typecheck/lint for Factory workspace changes.
