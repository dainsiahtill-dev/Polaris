# Role Run Evidence Refresh Desktop Blueprint

Date: 2026-05-24

## Problem

PM, Chief Engineer, and Director workbenches can launch or export orchestration runs, then show the run evidence strip. The strip currently reflects the first backend snapshot only. A run that later completes, fails, or is cancelled can remain visually stuck at `RUNNING`, which weakens operator trust and makes the desktop surface harder to audit.

## Scope

- Shared frontend evidence component: `RoleRunEvidenceStrip`
- PM workbench run evidence for `/v2/pm/runs/{run_id}`
- Chief Engineer and Director run evidence for `/v2/director/runs/{run_id}`
- Existing workbench and shared component tests

## Data Flow

```text
PM / Chief Engineer / Director action
  -> orchestration run_id
  -> RoleRunEvidenceStrip
  -> manual refresh button
  -> workbench get*Run backend query
  -> auto-refresh while status is non-terminal
  -> terminal snapshot disables auto-refresh and cancel
```

## Design

1. Add an icon-only refresh control with an accessible label and stable dimensions.
2. Preserve the current snapshot while refresh is in progress, avoiding blank flicker.
3. Start polling only when a visible run is non-terminal and not already refreshing or cancelling.
4. Stop polling on terminal statuses, errors, or cancellation.
5. Keep PM and Director backend contracts unchanged; this is a desktop observability improvement.

## Verification

- Shared component test covers refresh button behavior and auto-refresh marker.
- PM workbench test covers refreshing `/v2/pm/runs/{run_id}` into a terminal snapshot.
- Director workbench test covers refreshing `/v2/director/runs/{run_id}`.
- Chief Engineer workbench test covers refreshing the Director run created from CE handoff.
