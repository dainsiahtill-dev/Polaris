# PM, ChiefEngineer, and Director Task-Market Routing Blueprint

Date: 2026-06-03
Status: Draft for implementation

## Problem

The current workflow has all the pieces for a governed PM -> ChiefEngineer -> Director chain, but the runtime path is ambiguous:

- PM mainline dispatch publishes every task to `pending_design`.
- Shadow dispatch publishes every task directly to `pending_exec`.
- ChiefEngineer can claim `pending_design`, generate blueprint evidence, and advance work to `pending_exec`.
- Director can claim `pending_exec`, but the UI and E2E evidence can lose task visibility when only workflow/task-market evidence exists.

The user requirement is not a single fixed path. PM may send simple work directly to Director, and may send complex work to ChiefEngineer first. The complex game audit must use the full planning/blueprint route before Director writes code.

## Target Architecture

```text
PM task contract
  |
  |-- direct execution task -----------------------> task_market.pending_exec
  |
  `-- blueprint-required task -> task_market.pending_design
                                |
                                v
                           ChiefEngineer
                                |
                                v
                         persisted blueprint
                                |
                                v
                         task_market.pending_exec
                                |
                                v
                           Director workers
                                |
                                v
                               QA
```

## Routing Contract

PM dispatch owns the routing decision for each task and records it in task-market metadata:

- `route = direct_to_director`
- `route = chief_blueprint_required`

The task-market stage follows that route:

- `direct_to_director` -> `pending_exec`
- `chief_blueprint_required` -> `pending_design`

ChiefEngineer must not become the owner of Director execution. It claims `pending_design`, persists blueprint evidence, then advances the task to `pending_exec` with:

- `blueprint_id`
- `blueprint_path`
- `runtime_blueprint_path`
- `scope_paths`
- `target_files`
- `route = chief_blueprint_required`

Director workers claim `pending_exec` only. Parallelism is controlled by Director worker pool settings and scope-conflict checks, not by PM.

## UI Evidence

The Director workspace should show both task-market and runtime task evidence. If task-market rows are available, the UI should expose:

- task id and title
- current stage/status
- route
- blueprint readiness
- worker/claimed-by state

This avoids a false empty task board when PM/ChiefEngineer used task-market rather than legacy runtime task projection.

## Verification Plan

1. Unit test PM dispatch routing:
   - direct task publishes `pending_exec`
   - complex or blueprint-required task publishes `pending_design`
   - routing metadata is present
2. Unit test ChiefEngineer consumer:
   - claims `pending_design`
   - persists blueprint metadata
   - advances the same item to `pending_exec`
3. Unit test Director task-market visibility:
   - `pending_exec` task-market rows are normalized into Director task responses with route and blueprint fields
4. Electron E2E:
   - complex game audit shows PM contract, ChiefEngineer blueprint coverage, Director task queue, diff evidence, and QA pass.
