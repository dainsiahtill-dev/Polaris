# PM Document Subroute Order Contract

Date: 2026-05-23
Classification: one_off
Owner: Codex

## Problem

PM document version and diff APIs are part of the desktop document workflow.
The frontend uses the canonical v2 forms:

```text
GET /v2/pm/documents/{path}/versions
GET /v2/pm/documents/{path}/compare
```

The legacy PM management namespace still exposes matching routes under
`/pm/documents/{path}/versions` and `/pm/documents/{path}/compare`, but three
integration tests are skipped because the generic route
`/pm/documents/{doc_path:path}` is declared before the two subroutes.

FastAPI route matching is order-sensitive. With the generic path route first,
`/pm/documents/test.md/versions` is interpreted as a document detail request
for `test.md/versions` instead of a versions query.

## Architecture

```text
PMDocumentPanel
  -> pmDocumentService.versions / compare
  -> /v2/pm/documents/{path}/versions|compare
  -> pm_management v2 aliases
  -> get_document_versions / compare_document_versions

legacy clients
  -> /pm/documents/{path}/versions|compare
  -> same handlers, ordered before generic document detail route
```

## Scope

- Move legacy GET document versions and compare route declarations before the
  generic legacy GET document detail declaration.
- Replace skipped PM management integration tests with active assertions.
- Preserve v2 aliases and existing handler behavior.

## Non-Goals

- No PM adapter rewrite.
- No document storage behavior change.
- No new endpoint names or second route truth.

## Verification Plan

- `.venv\Scripts\python.exe -m ruff check src/backend/polaris/delivery/http/routers/pm_management.py src/backend/polaris/tests/integration/delivery/routers/test_pm_management_router.py --fix`
- `.venv\Scripts\python.exe -m ruff format src/backend/polaris/delivery/http/routers/pm_management.py src/backend/polaris/tests/integration/delivery/routers/test_pm_management_router.py`
- `.venv\Scripts\python.exe -m mypy src/backend/polaris/delivery/http/routers/pm_management.py src/backend/polaris/tests/integration/delivery/routers/test_pm_management_router.py`
- `.venv\Scripts\python.exe -m pytest src/backend/polaris/tests/integration/delivery/routers/test_pm_management_router.py -q`
- `npm run test -- PMDocumentPanel pmService`
- `npm run typecheck`
- `npm run lint`
- `npm run test -- PMPage ChiefEngineerPage DirectorPage PMWorkspace ChiefEngineerWorkspace DirectorWorkspace PMDocumentPanel pmService`
- `.venv\Scripts\python.exe -m pytest src/backend/polaris/tests/integration/delivery/routers/test_pm_management_router.py src/backend/polaris/tests/unit/delivery/http/routers/test_pm_management_v2.py src/backend/polaris/tests/unit/delivery/http/routers/test_v2_pm_router.py -q`
