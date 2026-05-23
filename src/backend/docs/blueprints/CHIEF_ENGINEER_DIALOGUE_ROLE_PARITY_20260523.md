# Chief Engineer Dialogue Role Parity

Date: 2026-05-23

## Finding

The shared `llm.dialogue` backend cell registers `chief_engineer` alongside PM,
Architect, Director, and QA. The active desktop `AIDialoguePanel` also accepts
`chief_engineer` and Chief Engineer Workspace renders it through the shared
dialogue stack.

Two frontend role-chat type surfaces still narrowed dialogue roles to PM,
Architect, Director, and QA:

- `src/frontend/src/services/api.types.ts`
- `src/frontend/src/app/components/ai-dialogue/useRoleChat.ts`

This creates type drift between the backend dialogue contract and the desktop
dialogue components. It makes Chief Engineer chat support look exceptional even
though the backend route is generic and the desktop already uses the same panel.

## Contract

- Chief Engineer must be accepted by shared role-chat TypeScript contracts.
- The legacy `useRoleChat` hook must use the same role type as the active
  conversation/dialogue stack.
- The generic backend status route must have explicit Chief Engineer coverage.
- PM and Director role-chat behavior remains unchanged.

## Boundary

- Target cell: `llm.dialogue`
- Shared backend route: `polaris.delivery.http.routers.role_chat`
- Frontend scope: shared role-chat typing and tests for desktop dialogue parity.
- No role-specific duplicate dialogue implementation is introduced.
- No target-project code is generated or modified.

## Verification

- Backend role-router test proves `/v2/role/chief_engineer/chat/status`.
- Frontend service tests prove Chief Engineer role-chat status and stream paths.
- Frontend role contract tests prove shared types accept Chief Engineer.
- TypeScript typecheck and lint confirm no stale narrowing remains.
