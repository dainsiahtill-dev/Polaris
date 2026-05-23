# PM Desktop Tasks Adapter And LLM Gate Contract

Date: 2026-05-23
Status: Proposed

## Context

The Factory desktop PM task list calls `GET /v2/pm/tasks`. A runtime error showed the route returning HTTP 500 with:

`ImportError: get_pm function not found in pm_integration module`

At the same time, the desktop runtime could show PM as blocked even after a PM role deep test passed.

## Root Causes

1. `ScriptsPMAdapter` imported `pm_integration` through the `polaris.delivery.cli.pm` package attribute. That package exposes a legacy `pm_integration = None` placeholder, so the adapter could cache `None` instead of the real `polaris.delivery.cli.pm.pm_integration` submodule.
2. `build_llm_status` resolved runtime readiness from `settings.workspace` only. Desktop runtime paths can carry the active project in `settings.workspace_path`, so status projection could read stale LLM config or test-index evidence from a different workspace.

## Contract

1. PM HTTP adapters must import the canonical PM integration submodule explicitly.
2. `/v2/pm/tasks` must not produce an unhandled import error when the PM package placeholder exists.
3. LLM readiness projections must prefer `workspace_path` over `workspace` when present and must use the same active workspace for config, cache, test-index, and config mtime lookup.
4. A role is blocked only when its active-workspace binding and active-workspace test evidence fail the existing readiness rules.

## Verification

- Unit-test `ScriptsPMAdapter` with the package-level placeholder set to `None`.
- Integration-test `/v2/pm/tasks` through the real adapter path on an uninitialized workspace and assert a controlled PM-not-initialized response instead of HTTP 500.
- Unit-test `build_llm_status` with stale `workspace` and active `workspace_path`, asserting that active workspace drives config and test-index reads.
