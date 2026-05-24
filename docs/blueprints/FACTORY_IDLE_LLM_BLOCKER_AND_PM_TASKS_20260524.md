# Factory Idle LLM Blocker And PM Tasks

Date: 2026-05-24

## Problem

- Factory mode surfaced `blocked: pm` in the LLM runtime overlay even when no Factory run was active.
- `/v2/pm/tasks` could return HTTP 500 if the legacy PM adapter import path was temporarily unavailable or poisoned.

## Fix

- Factory LLM blockers are now visible only while a Factory run is active or starting.
- Desktop PM task list aliases now degrade to an idle empty projection with `reason=PM_RUNTIME_UNAVAILABLE` instead of a transport failure when PM runtime import fails.

## Verification

- Backend unit coverage: `test_v2_list_tasks_returns_idle_projection_when_pm_runtime_import_fails`.
- Frontend unit coverage: `does not show stale Factory role blockers while Factory is idle`.
