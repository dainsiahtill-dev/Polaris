# polaris/cells/roles/kernel/internal/turn_transaction_controller.py - class TurnTransactionController (Facade; 1981 lines on disk / 51 methods)

kind=god-class-split effort=medium

# Decomposition Blueprint: TurnTransactionController (G8 part)

Target: `polaris/cells/roles/kernel/internal/turn_transaction_controller.py`
Class: `TurnTransactionController` (Facade) - 1981 lines on disk / 51 methods.
Context: HOT single-commit kernel turn path (ADR-0071). Cell `roles.kernel`, layer `internal/`.

> WARNING: prompt states 1816 lines; disk is 1981. Several `transaction/` modules are dated 2026-06-20 (actively changing). Re-anchor ALL line numbers before editing.

## 1. Current State

This file is ALREADY a Facade. A prior pass (docstring lines 35-50) moved ~2000 lines into the `transaction/` subpackage (30+ modules). ~35 of 51 methods are already thin delegators to those modules or to 5 injected handler objects: `_finalization_handler`, `_handoff_handler`, `_tool_batch_executor`, `_retry_orchestrator`, `_stream_orchestrator`.

The residual god-class fat lives in a handful of fat methods:

| Method | Lines | ~Size | Role |
|---|---|---|---|
| `__init__` | 218-328 | 110 | sub-handler wiring + 4 proxy closures + session state |
| `_execute_turn` | 944-1297 | 353 | HOT non-stream orchestrator (5 phases inline) |
| `_build_decision_messages` | 1433-1616 | 183 | control-plane prompt synthesis (bilingual literals) |
| `_handle_final_answer` | 1827-1981 | 154 | final-answer + 2 block-gates |
| `_build_turn_result` | 1622-1696 | 75 | single-commit result assembly |
| `execute` / `execute_stream` | 734-939 | 205 | ~95% duplicated session/correlation/truthlog boilerplate |

## 2. Public Surface (FROZEN)

- **Subclass coupling**: `TransactionKernel` (transaction_kernel.py:15) subclasses this and calls `super().__init__/execute/execute_stream`. Production builds `TransactionKernel` at kernel/core.py:758, turn_engine.py:659, turn_engine/engine.py:536. `__init__(llm_provider, tool_runtime, config=None, workflow_runtime=None, llm_provider_stream=None, development_runtime=None)` is frozen.
- **Public methods**: `execute`, `execute_stream`, `on_event` - signatures frozen.
- **`llm_provider` property** with propagating setter (-> `_finalization_handler`, `_retry_orchestrator`).
- **Instance attrs reached by tests**: `decoder`, `_retry_orchestrator`, `_stream_orchestrator`, `_tool_batch_executor`, `_finalization_handler`, `_session_phase_manager`, `_session_modification_contract`, `_turn_outcome_history`.
- **Private methods called directly by tests (frozen)**: `_execute_turn`, `_execute_turn_stream`, `_execute_tool_batch`, `_retry_tool_batch_after_contract_violation`, `_call_llm_for_decision[_stream]`, `_build_decision_messages`, `_handle_final_answer`.
- **Classmethods pinned on the CLASS by parity test**: `_apply_delivery_mode_filter`, `_inherit_materialize_from_history`.
- **Module `__all__` (144-153)** re-export barrel + class-level intent constants (199-216): frozen import/monkeypatch targets. `transaction_kernel.py` imports `TransactionConfig` + `TurnTransactionController` from here.
- **Architecture fence**: literal `TurnTransactionController(` banned in `delivery/` (test_delivery_internal_import_fence.py:154).

## 3. Plan (extract-to-sibling-then-leave-shim; each step suite-green)

0. Characterization tests for uncovered branches (see section 5) - safety net first.
1. Extract `_build_decision_messages` body -> `transaction/decision_message_builder.py`; leave method as delegating shim (still injected as callback into StreamOrchestrator).
2. Extract Phase1b delivery-contract chain (998-1071) -> `transaction/delivery_contract_resolver.py`, passing facade helpers as callables to preserve monkeypatch penetration.
3. Extract Phase4 mutation-contract guard reconciliation (1166-1250) -> `transaction/mutation_contract_guard.py`.
4. Extract Phase2+3 decode pipeline (1079-1163) -> `transaction/decision_pipeline.py`. `_execute_turn` becomes a slim orchestrator (method stays - tests call it).
5. Extract the two block-gates in `_handle_final_answer` (1840-1956) -> `transaction/final_answer_gates.py` returning optional blocked-descriptors. Method stays (recon-gate tests).
6. Consolidate execute/execute_stream boilerplate -> `transaction/turn_session_scope.py`.
7. (Optional) Move proxy-closure wiring into `_build_subsystems()` helper.

Re-run after EACH step: `ruff check --fix`, `ruff format`, `mypy`, full `roles/kernel` pytest + integration + benchmark latency.

## 4. Risks
- Hot-path: per-turn allocation; prefer module free functions over per-turn classes.
- ADR-0071 commit invariants: do NOT reorder `transition_to` / `state_history.append` / `ledger.record_*` - guards read ledger state.
- Single commit point: `record_session_state_snapshot` BEFORE `finalize()` (in `_build_turn_result`) is the auditability contract; gate paths finalize then build result - keep ordering.
- Monkeypatch penetration via late-bound `self.method` callbacks injected at `__init__` - keep facade methods as shims.
- `llm_provider` setter propagation (pinned by tests) - do not move.
- Lazy import in `_drain_speculative_tasks` (circular-import avoidance) - keep local.
- `contextlib.suppress(ValueError)` around contextvar resets + truthlog fire-and-forget RuntimeError suppression - preserve verbatim.
- Polaris 8 flag: `_build_decision_messages` embeds large bilingual prompt-engineering literals (business/prompt content in kernel internal). Move verbatim; do NOT delete/rewrite in a behavior-preserving pass; flag for separate review.

## 5. Coverage Gaps (characterize BEFORE extracting)
- `KERNELONE_DELIVERY_MODE_TRACE` trace logging (962-979).
- no-user-turn anomaly flag (980-997).
- downgrade-no-write-tools (1038-1062).
- auto-upgrade vs upgrade-blocked (1174-1210).
- text-only-tool-batch suppression (1107-1132).
- ADR-0090 I3 decode-corrective re-ask (1081-1095).
- `_build_decision_messages` branch matrix (proposal/super-readonly/benchmark/implementing-HARD-GATE/bootstrap-write-retry).
- `_build_turn_result` snapshot-before-finalize.
- `_emit_phase_event` truthlog fire-and-forget + no-loop suppression (596-617).

## 6. Test Guard
test_transaction_kernel_facade.py (primary), test_transaction_controller.py, test_recon_gate.py, test_mutation_guard_soft_mode.py, transaction/tests/test_delivery_mode_filter_parity.py, test_stream_nonstream_parity_transaction_kernel.py (critical after step 6), test_integration_transactional_flow.py, integration/.../test_transaction_kernel_e2e.py, architecture/test_delivery_internal_import_fence.py, benchmark/test_latency_baseline.py.