# polaris/cells/director/tasking/internal/worker_executor.py (class WorkerExecutor) — G7 decomposition steps 6-8

kind=god-class-split effort=medium

# Blueprint: WorkerExecutor decomposition (G7) — steps 6-8 (RESUME)

**Status:** Steps 1-9 DONE. Steps 6 (verification_repair.py / `VerificationRepair`), 7 (codegen_rounds.py / `CodegenRoundPlanner`), 8 (prompt_builder.py / `PromptBuilder`) extracted 2026-06-21; worker_executor.py 1333 -> 998 lines. Characterization tests added first for all three clusters (test_verification_repair.py 39, test_codegen_rounds.py 22, test_prompt_builder.py 24). Guard suite green after each step (219 passed, 1 pre-existing unrelated subprocess-monkeypatch failure). Three new modules mypy --strict clean + ruff clean; worker_executor.py pre-existing mypy error count reduced 36 -> 33 (no new error classes). §8 dead funcs (`_get_framework_guidance`, `_extract_functional_requirements`, `_code_generation_round_chunk_configured`, class `_is_test_like_target_file`) carried VERBATIM with dead-code banners; separate §8 cleanup ADR still pending. All test-called private methods kept as delegating instance shims; `_apply_response_operations` + `_compact_prompt_fragment` stay on the coordinator. Public surface (public barrel, cross-root execution shim, monkeypatch class-path) verified byte-identical.
**Target:** `polaris/cells/director/tasking/internal/worker_executor.py` — class `WorkerExecutor`.
**ACGA 2.0:** new collaborators are siblings under the SAME cell `internal/`; cross-cell stays via `public/`; direction delivery->application->domain->kernelone preserved.

## Hard contracts (preserve byte-identical) — step 6-8 scope
- `WorkerExecutor(workspace, message_bus=None, worker_id="")` ctor + class path string (monkeypatch contract) unchanged.
- `_build_code_generation_rounds(self, task)` and `_build_code_generation_prompt(self, task, *, round_index=0, round_total=0, round_files=None)` MUST remain instance methods (delegating shims) — 11 live test callers (`test_worker_executor.py:415/434/452/475/498/516/544/517/546`, `test_worker_executor_tech_stack.py:177/234`).
- `_compact_prompt_fragment` listed as a hard-contract method (blueprint line 11) — keep as instance shim; inject as a callable into prompt_builder, do NOT relocate off the instance.
- `@staticmethod _parse_unresolved_import_entry` and `@staticmethod _path_under_scope` keep their static decorator on the class shim. `_path_under_scope` already delegates to `path_predicates.path_under_scope`.
- Every other step 6-8 method has ZERO external callers (grep over `polaris/` confirms internal-only): may become delegating shims; behavior must not change.

## Lazy-import dance (load-bearing)
worker_executor.py lines 90-130 defer `CodeGenerationEngine`/`FileApplyService` via `importlib` + `contextlib.suppress(ImportError)`. New modules `verification_repair.py`, `codegen_rounds.py`, `prompt_builder.py` MUST import only stdlib + `path_predicates` + domain `Task` (+ injected `token_service`/collaborator refs). NEVER import `code_generation_engine`/`file_apply_service` at module top.

## Cluster map (current lines)
**6 verification_repair** — `_verification_feedback` 616-637, `_parse_unresolved_import_entry` 639-649 (static), `_candidate_paths_for_unresolved_import` 651-694, `_path_under_scope` 696-699 (already shim), `_repair_path_allowed` 701-708, `_unresolved_import_repair_records` 710-739, `_verification_repair_target_paths` 741-755, `_verification_repair_prompt_section` 757-770.

**7 codegen_rounds** — `_construction_file_plans` 777-785, `_build_code_generation_rounds` 797-847, `_resolve_code_generation_round_chunk_size` 849-863 (static), `_code_generation_round_chunk_configured` 865-870 (static, DEAD), `_effective_code_generation_round_chunk_size` 872-878, `_is_test_like_target_file` 880-886 (static shim, class copy DEAD).

**8 prompt_builder** — `_extract_architecture_context` 787-790, `_get_module_for_task` 792-795, `_extract_functional_requirements` 1013-1032 (DEAD), `_get_framework_guidance` 1034-1053 (DEAD, §8), `_build_code_generation_prompt` 1055-1227.

## §8 flag (do NOT delete here)
`_get_framework_guidance` (FastAPI/Flask templates) and `_extract_functional_requirements` are DEAD (zero callers) §8 business code. Carry VERBATIM into prompt_builder.py with a docstring banner; open a SEPARATE §8 cleanup ADR. Also dead-but-carry: `_code_generation_round_chunk_configured`, the class-level `_is_test_like_target_file` shim.

## Execution plan (atomic-green; guard suite after EACH)
**6a CHARACTERIZATION FIRST** — `tests/test_verification_repair.py` against the class as-is:
- `_verification_feedback`: 4 precedence shapes (`previous_verification_result`; `phase_context.verification_result`; `task_context.previous_verification_result`; `task_context.phase_context.verification_result`) each returns the embedded dict; empty/non-dict -> `{}`.
- `_parse_unresolved_import_entry`: `'a.ts: ../b'`->`('a.ts','../b')`; strips `` ` `` `'` `"` around ref; `'a.ts: b'` (no leading dot)->None; `'noColon'`->None; `'a.ts:'`->None; backslash in source normalized to `/`.
- `_candidate_paths_for_unresolved_import` (create files on tmp_path): `.ts` source + `../../src/app` with `src/app.ts` present -> `['src/app.ts']`; `'../'`-escaping or absolute resolved -> `[]`; explicit-extension ref (`.json`/`.ts`) -> single resolved candidate when it exists; dedup preserves order; only existing concrete paths survive.
- `_repair_path_allowed`: in `normalize_target_files`->True; under a `scope_paths` entry->True; non-existent->False; neither->False.
- `_unresolved_import_repair_records`: record `{source_file, import_ref, candidate_files[:3]}`; dedup on parsed tuple; skip when source not allowed; skip when no allowed candidates.
- `_verification_repair_target_paths`: source + first candidate, dedup, `/` normalization.
- `_verification_repair_prompt_section`: empty -> `'- No previous verification failure was provided.'`; populated -> two header bullets + per-record `'<src> imports <ref>; allowed repair candidate(s): ...'`, `records[:8]` / `candidates[:3]`.

**6b EXTRACT** `internal/verification_repair.py` (verbatim docstring matching target_file_resolver.py style). `class VerificationRepair(target_resolver, workspace_probe)` exposing feedback/unresolved_import_repair_records/repair_target_paths/repair_prompt_section; `_parse_unresolved_import_entry` as module function. Ctor: `self._verification_repair = VerificationRepair(self._target_resolver, self._workspace_probe)`. Replace the 7 bodies with one-line shims (keep `@staticmethod` decorators). Suite green.

**7a CHARACTERIZATION** — add to existing test file(s) the UNTESTED branches: `construction_plan.rounds` path (non-concrete filtering, `[[]]` when all filtered); empty/None plan falls through to `normalize_target_files`; `_construction_file_plans` reads `metadata.construction_plan.files`; `_resolve_code_generation_round_chunk_size` clamp (`>8`->8, non-int->0, `'0'`->0); `[[]]` when no targets. Suite green.

**7b EXTRACT** `internal/codegen_rounds.py` (verbatim docstring). Move `_build_code_generation_rounds` + `_construction_file_plans` + 3 chunk statics + DEAD `_code_generation_round_chunk_configured` + DEAD `_is_test_like_target_file` shim (carry verbatim). `class CodegenRoundPlanner(verification_repair, target_resolver)`. `_build_code_generation_rounds` body on class becomes a shim (MUST stay on instance). Suite green.

**8a CHARACTERIZATION** — pin the prompt: `_extract_architecture_context`/`_get_module_for_task` returns; arch-hints assembly (`module_order` truncation `'... 及其他 N 个模块'`, `module_arch` layer/deps/`stability>0.3`, `violation_constraints` ❌/⚠️ `[:2]`); construction_hints (`implementation_steps[:2]` join, fallback `'follow ChiefEngineer file plan'`, target membership filter, `[:12]` cap); `target_scope_rule` three-way branch. Suite green.

**8b EXTRACT** `internal/prompt_builder.py` (verbatim docstring + §8 dead-code banner). Move `_build_code_generation_prompt` + `_extract_architecture_context` + `_get_module_for_task` + DEAD `_extract_functional_requirements` + DEAD `_get_framework_guidance`. `class PromptBuilder(token_service, target_resolver, verification_repair, codegen_rounds)`; receive `_compact_prompt_fragment` as a callable (do not relocate it off the instance). Carry the Chinese guidance literals + Output-contract block byte-identical. `_build_code_generation_prompt` body on class becomes a shim (MUST stay on instance). Suite green.

**9 SLIM + VERIFY** — coordinator left with `execute`/`_execute_*`, lazy wiring, fail-closed stubs, `_apply_response_operations` + file-service pass-throughs, and the two test-contract shims. Full guard suite + ruff + mypy --strict. Update this doc status.

## Test guard
`pytest polaris/cells/director/tasking/tests/test_worker_executor.py polaris/cells/director/tasking/tests/test_contracts.py polaris/tests/test_worker_executor_smoke.py polaris/tests/test_worker_executor_tech_stack.py polaris/tests/test_worker_service_responsiveness.py -q` + ruff check --fix + ruff format + mypy --strict, after each step. Existing step 6-8 guards: `test_worker_executor.py:404-556`, `test_worker_executor_tech_stack.py:161/224/234`.

## Coverage gaps (write characterization tests before extracting)
Cluster 6: 7 internal methods have NO direct test (only the `:525` end-to-end repair-round). Cluster 7: `construction_plan.rounds` branch, chunk-size clamp, `_construction_file_plans`, `[[]]` fallback untested. Cluster 8: arch-hints + construction_hints assembly, `target_scope_rule` branches untested. Dead code carried verbatim: `_get_framework_guidance`, `_extract_functional_requirements`, `_code_generation_round_chunk_configured`, class `_is_test_like_target_file` shim.

## Risks
Lazy-import dance load-bearing; the two builder methods are instance-method test contracts; §8 dead business code carried not deleted; `_compact_prompt_fragment` stays on instance (inject as callable); FS-dependent candidate methods need tmp_path files in tests; hot-path — construct collaborators once in ctor, single delegation hop; Chinese/Output-contract literals encoding-sensitive (8a guards drift).