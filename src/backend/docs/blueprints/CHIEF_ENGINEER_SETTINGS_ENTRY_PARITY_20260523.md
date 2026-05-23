# Chief Engineer Settings Entry Parity Blueprint

Date: 2026-05-23
Status: implemented
Classification: pattern

## Problem

PM and Director role workspaces expose the shared Settings surface from their
desktop headers. Chief Engineer has the same desktop role status and depends on
the same backend configuration, but its header has no Settings entry. Users must
leave the role workspace before adjusting runtime or provider configuration.

## Scope

This change is limited to the Electron frontend role workspace shell and its
governance evidence:

- Add an optional `onOpenSettings` callback to `ChiefEngineerWorkspace`.
- Render a settings icon button in the Chief Engineer header using the same
  disabled fallback contract as PM and Director.
- Wire the main App Chief Engineer route to the existing shared
  `uiActions.openSettings()` action.
- Add a focused component regression test for the callback path.

No backend runtime contract, Cell boundary, graph ownership, or settings API
changes are required.

## Architecture Sketch

```text
App role router
  -> ChiefEngineerWorkspace(onOpenSettings = uiActions.openSettings)
      -> Header settings icon
          -> Shared Settings modal/surface
```

The settings state owner remains the existing App UI state. Chief Engineer only
receives a presentation callback, matching PM and Director.

## Assumption Register

- `PMWorkspace` and `DirectorWorkspace` already expose optional
  `onOpenSettings` callbacks.
- `App.tsx` already owns the shared settings action through `uiActions`.
- Chief Engineer workspace is a frontend composition surface; opening settings
  should not create a new backend route.
- Optional callback keeps embedded or test hosts from needing a settings owner.

## Verification Plan

- `npm run test -- ChiefEngineerWorkspace`
- `npm run typecheck`
- `npm run lint`
- Cross-role regression:
  `npm run test -- PMWorkspace ChiefEngineerWorkspace DirectorWorkspace PMWorkbenchPanel DirectorWorkbenchPanel PMTaskPanel DirectorTaskPanel PMDiagnosticsPanel pmService chiefEngineerService RoleChatPanel api.roleChatService`
