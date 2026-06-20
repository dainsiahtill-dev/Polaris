# G8c: /home/dains/Documents/polaris/src/backend/polaris/cells/roles/kernel/internal/llm_caller/invoker.py — class LLMInvoker, mega-methods call (L458-1040, ~583 lines) and call_structured (L1046-1473, ~428 lines).

## current_state
LLMInvoker (__slots__-based, 1828 lines, ~28 methods) is the consolidated non-stream/structured/stream LLM service. Two mega-methods dominate. call (L458) is a single linear try/except body that: resolves max_tokens + healthy binding, builds an LLMCaller and prepared request (caller._prepare_llm_request), emits call_start, computes cache_eligibility, then runs an inline "fallback ladder" of up to 5 sequential executor.invoke() calls — (1) native_tools_unavailable text fallback returning early, (2) cache lookup returning early, (3) primary invoke, (4) native-tool text fallback on is_native_tool_calling_unsupported, (5) structured/response_format fallback on is_response_format_unsupported, (6) reasoning-truncation re-ask, (7) role-binding fallback via _try_role_binding_fallback — each rung repeating the same response_ok/error-string extraction idiom (ok/_has_error/_raw_error/response_error/is_response_ok, ~6 lines x5). On failure it emits call_error + returns LLMResponse(error). On success it normalizes output (output_text, ResponseNormalizer.extract_text fallback), resolves provider/model, extracts native tool calls, puts cache, emits call_end, returns LLMResponse. Three except arms (CancelledError re-raise, (ImportError/AttributeError/TypeError/ValueError), RuntimeError) each emit call_error and (except cancel) return LLMResponse. call_structured (L1046) mirrors the shape: prepare + call_start, then three strategy branches each with their own success emit_end+return and failure handling — (1) native_response_format invoke+extract_json+validate, (2) Instructor create_structured, (3) _build_structured_fallback_request invoke+extract_json+validate with parse-error arm — plus CancelledError/RuntimeError except arms returning StructuredLLMResponse. Metadata dicts are built inline and wrapped via _with_context_os_audit; elapsed_ms recomputed at every exit. Shared helpers already extracted: _try_role_binding_fallback, _emit_* delegates, _is_cache_eligible, _allow_native_tool_text_fallback, _extract_context_snapshot_ref, _with_context_os_audit (module fn). call_stream already delegates to StreamEngine — the proven decomposition template.

## public_surface
FROZEN (must not change): async signatures and return types of LLMInvoker.call(...)->LLMResponse and LLMInvoker.call_structured(...)->StructuredLLMResponse (all params incl. defaults: profile, system_prompt, context, response_model, temperature=0.7, max_tokens=4000, [max_retries=3 for structured], prompt_fingerprint, platform_retry_max=1, run_id, task_id, attempt=0, turn_round=0, event_emitter). These are duck-typed by callers (no shared ABC enforces them). Callers of .call: kernel/internal/kernel/core.py:628,659; kernel/internal/kernel/turn_engine.py:532,563; kernel/internal/turn_engine/engine.py:400,431; invoker.call_decision (L1724) and call_finalization (L1749) via DecisionCaller/FinalizationCaller; deprecated caller.py:895 shim. Callers of .call_structured: caller.py:936 deprecated shim only. Direct tests: test_llm_invoker_role_binding_fallback.py (4x invoker.call). The byte-identical contract extends to: emitted event sequence/args (_emit_call_start/end/error/retry), every metadata key/value and _with_context_os_audit wrapping, LLMResponse/StructuredLLMResponse field population (content/error/error_category/token_estimate/tool_calls/tool_call_provider/metadata; data/raw_content/validation_errors), elapsed_ms rounding, log lines, and exception re-raise vs return semantics. The 5 already-private helpers and the StreamEngine delegate pattern stay as-is.

## plan_steps
- Step 0 (red->characterization): Before touching code, add characterization tests pinning current behavior of BOTH mega-methods using an injected fake executor (LLMInvoker(executor=...)) — cover all coverage_gaps below. Run pytest green to establish the baseline. No production edits yet.
- Step 1: Extract a private dataclass-free helper _build_call_context(self, profile, context, system_prompt, max_tokens, run_id, role_id) returning a small namespace (call_id, run_id, task_id, role_id, model, effective_max_tokens, start_time) replacing the L476-486 preamble of call. Keep call_structured preamble inline for now. Run pytest + mypy + ruff. Green.
- Step 2: Extract _prepare_and_emit_start(self, *, caller_builder, profile, system_prompt, context, temperature, max_tokens, response_model, platform_retry_max, stream, role_id, run_id, task_id, attempt, turn_round, event_emitter, extra_start_metadata, structured) -> (caller, prepared, context_result, prompt_tokens). This consolidates the identical LLMCaller construction (L494-501 / L1076-1083), _prepare_llm_request, and the _emit_call_start_event block (differs only by the structured-vs-plain start metadata keys passed via extra_start_metadata). Verify call_start metadata byte-identical for both. Green.
- Step 3: Extract pure helper _read_response_status(response) -> tuple[bool, str] returning (is_response_ok, response_error) implementing the repeated ok/_has_error/_raw_error/response_error/is_response_ok idiom exactly (5 copies in call, 1 in call_structured fallback). Replace all 6 sites. This is the highest-duplication, lowest-risk extraction. Green.
- Step 4: Extract the native-tools-unavailable branch (L545-621) into _handle_native_tools_unavailable(self, *, caller, prepared, profile, context, model, role_id, run_id, task_id, attempt, call_id, event_emitter, start_time) -> LLMResponse | None (returns LLMResponse on early-return success/error, None to continue). Preserve exact metadata and the native_tools_text_fallback success shape. Green.
- Step 5: Extract the cache-hit branch (L623-679) into _try_cache_hit(...) -> LLMResponse | None and the cache-put (L872-880) into _store_cache(...). Preserve enable_cache/prompt_fingerprint/cache_eligible guard and emit_call_end on hit. Green.
- Step 6: Extract the fallback ladder (L698-783) into _run_fallback_ladder(self, *, caller, executor, prepared, profile, context, response, active_request, response_model, response_error, is_response_ok, allow_native_tool_text_fallback, system_prompt, temperature, effective_max_tokens, platform_retry_max, role_id, run_id, task_id, attempt, model, call_id, event_emitter) -> a result namespace (response, active_request, profile, prepared, model, native_tool_fallback, native_response_fallback, response_error, is_response_ok). Keep the 4 rungs (native-tool text, response_format, reasoning-truncation, role-binding) in exact order; _try_role_binding_fallback stays as the inner call. Green.
- Step 7: Extract the error-return builder (L785-834) into _build_call_error_response(...) -> LLMResponse (emits error event + returns) and the success builder (L836-935) into _finalize_call_response(...) -> LLMResponse (normalize text incl. ResponseNormalizer, extract_native_tool_calls, cache put, emit_call_end). After this, call's try-body is a short orchestration of the phase helpers. Green.
- Step 8: Extract call's three except arms into _call_exception_response(self, exc, ...) where the two return-arms share a builder and CancelledError keeps its own emit+raise (must NOT route through a returning helper). Verify re-raise semantics preserved. Green.
- Step 9: Apply the symmetric decomposition to call_structured: extract _try_native_response_format_structured(...) -> StructuredLLMResponse | None (L1127-1190, incl. the inner is_response_format_unsupported swallow), _try_instructor_structured(...) -> StructuredLLMResponse | None (L1192-1258), _run_structured_fallback(...) -> StructuredLLMResponse (L1259-1407 incl. parse-error arm), and reuse _read_response_status + an error/end emit pattern. Keep the RuntimeError-swallow-and-continue control flow identical. Green.
- Step 10: Extract call_structured's CancelledError/RuntimeError except arms mirroring Step 8 (structured metadata variant). Green.
- Step 11: Final pass — confirm both mega-methods are now <~60 line orchestrators; run full module test suite (test_llm_invoker_*, test_llm_caller*, test_stream_parity*, test_structured_findings) + mypy + ruff check/format on invoker.py. Diff-review metadata dicts key-by-key against pre-refactor capture. Green.

## risks
- Metadata drift: dozens of inline metadata dicts have subtly different key sets per exit (e.g. call_start vs call_end vs error; native_tool_mode source differs at L804-809 using active_context.get(...) or prepared.*). Moving these into helpers risks dropping/reordering keys. Mitigate: capture metadata via fake event_emitter in Step 0 and assert dict-equality.
- elapsed_ms is recomputed at EACH exit point from start_time; if start_time is passed into a helper instead of recomputed there, timing-derived values stay correct, but any helper that recomputes time.perf_counter() at a different point changes rounded ms (cosmetic but breaks byte-identical metadata asserts). Pass start_time, recompute inside helper at the same logical point.
- Control-flow via sentinel return (LLMResponse | None) for early-return branches (native-tools, cache) must exactly preserve which branches fall through vs return; an off-by-one in the None-vs-response contract silently changes whether the primary invoke runs.
- CancelledError MUST re-raise, not return — routing it through a shared returning error helper would swallow cancellation and break asyncio task cancellation across the kernel turn engine.
- The fallback ladder mutates many locals (profile, prepared, active_request, model, native_tool_fallback, native_response_fallback) that are later read by the error/success finalizers; extracting it into a function changes them from closure mutation to returned namespace — every downstream read must be repointed or behavior diverges.
- Exception type tuples differ between arms ((ImportError,AttributeError,TypeError,ValueError) vs RuntimeError) and between call (logger.error/exception) and call_structured; consolidating except handling risks broadening/narrowing caught types or changing log severity.
- is_response_ok idiom has a non-obvious rule: ok=True WITH a non-empty error string counts as failure (handles providers returning ok=True + 'Unknown field: tools'); _read_response_status must reproduce the bool/isinstance guard exactly.
- Cache eligibility is computed once (cache_eligible) and reused for both get and put; the put guard at L872 re-checks the SAME flags — keep a single source so get/put stay consistent.
- Duck-typed surface: no ABC pins call/call_structured signatures, so an accidental kwarg-name change (e.g. event_emitter) would only fail at the kernel/turn_engine call sites at runtime, not at import. Keep signatures byte-identical.
- call_structured's native_response_format branch swallows RuntimeError and FALLS THROUGH to Instructor/fallback unless is_response_format_unsupported is false (then it re-raises into the outer RuntimeError handler) — this two-level control flow is easy to flatten incorrectly.

## test_guard
Baseline before refactor: cd /home/dains/Documents/polaris/src/backend && python -m pytest polaris/cells/roles/kernel/tests/test_llm_invoker_role_binding_fallback.py polaris/cells/roles/kernel/tests/test_llm_invoker_final_request_receipt.py polaris/cells/roles/kernel/tests/test_llm_caller_components.py polaris/cells/roles/kernel/tests/test_llm_caller.py polaris/cells/roles/kernel/tests/test_llm_caller_text_fallback.py polaris/cells/roles/kernel/tests/test_stream_parity.py polaris/cells/roles/kernel/tests/test_structured_findings.py -q. Plus the new Step-0 characterization tests. After EACH step rerun that suite + ruff check polaris/cells/roles/kernel/internal/llm_caller/invoker.py --fix && ruff format && mypy polaris/cells/roles/kernel/internal/llm_caller/invoker.py. Refactor is behavior-preserving ONLY if every step stays green with zero metadata-dict diffs.

## coverage_gaps
- call: native_tool_mode=='native_tools_unavailable' WITH allow_native_tool_text_fallback + native_tool_schemas -> text-fallback success early-return (L548-579) — untested.
- call: native_tools_unavailable with fallback disallowed/failed -> build_native_tool_unavailable_error error return (L582-621) — untested.
- call: cache hit early-return path (L624-679) and cache put on success (L872-880) — untested (existing tests use enable_cache=False).
- call: native-tool text fallback rung on is_native_tool_calling_unsupported(response_error) (L698-713) — untested.
- call: response_format fallback rung on is_response_format_unsupported (L715-729) — untested.
- call: reasoning-truncation re-ask rung (_is_reasoning_truncation, L731-748) — untested; high-value (director_no_materialized_changes guard).
- call: empty-output ResponseNormalizer.extract_text recovery (L847-853) and native_tool_calls extraction/tool_call_provider population (L865-867,917-918) — untested.
- call: asyncio.CancelledError arm (L937-960) re-raise + cancel event — untested.
- call: (ImportError/AttributeError/TypeError/ValueError) vs RuntimeError except arms metadata/log differences (L962-1040) — untested.
- call_structured: native_response_format success path (L1127-1184) incl. empty-content json.dumps(raw) fallback and response_model validation — UNTESTED (no direct behavioral test of call_structured exists).
- call_structured: native_response_format non-unsupported error -> RuntimeError raised then swallowed-and-warned (L1185-1189) — untested.
- call_structured: Instructor path success + RuntimeError->fallback (L1192-1258) — untested (INSTRUCTOR_AVAILABLE branch).
- call_structured: _build_structured_fallback_request invoke success + extract_json + validate + emit_end (L1315-1371) — untested.
- call_structured: fallback not-ok error return (L1273-1313) and parse-failure validation_fail arm with validation_errors (L1372-1407) — untested.
- call_structured: CancelledError (L1409-1433) and RuntimeError (L1435-1473) except arms — untested.
- Shared: is_response_ok rule where ok=True + non-empty error string counts as failure — not pinned by any test.

## full
# G8c — Behavior-Preserving Decomposition Blueprint: `LLMInvoker.call` / `LLMInvoker.call_structured`

File: `polaris/cells/roles/kernel/internal/llm_caller/invoker.py` (1828 lines, class `LLMInvoker`, `__slots__`-based, ~28 methods).

## 1. Target & Goal
Decompose two mega-methods into cohesive phase helpers with BYTE-IDENTICAL behavior (events, metadata, return shapes, exception semantics):
- `call` (L458–1040, ~583 lines)
- `call_structured` (L1046–1473, ~428 lines)

`call_stream` (L1479) already delegates to `StreamEngine` and is the proven in-file template for this decomposition.

## 2. Anatomy of `call` (linear try-body + 3 except arms)
| Lines | Phase | Proposed helper |
|---|---|---|
| 475–486 | preamble: call_id/run_id/task_id/role_id/model/effective_max_tokens/start_time | `_build_call_context` |
| 488–541 | LLMCaller build + `_prepare_llm_request` + `_emit_call_start_event` | `_prepare_and_emit_start` |
| 543 | cache eligibility | (keep inline; pure `_is_cache_eligible`) |
| 545–621 | native_tools_unavailable: text-fallback success OR error return | `_handle_native_tools_unavailable -> LLMResponse|None` |
| 623–679 | cache hit early-return | `_try_cache_hit -> LLMResponse|None` |
| 681–696 | primary `executor.invoke` + status read | core orchestration + `_read_response_status` |
| 698–783 | fallback ladder: native-tool text / response_format / reasoning-truncation / role-binding | `_run_fallback_ladder -> namespace` |
| 785–834 | error return | `_build_call_error_response -> LLMResponse` |
| 836–935 | success: normalize/extract/cache-put/emit_end | `_finalize_call_response -> LLMResponse` |
| 937–960 | CancelledError -> emit + re-raise | keep inline (must re-raise) |
| 962–1040 | two return-except arms | `_call_exception_response` (shared builder) |

The repeated status idiom (`response_ok`/`_has_error`/`_raw_error`/`response_error`/`is_response_ok`) appears 5x in `call` and 1x in `call_structured` -> single pure `_read_response_status(response) -> (is_response_ok, response_error)`.

## 3. Anatomy of `call_structured` (3 strategy branches + 2 except arms)
| Lines | Phase | Proposed helper |
|---|---|---|
| 1063–1124 | preamble + prepare + call_start (structured start metadata) | reuse `_prepare_and_emit_start(structured=True)` |
| 1127–1190 | native_response_format invoke -> extract_json -> validate -> emit_end; non-unsupported error re-raises then swallowed | `_try_native_response_format_structured -> StructuredLLMResponse|None` |
| 1192–1258 | Instructor `create_structured` (guarded by `INSTRUCTOR_AVAILABLE`) | `_try_instructor_structured -> StructuredLLMResponse|None` |
| 1259–1407 | `_build_structured_fallback_request` invoke -> not-ok error return / extract+validate success / parse-fail arm | `_run_structured_fallback -> StructuredLLMResponse` |
| 1409–1433 | CancelledError -> emit + re-raise | keep inline |
| 1435–1473 | RuntimeError -> emit + return | `_structured_exception_response` |

Critical: branches (1) and (2) return on success, else **fall through** (RuntimeError swallowed + warning). This two-level control flow must not be flattened into mutually-exclusive if/elif.

## 4. Frozen Public Surface
`call(...) -> LLMResponse` and `call_structured(...) -> StructuredLLMResponse` signatures (all params + defaults) are duck-typed (no ABC). External callers: `kernel/internal/kernel/core.py` (628,659), `kernel/internal/kernel/turn_engine.py` (532,563), `kernel/internal/turn_engine/engine.py` (400,431), `call_decision`/`call_finalization` -> DecisionCaller/FinalizationCaller, and deprecated `caller.py` shims (895/936). The `ILLMInvoker` protocol in `services/contracts.py` is a DIFFERENT surface (invoke/invoke_stream/invoke_structured) — not these methods. Byte-identical contract includes: event call order+args, all metadata keys/values + `_with_context_os_audit` wrapping, `elapsed_ms` rounding, response field population, log lines, re-raise-vs-return.

## 5. Execution Strategy (atomic green)
Extract lowest-risk highest-duplication helpers first (`_read_response_status`), then early-return branches behind `LLMResponse | None` sentinels, then the stateful fallback ladder (convert closure mutation -> returned namespace), then finalizers, then except arms. Mirror onto `call_structured`. Every step: pytest + mypy + ruff, zero metadata-dict diffs. See `plan_steps`.

## 6. Coverage Gaps -> Characterization Tests (Step 0, before any edit)
`call_structured` has essentially NO direct behavioral test (only a shim test of `caller.py`). `call` is covered only for success + role-binding fallback + auth-error-no-fallback. All fallback rungs, cache, native-tools-unavailable, reasoning-truncation, ResponseNormalizer recovery, and both structured strategy branches are untested — full list in `coverage_gaps`. Pin them with an injected fake `executor` (`LLMInvoker(executor=fake)`) and a fake `event_emitter` asserting metadata dict-equality.

## 7. Risks
Metadata drift, elapsed_ms recompute-point, sentinel None-vs-response contract, CancelledError must re-raise (not route through returning helper), fallback-ladder local mutation, divergent except tuples/log severity, the ok=True+error-string failure rule, single-source cache eligibility for get/put. Full list in `risks`.