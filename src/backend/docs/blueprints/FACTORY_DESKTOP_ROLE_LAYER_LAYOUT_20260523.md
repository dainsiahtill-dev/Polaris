# Factory Desktop Role Layer Layout

Date: 2026-05-23
Classification: pattern
Owner: Codex

## Problem

Factory mode currently renders PM and Director as two full desktop workspaces
side by side, then adds a third realtime panel and the floating LLM runtime
overlay. The result is a crowded horizontal composition where nested headers,
sidebars, chat panes, runtime status, and audit evidence compete at the same
visual level.

The desktop experience needs a clear three-role hierarchy for:

- PM: planning and task contract evidence.
- Chief Engineer: blueprint and handoff readiness.
- Director: execution and file/tool evidence.

## Architecture

```text
Factory shell
  -> global run header and controls
  -> role layer rail: PM -> Chief Engineer -> Director
  -> focused role console, one layer visible at a time
  -> operations rail: live activity + gates + artifacts + summary
  -> compact LLM runtime overlay in Factory mode
```

## Scope

- Replace side-by-side full role consoles with a role layer switcher.
- Add a Chief Engineer blueprint layer derived from task blueprint evidence and
  Factory role status.
- Move audit evidence from the left sidebar into a right operations rail.
- Keep PM and Director full consoles available as focused layers.
- Compact the Factory LLM runtime overlay so it no longer opens over the main
  operations rail by default.

## Non-Goals

- No backend API changes.
- No real LLM/provider invocation.
- No changes to PM, Chief Engineer, or Director standalone pages.
- No Electron E2E claim without rerunning the Electron court workflow.

## Verification Plan

- `npm run test -- FactoryWorkspace LlmRuntimeOverlay`
- `npm run typecheck`
- `npm run lint`
- `npm run test -- PMPage ChiefEngineerPage DirectorPage FactoryWorkspace LlmRuntimeOverlay`
