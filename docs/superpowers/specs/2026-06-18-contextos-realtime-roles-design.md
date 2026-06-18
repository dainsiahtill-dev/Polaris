# ContextOS Real-Time All-Roles View — Design

**Date:** 2026-06-18  
**Scope:** Frontend-only enhancement of the ContextOS realtime dashboard.  
**Goal:** Make every role’s realtime context situation and internal ContextOS state observable, and improve the UI.

## Problem

The ContextOS dashboard (`src/frontend/src/app/components/contextos/`) already has:

- A `RoleInternalContext` interface and a `RoleCard.internalContext` field.
- A WebSocket-driven telemetry pipeline (`buildTelemetryFromStream`) that parses `llm`, `runtime_events`, and `process` streams.
- Backend events that carry `actor` (PM, Director, QA, ChiefEngineer, Architect) and structured ContextOS signals (`context.build` with `items_count`/`total_tokens`, `context.snapshot` with `snapshot_hash`, LLM usage).

However, `buildContextOSModel()` never populates `RoleCard.internalContext`, and the UI (`ContextOSWorkspace.tsx`) renders only a flat grid of role hex cards with no drill-down. The result: users cannot see *per-role* ContextOS internals such as each role’s event stream, projection count, context item/token counts, receipts, or current task.

## Approach

**Chosen approach: A — Frontend-only derivation from existing telemetry.**

Rationale:

- The existing WS event stream already contains enough role-tagged, ContextOS-structured signals to derive per-role internal state (actor, context.build, context.snapshot, llm usage, task refs).
- It delivers the required capability immediately without backend changes, avoids crossing the public/internal Cell fence, and keeps the change verifiable with frontend tests.
- The data model already reserves `RoleInternalContext` / `RoleCard.internalContext`, so this is a natural completion of an unfinished surface rather than a new subsystem.

Backend emission of richer per-role ContextOS lifecycle events (Approach B) is deferred until telemetry gaps are proven in practice.

## Design

### Data model changes

1. `contextOSData.ts`
   - Add a helper `buildRoleInternalContext(role, telemetry, blockedRoles)` that filters telemetry events by role aliases and aggregates:
     - `events`: the role’s most recent ContextOS events (subset, capped at `MAX_ROLE_EVENTS = 8`).
     - `eventCount`, `projectionCount`, `receiptCount`.
     - `contextItemsCount`, `contextTokensLatest` from the role’s own `context.build` events.
     - `totalTokens`, `promptTokens`, `completionTokens` from usage-bearing events.
     - `calls` via `telemetryRoleCalls` to avoid duplicating the telemetry-layer call definition.
     - `lastEventAt` epoch (treating `epoch <= 0` as invalid).
     - `state` reuses `roleState(role.key)` so it stays consistent with `RoleCard.state` (blocked > running > events > idle).
     - `currentTaskId` / `currentTaskTitle` remain `null` until `ContextOSEvent` carries task refs.
   - Populate `internalContext`, `lastEventAt`, `projectionCount`, `contextItemsCount`, and `receiptCount` inside the existing `RoleCard` builder.

2. `contextOSTelemetry.ts`
   - Export `ACTOR_ROLE_ALIASES` and add `filterEventsForRole(events, roleKey)` for reuse.
   - Add `telemetryRoleCalls(telemetry, roleId)` so per-role call counts share the same aggregation rule as the top-level telemetry.

### UI changes

1. `ContextOSWorkspace.tsx`
   - `RoleHex` now shows relative freshness when a role has a recent event.
   - Add a new `RoleInternalPanel` rendered inline when a role card is selected:
     - Header: role title, court title, state badge, last-event freshness, and token chip (when `totalTokens > 0`).
     - 4-stage internal mini-pipeline: TruthLog → WorkingMem → ProjectionEngine → ReceiptStore, each with `data-testid` + `data-state` for testability and a right-edge scroll fade for narrow viewports.
     - Stats grid: events, projections, receipts, calls, prompt tok, completion tok.
     - Recent event list (up to 8) with token / duration / receipt badges, `aria-label`, and an `aria-live="polite"` region; truncation indicator when `eventCount > 8`.
   - Selection continues to cross-filter the decision log.

### Testing

- `contextOSData.test.ts`: per-role internal context, context.build structured signals, blocked role, running-without-events state consistency, `lastEventAt`, null fallbacks for `contextItemsCount`/`contextTokensLatest`, `currentTaskId`/`currentTaskTitle`, and `MAX_ROLE_EVENTS` truncation.
- `ContextOSWorkspace.test.tsx`: panel open/close, internal pipeline stage state/metrics, token header chip conditional, zero-token role header absence, event token/duration badges.

### Adversarial review fixes applied

- `RoleInternalContext.state` now matches `RoleCard.state` for running roles that have not yet emitted WS events.
- `lastEventAt` uses an explicit `epoch > 0` check instead of falsy `||`.
- `calls` aggregation delegates to `telemetryRoleCalls` to avoid diverging from the telemetry layer.
- `WorkingMem` mini-pipeline stage uses `(contextItemsCount ?? 0) > 0` to avoid showing "active" with 0 items.
- Added truncation indicator and `aria-live` / `aria-label` to the event list.

### Out of scope (deferred)

- New backend endpoints or services.
- New runtime event types.
- Cross-role diff/comparison beyond the existing decision-log filter.
- Whole-word actor alias matching (current substring matching is pre-existing and shared with `telemetryRoleTokens`/`telemetryRoleEvents`).

## Verification

- `npm run typecheck` ✅
- `npm run lint` ✅ (only pre-existing unrelated warning in `useFactoryBench.ts`)
- `npm run test -- src/frontend/src/app/components/contextos` ✅ 47/47 passed
- Full `npm run test` ✅ 1053/1057 passed; 4 failures are pre-existing `src/hooks/useFactory.test.ts` failures unrelated to ContextOS changes.
