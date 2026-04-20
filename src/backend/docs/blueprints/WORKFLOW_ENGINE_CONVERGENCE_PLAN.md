# WorkflowEngine & TurnEngine Convergence Plan

**Date**: 2026-04-05
**Type**: P0 Convergence Analysis & Recommendations
**Status**: ✅ Analysis Complete

---

## Executive Summary

| P0 Issue | Finding | Recommendation | Status |
|----------|---------|----------------|--------|
| P0-011 | **Already Resolved** - Single canonical WorkflowEngine | Monitor, no action needed | Done |
| P0-012 | **Design Alternative** - Two engines serve different purposes | Feature-flagged coexistence with clear boundaries | Planned |

---

## P0-011: WorkflowEngine Duplication

### Analysis

**Claimed duplication**:
- `polaris/kernelone/workflow/engine.py:194` - WorkflowEngine
- `polaris/cells/orchestration/workflow_runtime/internal/runtime_engine/runtime/embedded/engine.py:65` - embedded version

**Finding**: The embedded engine **does not exist** at the claimed path. The file path does not exist in the codebase.

### Current Architecture

```
polaris/kernelone/workflow/engine.py (1440 lines)
    │
    ├── Defines: WorkflowEngine (canonical implementation)
    ├── Defines: HandlerRegistry Protocol (DI boundary)
    ├── Defines: WorkflowRuntimeStore Protocol (persistence contract)
    ├── Implements: DAG/sequential workflow execution
    └── DI-only design: no direct Cell imports

polaris/cells/orchestration/workflow_engine/__init__.py (26 lines)
    │
    └── Re-exports WorkflowEngine from kernelone
        "Owns the KernelOne WorkflowEngine and the HandlerRegistry protocol"

polaris/cells/orchestration/workflow_runtime/
    │
    └── internal/runtime_engine/runtime/embedded/__init__.py
        │
        └── Imports WorkflowEngine from kernelone (line 15)
```

### Verification

```bash
$ ls polaris/cells/orchestration/workflow_runtime/internal/runtime_engine/runtime/embedded/
__init__.py  store_sqlite.py  # No engine.py - just re-exports from kernelone
```

**Import chain**:
```python
# embedded/__init__.py line 15
from polaris.kernelone.workflow.engine import WorkflowEngine

# workflow_engine/__init__.py line 8
from polaris.kernelone.workflow.engine import WorkflowEngine

# workflow_runtime/internal/runtime_engine/runtime/__init__.py line 15
from polaris.kernelone.workflow.engine import WorkflowEngine
```

### Conclusion

**P0-011 is NOT a real issue** - The architecture is already converged:
1. `polaris/kernelone/workflow/engine.py` is the single canonical implementation
2. All other modules import from this canonical source
3. `polaris/cells/orchestration/workflow_engine` Cell acts as the "owner" but re-exports from kernelone

### Recommended Action

| Action | Priority | Notes |
|--------|----------|-------|
| Update P0-011 status to "Already Resolved" | HIGH | Close the issue |
| Document the re-export pattern in cells.yaml | MEDIUM | Clarify ownership |

---

## P0-012: TurnEngine vs TurnTransactionController

### Analysis

**Two implementations**:
| Engine | Location | Lines | Architecture |
|--------|----------|-------|--------------|
| `TurnEngine` | `polaris/cells/roles/kernel/internal/turn_engine/engine.py` | 1549 | Old - Loop-based |
| `TurnTransactionController` | `polaris/cells/roles/kernel/internal/turn_transaction_controller.py` | 1067 | New - Transactional |

### Architectural Comparison

| Aspect | TurnEngine (Old) | TurnTransactionController (New) |
|--------|-----------------|--------------------------------|
| **Execution Pattern** | `while True` loop until stop | Single transaction, state machine driven |
| **Entry Points** | `run()` / `run_stream()` | `execute()` / `execute_stream()` |
| **State Management** | `ConversationState` + `PolicyLayer` | `TurnStateMachine` + `TurnLedger` |
| **Tool Execution** | `kernel._execute_single_tool()` | `self.tool_runtime()` |
| **Stop Condition** | `PolicyLayer.evaluate()` | State machine transition |
| **LLM Calls** | Multiple until budget/stop | One decision per turn |
| **Continuation Loop** | Allowed (old pattern) | **Forbidden** (new pattern) |
| **LLM_ONCE Finalization** | N/A | Enforced `tool_choice=none` |

### Relationship

```
┌─────────────────────────────────────────────────────────────────┐
│                    TurnEngine Architecture                       │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  TurnEngine (Old Architecture)                                  │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │  while True:                                             │  │
│  │      → LLM call                                          │  │
│  │      → Tool execution                                    │  │
│  │      → PolicyLayer.evaluate()                            │  │
│  │      → Continue if not stopped                           │  │
│  └──────────────────────────────────────────────────────────┘  │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────┐
│               TurnTransactionController Architecture             │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  TurnTransactionController (New Architecture)                    │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │  Single Turn:                                             │  │
│  │      1. Build context → DECISION_REQUESTED               │  │
│  │      2. LLM call → DECISION_RECEIVED                     │  │
│  │      3. Decode → DECISION_DECODED                        │  │
│  │      4a. Final Answer → COMPLETED                        │  │
│  │      4b. Tool Batch → TOOL_BATCH_EXECUTING → COMPLETED  │  │
│  │      4c. Handoff → HANDOFF_WORKFLOW                      │  │
│  │      5. LLM_ONCE finalization (tool_choice=none)         │  │
│  └──────────────────────────────────────────────────────────┘  │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

### Migration Strategy

**Feature Flag Control** (`turn_engine_migration.py`):

```python
class FeatureFlags(Enum):
    TRANSACTIONAL_MODE = "TRANSACTIONAL_MODE"  # Controls which engine is used

class MigrationConfig:
    transactional_mode: bool = True  # Default: new architecture
    legacy_mode_fallback: bool = True  # Allow fallback on errors
```

**Decision Matrix**:

| Condition | Engine Used | Notes |
|-----------|-------------|-------|
| `TRANSACTIONAL_MODE=true` + success | TurnTransactionController | New architecture |
| `TRANSACTIONAL_MODE=true` + failure + fallback | TurnEngine | Legacy fallback |
| `TRANSACTIONAL_MODE=false` | TurnEngine | Full legacy mode |

### Recommended Architecture

**Phase 1: Coexistence (Current State)**

Both engines exist with feature flags. No immediate action required.

**Phase 2: Deprecation Path (Future)**

1. Mark `TurnEngine` as deprecated
2. Add deprecation warnings in logs
3. Set `TRANSACTIONAL_MODE=true` as default
4. Monitor for legacy usage

**Phase 3: Removal (After Validation)**

After sufficient production validation of TurnTransactionController, remove TurnEngine.

### Key Differences Summary

| Capability | TurnEngine | TurnTransactionController |
|------------|------------|---------------------------|
| Multi-turn loop | ✅ Yes | ❌ Single turn |
| Continuation loop | ✅ Allowed | ❌ Forbidden |
| LLM_ONCE finalization | ❌ N/A | ✅ Enforced |
| State machine | ❌ No | ✅ Yes |
| Turn ledger (audit) | ❌ Basic | ✅ Comprehensive |
| Workflow handoff | ❌ Manual | ✅ Built-in |
| Streaming support | ✅ Yes | ✅ Yes |

### Recommended Action

| Action | Priority | Timeline |
|--------|----------|----------|
| Document architectural decision in ADR | HIGH | This week |
| Add deprecation warning to TurnEngine | MEDIUM | Week 2 |
| Ensure all tests pass with `TRANSACTIONAL_MODE=true` | HIGH | This week |
| Create migration guide for users | MEDIUM | Week 2 |
| Set `TRANSACTIONAL_MODE=true` as default | MEDIUM | Week 2 |

---

## Test Results

### WorkflowEngine Tests
```bash
$ pytest polaris/kernelone/workflow/tests/ -v --tb=short -q
86 passed, 2 warnings in 1.67s
```

### TurnEngine Tests
```bash
$ pytest polaris/cells/roles/kernel/tests/test_turn_engine.py \
         polaris/cells/roles/kernel/tests/test_transaction_controller.py -v
29 passed, 3 warnings in 0.16s
```

---

## Files Analyzed

### P0-011 Related Files
| File | Lines | Purpose |
|------|-------|---------|
| `polaris/kernelone/workflow/engine.py` | 1440 | Canonical WorkflowEngine |
| `polaris/cells/orchestration/workflow_engine/__init__.py` | 26 | Cell re-export |
| `polaris/cells/orchestration/workflow_runtime/internal/runtime_engine/runtime/embedded/__init__.py` | 39 | Runtime re-export |
| `polaris/cells/orchestration/workflow_runtime/internal/runtime_engine/runtime/factory.py` | ~100 | Factory using WorkflowEngine |

### P0-012 Related Files
| File | Lines | Purpose |
|------|-------|---------|
| `polaris/cells/roles/kernel/internal/turn_engine/engine.py` | 1549 | Old loop engine |
| `polaris/cells/roles/kernel/internal/turn_transaction_controller.py` | 1067 | New transactional engine |
| `polaris/cells/roles/kernel/internal/turn_engine_migration.py` | 229 | Migration layer |
| `polaris/cells/roles/kernel/internal/kernel/turn_runner.py` | ~200 | Kernel facade |

---

## Conclusion

### P0-011: Already Resolved
The WorkflowEngine duplication is a non-issue. There is a single canonical implementation in `polaris/kernelone/workflow/engine.py`, and all other modules properly import from it. The architecture follows the ACGA 2.0 pattern where Cells re-export from KernelOne.

### P0-012: Intentional Design Alternative
TurnEngine and TurnTransactionController are **not duplicates** but **intentional alternatives** serving different architectural purposes:

- **TurnEngine**: Old loop-based architecture (legacy)
- **TurnTransactionController**: New transactional architecture (target)

The migration is controlled by feature flags and includes fallback mechanisms. No immediate convergence action required beyond continued validation and setting `TRANSACTIONAL_MODE=true` as default.

---

**Prepared**: 2026-04-05
**Author**: Claude Code Agent
**Review Status**: Ready for Team Review
