# PM/CE/Director Desktop Handoff Diagnostics Blueprint

Date: 2026-05-24

## Scope

Improve the Chief Engineer desktop diagnostics contract so it reports Director handoff readiness from actual PM task coverage instead of treating any persisted blueprint as sufficient.

## Current Gap

`/v2/chief-engineer/diagnostics` reports `director_handoff_ready=true` whenever at least one blueprint is loadable. In a PM -> Chief Engineer -> Director flow, this is too weak: Director should only be considered handoff-ready when every active PM task in `runtime/tasks/plan.json` has matching Chief Engineer blueprint evidence.

## Target Contract

`blueprints` diagnostics should include:

- `planned_tasks`: count of PM plan tasks discovered from `runtime/tasks/plan.json`
- `covered_tasks`: count of planned task IDs with matching persisted blueprint payloads
- `missing_task_ids`: planned task IDs with no matching blueprint
- `director_handoff_ready`: true only when planned task coverage is complete; false when the plan exists and coverage is partial or empty

When no PM plan exists, keep existing store health behavior for compatibility but expose zero planned/covered counts so the UI can distinguish store readiness from task-pool readiness.

## Data Flow

Chief Engineer diagnostics route -> active workspace -> read-only PM plan snapshot -> read-only blueprint store -> coverage projection -> desktop diagnostics card.

No writes, no new state owner, no new Cell. This reuses the existing `chief_engineer.blueprint` persistence contract and the current delivery route.

## Verification

- Backend router unit tests for complete and partial coverage.
- Targeted Ruff, Mypy, and Pytest for touched backend files.
- Frontend service/component tests for the enriched diagnostics fields.
