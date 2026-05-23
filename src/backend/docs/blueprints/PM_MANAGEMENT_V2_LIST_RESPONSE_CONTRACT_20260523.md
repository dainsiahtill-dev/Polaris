# PM Management V2 List Response Contract Blueprint

Date: 2026-05-23

## Problem

The desktop v2 API client declares PM management list responses as:

- `PmTaskListResponse`: `items` + `total`
- `PmRequirementListResponse`: `items` + `total`

The backend v2 PM management aliases currently return PM-internal collection names:

- `tasks` + `pagination`
- `requirements` + `pagination`

The routes exist, but typed desktop consumers cannot rely on the declared response shape.

## Scope

- PM management delivery router response normalization.
- Existing legacy response keys remain intact.
- Focused backend and frontend service tests.

## Architecture

```text
Desktop service
  -> GET /v2/pm/tasks or /v2/pm/requirements
  -> PM management router
  -> ScriptsPMAdapter
  -> PM task/requirements internals
  -> compatibility response:
       legacy key: tasks|requirements
       desktop key: items
       total: pagination.total or len(items)
```

## Contract

The v2 list aliases return both:

- existing PM-internal collection keys for compatibility;
- `items` and `total` for the desktop typed API.

No target-project files are touched.

## Verification Plan

- `ruff check` and `ruff format` for changed backend files.
- `mypy` for changed backend files and focused router tests.
- `pytest src/backend/polaris/tests/unit/delivery/http/routers/test_pm_management_v2.py -v`.
- Focused frontend service tests for PM task and requirement list response shape.
- `npm run typecheck`.
