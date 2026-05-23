# Role Page Router Convergence Blueprint

Date: 2026-05-23
Status: implemented
Classification: pattern

## Problem

Chief Engineer now has a page-level wrapper and `App.tsx` routes through it, but
PM and Director were still assembled inline in `App.tsx`. The existing PM and
Director page wrappers also did not preserve every runtime evidence prop from
the inline branches, such as PM terminal blockage context, task traces,
Director file edit events, and cross-role running state in the runtime overlay.

This left the three role desktops with two composition patterns and made page
contracts weaker than the App branches they were meant to represent.

## Scope

This change is limited to frontend role desktop composition and governance
evidence:

- Extend `PMPage` so it forwards the same PM runtime banner, quality, task
  trace, file edit, and Director running evidence that App currently has.
- Extend `DirectorPage` so it forwards file edit evidence, task trace evidence,
  and PM running state to the workspace and overlay.
- Route App PM and Director branches through `PMPage` and `DirectorPage`.
- Add tests that prove the page wrappers carry the runtime evidence into their
  workspace and overlay children.

No backend route, Cell public contract, graph edge, or state owner changes are
required.

## Architecture Sketch

```text
App role router
  -> PMPage
      -> PMWorkspace
      -> LlmRuntimeOverlay(activeView = pm)

  -> ChiefEngineerPage
      -> ChiefEngineerWorkspace
      -> LlmRuntimeOverlay(activeView = chief_engineer)

  -> DirectorPage
      -> DirectorWorkspace
      -> LlmRuntimeOverlay(activeView = director)
```

App remains the state owner for current runtime snapshots. Page wrappers are
presentation contracts that preserve the same data path already used by the
inline branches.

## Assumption Register

- `App.tsx` is the active desktop role router.
- PM and Director page wrappers already exist and should be the canonical role
  page composition layer.
- The wrapper conversion must preserve current App behavior before adding new
  UI behavior.
- Runtime overlay evidence should keep the same role-specific `activeView`
  tokens.

## Verification Plan

- `npm run test -- PMPage ChiefEngineerPage DirectorPage`
- `npm run typecheck`
- `npm run lint`
- Cross-role regression:
  `npm run test -- PMPage ChiefEngineerPage PMWorkspace ChiefEngineerWorkspace DirectorWorkspace PMWorkbenchPanel DirectorWorkbenchPanel PMTaskPanel DirectorTaskPanel PMDiagnosticsPanel pmService chiefEngineerService RoleChatPanel api.roleChatService`

