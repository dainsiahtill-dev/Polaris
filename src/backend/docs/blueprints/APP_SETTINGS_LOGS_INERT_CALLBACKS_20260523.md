# App Settings And Logs Inert Callbacks

Date: 2026-05-23

## Scope

- Frontend App wiring: `src/frontend/src/app/App.tsx`
- UI state hook: `src/frontend/src/hooks/useUIState.ts`
- Existing settings source: `/v2/llm/status`
- Existing logs modal banner control: `src/frontend/src/app/components/LogsModal.tsx`

## Root Cause

Two App-level callbacks were wired as empty functions:

- `SettingsModal.onLlmStatusChange` dropped the LLM readiness payload loaded from `/v2/llm/status`, so Settings-driven status refreshes did not update the desktop readiness gate.
- `LogsModal.onDismissBanner` rendered a dismiss button for log banners, but clicking it did not clear `ui.logsBanner`.

## Fix

- Routed SettingsModal LLM status updates into the existing `applyLlmStatusPayload()` gate path.
- Reset the App LLM gate to `UNKNOWN` when SettingsModal reports a null status.
- Added `dismissLogsBanner()` to `useUIState()`.
- Wired `LogsModal.onDismissBanner` to clear only the banner while keeping the logs modal open.

## Verification

- `npx eslint src/frontend/src/app/App.tsx src/frontend/src/hooks/useUIState.ts src/frontend/src/hooks/useUIState.test.tsx src/frontend/src/app/components/SettingsModal.test.tsx src/frontend/src/app/components/LogsModal.test.tsx`
- `npm test -- src/hooks/useUIState.test.tsx src/app/components/SettingsModal.test.tsx src/app/components/LogsModal.test.tsx`
- `npm run lint`
- `npm run typecheck`
- `npm test`
- `npm run build`

All commands passed on 2026-05-23. The final full frontend suite reported 75 files and 668 tests passed.
