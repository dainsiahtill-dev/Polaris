# Role Chat Registered Role Contract

Date: 2026-05-23

## Finding

The backend generic role-chat router is backed by `llm.dialogue`
`get_registered_roles()`, which currently exposes the role prompt templates for:

- `pm`
- `architect`
- `chief_engineer`
- `director`
- `qa`

The broader conversation/session role type also includes `scout`, but Scout is
not registered as a role-chat prompt template. The older V2 `RoleChatPanel` still
offered Scout and its hook/service accepted arbitrary strings, so the UI could
send users into a backend route that would reject the selected role.

## Contract

- Role-chat UI controls must only offer backend-registered role-chat roles.
- Role-chat service and hooks must be typed with the role-chat role union, not
  arbitrary strings and not the broader conversation/session role union.
- Chief Engineer remains first-class in the role-chat route and UI.
- Conversation/session surfaces may continue to include Scout independently.

## Boundary

- Target cell: `llm.dialogue`
- Frontend scope: V2 role-chat hook, service typing, and panel role selector.
- Backend scope: existing role-router tests already cover the registered roles
  endpoint and Chief Engineer status; no production backend change is required.
- No role-specific duplicate dialogue implementation is introduced.
- No target-project code is generated or modified.

## Verification

- Frontend service tests for `/v2/role/chat/roles` response shape and Chief
  Engineer route-chat paths.
- V2 role-chat panel tests proving Chief Engineer is selectable and Scout is not.
- Role contract tests proving Scout is excluded from role-chat while Chief
  Engineer is included.
- TypeScript typecheck and lint.
