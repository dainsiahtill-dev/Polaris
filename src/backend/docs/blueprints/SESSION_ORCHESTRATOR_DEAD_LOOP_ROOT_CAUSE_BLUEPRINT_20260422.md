# Session Orchestrator Dead-Loop Root Cause Blueprint (2026-04-22)

**Status**: Planned for immediate implementation  
**Scope**: `roles.kernel` + `roles.runtime` mutation workflow convergence  
**Primary Targets**:
- `polaris/cells/roles/kernel/internal/transaction/receipt_utils.py`
- `polaris/cells/roles/kernel/internal/transaction/tool_batch_executor.py`
- `polaris/cells/roles/kernel/internal/transaction/retry_orchestrator.py`
- `polaris/cells/roles/kernel/internal/transaction/contract_guards.py`
- `polaris/cells/roles/kernel/internal/kernel/core.py`
- `polaris/cells/roles/runtime/internal/session_orchestrator.py`

## 1. Objective

Eliminate the recurring CLI dead loop where a `MATERIALIZE_CHANGES` request keeps emitting
`glob/repo_rg/repo_tree` across turns and never converges to `read_file -> write`.

This fix must be structural:

1. Preserve executed tool receipts across continuation boundaries.
2. Track turn boundaries explicitly instead of inferring them from unstable request identity.
3. Upgrade “known target but still exploring” from prompt advice to executor-level policy.
4. Reuse existing bootstrap-read recovery so the runtime can self-heal when the model still misses the required read.

## 2. Root Cause

The dead loop is not caused by a single weak prompt. It is produced by three broken links:

1. `ToolBatchRuntime.execute_batch()` returns `BatchReceipt` model objects, but downstream merge code only accepts `dict`.
   Result: continuation turns receive an empty `batch_receipt` even when tools succeeded.
2. Because continuation receipts are empty, `RoleSessionOrchestrator` cannot surface search hits into working memory.
   Result: the next turn still believes target files are unknown and keeps exploring.
3. Tool gateway counters still rely on `run_id/request identity` heuristics.
   Result: real CLI flows with `run_id=None` can accumulate tool-call counts across turns and eventually reject valid tools.

## 3. Architecture (Text Diagram)

```text
User Prompt
  -> TurnTransactionController
    -> ToolBatchExecutor
      -> ToolBatchRuntime
        -> BatchReceipt(model objects)
      -> receipt_utils.normalize/merge
      -> TurnLedger.mutation_obligation(read/write evidence)
      -> continue_multi_turn / retry_orchestrator
        -> RoleSessionOrchestrator continuation prompt
          -> known target paths surfaced
          -> next turn must read or bootstrap-read
```

## 4. Module Responsibilities

### `receipt_utils.py`
- Provide canonical receipt normalization for `BatchReceipt`, dict payloads, and nested result models.
- Provide canonical merged batch receipt construction shared by executor and retry paths.
- Record direct-read evidence into `TurnLedger.mutation_obligation.read_evidence_count`.

### `tool_batch_executor.py`
- Reset tool-runtime turn boundary explicitly at batch start.
- Never drop `BatchReceipt` model objects during merge.
- Reject exploration-only batches once target files are already known and no direct read evidence exists.
- Emit contract-violation events with preserved `single_batch_contract_violation` compatibility.

### `retry_orchestrator.py`
- Reuse canonical receipt normalization.
- When a mutation-contract violation requires a fresh read but the failed batch has no write target,
  synthesize a bootstrap-read batch from target file paths already present in retry context.

### `contract_guards.py`
- Reuse bootstrap-read construction for both stale-edit recovery and known-target recovery.

### `core.py`
- Provide an explicit turn-boundary reset API for cached tool gateways.
- Ensure real CLI flows with `run_id=None` still get a stable per-turn key.

### `session_orchestrator.py`
- Continue consuming normalized receipts so search hits and read evidence actually reach working memory.

## 5. Core Data Flow

1. A mutation turn executes exploratory tools.
2. `receipt_utils.normalize_batch_receipts()` converts model receipts into plain dicts.
3. `merge_batch_receipts()` preserves all search results for continuation.
4. `RoleSessionOrchestrator` injects the actual candidate file paths into the continuation prompt.
5. The next turn is now classified as “target files known”.
6. If the model still emits only broad exploration:
   - executor raises `single_batch_contract_violation: ... requires_bootstrap_read`
   - retry orchestrator synthesizes `read_file` bootstrap from known target paths
   - follow-up write happens with fresh read context in the same recovery loop

## 6. Technical Rationale

1. **State truth before prompt pressure**: if receipts are lost, stronger prompts do not help.
2. **Turn boundary must be explicit**: runtime policy cannot depend on `run_id=None` or Python object identity.
3. **Executor policy beats conversational reminders**: once a target file path is known, further broad exploration is wasted work.
4. **Bootstrap recovery already exists**: extend the same recovery family instead of inventing a second loop-escape mechanism.

## 7. Validation Plan

Quality gates:
1. `python -m ruff check --fix <changed_paths>`
2. `python -m ruff format <changed_paths>`
3. `python -m mypy <changed_paths>`
4. `python -m pytest -q <targeted_tests>`

Required regression coverage:
1. `BatchReceipt` model objects merge into non-empty continuation receipts.
2. `RoleSessionOrchestrator` continuation prompt surfaces search hits from model receipts.
3. Direct `read_file` success increments `read_evidence_count`.
4. Known-target exploration-only batches are rejected.
5. Retry path can bootstrap a read from context-derived target paths.
6. Explicit turn-boundary reset prevents cross-turn tool counter leakage when `run_id=None`.
