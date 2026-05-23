# PM Management Active Workspace Path Contract Blueprint

Date: 2026-05-23

## Problem

The PM management delivery router resolves every workspace-scoped endpoint from
`settings.workspace`. In the desktop runtime, the active target workspace is
stored in `settings.workspace_path`; `settings.workspace` can still point at the
Polaris repository or another stale value.

This means desktop PM document, task, requirement, status, health, and init
routes can read or write the wrong workspace even when the Electron shell has a
different active workspace selected.

## Scope

- PM management delivery router workspace resolution only.
- Preserve legacy fallback to `settings.workspace`.
- Do not change PM adapter behavior, task schema, or target project files.

## Architecture

```text
Desktop PM service
  -> /v2/pm/*
  -> PM management router
  -> active workspace resolver:
       settings.workspace_path first
       settings.workspace fallback
  -> ScriptsPMAdapter(active_workspace)
```

## Contract

Every PM management endpoint that creates a workspace-bound `ScriptsPMAdapter`
uses the same resolver and prefers `settings.workspace_path` when present.

If neither workspace field is configured, the router fails before constructing
the adapter with a structured `WORKSPACE_NOT_CONFIGURED` error.

## Verification Plan

- Add a router regression proving `/v2/pm/documents` constructs the PM adapter
  with `workspace_path` when `workspace` is stale.
- `ruff check` and `ruff format` for changed backend files.
- `mypy` for changed backend files and focused router tests.
- `pytest src/backend/polaris/tests/unit/delivery/http/routers/test_pm_management_v2.py -v`.
- Focused PM desktop frontend service and component tests.
- `npm run typecheck`.
- `npm run test:e2e`.
