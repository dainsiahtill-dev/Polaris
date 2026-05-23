# Control Panel Dialogue Clear Runtime Evidence

Date: 2026-05-23

## Scope

- Frontend App wiring: `src/frontend/src/app/App.tsx`
- Runtime facade: `src/frontend/src/app/hooks/useRuntime.ts`
- Sidebar pass-through: `src/frontend/src/app/components/ContextSidebar.tsx`
- Runtime service: `src/frontend/src/services/api.ts`
- Backend route reused: `/v2/runtime/clear`

## Root Cause

The right-side dialogue panel rendered an active `清空日志` control, but `App.tsx` passed an empty `onClearDialogueLogs` callback. Clicking the control did not call the existing runtime clear endpoint and did not clear the in-memory dialogue projection shown in the desktop UI.

## Fix

- Wired `onClearDialogueLogs` to `runtimeService.clearDialogue()`.
- Switched the frontend clear service to the v2 runtime clear endpoint with `scope: "dialogue"`.
- Exposed `setDialogueEvents()` from `useRuntime()` so the App can clear the visible dialogue projection after the backend call succeeds.
- Passed the live clearing state through `ContextSidebar` into `DialoguePanel`.

## Verification

- `npx eslint src/frontend/src/app/App.tsx src/frontend/src/app/hooks/useRuntime.ts src/frontend/src/services/api.ts src/frontend/src/services/__tests__/runtimeService.test.ts src/frontend/src/app/components/__tests__/ContextSidebar.test.tsx`
- `npm test -- src/services/__tests__/runtimeService.test.ts src/app/components/__tests__/ContextSidebar.test.tsx src/app/hooks/__tests__/useRuntime.test.ts`
- `npm run lint`
- `npm run typecheck`
- `npm test`
- `npm run build`

All commands passed on 2026-05-23. The final full frontend suite reported 75 files and 667 tests passed.
