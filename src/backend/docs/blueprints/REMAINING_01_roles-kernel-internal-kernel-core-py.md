# polaris/cells/roles/kernel/internal/kernel/core.py — class RoleExecutionKernel

kind=completed-refactor-record effort=closed

# Blueprint G3 — Decompose `RoleExecutionKernel` (core.py) into Thin Coordinator + Collaborators

## 0. Scope & invariants
- Target: `polaris/cells/roles/kernel/internal/kernel/core.py`, class `RoleExecutionKernel` (line 138 → EOF, 2509-line file, ~57 methods).
- Behavior-preserving, atomic-green: suite stays green after EACH step.
- ACGA 2.0: all new modules are siblings under `internal/kernel/` (same cell, internal). No new cross-cell imports. roles.kernel must NOT import roles.runtime (release-gate enforced).
- Style: extract-to-sibling-module, leave the class as a thin API shell.

## 1. Current state
This blueprint has been completed and is retained as a historical refactor record.
`RoleExecutionKernel` is now a thin public API shell: construction/configuration
state lives in `core.py`, while execution behavior is owned by sibling modules.
The active turn path is:

- `RoleExecutionKernel.run` -> `non_stream_turn_flow.execute_non_stream_role_turn`
- `RoleExecutionKernel.run_stream` -> `stream_turn_flow.execute_stream_role_turn`
- both flows instantiate `transaction_turn_executor.TransactionTurnExecutor`
- `TransactionTurnExecutor` creates and calls the canonical `TransactionKernel`
  through `transaction_factory.create_transaction_kernel`

## 2. Target module map (new siblings)
| Module | Responsibility | Holds |
|---|---|---|
| `transaction_factory.py` | TransactionKernel assembly | closures-as-funcs + `build_llm_provider/tool_runtime/llm_provider_stream` (weakref callbacks) |
| `transaction_turn_executor.py` | TransactionKernel-backed turn application service | `TransactionTurnExecutor.execute_turn` / `execute_stream` |
| `turn_orchestrator.py` | public run loop | `run_turn`, `run_turn_stream` (retry/quality) |
| `tool_dispatch.py` | per-tool dispatch | `execute_single_tool`, gateway turn-boundary helpers |
| `prompt_parse_event.py` | prompt/context/event/parse helpers | the small leaf helpers |

`core.py` retains: `__init__`, `create_default`, `config`,
`context_gateway_config_factory`, `run`, and `run_stream`.

## 3. Public surface (FROZEN)
- `RoleExecutionKernel` re-exported by `internal/kernel/__init__.py`, `public/service.py`, lazy `cells/roles/kernel/__init__.py`. Module `__all__` constants byte-identical.
- `__init__` keyword names + all `self._*` attributes frozen (read by siblings/tests).
- Cross-module contract: external callers use `kernel.run/run_stream`; TransactionKernel
  assembly stays inside roles.kernel via `transaction_factory.create_transaction_kernel`.
- Active callback protocols: `OutputParser` owns visible tool-call sanitation; tool
  execution enters through `kernel.tool_runtime_executor.execute_single_tool`.

## 4. Plan (atomic-green; see plan_steps for the ordered list)
STEP 0 characterization → 1 closures→funcs → 2 nested classes→factory builders → 3 turn/stream exec + dedupe preamble → 4 run/run_stream loop → 5 tool dispatch → 6 leaf helpers → 7 slim & verify gates.

## 5. Risks
Hot-path (per-turn/per-tool); preserve load-bearing function-local lazy imports (circular-import avoidance); weakref+closure+`__slots__` capture semantics; monkeypatch/bound-method shims mandatory; frozen `_create_transaction_kernel` cross-module signature; **§8 violation flagged**: embedded weak-Director Prong-A/R7/Fix-11 write-vs-edit heuristics + trace logging live in the turn/stream bodies — move verbatim, do NOT delete (separate governance pass); preserve turn-vs-stream PRONG_A_TRACE asymmetry when deduping.

## 6. Test guard & coverage gaps
Guards: test_role_execution_kernel_contract.py, test_policy_interceptor.py, test_roles_kernel.py, test_role_kernel_write_budget.py, test_run_stream_parity.py, test_turn_engine_compat_methods.py, architecture/test_kernelone_release_gates.py.
Gaps requiring characterization tests BEFORE extraction: the 4 big bodies (turn result-mapping branches, stream event-translation matrix, run retry/quality loop, `_create_transaction_kernel` closures) and the integrated tool-definitions preamble heuristics. Write these in STEP 0.

## 7. Effort: large (4 big bodies + dispatch + helpers; gated by characterization tests).
