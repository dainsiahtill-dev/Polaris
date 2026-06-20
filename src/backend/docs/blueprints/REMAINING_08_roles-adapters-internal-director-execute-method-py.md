# polaris/cells/roles/adapters/internal/director/execute_method.py :: _execute_standard_llm_flow (and the execute_method.py god-file as a whole)

kind=confirm-status effort=medium

# G2 Status Confirmation Blueprint — `director/execute_method.py`

## Verdict: PARTIALLY DONE — NOT done. Effort to finish: **medium**.

The prior warm-up was real and substantial, but the named god-function `_execute_standard_llm_flow` is **still 774 lines** and is **not yet a thin orchestrator**. ~518 of those lines are inline mutation loops that were never extracted.

## Measured current state (file = 3004 lines)
- `_execute_standard_llm_flow`: lines **1013-1786 = 774 lines**.
- `MaterializationState` frozen dataclass: lines 947-1010 (exists; from_locals/as_locals/with_diff/with_affected).
- **14 `_phase_*` helpers exist** and are invoked: `_phase_deterministic_cleanup` (1789), `_phase_existing_scope_preflight` (1838), `_phase_first_llm_call` (1947 async), `_phase_direct_fallback` (2034), `_phase_empty_write_retry` (2087 async), `_phase_typescript_reexport_repair` (2134), `_phase_python_unittest_repair` (2167), `_phase_pre_materialization_target_repair` (2204), `_phase_no_materialized_changes` (2255), `_phase_existing_scope_verified` (2400), `_phase_missing_write_receipt` (2505), `_phase_quality_failed` (2607), `_phase_semantic_quality_failed` (2723).
- Three leaf modules already extracted with a **lossless bottom-of-file re-export shim**: `deterministic_repairs/` (imported 2858), `quality_gate.py` (2950), `task_scope_paths.py` (2985), all `# noqa: E402 (deferred for circular-import safety)`.

## Why it is NOT done — 4 un-extracted inline blocks inside the flow
| Block | Lines | Size | What it is |
|---|---|---|---|
| A | 1157-1229 | 73 | pre-materialization quality recompute |
| B | 1296-1514 | **219** | progress-aware quality-repair loop (largest) |
| C | 1538-1615 | 78 | semantic-quality repair loop |
| D | 1639-1786 | 148 | materialized-paths reconcile + completion metadata + finalize + return |

Between every phase the flow round-trips `state.as_locals()` / `MaterializationState.from_locals(...)` (1141, 1223, 1296, 1508, 1538, 1609, 1639) — the mutation soup the dataclass was meant to remove still persists in the inline tail.

## Frozen public surface (must stay byte-identical)
1. `execute_director_task(adapter, task_id, input_data, context)` — sole cross-module entry (adapter.py:25/226; reached via `cells/director/execution/public/service.py` and roles/runtime handlers). Full result-dict contract frozen.
2. **The module namespace itself is an import barrel**: `test_director_adapter_pure.py` (6220 lines) imports ~100+ underscore symbols from `execute_method`; every alias in the 2858-3004 re-export blocks must keep resolving here.
3. **Monkeypatch**: `execute_method.scan_workspace_artifact_quality` (patched at 6190/6211) — keep the top-of-file module-level name (lines 28-30) and resolve at call time.
4. `DirectorToolExecutor` re-export (32-34); `_pin_materialize_context_delivery_mode` reached via getattr in test_pin_materialize_delivery_mode.py.

## Plan (atomic-green, extract-to-sibling-then-delegate)
1. Baseline green: `pytest polaris/cells/roles/adapters/tests/ -q`.
2. **Characterization first** — entrypoints have ZERO direct tests; add e2e tests for the success dict and all four error-path dicts with a fake adapter.
3. Extract Block B (219 lines) → `_phase_quality_repair_loop` (async sibling). Biggest win.
4. Extract Block A → `_phase_pre_materialization_quality`.
5. Extract Block C → `_phase_semantic_quality_repair_loop` (async).
6. Extract Block D → `_phase_finalize_materialization` (success epilogue).
7. (Optional/last) collapse the as_locals/from_locals round-trips to thread `MaterializationState` directly.
8. Verify flow is a thin <~120-line spine; mypy + ruff; do NOT move the bottom re-export blocks.

## Risks
- The three bottom deferred-import blocks are load-bearing for circular-import safety AND the test barrel — keep at bottom, keep all `X as X` aliases; put new helpers IN execute_method.py, not in the leaf modules.
- `scan_workspace_artifact_quality` must resolve via the module namespace at call time (don't bind a local).
- Hot path (factory-bench); F31/F16/L-series fixes embedded inline — preserve exact diff→error→static-smoke→runtime-smoke→filter ordering (duplicated verbatim in Blocks B and C) and the prev/current progress-budget gating.
- **§8 flag (do not delete in this pass)**: `deterministic_repairs/` holds framework-specific business code (JS/TS/zod/typeorm/npm-test-script generation, pinned dependency-version tables) inside a role adapter — flag for governance, keep functioning.

## Test guard
`test_director_adapter_pure.py` (dominant), `test_pin_materialize_delivery_mode.py`, `test_declared_target_case_insensitive.py`, `test_director_realtime_file_events.py`; cross-cell: `cells/director/execution/tests/test_execution_contracts.py`, `cells/roles/runtime/public/tests/test_role_runtime_object_contracts.py`.

## Coverage gaps (write characterization tests BEFORE extracting Blocks A-D)
- `execute_director_task` and `_execute_standard_llm_flow`: no direct tests.
- Block B budget/loop, Block C semantic loop, Block D no_physical_files + completion-metadata + finalize-failure fallback: only indirect coverage.
- 13 of 14 `_phase_*` helpers (all except `_phase_no_materialized_changes`) lack direct unit tests.

## Critical files for implementation
- /home/dains/Documents/polaris/src/backend/polaris/cells/roles/adapters/internal/director/execute_method.py
- /home/dains/Documents/polaris/src/backend/polaris/cells/roles/adapters/internal/director/quality_gate.py
- /home/dains/Documents/polaris/src/backend/polaris/cells/roles/adapters/internal/director/deterministic_repairs/generic_repairs.py
- /home/dains/Documents/polaris/src/backend/polaris/cells/roles/adapters/tests/test_director_adapter_pure.py
- /home/dains/Documents/polaris/src/backend/polaris/cells/roles/adapters/internal/director/adapter.py