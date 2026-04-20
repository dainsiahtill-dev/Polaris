# Cognitive & ContextOS Bugfix Blueprint

**Date**: 2026-04-10  
**Architect**: Principal Architect (Claude Code)  
**Team**: 10x Senior Python Engineers  
**Scope**: 14 bugs across 6 files  

---

## Executive Summary

This blueprint addresses 14 bugs discovered during deep audit of the Cognitive Life Form and ContextOS subsystems:

- **6 Critical/High**: Memory leaks, workspace isolation failures, data loss risks
- **3 Medium**: Off-by-one errors, API inconsistencies, logic bugs
- **5 Low**: Durability issues, documentation errors

---

## System Architecture

### Module Dependencies

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                           Cognitive Subsystem                                │
├─────────────────────────────────────────────────────────────────────────────┤
│  polaris/kernelone/cognitive/                                                │
│  ├── context.py              # Session management (H-3, H-4, L-1, L-2, L-3)  │
│  ├── orchestrator.py         # Main orchestrator (verified OK)               │
│  └── execution/                                                              │
│      ├── acting_handler.py   # Tool execution (verified OK)                  │
│      └── rollback_manager.py # Rollback logic (H-5, H-6)                     │
└─────────────────────────────────────────────────────────────────────────────┘
                                      │
                                      ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                         ContextOS / TurnEngine                               │
├─────────────────────────────────────────────────────────────────────────────┤
│  polaris/cells/roles/kernel/internal/                                        │
│  ├── tool_loop_controller.py # Tool loop state (H-1, H-2, M-1)               │
│  ├── policy/layer/budget.py  # Budget policy (M-2)                           │
│  ├── turn_engine/engine.py   # Turn execution (M-3)                          │
│  └── context_gateway.py      # Context building (L-4)                        │
└─────────────────────────────────────────────────────────────────────────────┘
```

### Data Flow

```
User Request
    │
    ▼
┌──────────────┐    ┌──────────────────┐    ┌─────────────────┐
│ Orchestrator │───▶│ Session Manager  │───▶│ Disk Persistence│
└──────────────┘    └──────────────────┘    └─────────────────┘
                           │
                           ▼
                    ┌──────────────┐
                    │   Context    │
                    └──────────────┘
                           │
            ┌──────────────┼──────────────┐
            ▼              ▼              ▼
    ┌─────────────┐ ┌──────────┐  ┌──────────────┐
    │ ToolLoopCtrl│ │ Budget   │  │ RollbackMgr  │
    └─────────────┘ └──────────┘  └──────────────┘
```

---

## Bug Categorization & Fix Strategy

### Category A: State Management Bugs (H-1, H-2, M-1)
**File**: `tool_loop_controller.py`

**Root Cause**: Incomplete cleanup and unbounded growth of tracking data structures.

**Fix Strategy**:
1. Add `_recent_successful_counts.clear()` to `clear_history()`
2. Implement dict trimming synchronized with list trimming
3. Fix off-by-one: `get(call_key, 0)` instead of `get(call_key, 1)`

---

### Category B: Workspace Isolation (H-3, H-4)
**File**: `context.py`

**Root Cause**: Global singleton pattern with single workspace tracking.

**Fix Strategy**:
1. Replace single `_global_workspace` with `dict[str, CognitiveSessionManager]`
2. Move try/except inside the loop for per-file error handling
3. Add `mkdir(parents=True, exist_ok=True)` before temp file creation

---

### Category C: Rollback Manager (H-5, H-6)
**File**: `rollback_manager.py`

**Root Cause**: Dead code with memory leaks.

**Fix Strategy**:
1. Add cleanup in `execute_rollback()` and `abort_rollback()`
2. Document that `prepare_rollback` is currently unused but functional
3. Consider adding TTL or LRU cache for snapshots

---

### Category D: Budget Policy (M-2)
**File**: `budget.py`

**Root Cause**: Missing guard for `max_turns=0` edge case.

**Fix Strategy**:
1. Add check: `if self.max_turns > 0 and turn_count >= self.max_turns:`
2. Add validation in `__init__` to reject `max_turns <= 0`

---

### Category E: API Consistency (M-3)
**File**: `engine.py`

**Root Cause**: `run_stream()` signature drift from `run()`.

**Fix Strategy**:
1. Add `attempt: int = 0` parameter
2. Add `response_model: type | None = None` parameter (may raise NotImplementedError if used)

---

### Category F: Durability & Documentation (L-1, L-2, L-3, L-4)

**Fix Strategy**:
1. Add `f.flush()` + `os.fsync(f.fileno())` before rename
2. Use `suppress(FileNotFoundError)` instead of broad `Exception`
3. Update dedupe comment to match implementation

---

## Testing Strategy

### Unit Tests Required

1. **tool_loop_controller_test.py**:
   - `test_clear_history_resets_counts` - Verify H-1 fix
   - `test_counts_dict_trimmed_with_list` - Verify H-2 fix
   - `test_off_by_one_initial_count` - Verify M-1 fix

2. **context_test.py**:
   - `test_workspace_isolation` - Verify H-3 fix
   - `test_corrupted_session_skips_only_one` - Verify H-4 fix
   - `test_atomic_write_with_fsync` - Verify L-1 fix

3. **rollback_manager_test.py**:
   - `test_snapshots_cleaned_after_rollback` - Verify H-6 fix

4. **budget_test.py**:
   - `test_max_turns_zero_treated_as_unlimited` - Verify M-2 fix

5. **engine_test.py**:
   - `test_run_stream_signature_matches_run` - Verify M-3 fix

---

## Engineering Standards Compliance

| Standard | Application |
|----------|-------------|
| PEP 8 / Ruff | All code must pass `ruff check --fix` |
| Type Safety | Full type annotations, mypy --strict clean |
| Defensive Programming | No bare `except:`, explicit exception types |
| Documentation | All public methods have docstrings |
| DRY | Shared logic extracted to helpers |
| No Over-engineering | Minimal changes to fix bugs |

---

## Execution Plan

| Agent | Assignment | Bugs | Priority |
|-------|-----------|------|----------|
| Agent-1 | ToolLoopController State | H-1, H-2, M-1 | P0 |
| Agent-2 | Session Manager Isolation | H-3 | P0 |
| Agent-3 | Session Loading Robustness | H-4 | P0 |
| Agent-4 | Rollback Manager Cleanup | H-5, H-6 | P0 |
| Agent-5 | Budget Policy Edge Case | M-2 | P1 |
| Agent-6 | Engine API Consistency | M-3 | P1 |
| Agent-7 | Atomic Write Durability | L-1, L-2, L-3 | P2 |
| Agent-8 | Documentation Fix | L-4 | P2 |
| Agent-9 | Integration Tests | All | P1 |
| Agent-10 | Final Verification | All | P0 |

---

## Success Criteria

1. All 14 bugs fixed with minimal code changes
2. 100% test coverage for fixed bugs
3. `ruff check --fix` passes on all modified files
4. `pytest` passes on all cognitive and kernel tests
5. No new mypy errors introduced

---

## Risk Mitigation

| Risk | Mitigation |
|------|------------|
| Breaking existing functionality | Comprehensive test suite, minimal changes |
| Performance regression | Benchmark tool loop controller before/after |
| Concurrent access issues | Use locks where necessary |
| Data loss | Maintain backward compatibility in serialization |

---

*Document Version: 1.0*  
*Approved by: Principal Architect*  
*Implementation Deadline: 2026-04-10*
