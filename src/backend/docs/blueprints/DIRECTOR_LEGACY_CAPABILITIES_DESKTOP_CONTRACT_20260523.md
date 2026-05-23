# Director Legacy Capabilities Desktop Contract

Date: 2026-05-23
Classification: one_off
Owner: Codex

## Problem

The desktop system services panel still reads the deprecated
`/arsenal/director/capabilities` route for the Director capability overview.
That route uses the shared `RoleCapabilitiesResponse` response model but
returns only `role` and `capabilities`, omitting the required `ok` envelope.

The frontend also treats `capabilities` as an array, while the backend role
capability contract can return a host-scoped map such as
`{ "electron_workbench": ["read_files"] }`. The result is weak desktop evidence:
the route can fail response validation and a valid capability matrix can render
as offline.

## Architecture

```text
SystemServicesTab
  -> GET /arsenal/director/capabilities
  -> delivery.http.routers.arsenal
  -> polaris.domain.entities.capability.get_role_capabilities("director")
  -> RoleCapabilitiesResponse { ok, role, capabilities }
```

## Scope

- Preserve the deprecated route for compatibility.
- Align its envelope with the canonical `/arsenal/v2/director/capabilities`
  and `/v2/director/capabilities` shape.
- Normalize host-scoped capability maps in the desktop settings panel into
  stable display labels.
- Re-enable the skipped integration contract test for the deprecated route.

## Non-Goals

- No new role capability source of truth.
- No graph ownership change.
- No rewrite of the settings services panel.

## Verification Plan

- `.venv\Scripts\python.exe -m ruff check src/backend/polaris/delivery/http/routers/arsenal.py src/backend/polaris/tests/integration/delivery/routers/test_arsenal_router.py --fix`
- `.venv\Scripts\python.exe -m ruff format src/backend/polaris/delivery/http/routers/arsenal.py src/backend/polaris/tests/integration/delivery/routers/test_arsenal_router.py`
- `.venv\Scripts\python.exe -m mypy src/backend/polaris/delivery/http/routers/arsenal.py src/backend/polaris/tests/integration/delivery/routers/test_arsenal_router.py`
- `pytest src/backend/polaris/tests/integration/delivery/routers/test_arsenal_router.py -q`
- `npm run test -- SystemServicesTab`
- `npm run typecheck`
- `npm run lint`
- `npm run test -- PMPage ChiefEngineerPage DirectorPage PMWorkspace ChiefEngineerWorkspace DirectorWorkspace SystemServicesTab`
- `pytest src/backend/polaris/tests/integration/delivery/routers/test_arsenal_router.py src/backend/polaris/tests/unit/delivery/http/routers/test_arsenal_v2.py src/backend/polaris/tests/unit/delivery/http/routers/test_v2_director_router.py -q`
