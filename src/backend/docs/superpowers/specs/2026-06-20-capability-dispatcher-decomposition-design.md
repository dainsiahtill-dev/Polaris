# Capability Dispatcher Decomposition — Design & Plan (2026-06-20)

> Status: APPROVED (director sign-off 2026-06-20 — deep behavioral refactor, full scope).
> Source: 15-agent deliberation workflow `next-refactor-deliberation` (survey×6 → propose×5 → judge×3 → synthesize×1).
> This document is the **contract** for every implementation agent in this campaign. Read it fully before editing.

## Goal

Eliminate (not relocate) the worst remaining structural + type-safety debt in the backend by behaviorally
decomposing the **1,839-line, 13-way capability dispatcher god-function**
`execute_role_capability_invocation` (src/backend/polaris/cells/roles/runtime/public/capability_commands.py:1479-3318)
into a **typed `CapabilityHandler` Strategy/dispatch-table**, while landing three approved companion fixes.

## Why this target (judge consensus 2-of-3 #1)

- Largest single unit in the survey (1,839 lines = 55% of a 3,318-line file that was itself an *output* of the prior
  lossless campaign — relocated but never decomposed).
- Textbook switch-statement SRP violation: 13 capability-identity boolean flags + 13 `if/elif` branches (lines ~1537-1674).
- Most concentrated **zero-`Any`-in-public** violation: 12 `Any | None` `*_service` kwargs on the public signature (~1482-1493).
- Best lossless caller story: all 32 callers are the contract tests in
  `cells/roles/runtime/public/tests/test_role_runtime_object_contracts.py` (public facade preserved).
- Sits on the hottest self-editing seam (routes `execute_director_task` / the announce-not-write path).

## Honest scope boundary (DO NOT OVERSELL)

This refactor removes **ZERO import-time graph edges** (only 1 module-level cross-cell import at line 28; the ~37
cross-cell imports are lazy/function-local). The win is **SRP + zero-`Any`-public + AI-agent edit reliability** — it
is **NOT** a cycle/hub break. Anyone reporting this work must not claim a dependency-graph improvement for the
dispatcher itself.

## Hard constraints (apply to every task)

- **Behavioral safety = characterization-first.** This changes internal control flow of a PUBLIC cell facade. Safety
  comes from a Phase-0 oracle that snapshots, from **git HEAD**, every branch's `RoleCapabilityInvocationResultV1`
  field values AND the exact `error_code` string literals; diff against it after EVERY slice.
- **Byte-identical wire values.** `error_code` strings must stay identical (e.g. `role_mismatch`,
  `capability_not_mounted`, `capability_role_denied`, `capability_contract_mismatch`,
  `capability_fingerprint_mismatch`, `payload_ref_outside_turn_context`, `invalid_budget_metadata`). The new
  `CapabilityInvocationError` codes MIRROR these literals; never invent new strings.
- **errors.py EXCLUSION respected.** The new exception lives in
  `cells/roles/runtime/internal/capability/errors.py`, NOT `kernelone/errors.py`. It inherits `KernelOneError`.
- **IRON-LAW §8.** Capability routing is pure platform mechanics — zero target-project/business code.
- **Public surface preserved.** `execute_role_capability_invocation` keeps its name and is still re-exported by
  `cells/roles/runtime/public/service.py`. Only the 12 `Any` kwargs change (to typed ports / a typed deps model).
- **Cross-cutting prelude stays VERBATIM in the dispatcher.** The role/capability-mount/role-allow/contract/
  fingerprint/payload_ref precondition guards (~1496-1618) are ordering-sensitive; keep them in the dispatcher, do
  not push into handlers.
- **Quality gates (fail-closed).** Every task ends GREEN on: `ruff check <paths> --fix && ruff format`,
  `mypy --strict <paths>` → "Success: no issues found" (ZERO `Any` in new public interfaces, NO new `# type: ignore`),
  and the covering pytest suite. All file I/O explicit UTF-8.

## Architecture route — typed CapabilityHandler registry

1. **`CapabilityHandler` Protocol** (`internal/capability/protocol.py`): three SRP methods —
   `validate(command) -> None` (raises a coded `CapabilityInvocationError` on bad payload),
   `invoke(command, deps) -> <raw>`, `map_result(raw, command) -> RoleCapabilityInvocationResultV1`.
2. **`CapabilityDeps`** (`internal/capability/deps.py`): a frozen dataclass with explicit **Protocol-typed** optional
   service ports replacing the 12 `Any | None` kwargs (e.g. `BudgetGuardPort`, `WorkspaceGuardPort`, `PermissionPort`,
   `ArchitectDesignPort`, `DirectorExecutionPort`, `QaAuditPort`, `BlueprintPort`, `CodeIntelligencePort`,
   `TaskMarketPort`, `RuntimeProjectionPort`, `LlmControlPlanePort`, `VerificationGuardPort`). Consumer-owned ports
   (defined in roles.runtime/internal), keeping the change inside one cell — zero `Any` on the public interface.
3. **`CapabilityHandlerRegistry`** (`internal/capability/registry.py`): frozen, keyed on the identity tuple
   `(capability_id, owner_cell, command_contract)` — exactly the tuple the 13 `is_*` flags reconstruct by hand.
   Each handler owns its own function-local lazy import of its owner cell (the lazy cross-cell imports move out of the
   dispatcher into owner-aligned handler modules).
4. **`CapabilityInvocationError(KernelOneError)`** (`internal/capability/errors.py`): stable codes mirroring the
   oracle's `error_code` strings.
5. **Reduced dispatcher**: `TypeError` guard + verbatim prelude guards + registry lookup + `handler.validate/invoke/
   map_result` + one coded unknown-capability path (target ≤150 lines).

## The 13 capabilities (verify the EXACT set + identity tuples from code lines ~1537-1674 in Phase 0)

director_task_execution · budget_reservation · workspace_guard · boundary_validation · qa_pytest_verification ·
qa_visual_audit · qa_audit_verdict · qa_traceback_parse · blueprint_generation · ce_ast_dependency ·
pm_critical_path · task_market_dispatch · pm_runtime_projection.
NOTE non-uniform branches: `budget_reservation`, `workspace_guard`, `boundary_validation` chain multi-stage
validation (not the simple validate→invoke→map shape) — `validate`/`invoke` must absorb this per-branch divergence.

## Phased plan (dispatcher)

- **Phase 0 — Oracle (no code change).** Pin GREEN baseline; capture from git HEAD every branch's result-shape fields
  + `error_code` literals + the public `__all__`/service.py re-export surface. Confirm the exact 13 identity tuples.
- **Phase 1 — Typed seam (additive, zero behavior change).** Add Protocol + `CapabilityDeps` + registry +
  `CapabilityInvocationError`. Add an OPTIONAL `handlers: CapabilityHandlerRegistry | None = None` kwarg ALONGSIDE the
  existing 12 kwargs (do not remove yet). Dispatcher behavior unchanged.
- **Phase 2 — Template slice.** Extract `director_task_execution` into a handler; dispatcher delegates to the registry
  for THIS capability, legacy `if/elif` fall-through for the other 12 (hybrid coexistence → independently revertible).
- **Phase 3 — Fan-out.** Extract the remaining 12 families, ONE family per commit; each owns its lazy owner-cell
  import; delete its legacy arm only after its handler is GREEN + oracle-diff clean.
- **Phase 4 — Collapse.** Reduce the dispatcher to prelude + lookup + dispatch; delete the 13 `is_*` flag block + the
  `if/elif` ladder; remove the 12 `Any` kwargs (migrate test injections to a fakes-registry helper); assert ZERO `Any`.
- **Phase 5 — Fitness test.** AST test asserting the dispatcher stays ≤150 lines, exposes no `Any` `*_service` kwargs,
  every registered identity tuple has exactly one handler, and no per-capability lazy cross-cell import remains in
  capability_commands.py. Prevents regrowth.

### Per-phase verification (run fresh; evidence before claims)
```
ruff check cells/roles/runtime/internal/capability cells/roles/runtime/public/capability_commands.py --fix && ruff format
mypy --strict cells/roles/runtime/internal/capability cells/roles/runtime/public/capability_commands.py
pytest cells/roles/runtime/public/tests/test_role_runtime_object_contracts.py -q
# + per-branch result-shape/error_code diff vs the Phase-0 HEAD oracle for the touched capability
```

## Companion streams (approved, separate from the dispatcher)

- **Stream: warm-up (Phase -1).** In-place phase-extraction of `_execute_standard_llm_flow`
  (cells/roles/adapters/internal/director/execute_method.py:937-2151, ~1214 lines) into named `_phase_<name>(state)`
  helpers threading ONE frozen `MaterializationState` accumulator. Same signature, single private caller (line ~686
  / actual `execute_director_task`). No re-export shim (private). Verify: `test_director_adapter_pure.py` (184 tests)
  + lossless diff vs HEAD on the 7 return-dict shapes + the signature. Proves the extraction recipe on a zero-public
  -surface target.
- **Stream: async correctness fix.** Delete the blocking `_async_write_text` "w"-path + `_checkpoint_session` inline
  `open()`/`os.fsync`; route ALL persistence through `kernelone.fs.text_ops.write_text_atomic` (and a locked append
  for the delta "a"-path) via `asyncio.to_thread`; add `ArtifactPersistError(KernelOneError)` in roles.runtime
  internal (NOT errors.py); remove the silent `aiofiles ImportError` swallow. Keep delta-append semantics
  (`---END_DELTA---`). Files: session_artifact_store.py, session_orchestrator.py. Verify the covering session tests +
  add targeted unit tests (atomicity, lock, no event-loop block).
- **Stream: companion architecture sub-wins (CONSERVATIVE, re-verify before claiming).** (a) Remove the dead no-op
  `register_all_adapters` coupling (verify zero behavioral reliance — real wiring is the module-level
  `configure_orchestration_role_adapter_factory`; check all callers/tests first). (b) Move generic
  `read_json`/`read_readme_title` down to `kernelone/fs` and repoint `workspace.integrity` + `runtime.projection`
  consumers to break the workspace↔projection cycle. Re-verify the cycle with an import/SCC check; re-baseline
  `tests/architecture/test_kernelone_reverse_dep_fence.py`. Do NOT claim the orchestration↔roles cycle is broken
  (roles.adapters→orchestration reverse edge survives). Add a cell-SCC fitness test only if it passes honestly.

## Execution order (collision-safe)

1. **Wave A (parallel, disjoint files + disjoint test suites):** warm-up ∥ async-fix ∥ dispatcher Phase 0+1.
2. Review + independent verification (HEAD-worktree where lossless claims are made).
3. **Wave B (serial — shared dispatcher file):** dispatcher Phase 2 → 3 (per-family) → 4 → 5.
4. **Wave C (standalone, conservative):** companion sub-wins.
5. Final full verification + report. All changes left UNCOMMITTED in the working tree (commit only on director request).
