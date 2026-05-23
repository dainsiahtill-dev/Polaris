# PM Desktop Task Create Route Blueprint

Date: 2026-05-23

## Problem

The desktop API client exposes `pmTaskService.create()` as `POST /v2/pm/tasks`, but the PM management router only registers `GET /v2/pm/tasks`. The route table therefore proves the path exists but not the required create method.

## Scope

- Delivery HTTP PM management router.
- Existing PM adapter used by the PM management router.
- Focused router tests for the desktop `/v2/pm/tasks` alias and the legacy `/pm/v2/pm/tasks` alias.

No target-project code is touched.

## Architecture

```text
React desktop client
  -> POST /v2/pm/tasks
  -> polaris.delivery.http.routers.pm_management
  -> ScriptsPMAdapter.create_task()
  -> existing PM TaskOrchestrator.register_task()
  -> pm_data/tasks/registry.json + task history
```

## Contract

Request:

- `subject` is required and maps to the PM task title.
- `description`, `priority`, `status`, `acceptance`, `assignee`, `due_date`, `tags`, `parent_id`, and `metadata` are optional.

Response:

- Returns the created PM task detail.
- Includes both `title` and `subject` so existing PM internals and desktop client types can both consume the response.

## Verification Plan

- `ruff check` and `ruff format` for changed backend files.
- `mypy` for changed backend files and focused tests.
- `pytest src/backend/polaris/tests/unit/delivery/http/routers/test_pm_management_v2.py -v`.
- Focused frontend service tests covering `pmTaskService.create`.
