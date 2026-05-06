# Project Director 10-Batch Audit Blueprint - 2026-05-06

Status: active
Owner: Project Director (Codex)
Scope: Polaris end-to-end reliability, runtime streaming, and production hardening

## 1. Current Understanding

### Project goal
Polaris is a meta-tool platform. This mission is to drive deep reliability audit and targeted hardening without broad refactor, then verify with executable evidence.

### Module responsibilities (current focus)
- `src/backend/polaris/delivery/ws`: runtime websocket protocol and stream delivery.
- `src/backend/polaris/infrastructure/messaging/nats`: JetStream consumer and realtime event transport.
- `src/backend/polaris/infrastructure/realtime/process_local`: local signal fanout and watch wake-up.
- `src/frontend/src/app/hooks` + `src/frontend/src/runtime/transport`: runtime stream ingestion and filtering.
- `src/electron`: desktop bootstrap and backend/workspace config handoff.

### Code path currently under investigation
`Electron startup -> settings/workspace resolve -> backend runtime ws -> v2 subscribe -> nats/local signal -> frontend parser/store -> llm/runtime/dialogue panels`.

### Current problem hypothesis
- The primary issue is not a single crash point.
- The issue is likely a chain mismatch across protocol state, unsubscribe semantics, run pointer transition, and client filtering.

## 2. Evidence Register

### Read files and collected reports
- Root and backend agent governance docs (`AGENTS.md`, `src/backend/AGENTS.md`, `src/backend/docs/AGENT_ARCHITECTURE_STANDARD.md`).
- WS core/protocol/loop and stream modules.
- Journal channel and run pointer modules.
- Frontend runtime connection and parsing hooks.
- Electron config and process entry files.
- Multiple independent subagent reports (Batch 1 complete, Batch 2 in progress).

### Confirmed call chain
1. `runtime_endpoint` routes to `runtime_websocket`.
2. `runtime_websocket` enters `run_main_loop`.
3. `run_main_loop` dispatches to `handle_client_message` and stream emitters.
4. v2 path relies on `JetStreamConsumerManager` connection lifecycle.
5. Frontend runtime hooks parse and filter events before panel state update.

### Confirmed risks
- `UNSUBSCRIBE` semantics can cut entire v2 stream.
- Signature window state can drift due to copy-by-value handling.
- Run pointer transition can skip initial lines of a new run file.

### Not yet confirmed
- Whether `signal_hub` wake-up filtering is a direct user-facing blocker in this environment.
- Whether frontend parsing filters still drop valid events after recent local edits.
- Whether Electron startup workspace override is fully consistent under all fallback branches.

## 3. Defect Analysis (Current)

### Defect A: v2 unsubscribe disconnects whole consumer
- Trigger: partial channel unsubscribe.
- Direct cause: unconditional disconnect of consumer manager.
- Impact: runtime v2 event stream stops until full resubscribe.

### Defect B: run transition state can skip early journal lines
- Trigger: `latest_run.json` changes to a new run whose file size is larger than previous cursor position.
- Direct cause: incremental cursor state does not always reset on file identity change.
- Impact: missing llm/runtime events at run start.

### Defect C: dedupe window bookkeeping is not fully shared
- Trigger: snapshot/incremental path passing copied signature order buffer.
- Direct cause: bounded deque trim cannot reliably mutate outer state.
- Impact: long-run dedupe drift, possible false duplicate suppression and memory growth.

## 4. Repair Strategy (Minimal and Safe)

### Intended minimal modifications
1. Stabilize v2 subscribe/unsubscribe state machine without changing public API shape.
2. Add run file identity reset guard in journal incremental read path.
3. Ensure dedupe order container is shared mutable state across all emission paths.
4. Tighten protocol error handling for malformed numeric fields/cursor.

### Explicit non-changes
- No broad redesign of ws protocol.
- No graph/cell boundary reshaping.
- No unrelated router or UI refactors.

### Why this is safest
- All changes stay inside existing ownership boundaries (`delivery/ws`, `infrastructure/messaging`, runtime hooks).
- External contract remains compatible while fixing correctness.

## 5. 10-Batch Execution Plan

Batch composition rule: each batch uses up to 5 subagents and returns evidence with file/line references.

1. Batch 1 (done): WS protocol and journal chain root-cause audit.
2. Batch 2 (running): NATS consumer + ws loop + signal hub + frontend parser + electron handoff.
3. Batch 3: runtime state owner + storage layout + settings fallback consistency.
4. Batch 4: ws test coverage and missing regression inventory.
5. Batch 5: PM/Director runtime event publication chain.
6. Batch 6: role gating and channel authorization correctness.
7. Batch 7: retry/idempotency in async runtime tasks.
8. Batch 8: exception taxonomy and error response contract consistency.
9. Batch 9: observability and evidence completeness for stream failures.
10. Batch 10: consolidation audit and patch acceptance readiness review.

## 6. Test Plan

### Happy path
- v2 subscribe receives runtime events continuously.
- llm/dialogue panels receive expected stream events after run start.

### Edge cases
- Partial unsubscribe does not terminate unrelated channels.
- Repeated subscribe updates do not leak consumers.
- Run pointer switches across runs keep first events visible.

### Exception paths
- Malformed cursor/tail input yields structured error and no loop crash.
- Consumer disconnect during stream loop recovers cleanly.

### Regression cases
- Existing ws tests continue to pass.
- New tests cover unsubscribe semantics, run transition cursor reset, and shared dedupe container behavior.

## 7. Rollback Plan

If a patch regresses behavior:
1. Revert only touched files in this batch.
2. Re-run affected ws/frontend tests.
3. Confirm runtime stream baseline before moving to next batch.

Files changed per batch must be logged in the final audit report with before/after evidence.
