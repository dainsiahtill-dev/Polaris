# Chief Engineer Page Wrapper Parity Blueprint

Date: 2026-05-23
Status: implemented
Classification: pattern

## Problem

PM and Director each have page-level wrappers that define the role desktop
contract: workspace props, shared settings callback, error boundary, runtime
overlay, and toaster placement. Chief Engineer was still assembled inline in
`App.tsx`, which made the role desktop contract harder to test and kept Chief
Engineer one layer behind the PM/Director page structure.

## Scope

This change is limited to frontend role desktop composition and governance
evidence:

- Add `ChiefEngineerPage` with the same wrapper responsibilities as `PMPage`
  and `DirectorPage`.
- Export `ChiefEngineerPage` from the page barrel.
- Route the App Chief Engineer branch through `ChiefEngineerPage`.
- Add a page test proving the shared settings callback reaches
  `ChiefEngineerWorkspace`.

No backend endpoint, runtime ownership, graph edge, or Cell public contract is
changed.

## Architecture Sketch

```text
App activeRoleView === chief_engineer
  -> ChiefEngineerPage
      -> ErrorBoundaryClass
      -> ChiefEngineerWorkspace
      -> LlmRuntimeOverlay(activeView = chief_engineer)
      -> Toaster
```

The page remains a presentation contract. Chief Engineer backend functions
continue to be reached by the workspace service calls already covered by the
Chief Engineer workspace and service tests.

## Assumption Register

- `PMPage` and `DirectorPage` are the existing role page wrapper pattern.
- Chief Engineer should retain the same `activeView` token used by runtime
  overlay evidence: `chief_engineer`.
- App remains the owner of role routing, shared settings state, and runtime
  snapshot data.
- This wrapper does not need a new backend route because it only composes
  existing role desktop state.

## Verification Plan

- `npm run test -- ChiefEngineerPage ChiefEngineerWorkspace`
- `npm run typecheck`
- `npm run lint`
- Cross-role page/workspace regression:
  `npm run test -- PMPage ChiefEngineerPage PMWorkspace ChiefEngineerWorkspace DirectorWorkspace PMWorkbenchPanel DirectorWorkbenchPanel PMTaskPanel DirectorTaskPanel PMDiagnosticsPanel pmService chiefEngineerService RoleChatPanel api.roleChatService`

