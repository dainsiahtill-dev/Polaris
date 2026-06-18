# ContextOS Real-Time Multi-Role Internal View — Design Spec

**Date:** 2026-06-18  
**Topic:** `contextos-realtime`  
**Status:** Draft (awaiting implementation)  

---

## 1. Goal

Continue improving the **ContextOS real-time dashboard** so that it can:

1. Observe the **real-time context situation of all roles** (PM, Architect, Chief Engineer, Director, QA, Scout).
2. Observe the **internal ContextOS situation of each role** (its events, projections, context items, tokens, receipts, current task, state).
3. Further improve the **UI/UX** of the dashboard.

The implementation must reuse the existing real-time transport and telemetry layers as much as possible, and must follow Polaris code-quality gates (`ruff`, `mypy`, `pytest`).

---

## 2. Current State

- `ContextOSWorkspace` (`src/frontend/src/app/components/contextos/ContextOSWorkspace.tsx`) renders a real-time dashboard.
- Data arrives via `useRuntime` WebSocket props: `llmStreamEvents`, `executionLogs`, `processStreamEvents`, `snapshot`, `dialogueEvents`, `llmRuntimeState`.
- `buildTelemetryFromStream` (`contextOSTelemetry.ts`) turns the WebSocket `LogEntry` streams into `ContextOSTelemetry`.
- `buildContextOSModel` (`contextOSData.ts`) turns telemetry + snapshot into `ContextOSModel`.
- The dashboard currently shows:
  - A system pipeline (8 stages).
  - Component health cards.
  - 5 role hex cards (PM, Architect, Chief Engineer, Director, QA) that only filter the decision stream.
  - Decision log / receipts.
  - Context budget, mode distribution, event-type distribution.
- The canonical durable log is the **LogEventWriter journal** (`runtime/logs/journal.{raw,norm,enriched}.jsonl` or `runtime/runs/{run_id}/logs/journal.{raw,norm,enriched}.jsonl`). The WebSocket streams are a live subset and can be capped/lossy.
- A `/v2/files/read?path=...&tail_lines=...` endpoint already exists and can read workspace files including the journal logs.

---

## 3. Requirements

### 3.1 Functional

1. **All roles visible**
   - Add **Scout** as a 6th role in the ContextOS role signal plane.
   - Preserve the existing 5 main roles.
   - Scout must be marked as read-only / auxiliary so users understand it does not take turns.

2. **Per-role internal ContextOS view**
   - Clicking a role card opens a **role detail drawer/panel** showing:
     - Role identity, state, and binding status.
     - Per-role event timeline (filtered from real-time telemetry).
     - Per-role token usage and call count.
     - Context projection metrics (projections, context items, context tokens, receipts).
     - Current task / session binding when available from the snapshot.
     - Recent context snapshots / receipts.
   - The existing decision-stream filter can be moved into the drawer or kept as a secondary action.

3. **Real-time journal log integration**
   - Introduce `useContextOSTelemetry(workspace, runId, intervalMs = 4000)` that polls the enriched journal log at `runtime/runs/{run_id}/logs/journal.enriched.jsonl` (or `runtime/logs/journal.enriched.jsonl` when `run_id` is unavailable) via `/v2/files/read`.
   - Parse JSONL lines into `LogEntry`-like objects and feed them into `buildTelemetryFromStream`.
   - Merge file-polled events with WebSocket events **by event id** when ids overlap; otherwise treat file events as a backfill source used when WebSocket telemetry is stale or capped.
   - Fall back gracefully when the journal file is missing or unreadable.

4. **UI improvements**
   - Role cards show richer real-time info (last event age, mini activity indicator, internal context count).
   - Role detail drawer uses a clean two-column layout: summary left, timeline right.
   - Add activity sparklines or recent-event bars for each role.
   - Improve empty / loading / offline states.

### 3.2 Non-functional

- **No polling without purpose**: the 4s poll is only for the observations file; everything else stays push-driven.
- **Honest metrics**: estimate only when real data is missing; label estimates explicitly.
- **Strict TypeScript**: no `any`, `strict` mode.
- **Test coverage**: unit tests for the new hook behavior, merge logic, and per-role model derivation; update/extend existing component tests.
- **No backend duplication**: reuse `/v2/files/read`; only add a small utility if parsing JSONL on the frontend becomes unwieldy.

---

## 4. Proposed Approaches

### 4.1 Approach A — Minimal Frontend-Only

- Add scout role and a per-role detail drawer.
- Use only existing WebSocket telemetry; no observations-file polling.

**Pros:** Small surface, fast to ship, no new network traffic.  
**Cons:** Does not satisfy the requirement to observe the canonical observations file; per-role detail will be limited by WebSocket buffer caps.

### 4.2 Approach B — File-Polling Primary

- Replace WebSocket-derived telemetry with `useContextOSTelemetry` polling `llm.observations.jsonl` every 4s.
- Keep WebSocket only for connection state.

**Pros:** Single consistent data source matching the canonical observations log.  
**Cons:** Adds polling latency; loses the "event arrives → render immediately" feel; higher server load; breaks existing real-time freshness semantics.

### 4.3 Approach C — Hybrid (Recommended)

- Keep WebSocket telemetry as the **live, immediate** source.
- Add `useContextOSTelemetry` as a **completeness/backfill** source that polls the observations file every 4s.
- Merge the two streams with id-based deduplication.
- Use the merged telemetry for the main dashboard and for the per-role detail drawer.

**Pros:**
- Preserves real-time feel.
- Fills historical gaps and buffer-capped losses from the canonical file.
- Per-role detail gets richer data.
- Low risk: WebSocket path is unchanged; file polling is additive.

**Cons:** Slightly more complex merge logic; requires stable event ids.

**Decision:** Adopt **Approach C**.

---

## 5. Architecture

### 5.1 Data Flow

```
┌─────────────────────────────────────────────────────────────┐
│  Backend                                                    │
│  ─────────────────────────────────────────────────────────  │
│  runtime/logs/journal.*.jsonl  ◄── LogEventWriter           │
│  /v2/files/read  ◄── serves tail of journal files           │
│  /v2/ws/runtime  ◄── pushes live LogEntry streams           │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│  Frontend                                                   │
│  ─────────────────────────────────────────────────────────  │
│  useRuntime ──► buildTelemetryFromStream ──┐                │
│                                            ▼                │
│  useContextOSTelemetry ──► parseJournalFile ──► merge       │
│                                            │                │
│                                            ▼                │
│                                ContextOSTelemetry             │
│                                            │                │
│                                            ▼                │
│  buildContextOSModel ──► ContextOSModel ──► ContextOSWorkspace
│                                            │                │
│                                            ▼                │
│                              RoleContextDrawer (per role)   │
└─────────────────────────────────────────────────────────────┘
```

### 5.2 New / Changed Modules

| Module | Change |
|--------|--------|
| `src/frontend/src/app/components/contextos/useContextOSTelemetry.ts` | **New hook**: polls observations file, parses JSONL, merges with optional WS telemetry. |
| `src/frontend/src/app/components/contextos/contextOSTelemetry.ts` | Add `mergeTelemetry(a, b)` helper and JSONL-to-`LogEntry` adapter. |
| `src/frontend/src/app/components/contextos/contextOSData.ts` | Add `buildRoleContextModel(role, model)`; extend `RoleCard` with `lastEventAt`, `projections`, `contextItems`, `receipts`; add Scout role. |
| `src/frontend/src/app/components/contextos/RoleContextDrawer.tsx` | **New component**: per-role detail drawer. |
| `src/frontend/src/app/components/contextos/ContextOSWorkspace.tsx` | Wire new hook, open drawer on role card click, richer role cards. |
| `src/frontend/src/app/components/contextos/contextOSData.test.ts` | Add tests for per-role model and Scout role. |
| `src/frontend/src/app/components/contextos/useContextOSTelemetry.test.ts` | **New tests** for hook. |
| `src/frontend/src/services/api.ts` | Add `fileV2Service.readTail(path, tailLines)` convenience if not present. |

### 5.3 Key Data Structures

```typescript
// useContextOSTelemetry.ts
export interface UseContextOSTelemetryOptions {
  workspace: string;
  runId?: string | null;
  intervalMs?: number;
  tailLines?: number;
  wsTelemetry?: ContextOSTelemetry | null;
  enabled?: boolean;
}

export interface UseContextOSTelemetryResult {
  telemetry: ContextOSTelemetry;
  loading: boolean;
  error: string | null;
  lastPollAt: number | null;
}

// contextOSData.ts
export interface RoleInternalContext {
  roleId: string;
  events: ContextOSEvent[];
  eventCount: number;
  projectionCount: number;
  receiptCount: number;
  contextItemsCount: number | null;
  contextTokensLatest: number | null;
  totalTokens: number;
  calls: number;
  lastEventAt: number | null;
  currentTaskId: string | null;
  currentTaskTitle: string | null;
  state: PipelineState;
}

export interface RoleCard {
  // existing fields
  lastEventAt: number | null;
  projectionCount: number;
  contextItemsCount: number | null;
  receiptCount: number;
  internalContext: RoleInternalContext;
}
```

---

## 6. Merge Strategy

1. Resolve the journal path: `runtime/runs/{run_id}/logs/journal.enriched.jsonl` if `run_id` is present, otherwise `runtime/logs/journal.enriched.jsonl`.
2. Read the tail via `/v2/files/read` and parse JSONL lines into `LogEntry[]` using `JSONL.parseLines` (new helper). Skip malformed lines.
3. Run `buildTelemetryFromStream(fileLlm, fileExecution, fileProcess)` to produce `fileTelemetry`.
4. Merge with `wsTelemetry`:
   - **Primary source**: WebSocket telemetry when it is active and fresh (last event < 30s).
   - **Backfill source**: file telemetry when WebSocket is offline, stale, or capped.
   - When both sources have events, attempt id-based deduplication with a `Map<string, ContextOSEvent>` keyed by `event.id`; because WS and journal ids may differ, also skip file events whose `(epoch, actor, name)` tuple is already present in the WS set.
   - Recompute aggregates (`totalTokens`, `calls`, `byActor`, etc.) from the merged event set.
5. Cap merged events at `MAX_EVENTS` (120) for render, but keep `parsedLines` honest (count of unique parsed lines from the chosen source(s)).

---

## 7. UI Design

### 7.1 Role Signal Plane

- 6 role cards in a responsive grid (`grid-cols-2 sm:grid-cols-3 lg:grid-cols-6`).
- Each card shows:
  - Initial / court title / role title.
  - State dot.
  - Detail metric (tokens or events).
  - Tiny "last activity" age.
- Click opens the drawer.
- A small filter button on each card (or in the drawer header) keeps the decision-stream filter.

### 7.2 Role Context Drawer

- Right-side drawer, width `max-w-2xl`.
- Header: role title, court title, state badge, close button.
- Left column (summary):
  - Calls, tokens, projections, receipts, context items.
  - Current task (if bound from snapshot).
  - Provider/binding status.
- Right column (timeline):
  - Filtered event list for this role, newest first.
  - Each row: time, event name, summary, token pill, latency pill, receipt pill.
- Footer: hint explaining data sources ("实时遥测 + 观测日志回填").

### 7.3 Empty / Offline States

- If no role events: "该角色暂无活动 — 启动对应角色后将实时流入".
- If file polling fails: show a subtle warning icon in the dashboard header with retry info.

---

## 8. Testing Plan

1. **Unit tests**
   - `useContextOSTelemetry.test.ts`: parsing, merge dedup, error handling, polling interval.
   - `contextOSData.test.ts`: `buildRoleContextModel` returns correct per-role metrics; Scout role present.
   - `ContextOSWorkspace.test.tsx`: clicking a role card opens the drawer; decision filter still works.

2. **Type checks**
   - `npm run typecheck` must pass.

3. **Lint / format**
   - `npm run lint` and `ruff format` on any touched Python files (none expected except possibly a tiny backend helper if needed).

4. **Manual / E2E**
   - Run `npm run dev` and open ContextOS view; verify role cards, drawer, and polling.

---

## 9. Risks & Mitigations

| Risk | Mitigation |
|------|------------|
| File polling creates duplicate events | Prefer fresh WebSocket telemetry; backfill with journal only when stale/capped; dedupe by id and `(epoch, actor, name)` tuple. |
| Journal path varies by run_id / workspace | Resolve path from `runId` prop with fallback to `runtime/logs/`; use `/v2/files/read`. |
| Large journal file causes lag | Default `tail_lines=400` and `max_chars=20000`; cap parsed lines. |
| Scout has no telemetry actor alias | Add `'scout'` to `ACTOR_ROLE_ALIASES` and `ROLE_DEFINITIONS`; gracefully degrade if no events. |
| Adding drawer hurts mobile UX | Drawer is responsive and closes on outside click / Esc. |

---

## 10. Future Optimization

- Backend SSE endpoint for journal file tail (avoids polling).
- Push role-internal context snapshots over the runtime WebSocket.
- Expand per-role detail with actual memory artifact previews (when a safe public API is available).

---

## 11. Self-Review Checklist

- [x] No placeholders or TODOs left unresolved.
- [x] Architecture matches feature descriptions.
- [x] Scope is focused on a single implementation plan.
- [x] Requirements are explicit and unambiguous.
- [x] Existing code paths are reused; no duplicate backend logic.

