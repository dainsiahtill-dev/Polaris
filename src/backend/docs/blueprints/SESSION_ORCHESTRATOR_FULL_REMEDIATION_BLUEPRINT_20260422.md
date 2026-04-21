# Session Orchestrator Full Remediation Blueprint (2026-04-22)

**Status**: Implemented and validated  
**Scope**: `roles.runtime` + `roles.kernel` core continuation loop hardening  
**Primary Files**:
- `polaris/cells/roles/runtime/internal/session_orchestrator.py`
- `polaris/cells/roles/kernel/internal/transaction/tool_batch_executor.py`
- `polaris/cells/roles/kernel/internal/kernel/core.py`
- `polaris/cells/roles/runtime/internal/tests/test_session_orchestrator.py`
- `polaris/cells/roles/kernel/tests/test_mutation_guard_soft_mode.py`
- `polaris/cells/roles/kernel/internal/kernel/tests/test_facade_refactor.py`

## 1. Objective

Fix the real CLI regression where MATERIALIZE_CHANGES tasks loop in EXPLORING until `max_turns_exceeded`, while never reaching `read_file -> write_*` execution.

## 2. Root Cause Summary

1. **Receipt loss across continuation**
   - `ToolBatchExecutor` returns `batch_receipt=receipts[0]` in mutation-bypass flows.
   - Parallel tool batches lose all but first receipt, so Phase/WorkingMemory consume incomplete evidence.

2. **WorkingMemory parsing misses exploration outputs**
   - `glob` parsing reads `result_data["result"]` but handler emits `result_data["results"]`.
   - `repo_rg` memory line only keeps pattern, not matched files/snippets.

3. **Instruction regression in EXPLORING**
   - Continuation prompt still appends generic “继续探索和分析”, competing with hard constraints.
   - For mutation mode, exploration instructions need strict stepwise behavior.

4. **Tool-call budget leakage across turns**
   - `RoleExecutionKernel._execute_single_tool` resets execution count by `request.run_id` only.
   - When `run_id=None`, boundary key stays empty and `_execution_count` accumulates across turns.

5. **Exploration-only loop missing deterministic hard stop**
   - Even with stricter continuation instruction, repeated `glob/repo_rg`-only turns can still recur.
   - Runtime needed a cross-turn streak marker and executor-side enforcement to reject repeated exploration-only batches.

## 3. Architecture (Text Diagram)

```text
RoleSessionOrchestrator
  -> SessionStateReducer (phase + memory)
     -> Continuation Prompt Builder
        -> RoleExecutionKernel
           -> TurnTransactionController
              -> ToolBatchExecutor
                 -> ToolBatchRuntime
                    -> RoleToolGateway (count + auth + safety)
```

## 4. Module Responsibility Changes

### `tool_batch_executor.py`
- Build canonical merged batch receipt for continuation responses.
- Ensure `continue_multi_turn` carries complete `results/raw_results/success/failure` context.
- Add exploration streak hard-block enforcement: when marker is active, reject broad-exploration-only batches without `read_file`/write tools.

### `session_orchestrator.py`
- Parse `glob/repo_rg` result payloads by actual handler shape.
- Enrich WorkingMemory with concrete search hits and candidate file paths.
- Strengthen mutation-mode EXPLORING instruction to force `read_file` before repeated broad search.
- Track `_exploration_only_streak` in `structured_findings` and inject `EXPLORATION_STREAK_HARD_BLOCK` marker on repeated exploration-only turns.

### `kernel/core.py`
- Introduce stable per-turn boundary token fallback when `request.run_id` is empty.
- Reset gateway execution count/failure budget on boundary change reliably.

### `stream_orchestrator.py`
- Add merged-receipt debug summary log (`results/success/failure/tools`) in `continue_multi_turn` path for forensic observability.

## 5. Core Data Flow After Fix

1. Turn N executes tool batch (possibly parallel search tools).
2. `ToolBatchExecutor` merges all receipts into one canonical `batch_receipt`.
3. `CompletionEvent.batch_receipt` carries full evidence to orchestrator.
4. Continuation prompt includes concrete files/snippets from search output.
5. In mutation mode, EXPLORING instruction requires immediate `read_file`.
6. Turn boundary token changes per turn even without explicit run_id, so tool-call budget resets.
7. If exploration-only streak reaches threshold, runtime injects hard-block marker and executor rejects subsequent exploration-only batches.

## 6. Technical Decisions

- **Merge receipts in kernel layer**: keep state truth close to transaction runtime.
- **Result-shape tolerant parsing**: handle both top-level and nested payloads defensively.
- **Instruction hardening only for mutation mode**: avoid harming analyze-only tasks.
- **Boundary token fallback to request object identity**: deterministic intra-turn stability + inter-turn reset.

## 7. Validation Plan

Quality gates:
1. `ruff check <changed_paths> --fix`
2. `ruff format <changed_paths>`
3. `mypy <changed_paths>`
4. `pytest -q` on updated regression tests

Targeted regressions:
- `session_orchestrator` continuation memory/instruction behavior.
- `tool_batch_executor` merged receipt correctness.
- `RoleExecutionKernel` turn-boundary reset behavior for empty run_id.
- `ToolBatchExecutor` rejects exploration-only retry batches once streak hard-block marker is active.

## 8. Risks & Boundaries

- Over-aggressive exploration suppression could reduce valid discovery on first turn.
- Receipt merge must preserve existing `BatchReceipt` shape compatibility.
- Boundary-token change must not reset counter within the same turn.

## 9. Deliverables

1. This blueprint (`docs/blueprints/SESSION_ORCHESTRATOR_FULL_REMEDIATION_BLUEPRINT_20260422.md`).
2. Verification card for structural bug.
3. Runtime/kernel code patches + tests.
4. Executed gate evidence.
