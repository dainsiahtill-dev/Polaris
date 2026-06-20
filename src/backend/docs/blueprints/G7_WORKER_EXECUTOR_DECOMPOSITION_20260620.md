# Blueprint: WorkerExecutor decomposition (G7) — behavior-preserving

**Status:** Blueprint ready. Execution = characterization-first, test-guarded, 9 steps. **NOT a safe one-shot** (behavioral refactor with coverage gaps).
**Target:** `polaris/cells/director/tasking/internal/worker_executor.py` — class `WorkerExecutor`, 68 defs, ~1828 lines.
**Source:** gap-audit 2026-06-20 (G7).

## Hard contracts (MUST preserve byte-identically)
- **Class + ctor:** `WorkerExecutor(workspace, message_bus=None, worker_id="")` — re-exported via `tasking/public/__init__.py:61` (+ 2 barrels) + cross-root shim `execution/internal/worker_executor.py`. Monkeypatched by `test_worker_service_responsiveness.py:28` (class path is a contract).
- **Only true public method:** `execute(self, task) -> TaskResult` (called from `task_execution_runner.py:122`, the subprocess entry point).
- **Engine back-reference (do NOT move off coordinator):** `_apply_response_operations(self, response, task_id="", llm_metadata=None, allowed_scope_paths=None)` — `CodeGenerationEngine` calls `self._executor._apply_response_operations(...)` (`code_generation_engine.py:1078`); `CodeGenerationEngine(workspace, self)` constructed at ctor line 218.
- **Test-called private methods (delegation shims must remain on the instance):** `_classify_task, _extract_tech_stack, _extract_files_from_response, _is_probable_file_path, _normalize_target_files, _normalize_scope_paths, _build_code_generation_rounds, _build_code_generation_prompt, _snapshot_workspace_files, _round_files_changed_since, _collect_existing_file_records, _write_code_generation_round_marker, _code_generation_round_marker_satisfied, _compact_prompt_fragment, _resolve_llm_call_timeout_hint, _apply_response_operations, _register_spin_guard, _invoke_generation_with_retries, _execute_{code_generation,file_creation,bootstrap,generic}, _raise_code_writing_forbidden, _fallback_*, _deterministic_repair_*`. Settable attrs tests rely on: `_code_engine` (set to fake / None at runtime — must stay a live-read instance attr), `workspace, _bus, _worker_id, _evidence_service, _file_service`. Module consts imported by tests: `_DEFAULT_DIRECTOR_LLM_CALL_TIMEOUT_SECONDS`, `_DEFAULT_DIRECTOR_RUNTIME_LLM_CALL_TIMEOUT_SECONDS`; dataclass `CodeGenerationResult`.

## CRITICAL — lazy-import dance is load-bearing
Ctor lines 119-170 defer `CodeGenerationEngine` + `FileApplyService` via `importlib` + `contextlib.suppress(ImportError)` to avoid circular imports at Director bootstrap. New collaborator modules MUST NOT import `code_generation_engine`/`file_apply_service` at module top — depend only on stdlib + kernelone (`KernelFileSystem`, `scan_workspace_artifact_quality`, `PathSecurityError`) + domain. Coordinator keeps the lazy wiring.

## Clusters → extract vs keep
- KEEP ON COORDINATOR: `execute`, `_execute_*` (orchestration, broad state), policy fail-closed stubs, `_apply_response_operations` + file-service pass-throughs, lazy engine/file-service wiring.
- EXTRACT (pure / single-field collaborators): A `task_classifier` (pure), G `response_parser` (pure), `path_predicates` (pure), E `workspace_probe(workspace)`, B `target_file_resolver(workspace)`, C `verification_repair`, D `codegen_rounds`, F `prompt_builder(token_service,...)`.

## §8 note (flag, do NOT delete here)
`_get_framework_guidance` (lines ~1498-1517) = DEAD (zero callers) but contains FastAPI/Flask business templates → §8 violation. `_extract_functional_requirements` also dead. Carry verbatim during Cluster-F extraction; open a SEPARATE §8 cleanup ADR to delete later. Do not couple to this pass.

## Extraction plan (each step independently test-green; run guard suite after each)
0. Baseline green. 1. `response_parser` (pure, smallest). 2. `task_classifier` (pure, well-tested). 3. `path_predicates` (pure leaves). 4. `workspace_probe` collaborator (marker round-trip semantics byte-identical). 5. `target_file_resolver` collaborator. 6. `verification_repair` (**NO direct tests — add characterization tests FIRST**). 7. `codegen_rounds`. 8. `prompt_builder` (largest; carry dead §8 funcs verbatim). 9. Slim coordinator. Each extract = new sibling module + thin delegating method retained on the class.

## Test guard + COVERAGE GAPS (extraction risk)
Guard suite: `pytest polaris/cells/director/tasking/tests/test_worker_executor.py polaris/cells/director/tasking/tests/test_contracts.py polaris/tests/test_worker_executor_smoke.py polaris/tests/test_worker_executor_tech_stack.py polaris/tests/test_worker_service_responsiveness.py -q` + ruff + mypy --strict.
NO direct coverage (write characterization tests before extracting): **entire verification-repair cluster C** (`_candidate_paths_for_unresolved_import`, `_repair_path_allowed`, `_unresolved_import_repair_records`, `_verification_repair_prompt_section`, `_verification_feedback`); scope-inference internals (`os.walk` ordering, `_SCOPE_INFERENCE_MAX_FILES` caps); `execute()` end-to-end envelope. Preserve these byte-for-byte; do not "tidy".
