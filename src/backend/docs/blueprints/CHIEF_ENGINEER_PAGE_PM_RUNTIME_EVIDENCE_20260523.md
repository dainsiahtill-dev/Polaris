# Chief Engineer Page PM Runtime Evidence Blueprint

Date: 2026-05-23
Status: implemented
Classification: pattern

## Problem

`ChiefEngineerPage` centralizes the Chief Engineer role desktop, but its runtime
overlay still hardcoded `pmRunning={false}`. Before the page wrapper conversion,
the inline App branch passed live PM running state into the overlay. This meant
the Chief Engineer desktop could hide active PM runtime evidence while PM and
Director pages preserved cross-role running state.

## Scope

This change is limited to frontend role desktop composition and governance
evidence:

- Add an explicit `pmRunning` prop to `ChiefEngineerPage`.
- Pass App's `effectivePmRunning` value into the Chief Engineer page.
- Forward `pmRunning` to `LlmRuntimeOverlay`.
- Extend the Chief Engineer page test to prove the overlay receives live PM
  running evidence.

No backend route, runtime state owner, graph edge, or Cell contract changes are
required.

## Architecture Sketch

```text
App effectivePmRunning
  -> ChiefEngineerPage.pmRunning
      -> LlmRuntimeOverlay(activeView = chief_engineer, pmRunning)
```

App remains the runtime snapshot owner. The Chief Engineer page remains a
presentation contract that preserves the same overlay evidence path as PM and
Director.

## Assumption Register

- App computes the authoritative PM running state as `effectivePmRunning`.
- `LlmRuntimeOverlay` is the shared role runtime evidence surface.
- Chief Engineer users need to see PM runtime activity because CE blueprints are
  downstream of PM planning.
- This is a frontend composition fix, not a backend runtime contract change.

## Verification Plan

- `npm run test -- ChiefEngineerPage`
- `npm run typecheck`
- `npm run lint`
- Cross-role regression:
  `npm run test -- PMPage ChiefEngineerPage PMWorkspace ChiefEngineerWorkspace DirectorWorkspace PMWorkbenchPanel DirectorWorkbenchPanel PMTaskPanel DirectorTaskPanel PMDiagnosticsPanel pmService chiefEngineerService RoleChatPanel api.roleChatService`

