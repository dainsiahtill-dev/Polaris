# L3-24 r68 C++ Inline-Constexpr Use-Before-Definition Blueprint

Date: 2026-08-27  
Owner: `director.runtime`  
Scope: generic C++ same-file declaration ordering for narrow inline `constexpr` variables

## Exact-run evidence

- Factory run: `factory_e32d2196f20b`.
- Generated workspace is evidence-only and was not edited by the main Agent.
- Exact compiler replay: `cmake -S <workspace> -B <workspace>/build-debug && cmake --build <workspace>/build-debug --parallel 1`.
- First causal error: `include/lunaris/invisible_diary.hpp:42:56: error: ‘DEFAULT_CIPHER_ALPHABET’ was not declared in this scope`.
- The same header defines exactly one `inline constexpr` variable with that identifier later at lines 59-60.
- `query_director_repair_coverage` reports `known_rule_matched=false`; the existing `cpp.use_before_definition` rule only accepts `.cpp` free-function calls.
- Task attempts 2-4 stayed inside the same Director task, but unrelated deterministic candidates were rolled back as `workspace_quality_repair_regression` / `workspace_quality_repair_equal_count_swap`; no later Director LLM call occurred.

## Invariants

1. Never edit the generated Bench workspace while diagnosing or implementing the platform fix.
2. Extend the existing runtime-owned `cpp.use_before_definition` rule; do not add adapter/Factory/Bench repair logic.
3. Only move one unambiguous, later, same-scope `inline constexpr` variable definition before its first compiler-proven use.
4. Preserve the exact definition text. Do not invent a declaration, type, initializer, or API.
5. Conditional compilation, multiple definitions, unsafe paths, non-`inline constexpr` variables, and ambiguous scopes fail closed.
6. Emit one precise `text_replace`/`edit_file` receipt; success still requires the original verifier to pass.

## Verification plan

- TDD: exact r68 diagnostic is uncovered before the change.
- Coverage: exact diagnostic maps to `cpp.use_before_definition` and its executable source tool.
- Plan/Run: moves the existing definition, emits one edit receipt, and does not use `write_file`.
- Negative tests: non-`inline constexpr` and conditional definitions remain unplanned.
- Offline exact-r68 replay: apply only to a copied workspace and rerun the exact CMake build.
- Fresh isolated L3-24 Bench is required after focused lint/type/test gates.
