# Chief Engineer Desktop Dialogue Bridge

Status: Implementing
Date: 2026-05-23

## Scope

This change closes a desktop/backend contract gap for the PM -> Chief Engineer -> Director workflow surface.
The backend already exposes the shared role dialogue and RoleSession APIs for `chief_engineer`; the desktop
Chief Engineer workspace did not expose that capability, and the frontend dialogue role contract omitted the role.

## Current Evidence

- `polaris.delivery.http.routers.role_chat` exposes `/v2/role/{role}/chat/status` and `/v2/role/{role}/chat/stream`.
- `polaris.delivery.http.routers.role_session` exposes `/v2/roles/sessions`.
- `polaris.delivery.http.v2.chief_engineer` exposes `/v2/chief-engineer/blueprints`.
- `src/frontend/src/services/conversationApi.ts` only typed `pm | architect | director | qa`.
- `ChiefEngineerWorkspace` showed blueprint evidence and Director handoff state, but no role dialogue panel.

## Design

```text
ChiefEngineerWorkspace
  -> AIDialoguePanel(dialogueRole="chief_engineer")
      -> /v2/role/chief_engineer/chat/status
      -> /v2/role/chief_engineer/chat/stream
      -> /v2/roles/sessions
      -> /v2/conversations

ChiefEngineerWorkspace
  -> /v2/chief-engineer/blueprints
```

The workspace keeps blueprint evidence as the primary first-column surface. A compact control column continues to
show Director readiness and worker state. A toggleable dialogue column makes the Chief Engineer role actionable
without inventing blueprint data or bypassing the existing backend role runtime.

## Boundaries

- Frontend only consumes existing public HTTP contracts.
- Backend source changes are documentation-only for this increment.
- No target-project or business-specific code is introduced.
- No new backend state owner or effect is introduced.

## Verification Plan

- Frontend type check: `npm run typecheck`
- Frontend targeted tests: Chief Engineer workspace and role contract tests through Vitest with jsdom setup.
- Backend targeted test: `python -m pytest -q polaris/tests/unit/delivery/http/routers/test_v2_chief_engineer_router.py`
