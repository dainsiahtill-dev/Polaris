# Codex-RS Five-Dimension Optimization Blueprint

**Date**: 2026-04-24
**Status**: Active
**Cell**: `roles.kernel` (primary), `roles.runtime` (correlation propagation)
**Authority**: `AGENTS.md` > `cells.yaml` > this Blueprint

---

## 1. Background & Motivation

Audit of OpenAI Codex-RS project identified 6 patterns worth importing into Polaris.
After user ruling, the final implementation order is:

1. **Correlation ID** -- already partially exists (`_TURN_REQUEST_ID_CONTEXT` etc.)
2. **TruthLog** -- `TurnTruthLogRecorder` exists but has a wiring gap
3. **Structured Cancellation + Circuit Breaker** -- `CancelToken` exists at 60-70% coverage
4. **Effect Policy Enforcement** -- `effects_allowed` declared in 54 cells but never checked
5. **ConfigStack** -- fragmented config with no cascade or validation

Circuit Breaker dimensional upgrade was already completed and verified (22 tests pass).

---

## 2. Architecture Analysis

### 2.1 Event Emission Dual Path (Critical Finding)

`TurnTransactionController` has TWO event emission paths:

```
Path A (yield path) - execute_stream() lines 1077-1091:
  StreamOrchestrator.execute_turn_stream() yields TurnEvent
    -> _execute_turn_stream() proxies yield
      -> execute_stream() attaches correlation + records to TruthLog + yields

Path B (callback path) - _emit_phase_event() line 768:
  StreamOrchestrator.emit_event(callback) -> _emit_phase_event()
    -> attaches correlation from context vars
    -> dispatches to _event_handlers[]
    -> does NOT record to TruthLog  <-- GAP
```

**Callers of Path B** (bypass TruthLog):
- `stream_orchestrator.py:975` -- `decision_requested`
- `stream_orchestrator.py:1026` -- `decision_completed`
- `turn_transaction_controller.py:1021` -- ErrorEvent (non-stream execute)
- `turn_transaction_controller.py:1206` -- `decision_requested` (non-stream)
- `turn_transaction_controller.py:1238` -- `decision_completed` (non-stream)
- `turn_transaction_controller.py:1965,1994` -- additional phase events

### 2.2 Correlation ID State

Context vars defined at `turn_transaction_controller.py:131-133`:
- `_TURN_REQUEST_ID_CONTEXT` -- set in `execute_stream()` line 1071
- `_TURN_SPAN_ID_CONTEXT` -- set in `execute_stream()` line 1072
- `_TURN_PARENT_SPAN_ID_CONTEXT` -- set in `execute_stream()` line 1073

**Gap**: `_emit_phase_event()` reads from context vars (line 772), but:
- Orchestrator layer (`RoleSessionOrchestrator`) does not set `parent_span_id`
- Session-level events (`SessionStartedEvent`, `SessionCompletedEvent`) have no correlation fields
- Non-stream `execute()` path does NOT set context vars

### 2.3 CancelToken Coverage

`CancelToken` at `speculation/models.py:138-164`:
- Lightweight cooperative flag (`_cancelled: bool`, `_reason: str | None`)
- `check_cancel(token)` raises `asyncio.CancelledError`

**Current coverage**:
- `tool_batch_executor.py:757` -- creates `batch_cancel_token`
- `tool_batch_runtime.py:512-528` -- 3-point cancel check
- `SpeculativeExecutor` -- uses cancel token

**Gaps**:
- Individual tool executor functions do not check cancel token
- `ExplorationWorkflowRuntime` has no cancel support
- `asyncio.gather(*tasks)` in tool runtime has no cleanup on partial cancel
- No cross-turn task leak detection

### 2.4 Effect Policy

`cell.yaml` declares `effects_allowed` (e.g., `fs.read:workspace/**`, `llm.invoke:roles/*`).
These are **never checked at runtime** before tool execution.

### 2.5 Config Fragmentation

- `Settings` (global env-based)
- `KernelConfig` (kernel-level)
- `TransactionConfig` (89 fields, zero validation)
- `ToolExecutionContext` (per-tool)

No cascade, no validation, no override layering.

---

## 3. Implementation Plan

### Phase 1: TruthLog Middleware (Engineer #1)

**Problem**: `_emit_phase_event()` callback path bypasses TruthLog recording.

**Solution**: Store `TurnTruthLogRecorder` reference on controller instance during
`execute_stream()` lifecycle, and record events in `_emit_phase_event()`.

**Files to modify**:
1. `turn_transaction_controller.py`:
   - Add `_active_truthlog_recorder: TurnTruthLogRecorder | None = None` instance field
   - In `execute_stream()`: set `self._active_truthlog_recorder = truthlog_recorder` before try block
   - In `execute_stream()` finally: clear `self._active_truthlog_recorder = None`
   - In `_emit_phase_event()`: after correlation attachment, if `self._active_truthlog_recorder` is not None,
     schedule best-effort async recording via `asyncio.get_event_loop().create_task()`

**Constraint**: Recording in `_emit_phase_event` must be fire-and-forget (non-blocking)
since the callback is synchronous. Use the same pattern as `_record_turn_truthlog_event`
wrapped in a try/except with logger.warning on failure.

**Risk**: `_emit_phase_event` is sync but `recorder.record()` is async. Must use
`asyncio.ensure_future()` or `loop.create_task()` with suppressed exceptions.

### Phase 2: Correlation ID Propagation (Engineer #2)

**Problem**: Context vars are set in `execute_stream()` but not in non-stream `execute()`.
Orchestrator layer does not propagate `parent_span_id`.

**Solution**:
1. In `execute()` (non-stream path): set the same 3 context vars as `execute_stream()`
2. In `RoleSessionOrchestrator.execute_stream()`: generate a session-level span_id,
   pass it as `parent_span_id` to `TurnTransactionController.execute_stream()`
3. Add `turn_request_id` field to `SessionStartedEvent` and `SessionCompletedEvent`

**Files to modify**:
1. `turn_transaction_controller.py`: Add context var setup to `execute()` path (~line 1000)
2. `polaris/cells/roles/runtime/internal/session_orchestrator.py`: Generate session span,
   pass as `parent_span_id` when calling kernel
3. `polaris/cells/roles/kernel/public/turn_events.py`: Add correlation fields to
   `SessionStartedEvent`, `SessionWaitingHumanEvent`, `SessionCompletedEvent`

### Phase 3: Structured Cancellation Hardening (Engineer #3)

**Problem**: Cancel token exists but coverage is 60-70%.

**Solution**:
1. Thread `cancel_token` through individual tool executor functions (not just batch level)
2. Add `check_cancel()` calls at key I/O boundaries in network tools
3. Implement `ExplorationWorkflowRuntime.cancel()` method
4. Replace bare `asyncio.gather(*tasks)` with try/finally that cancels remaining tasks
5. Add `_active_tasks: set[asyncio.Task]` tracking to `ToolBatchRuntime` for leak detection

**Files to modify**:
1. `tool_batch_runtime.py`: Add cancel check before each tool dispatch, cleanup gather
2. `tool_batch_executor.py`: Propagate cancel token into retry loops
3. `exploration_workflow.py`: Add `cancel()` method, check token in workflow loop
4. `speculation/models.py`: Add `CancelToken.cancel_after(timeout_seconds)` convenience

### Phase 4: Comprehensive Tests (Engineer #4)

**New test files**:
1. `tests/test_truthlog_emit_event_integration.py`:
   - Verify _emit_phase_event records to TruthLog when recorder is active
   - Verify callback-path events appear in JSONL output
   - Verify no recording when recorder is None (graceful degradation)

2. `tests/test_correlation_propagation.py`:
   - Verify non-stream execute() sets context vars
   - Verify session-level span_id flows to turn-level parent_span_id
   - Verify SessionStartedEvent gets correlation fields

3. `tests/test_cancellation_hardening.py`:
   - Verify cancel token propagates to individual tool executors
   - Verify gather cleanup on partial cancellation
   - Verify ExplorationWorkflowRuntime.cancel() stops workflow

4. `tests/test_circuit_breaker_regression.py`:
   - Re-verify all 22 existing tests still pass
   - Add edge case: dimension trigger before global threshold
   - Add edge case: effect_threshold_overrides with missing scope

### Phase 5: Effect Policy Compiler (Engineer #5)

**Problem**: `effects_allowed` in `cell.yaml` is declarative only, never enforced.

**Solution**: Build `EffectPolicyCompiler` that:
1. At kernel init, loads the cell's `effects_allowed` from `cell.yaml`
2. Compiles patterns into a `CompiledEffectPolicy` with glob matching
3. Before tool execution, checks tool's `effect_type` against compiled policy
4. On violation: log warning (soft mode) or reject execution (strict mode)

**Files to create/modify**:
1. NEW: `polaris/cells/roles/kernel/internal/transaction/effect_policy.py`:
   - `EffectPolicyCompiler.compile(effects_allowed: list[str]) -> CompiledEffectPolicy`
   - `CompiledEffectPolicy.check(effect_type: str, scope: str) -> PolicyVerdict`
2. `tool_batch_executor.py`: Add policy check before dispatching each tool
3. `cell.yaml`: No changes needed (declarations already exist)

**Mode**: Start in `warn` mode (log but don't block), configurable via env var
`KERNELONE_EFFECT_POLICY_MODE=warn|strict`.

---

## 4. Data Flow (Target State)

```
RoleSessionOrchestrator.execute_stream()
  |-- generates session_span_id
  |-- passes parent_span_id to kernel
  v
TurnTransactionController.execute_stream()
  |-- sets _TURN_REQUEST_ID_CONTEXT, _TURN_SPAN_ID_CONTEXT, _TURN_PARENT_SPAN_ID_CONTEXT
  |-- creates TurnTruthLogRecorder, stores as self._active_truthlog_recorder
  |
  |-- Path A (yield): events -> attach correlation -> record truthlog -> yield
  |-- Path B (callback): _emit_phase_event -> attach correlation -> record truthlog (async)
  |                                                                -> dispatch handlers
  v
  |-- ToolBatchExecutor
  |     |-- effect policy check (CompiledEffectPolicy)
  |     |-- cancel token propagated to each tool
  |     |-- circuit breaker (dimensional) on receipt
  |     v
  |-- ExplorationWorkflowRuntime (cancel-aware)
  |
  v
TurnTruthLogRecorder -> {workspace}/.polaris/runtime/events/kernel.turn.truthlog.events.jsonl
```

---

## 5. Verification Gates

After all implementation:

1. `ruff check polaris/cells/roles/kernel/ --fix`
2. `ruff format polaris/cells/roles/kernel/`
3. `mypy polaris/cells/roles/kernel/`
4. `pytest polaris/cells/roles/kernel/tests/ -q`
5. Existing 22 circuit breaker tests must not regress

---

## 6. Risk Assessment

| Risk | Severity | Mitigation |
|------|----------|------------|
| `_emit_phase_event` is sync, recorder is async | Medium | Use `asyncio.ensure_future()` with exception suppression |
| Adding fields to Session events breaks consumers | Low | All new fields default to `None`, backward compatible |
| Effect policy false positives in warn mode | Low | Start in warn mode, audit logs before enabling strict |
| Cancel token in gather may cause partial results | Medium | Use return_exceptions=True + post-filter cancelled |
| Config cascade complexity | High | Defer to Phase 6, only validate TransactionConfig now |

---

## 7. Out of Scope (This Blueprint)

- ConfigStack full cascade (deferred to future Blueprint)
- MCP server protocol decisions
- Guardian LLM-as-Judge pattern (premature per user ruling)
- SQ/EQ full protocol (rejected per user ruling)
- Sandbox capability-graded interface (separate workstream)
