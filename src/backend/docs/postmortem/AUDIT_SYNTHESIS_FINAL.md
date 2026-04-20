# ContextOS `transcript_events: (empty)` Root Cause Analysis
## Master Synthesis Report — Team Audit Final

**Date**: 2026-03-31
**Audit Scope**: Benchmark streaming path — ALL iterations showing `transcript_events: (empty)` in ContextOS snapshot
**Files Analyzed**:
- `polaris/cells/roles/runtime/public/service.py`
- `polaris/cells/roles/kernel/internal/turn_engine.py`
- `polaris/kernelone/context/session_continuity.py`
- `polaris/kernelone/benchmark/adapters/agentic_adapter.py`
- `polaris/cells/roles/kernel/internal/context_gateway.py`
- `polaris/cells/roles/kernel/internal/tool_loop_controller.py`

---

## Executive Summary

**Root Cause (Definitive)**: The streaming execution path (`TurnEngine.run_stream`) has two compounding defects that prevent `_controller._history` from ever accumulating events and prevent `turn_events_metadata` from ever being populated. The `complete` event is **never yielded** in normal streaming execution (content-only turns), and even if it were, `_append_transcript_cycle` is never called before the early-return path exits. This causes `_persist_session_turn_state` to be called with `turn_events_metadata=None`, which persists nothing to the session. Every subsequent turn reloads an empty `transcript_log`, creating a permanent empty-state loop.

---

## Root Cause Chain

### Phase 1: `_append_transcript_cycle` never called for content-only streaming turns

**File**: `polaris/cells/roles/kernel/internal/turn_engine.py`

**Location**: `run_stream()` method, lines 1427–1465

**Defect**: The early-return guard at line 1427 (`if not exec_tool_calls and not deferred_tool_calls:`) fires **before** `_append_transcript_cycle` is ever called. For content-only streaming turns (LLM responds with text/no tool calls), the code:
1. Materializes the turn at line 1382
2. Checks the early-return condition at line 1427 — which is **TRUE** (no tool calls)
3. Jumps to `_build_stream_complete_result` (line 1439) WITHOUT calling `_append_transcript_cycle`
4. The `complete` event is **never yielded** in this path
5. `turn_events_metadata` is constructed from `_controller._history`, which is **EMPTY**

The correct call to `_append_transcript_cycle` at line 1052 (inside the tool-execution path) is **never reached** for content-only turns because the early-return exits first.

**Contrast with `run()` (non-streaming)**: The non-streaming path at line 1008–1013 does call `_append_transcript_cycle` before the early return, and lines 865–876 explicitly handle the case where `final_content` was never appended:

```python
# run() lines 865-876 — CORRECT (handles missing append)
if final_content and (not turn_history or turn_history[-1][0] != "assistant"):
    turn_history.append(("assistant", final_content))
    turn_events_metadata.append(...)
```

`run_stream` has **NO equivalent guard** for the content-only early-return case.

---

### Phase 2: `complete` event never yielded in normal streaming execution

**File**: `polaris/cells/roles/kernel/internal/turn_engine.py`

**Location**: `run_stream()` method — the `complete` event is only yielded at line 1672 (`has_approval_required` path). In normal execution:

1. Content-only turn: early-returns WITHOUT yielding `complete`
2. Tool-execution turn: while loop continues; `has_approval_required` is `False`; loop goes to next iteration or exits silently when budget is exhausted

The `complete` event at line 1460 is **inside a `return` statement** (unreachable after `yield`), and line 1672 only fires when `has_approval_required=True`. In the normal benchmark streaming path, `has_approval_required` is almost always `False`.

---

### Phase 3: `execute_role_session` receives no `turn_events_metadata`

**File**: `polaris/cells/roles/runtime/public/service.py`

**Location**: `execute_role_session()`, lines 1150–1279

When streaming (`command.stream=True`), the code:
1. Iterates over `kernel.run_stream()` at line 1164
2. `final_stream_result` is **never set** because `complete` is never yielded in normal execution
3. After the async iteration loop exhausts, `final_stream_result is None`
4. Falls through to the error case at line 1221
5. Calls `_persist_session_turn_state(command, turn_history=[], turn_events_metadata=None)` — **nothing persisted**

Even though `stream_chat_turn` (called at line 1164) has a `finally` block that calls `_persist_session_turn_state`, it passes `turn_events_metadata=None` because `final_result.turn_events_metadata` was never set.

---

### Phase 4: Session persistence receives empty `turn_events_metadata`

**File**: `polaris/cells/roles/runtime/public/service.py`

**Location**: `_persist_session_turn_state()`, lines 507–643

When called with `turn_events_metadata=None` (from the streaming error path):
1. `events_to_persist` becomes `None` (line 553)
2. Falls through to legacy `turn_history` path (line 576)
3. `turn_history` is also `[]` (empty), so **nothing is added to the session**
4. `combined_turn_events` is `()` (empty tuple)
5. `persisted_ctx["session_turn_events"]` is **never set**
6. On the **next iteration**, `_build_session_request` loads `session_turn_events` from the session → **`None`**
7. `context_os_snapshot.transcript_log` becomes empty
8. `ToolLoopController._history` seeds from empty `transcript_log`
9. **Repeat from Phase 1 — permanently empty**

---

## Why Previous Fixes Did Not Work

| Fix Attempt | Why It Failed |
|-------------|---------------|
| Adding `session_turn_events` accumulation in `_persist_session_turn_state` | The accumulation logic (lines 606–637) is correct but **never reached** because `events_to_persist` is `None` when `turn_events_metadata=None` |
| SSOT Fix comments in `_build_session_request` (lines 1085–1099) | The code correctly loads prior `session_turn_events` from session, but **there is nothing to load** because the previous iteration stored nothing |
| `context_os_snapshot` seeding in `ToolLoopController.__post_init__` | Correctly seeds `_history` from `transcript_log`, but `transcript_log` is always empty because nothing was ever persisted |
| `_merge_transcript` pattern in `session_continuity.py` | The merge logic (lines 611–614) is correct but **always merges empty with current** because `turn_events_metadata` is always `None` in streaming |

All previous fixes addressed **Phase 4** (persistence) and **Phase 3** (loading), but none addressed **Phase 1** (event accumulation in `_controller._history`) or **Phase 2** (`complete` event yield). The chain is only as strong as its weakest link — Phase 1 is the entry point and it is broken.

---

## Definitive Root Cause

> **The streaming execution path (`TurnEngine.run_stream`) never calls `_append_transcript_cycle` for content-only turns AND never yields a `complete` event in normal execution. This causes `_persist_session_turn_state` to receive `turn_events_metadata=None`, which persists nothing to the session. Since nothing is ever persisted, every subsequent turn reloads an empty `transcript_log`, permanently trapping the session in an empty-state loop.**

---

## Additional Finding: No session reuse across benchmark iterations

**File**: `polaris/kernelone/benchmark/adapters/agentic_adapter.py`

**Location**: `stream_session()`, line 59

```python
session_id = f"agentic-bench-{case.case_id}-{uuid.uuid4().hex[:8]}"
```

Each benchmark iteration creates a **new session ID** via UUID. This means:
- Even if Phase 1–4 were fixed, events would still not accumulate across iterations
- Each iteration starts with a fresh session that has never had any events persisted
- The `session_id` format includes a random UUID suffix, making session reuse impossible

**Note**: This is a **contributing factor** (not the primary root cause) because even within a single session, the bug would still cause empty `transcript_events` by the second turn.

---

## Priority Ordering of Fixes

### P0 — Critical (No fix possible without these)

| Priority | Fix | Files | Lines |
|----------|-----|-------|-------|
| P0-1 | **`run_stream`**: Call `_append_transcript_cycle` BEFORE early-return for content-only turns | `turn_engine.py` | ~1427–1431 |
| P0-2 | **`run_stream`**: Yield `complete` event with `turn_events_metadata` for content-only early-return | `turn_engine.py` | ~1439–1465 |
| P0-3 | **`execute_role_session`**: Use `turn_events_metadata` from `complete` event's result (currently `final_stream_result` is never set) | `service.py` | ~1186–1193 |

### P1 — High (Ensures persistence works correctly)

| Priority | Fix | Files | Lines |
|----------|-----|-------|-------|
| P1-1 | **`_persist_session_turn_state`**: Ensure `turn_events_metadata` from streaming `complete` event is correctly passed to `events_to_persist` | `service.py` | ~553 |
| P1-2 | **`session_continuity.py`**: Verify `_build_context_os_persisted_payload` correctly reconstructs `transcript_log` from accumulated `turn_events` | `session_continuity.py` | ~667 |

### P2 — Medium (Session reuse for benchmark)

| Priority | Fix | Files | Lines |
|----------|-----|-------|-------|
| P2-1 | **`agentic_adapter.py`**: Use stable `session_id` per benchmark case (e.g., `f"agentic-bench-{case.case_id}"` without UUID suffix) | `agentic_adapter.py` | ~59 |

---

## Code References for Primary Fixes

### Fix P0-1: `run_stream` — call `_append_transcript_cycle` before early return

**Current (BROKEN)** at `turn_engine.py` ~1427:
```python
if not exec_tool_calls and not deferred_tool_calls:
    if final_content:
        self._append_transcript_cycle(...)  # Called AFTER check but result not used
    # ... builds result but doesn't yield complete
    return  # Never yields complete event
```

**Correct** — call `_append_transcript_cycle` BEFORE the check:
```python
# Append transcript BEFORE early-return check
if final_content or turn.native_tool_calls:
    self._append_transcript_cycle(controller=_controller, turn=turn, tool_results=[])

if not exec_tool_calls and not deferred_tool_calls:
    # ... build result with populated turn_events_metadata
    result = _build_stream_complete_result(turn_events_metadata=[...from _controller._history...])
    yield {"type": "complete", "result": result}
    return
```

### Fix P0-2: `run_stream` — yield `complete` event with `turn_events_metadata`

The `complete` event must be yielded with the final `RoleTurnResult` containing `turn_events_metadata` from `_controller._history`. Currently:
- Line 1460 `complete` yield is inside `return` (unreachable)
- Line 1672 only fires when `has_approval_required=True`

**Fix**: Ensure the early-return path yields `complete` with properly populated `turn_events_metadata`.

### Fix P0-3: `execute_role_session` — handle streaming `complete` event

**Current** at `service.py` ~1177:
```python
elif event_type == "complete":
    maybe_result = event.get("result")
    if isinstance(maybe_result, RoleTurnResult):
        final_result = maybe_result
```

The `complete` event is **never yielded** in normal streaming execution, so `final_result` is never set. After the async iteration loop, `final_result is None`, and the code falls through to the error case.

**Fix**: Ensure `run_stream` yields `complete` in all termination paths, and `execute_role_session` correctly captures `turn_events_metadata` from it.

---

## Summary

| Aspect | Finding |
|--------|---------|
| **Root Cause** | `run_stream` never calls `_append_transcript_cycle` for content-only turns AND never yields `complete` event in normal execution |
| **Effect** | `_controller._history` is always empty, `turn_events_metadata=None` is passed to `_persist_session_turn_state`, nothing is persisted |
| **Perpetuation** | Each turn reloads empty `transcript_log` from session, permanently trapping in empty-state loop |
| **Contributing Factor** | `agentic_adapter.py` generates new session_id per iteration (UUID suffix), preventing cross-iteration accumulation even if Phase 1–4 were fixed |
| **Previous Fixes** | All addressed persistence/loading but not event accumulation; chain broken at entry point |
| **Fix Priority** | P0-1 > P0-2 > P0-3 > P1-1 > P1-2 > P2-1 |

---

*Report produced by Team Lead audit synthesis. All findings trace through exact code paths with line references.*
