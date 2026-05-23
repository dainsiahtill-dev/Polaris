# Control Panel Brain Memory Surface

Date: 2026-05-23

## Scope

- Frontend App wiring: `src/frontend/src/app/App.tsx`
- Sidebar surface: `src/frontend/src/app/components/ContextSidebar.tsx`
- UI state hook: `src/frontend/src/hooks/useUIState.ts`
- Header control: `src/frontend/src/app/components/ControlPanel.tsx`
- Reused memory source: `runtime/memory/last_state.json` through `useMemory()`

## Root Cause

The top-level `明镜台 (Brain)` menu item was rendered as an active command, but `App.tsx` passed an empty `onOpenBrain` callback. The memory sidebar also received an empty cognition-mode setter, so the `认知` / `原始` toggle did not update desktop state from the App layer.

## Fix

- Made `ContextSidebar` support a controlled active tab while preserving its existing local default behavior.
- Added an explicit `setShowCognition()` action to `useUIState()`.
- Wired `明镜台 (Brain)` to select the existing backend-backed memory/cognition sidebar surface.
- Wired the sidebar cognition toggle to the App UI state instead of an empty callback.

## Verification

- `npx eslint src/frontend/src/app/App.tsx src/frontend/src/app/components/ContextSidebar.tsx src/frontend/src/app/components/ControlPanel.test.tsx src/frontend/src/app/components/__tests__/ContextSidebar.test.tsx src/frontend/src/hooks/useUIState.ts src/frontend/src/hooks/useUIState.test.tsx`
- `npm test -- src/hooks/useUIState.test.tsx src/app/components/__tests__/ContextSidebar.test.tsx src/app/components/ControlPanel.test.tsx`
- `npm run lint`
- `npm run typecheck`
- `npm test`
- `npm run build`

All commands passed on 2026-05-23. The final full frontend suite reported 74 files and 665 tests passed.
