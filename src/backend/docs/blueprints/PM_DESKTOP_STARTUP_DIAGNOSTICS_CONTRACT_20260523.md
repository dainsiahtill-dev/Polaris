# PM Desktop Startup Diagnostics Contract

Date: 2026-05-23

## Problem

The PM desktop diagnostics modal checked LanceDB, LLM readiness, and workspace state by issuing three raw frontend requests. That made PM startup troubleshooting harder to audit because endpoint selection, error normalization, and readiness semantics lived inside UI code.

## Contract

Add `GET /v2/pm/diagnostics` as a side-effect-free PM readiness snapshot:

- `lancedb`: normalized LanceDB availability from the existing health path.
- `llm`: normalized PM-relevant LLM readiness from the runtime projection public service.
- `workspace`: active workspace path plus docs presence.
- `issues`: deterministic issue tokens for the modal and tests.

The frontend consumes this route through `getPmStartupDiagnostics()` in `pmService.ts`. `PMDiagnosticsPanel` stays responsible for rendering and user interaction only.

## 2026-05-23 Follow-up: Active Workspace Precedence

Desktop runtime can hold a stale `settings.workspace` value while the selected
target workspace lives in `settings.workspace_path`. PM diagnostics must report
the same active workspace used by PM management and Chief Engineer desktop
routes:

1. `settings.workspace_path`
2. `settings.workspace`

This preserves legacy fallback while preventing diagnostics from checking the
Polaris repository when the desktop has selected a target project.

## Data Flow

```text
PMDiagnosticsPanel
  -> pmService.getPmStartupDiagnostics()
  -> GET /v2/pm/diagnostics
  -> runtime.projection.public.build_llm_status(settings)
  -> application.health.get_lancedb_status()
  -> workspace docs existence check
```

## Boundaries

This is a delivery-layer aggregation endpoint. It reuses existing public/runtime health capabilities and does not write state, start services, or mutate workspace files.

## Verification

- `src/backend/polaris/tests/unit/delivery/http/routers/test_v2_pm_router.py`
- `src/frontend/src/services/__tests__/pmService.test.ts`
- `src/frontend/src/app/components/pm/PMDiagnosticsPanel.test.tsx`
