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

- `pytest src/backend/polaris/tests/integration/delivery/routers/test_arsenal_router.py -q`
- `npm run test -- SystemServicesTab`
- `npm run typecheck`
- `npm run lint`
