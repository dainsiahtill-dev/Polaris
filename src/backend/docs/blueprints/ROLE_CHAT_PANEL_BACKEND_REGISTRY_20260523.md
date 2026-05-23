# Role Chat Panel Backend Registry

Date: 2026-05-23

## Finding

`RoleChatPanel` now uses the correct role-chat type, but its selector still uses
only a local role list. The backend already exposes `/v2/role/chat/roles`, which
is the authoritative runtime registry for the generic role-chat router.

If the backend registry changes, the UI can drift unless it reads the route and
uses the local list only as a bounded fallback.

## Contract

- The V2 role-chat selector loads roles from `/v2/role/chat/roles`.
- Backend roles are filtered through the typed `RoleChatRole` union before they
  reach UI state.
- Local role labels remain a fallback when the registry request fails or returns
  no known role-chat roles.
- Chief Engineer remains selectable and Scout remains excluded from role-chat.

## Boundary

- Target cell: `llm.dialogue`
- Frontend scope: V2 role-chat panel and focused tests.
- Backend scope: existing `/v2/role/chat/roles` route; no production backend
  change is required.
- No target-project code is generated or modified.

## Verification

- Focused RoleChatPanel tests for backend registry loading, Scout exclusion, and
  Chief Engineer message routing.
- Role-chat service and type regression tests.
- TypeScript typecheck and lint.
