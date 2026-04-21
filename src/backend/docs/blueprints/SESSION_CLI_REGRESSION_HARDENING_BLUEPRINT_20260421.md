# Session CLI Regression Hardening Blueprint (2026-04-21)

**Status**: Implemented and validated  
**Scope**: `roles.runtime` + `roles.kernel` + `director.delivery` CLI regression hardening  
**Primary Files**: `polaris/cells/roles/runtime/internal/session_orchestrator.py`, `polaris/cells/roles/kernel/internal/transaction/stream_orchestrator.py`, `polaris/cells/roles/kernel/internal/transaction/tool_batch_executor.py`, `polaris/cells/roles/kernel/internal/llm_caller/invoker.py`, `polaris/kernelone/llm/engine/stream/executor.py`

## 1. Objective

This blueprint fixes the real CLI failure chain observed in the director console:

1. Windows-style absolute paths such as `/C:/...` are misclassified as path traversal and then forwarded unchanged into tool execution.
2. Continuation turns lose the original `delivery_mode`, so a `materialize_changes` task silently degrades into `analyze_only` after the first turn.
3. Session runtime incorrectly terminates read-only failure turns if they contain visible analysis text.
4. LLM debug telemetry emits placeholder request data (`messages=[]`, `provider_id=""`, `model=""`) that misleads runtime diagnosis.
5. Cognitive-mode CLI bootstrap silently degrades embedding-dependent features without exposing a clear capability boundary.

The implementation must preserve current Cell boundaries: `roles.kernel` remains the turn execution authority, `roles.runtime` remains the session lifecycle owner, and `director.delivery` remains transport/projection only.

## 2. Problem Statement

The observed CLI log shows a deterministic regression chain:

- the model emits a `read_file` call using `/C:/...`;
- `tool_batch_executor` path rewriting rejects it as traversal instead of normalizing it to a workspace-relative path;
- the failed read produces a tool error but the session continues;
- the next continuation turn loses the original `materialize_changes` contract and falls back to `analyze_only`;
- the model returns textual diagnosis rather than another tool call;
- `RoleSessionOrchestrator` converts that visible diagnosis into `END_SESSION`, even though the task still has unsatisfied mutation obligation.

This is a structural defect across path normalization, continuation contract persistence, and session-level termination semantics.

## 3. Architecture

### 3.1 Text Architecture Diagram

```text
CLI Director Host
  -> roles.runtime / RoleSessionOrchestrator
     -> Continuation Prompt Contract
        -> roles.kernel / StreamOrchestrator
           -> DeliveryContract Resolver
           -> ToolBatchExecutor
              -> Workspace Path Rewriter
           -> LLM Caller / Stream Executor
     -> SessionStateReducer
     -> Terminal Semantics Guard
```

### 3.2 Module Responsibilities

#### `tool_batch_executor.py`

Owns canonical normalization of existing-file tool paths before execution.

It must:
- accept workspace-relative paths;
- accept Windows absolute paths that resolve inside the workspace;
- strip malformed leading slash patterns like `/C:/...` before security validation;
- reject real traversal attempts;
- avoid forwarding an uncorrected bad path when a safe normalized path can be derived.

#### `stream_orchestrator.py`

Owns continuation-turn delivery contract reconstruction.

It must:
- preserve `materialize_changes` across continuation turns;
- consume explicit continuation metadata rather than guessing solely from current turn ledger state;
- treat `exploring`, `content_gathered`, and `implementing` as sub-phases of the original delivery contract, not independent delivery-mode selectors.

#### `session_orchestrator.py`

Owns session-level terminal semantics.

It must:
- forbid read-only termination exemption when tool execution failed;
- forbid read-only termination exemption for unsatisfied `materialize_changes` tasks;
- continue multi-turn execution when a failed read left the task unresolved;
- surface enough continuation metadata for kernel-side reconstruction.

#### `invoker.py` and `stream executor`

Own LLM debug telemetry.

They must emit actual request diagnostics only after messages, provider, and model are resolved, so debug logs remain trustworthy.

## 4. Core Data Flow

### Failure-to-recovery path

1. Model emits `read_file(file='/C:/...')`.
2. Path rewriter normalizes it to `polaris/cells/.../session_orchestrator.py` if it resolves inside workspace.
3. Tool executes against canonical path.
4. If the read still fails, tool receipt marks failure and session/runtime keeps the task in `continue_multi_turn`.
5. Continuation prompt explicitly carries session delivery metadata.
6. Kernel reconstructs `DeliveryContract(mode=MATERIALIZE_CHANGES)` from that metadata.
7. Session runtime blocks premature end-of-session until mutation obligation is satisfied or a true policy stop is reached.

### Debug telemetry path

1. LLM request is prepared.
2. Provider/model are resolved.
3. Only then does debug stream emit request payload with real `messages`, `provider_id`, and `model`.

## 5. Technical Decisions

| Decision | Reason |
|----------|--------|
| normalize `/C:/...` before traversal check | current rejection is a false positive caused by path-shape mismatch, not real traversal |
| continuation metadata must carry delivery mode explicitly | new turn ledger cannot reconstruct prior frozen mode by itself |
| session termination exemption must check failure status and mutation obligation | visible explanation text is not proof of task completion |
| debug event emission must happen after request preparation | placeholder diagnostics are worse than missing diagnostics |
| embedding bootstrap warning remains non-blocking in this pass | it is a capability-gap warning, not the root cause of the failed task loop |

## 6. Implementation Plan

### Phase 1: Contract and terminal fixes

1. add explicit continuation metadata for delivery mode;
2. preserve `materialize_changes` in continuation turns;
3. tighten read-only termination exemption to require successful read-only execution;
4. block session completion if mutation obligation remains unsatisfied.

### Phase 2: Path normalization fixes

1. normalize malformed Windows absolute paths;
2. rewrite safe in-workspace absolute paths to workspace-relative canonical paths;
3. add regression coverage for `/C:/...` and normal `C:/...` inputs.

### Phase 3: Observability fixes

1. move `invoke_request` debug emission after request preparation;
2. move `invoke_start` emission after provider/model resolution;
3. add regression assertions for real debug payloads.

## 7. Validation Strategy

Targeted tests to add or update:

- `polaris/cells/roles/runtime/internal/tests/test_session_orchestrator.py`
- `polaris/cells/roles/kernel/internal/transaction/tests/test_stream_orchestrator.py`
- `polaris/cells/roles/kernel/internal/transaction/tests/test_task_contract_builder.py`
- `polaris/delivery/cli/director/tests/test_console_host_orchestrator.py`

Command gates:

- `python -m ruff check <paths> --fix`
- `python -m ruff format <paths>`
- `python -m mypy <paths>`
- `python -m pytest -q <targeted tests>`
- adjacent regressions for host continuity and director console integration

## 8. Risks and Boundaries

- Path normalization must not weaken real traversal protection.
- Continuation metadata must not create a second source of truth outside session/runtime.
- Session termination hardening must avoid trapping legitimate `analyze_only` tasks in unnecessary loops.
- Embedding bootstrap now degrades explicitly in plain console mode: missing embedding-port configuration logs an `info` skip instead of a misleading warmup failure warning.

## 9. Deliverables

1. this blueprint under `docs/blueprints/`;
2. verification card for the structural CLI regression;
3. code fixes across `roles.runtime` and `roles.kernel`;
4. targeted and adjacent regression evidence.

## 10. Verification Outcome

Executed and passed:

- `python -m ruff check polaris/cells/roles/runtime/internal/session_orchestrator.py polaris/cells/roles/runtime/internal/tests/test_session_orchestrator.py polaris/cells/roles/kernel/internal/transaction/tool_batch_executor.py polaris/cells/roles/kernel/internal/transaction/task_contract_builder.py polaris/cells/roles/kernel/internal/transaction/stream_orchestrator.py polaris/cells/roles/kernel/internal/transaction/tests/test_path_security.py polaris/cells/roles/kernel/internal/transaction/tests/test_stream_orchestrator.py polaris/cells/roles/kernel/internal/transaction/tests/test_task_contract_builder.py polaris/cells/roles/kernel/internal/llm_caller/invoker.py polaris/cells/roles/kernel/tests/test_llm_caller.py polaris/kernelone/llm/engine/stream/executor.py polaris/kernelone/llm/engine/stream/tests/test_executor.py --fix`
- `python -m ruff format polaris/cells/roles/runtime/internal/session_orchestrator.py polaris/cells/roles/runtime/internal/tests/test_session_orchestrator.py polaris/cells/roles/kernel/internal/transaction/tool_batch_executor.py polaris/cells/roles/kernel/internal/transaction/task_contract_builder.py polaris/cells/roles/kernel/internal/transaction/stream_orchestrator.py polaris/cells/roles/kernel/internal/transaction/tests/test_path_security.py polaris/cells/roles/kernel/internal/transaction/tests/test_stream_orchestrator.py polaris/cells/roles/kernel/internal/transaction/tests/test_task_contract_builder.py polaris/cells/roles/kernel/internal/llm_caller/invoker.py polaris/cells/roles/kernel/tests/test_llm_caller.py polaris/kernelone/llm/engine/stream/executor.py polaris/kernelone/llm/engine/stream/tests/test_executor.py`
- `python -m mypy polaris/cells/roles/runtime/internal/session_orchestrator.py polaris/cells/roles/kernel/internal/transaction/tool_batch_executor.py polaris/cells/roles/kernel/internal/transaction/task_contract_builder.py polaris/cells/roles/kernel/internal/transaction/stream_orchestrator.py polaris/cells/roles/kernel/internal/llm_caller/invoker.py polaris/kernelone/llm/engine/stream/executor.py`
- `python -m ruff check polaris/cells/roles/kernel/internal/transaction/intent_embedding_router.py polaris/cells/roles/kernel/internal/transaction/tests/test_intent_embedding_router.py --fix`
- `python -m ruff format polaris/cells/roles/kernel/internal/transaction/intent_embedding_router.py polaris/cells/roles/kernel/internal/transaction/tests/test_intent_embedding_router.py`
- `python -m mypy polaris/cells/roles/kernel/internal/transaction/intent_embedding_router.py`
- `python -m pytest -q polaris/cells/roles/kernel/internal/transaction/tests/test_intent_embedding_router.py`
- `python -m ruff check polaris/kernelone/tool_execution/tool_spec_registry.py polaris/kernelone/tool_execution/tests/test_tool_spec_registry.py --fix`
- `python -m ruff format polaris/kernelone/tool_execution/tool_spec_registry.py polaris/kernelone/tool_execution/tests/test_tool_spec_registry.py`
- `python -m mypy polaris/kernelone/tool_execution/tool_spec_registry.py`
- `python -m pytest -q polaris/kernelone/tool_execution/tests/test_tool_spec_registry.py`
- `python -m pytest -q polaris/cells/roles/runtime/internal/tests/test_session_orchestrator.py polaris/cells/roles/kernel/internal/transaction/tests/test_path_security.py polaris/cells/roles/kernel/internal/transaction/tests/test_stream_orchestrator.py polaris/cells/roles/kernel/internal/transaction/tests/test_task_contract_builder.py polaris/kernelone/llm/engine/stream/tests/test_executor.py`
- `python -m pytest -q polaris/cells/roles/kernel/tests/test_llm_caller.py`
- `python -m pytest -q polaris/cells/roles/runtime/internal/tests/test_session_orchestrator.py polaris/cells/roles/kernel/internal/transaction/tests/test_path_security.py polaris/cells/roles/kernel/internal/transaction/tests/test_stream_orchestrator.py polaris/cells/roles/kernel/internal/transaction/tests/test_task_contract_builder.py polaris/cells/roles/kernel/tests/test_llm_caller.py polaris/kernelone/llm/engine/stream/tests/test_executor.py`
- `python -m pytest -q polaris/cells/roles/runtime/tests/test_host_session_continuity.py polaris/delivery/cli/director/tests/test_console_host_orchestrator.py polaris/delivery/cli/director/tests/test_orchestrator_e2e_integration.py`

Notes:

- The previous order-sensitive `ToolSpecRegistry` context leak has been fixed. The formerly flaky combined pytest batch now passes in a single invocation.
