# Test Baseline Report

**Date**: 2026-03-31
**Role**: E8: Test Engineer
**Blueprint**: docs/blueprints/refactoring-1000-lines-20260331/BLUEPRINT.md

---

## 1. Executive Summary

This report establishes the test baseline for the 1000+ lines file refactoring initiative. Key findings:

| Metric | Status | Notes |
|--------|--------|-------|
| Total Tests Collected | ~8000+ | 11138 lines in collection output |
| Kernel Tests | 773 collected | 44 failed, 729 passed |
| Kernelone Context Tests | 571 collected | 1 failed, 570 passed |
| Critical Blocking Issue | **IMPORT ERROR** | `TurnEngine` not exported from package |

---

## 2. Test Collection Summary

### 2.1 Global Collection

```
pytest --collect-only -q
Total output lines: 11138
Estimated test count: ~8000+
```

### 2.2 Target Area Tests

| Area | Collected | Passed | Failed | Error |
|------|-----------|--------|--------|-------|
| `polaris/cells/roles/kernel/tests/` | 773 | 729 | 44 | 1 collection error |
| `polaris/kernelone/context/tests/` | 571 | 570 | 1 | 0 |

---

## 3. Kernel Roles Test Failures (44 Failed)

### 3.1 Failure Categories

| Category | Count | Representative Test |
|----------|-------|---------------------|
| Stream/Run Parity | 6 | `test_run_and_stream_produce_equivalent_content` |
| Tool Loop Controller | 4 | `test_creation_with_request`, `test_build_context_request` |
| Transcript Leak Guard | 3 | `test_context_gateway_apply_compression_truncates_not_corrupts` |
| LLM Caller Lifecycle | 2 | `test_call_response_format_fallback_uses_chat_mode_builder` |
| Metrics Collector | 6 | `test_counter_with_labels`, `test_record_cache_hit` |
| Pydantic Output Parser | 3 | `test_parse_with_fallback_extracts_json_when_possible` |
| Transaction Controller | 4 | `test_final_answer_turn`, `test_handoff_triggered_by_async_tool` |
| Policy Convergence | 2 | `test_run_single_failed_tool_cycle_does_not_trigger_stall` |
| Visible Output Contract | 3 | `test_stream_emits_only_sanitized_visible_content` |

### 3.2 Root Cause Analysis

**P0: Import Error (Blocking)**
```
ImportError: cannot import name 'TurnEngine' from
'polaris.cells.roles.kernel.internal.turn_engine'
```

**Cause**: `turn_engine/` package directory shadows `turn_engine.py` module file. The `__init__.py` only exports:
- `TurnEngineConfig`
- `SafetyState`

**Missing**: `TurnEngine` class itself (still in `turn_engine.py`, 1939 lines)

**P1: Context Overflow Error**
```
ContextOverflowError: Context overflow after compression:
315 tokens > 200 limit
```
Location: `context_gateway.py:737`

**P2: Tool Loop Stall Detection**
```
AssertionError: assert 'tool_loop_stalled' in 'TOOL_BLOCKED: ...'
```
Related to policy convergence tests.

---

## 4. Kernelone Context Test Failures (1 Failed)

### 4.1 Failure Detail

```
TestContinuityCache::test_cache_invalidation
Location: test_continuity.py:469
Error: AssertionError: assert {'summary': 'test'} is None
```

**Analysis**: Cache invalidation logic returning stale data instead of None.

---

## 5. Critical Blocking Issues

### 5.1 Import Shadowing (P0 - BLOCKS ALL KERNEL TESTS)

**Current State**:
```
polaris/cells/roles/kernel/internal/
├── turn_engine.py          # 1939 lines, contains TurnEngine class
├── turn_engine/            # Package directory
│   ├── __init__.py         # Only exports TurnEngineConfig, SafetyState
│   └── config.py           # Config classes (Wave 1 extraction)
```

**Problem**: Python prefers package over module file when importing.

**Impact**:
- `test_turn_history_persist_parity.py` fails at collection
- All tests importing `TurnEngine` will fail

**Required Fix**:
Option A: Add to `turn_engine/__init__.py`:
```python
from polaris.cells.roles.kernel.internal.turn_engine.engine import TurnEngine
# (after creating engine.py with TurnEngine class)

__all__ = ["TurnEngine", "TurnEngineConfig", "SafetyState"]
```

Option B: Temporarily add fallback import:
```python
# In turn_engine/__init__.py, add:
import sys
from pathlib import Path

# Import from original file if engine.py doesn't exist yet
_original_file = Path(__file__).parent.parent / "turn_engine.py"
if _original_file.exists():
    # Use importlib to load from file
    ...
```

### 5.2 Wave 1 Refactoring Status

**Completed**:
- `turn_engine/config.py` - Config extraction done

**Not Completed**:
- `turn_engine/engine.py` - TurnEngine class migration pending
- Proper `__init__.py` exports

---

## 6. Test Coverage Status

### 6.1 Before Refactoring

| File | Lines | Test File | Status |
|------|-------|-----------|--------|
| `turn_engine.py` | 1939 | `test_turn_history_persist_parity.py` | BLOCKED (import) |
| `llm_caller.py` | 2869 | `test_llm_caller.py` | 44 failed |
| `kernel.py` | 1761 | Various | Mixed |
| `context_os/runtime.py` | 2013 | `test_continuity.py` | 1 failed |
| `tool_loop_controller.py` | ~800 | `test_tool_loop_controller.py` | 4 failed |

### 6.2 Test Files to Monitor During Refactoring

**Priority Tests**:
1. `test_turn_history_persist_parity.py` - Must fix import first
2. `test_run_stream_parity.py` - 6 failures, stream/run parity
3. `test_transcript_leak_guard.py` - 3 failures, compression logic
4. `test_tool_loop_controller.py` - 4 failures, controller core
5. `test_llm_caller.py` - 2 failures, lifecycle
6. `test_continuity.py` - 1 failure, cache invalidation

---

## 7. Recommendations for Refactoring Team

### 7.1 Immediate Actions (Wave 1)

1. **E1 (TurnEngine Lead)**:
   - Fix `turn_engine/__init__.py` to export `TurnEngine`
   - Either create `engine.py` or add temporary import fallback

2. **E7 (Integration Architect)**:
   - Define import contract for all refactored modules
   - Ensure `__init__.py` exports all public APIs

### 7.2 Wave 2-3 Monitoring

- Run `pytest polaris/cells/roles/kernel/tests/ -v --tb=short` after each wave
- Focus on `test_run_stream_parity.py` failures (parity contract)
- Monitor `test_transcript_leak_guard.py` for compression behavior

### 7.3 Quality Gate Criteria

**Pass Criteria**:
- 0 import errors at collection
- Existing passed tests remain passed
- New module tests added for extracted components

**Fail Criteria**:
- Any new import errors
- Regression in previously passing tests
- Missing `__init__.py` exports

---

## 8. Appendix: Full Test Output

### 8.1 Kernel Tests Summary

```
44 failed, 729 passed, 5 warnings in 7.04s
```

### 8.2 Kernelone Context Tests Summary

```
1 failed, 570 passed, 2 warnings in 14.46s
```

### 8.3 Collection Errors

```
ERROR polaris/cells/roles/kernel/tests/test_turn_history_persist_parity.py
ImportError: cannot import name 'TurnEngine' from 'polaris.cells.roles.kernel.internal.turn_engine'
```

---

## 9. Next Actions

1. **Wave 1 Checkpoint**: Fix TurnEngine import issue before proceeding
2. **Wave 2 Execution**: Monitor test pass rate after each module extraction
3. **Wave 3 Execution**: Full regression test before final merge
4. **Day 3 Final**: Quality gate validation with 100% test pass requirement

---

*Report Generated: 2026-03-31*
*Test Engineer: E8*