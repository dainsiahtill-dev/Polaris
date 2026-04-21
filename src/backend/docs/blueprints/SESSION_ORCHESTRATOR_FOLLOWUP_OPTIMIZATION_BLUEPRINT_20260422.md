# Session Orchestrator Follow-up Optimization Blueprint (2026-04-22)

**Status**: Planned for immediate implementation  
**Scope**: `roles.kernel` transaction observability and guard robustness  
**Primary Targets**:
- `polaris/cells/roles/kernel/internal/transaction/tool_batch_executor.py`
- `polaris/cells/roles/kernel/tests/test_mutation_guard_soft_mode.py`

## 1. Objective

After the first remediation pass, continue hardening the runtime by:

1. Making exploration hard-blocks auditable via structured runtime events.
2. Adding regression tests for mixed tool-batch behavior under hard-block mode, ensuring guard is strict but not over-blocking.

## 2. Architecture (Text Diagram)

```text
RoleSessionOrchestrator
  -> TurnTransactionController
    -> ToolBatchExecutor (guard gate + policy enforcement)
      -> TurnEvent stream (ErrorEvent / TurnPhaseEvent)
      -> ToolBatchRuntime
```

## 3. Module Responsibilities

### `tool_batch_executor.py`
- Keep contract-violation hard-stop behavior unchanged.
- Emit structured `ErrorEvent` before raising exploration-streak violation, so telemetry can count/alert on this class of failure.
- Keep existing message prefix `single_batch_contract_violation` for retry/contract logic compatibility.

### `test_mutation_guard_soft_mode.py`
- Add a regression test to assert hard-block now emits `ErrorEvent(error_type="exploration_streak_hard_block")`.
- Add mixed-batch tests proving hard-block only rejects **exploration-only** batches, while allowing:
  - exploration + direct read
  - exploration + write tool

## 4. Core Data Flow

1. Decision enters `ToolBatchExecutor.execute_tool_batch`.
2. If `EXPLORATION_STREAK_HARD_BLOCK` marker is active and batch is exploration-only:
   - emit `ErrorEvent` with structured reason.
   - raise `RuntimeError(single_batch_contract_violation: exploration_streak_hard_block...)`.
3. If batch includes direct read or write tool:
   - bypass this specific hard-block and continue normal execution.

## 5. Technical Rationale

1. **Observability-first**: exceptions alone are insufficient for runtime governance dashboards.
2. **No behavior regression**: keep original error string and control-flow contract.
3. **Boundary precision**: enforce strict rejection only on the pathological loop pattern, not valid mixed remediation sequences.

## 6. Validation Plan

Quality gates:
1. `python -m ruff check --fix <changed_paths>`
2. `python -m ruff format <changed_paths>`
3. `python -m mypy <changed_paths>`
4. `python -m pytest -q polaris/cells/roles/kernel/tests/test_mutation_guard_soft_mode.py`

Regression goals:
- Hard-block emits structured error event.
- Mixed batches with `read_file`/write tools are not falsely blocked.
