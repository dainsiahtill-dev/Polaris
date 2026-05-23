# Director Workspace Settings Entry Audit

Date: 2026-05-23

## Findings

- The Director desktop header rendered a settings icon without a click handler. This made a visible control inert and prevented Director users from opening the shared settings surface used by PM and the main control panel.
- The Director code, terminal, and debug panels exposed visible toolbar actions without backing handlers.
- The PM task row exposed a hover action button with no backing menu or command.

## Root Cause

`DirectorWorkspace` did not expose an `onOpenSettings` host callback, while `App` already had the settings modal and backend-backed settings/LLM status flow. Some Director subpanels also rendered planned actions before their host contracts were wired.

## Fix

- Added optional `onOpenSettings` to `DirectorWorkspace`.
- Passed `uiActions.openSettings()` from the App Director role view.
- Threaded the callback through `DirectorPage` for page-level hosts.
- Disabled the header settings button when no host callback is available, including embedded factory usage.
- Wired Director code panel file opening through the existing Electron `openPath` bridge with workspace-relative path resolution.
- Wired Director terminal clear to local terminal output state.
- Wired Director debug-panel task actions to task inspection and Director task cancellation.
- Removed the unbacked PM task-row hover menu button.

## Verification

Targeted frontend checks must cover:

- `src/frontend/src/app/components/director/__tests__/DirectorWorkspace.capabilities.test.tsx`
- `src/frontend/src/app/components/director/DirectorWorkspace.tsx`
- `src/frontend/src/app/App.tsx`
- `src/frontend/src/app/pages/DirectorPage.tsx`

The regression tests assert that:

- the Director header settings button invokes the shared callback and is disabled when a host callback is absent;
- the code panel opens the latest file edit through `openPath`;
- the terminal clear button clears visible output;
- the debug panel calls Director task cancellation and can jump to the selected task detail.
