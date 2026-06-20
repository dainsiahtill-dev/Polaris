# polaris/cells/roles/kernel/internal/kernel/core.py — class RoleExecutionKernel

kind=god-class-split effort=large

# Blueprint G3 — Decompose `RoleExecutionKernel` (core.py) into Thin Coordinator + Collaborators

## 0. Scope & invariants
- Target: `polaris/cells/roles/kernel/internal/kernel/core.py`, class `RoleExecutionKernel` (line 138 → EOF, 2509-line file, ~57 methods).
- Behavior-preserving, atomic-green: suite stays green after EACH step.
- ACGA 2.0: all new modules are siblings under `internal/kernel/` (same cell, internal). No new cross-cell imports. roles.kernel must NOT import roles.runtime (release-gate enforced).
- Style: extract-to-sibling-module, leave delegating shim on the class.

## 1. Current state
A previous wave already extracted `commit_protocol.py`, `delivery_mode.py`, `tool_policy.py`, `error_handler.py`, `helpers.py`, `suggestions.py`, `tool_executor.py`, turning ~13 methods into one-line delegators. The residual bulk is 4 large in-class bodies (~1200 lines):
- `_create_transaction_kernel` (431→768, ~337 lines; 5 closures + 3 weakref-bound nested classes)
- `_execute_transaction_kernel_turn` (910→1180, ~270 lines)
- `_execute_transaction_kernel_stream` (1182→1447, ~265 lines)
- `run` (1572→1898, ~326 lines; retry + quality loop)
Plus `_execute_single_tool` (2057→2185, ~128 lines) and a long tail of prompt/parse/event/tool helper shims.

## 2. Target module map (new siblings)
| Module | Responsibility | Holds |
|---|---|---|
| `transaction_factory.py` | TransactionKernel assembly | closures-as-funcs + `build_llm_provider/tool_runtime/llm_provider_stream` (weakref callbacks) |
| `turn_execution.py` | single-turn + stream exec | `execute_transaction_kernel_turn/_stream` + shared `build_turn_tool_definitions` preamble |
| `turn_orchestrator.py` | public run loop | `run_turn`, `run_turn_stream` (retry/quality) |
| `tool_dispatch.py` | per-tool dispatch | `execute_single_tool`, gateway turn-boundary helpers |
| `prompt_parse_event.py` | prompt/context/event/parse helpers | the small leaf helpers |

`core.py` retains: `__init__`, `create_default`, `config`, all `_get_*` accessors, all `inject_*`, and thin delegating shims for every externally-referenced method.

## 3. Public surface (FROZEN)
- `RoleExecutionKernel` re-exported by `internal/kernel/__init__.py`, `public/service.py`, lazy `cells/roles/kernel/__init__.py`. Module `__all__` constants byte-identical.
- `__init__` keyword names + all `self._*` attributes frozen (read by siblings/tests).
- Cross-module 'private' contract: `runtime/public/service.py` calls `kernel._create_transaction_kernel(role, profile, request) -> TransactionKernel` and `kernel.run/run_stream`.
- Back-ref protocols: `turn_materializer` → `kernel._parse_content_and_thinking_tool_calls(...)`; `_ToolRuntime` weakref → `kernel._execute_single_tool(...)`, `kernel.reset_tool_gateway_turn_boundary(...)`.
- Monkeypatch targets (must remain bound-method attrs): `_execute_single_tool`, `_execute_tools`, `_split_tool_calls_by_write_budget`.

## 4. Plan (atomic-green; see plan_steps for the ordered list)
STEP 0 characterization → 1 closures→funcs → 2 nested classes→factory builders → 3 turn/stream exec + dedupe preamble → 4 run/run_stream loop → 5 tool dispatch → 6 leaf helpers → 7 slim & verify gates.

## 5. Risks
Hot-path (per-turn/per-tool); preserve load-bearing function-local lazy imports (circular-import avoidance); weakref+closure+`__slots__` capture semantics; monkeypatch/bound-method shims mandatory; frozen `_create_transaction_kernel` cross-module signature; **§8 violation flagged**: embedded weak-Director Prong-A/R7/Fix-11 write-vs-edit heuristics + trace logging live in the turn/stream bodies — move verbatim, do NOT delete (separate governance pass); preserve turn-vs-stream PRONG_A_TRACE asymmetry when deduping.

## 6. Test guard & coverage gaps
Guards: test_facade_refactor.py, test_policy_interceptor.py, test_roles_kernel.py, test_role_kernel_write_budget.py, test_run_stream_parity.py, test_turn_engine_compat_methods.py, architecture/test_kernelone_release_gates.py.
Gaps requiring characterization tests BEFORE extraction: the 4 big bodies (turn result-mapping branches, stream event-translation matrix, run retry/quality loop, `_create_transaction_kernel` closures) and the integrated tool-definitions preamble heuristics. Write these in STEP 0.

## 7. Effort: large (4 big bodies + dispatch + helpers; gated by characterization tests).