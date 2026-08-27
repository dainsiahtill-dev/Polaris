# L3-24 r55 C++ Use-Before-Definition Repair Blueprint

Date: 2026-08-27  
Owner: `director.runtime`  
Scope: generic C/C++ same-translation-unit declaration ordering only

## Exact-run evidence

- Factory run: `factory_f81dc5e8d1a8`
- Target workspace is evidence-only and was never modified.
- Exact verifier: `g++ -std=c++17 -fsyntax-only -I. -Isrc -Iinclude src/moon_phase.cpp`
- Exact residual: `src/moon_phase.cpp:40:38: error: ‘ymd_to_days’ was not declared in this scope`.
- Final provider request snapshot: `82dc9c36541c43b8f2699928`.
- The Director received the correct role, CE authority, target body, diagnostic, tools, and forced `edit_file` contract. Native edits produced effects and receipts, but the compiler residual remained, so rollback was correct.
- Repair coverage incorrectly selected `cpp.missing_private_members`; its header/class planner cannot repair a free function defined later in the same `.cpp` file.

## Invariants

1. Generated Bench projects remain read-only during platform diagnosis.
2. Coverage routes same-TU free-function declaration-order diagnostics to one executable `director.runtime` source tool.
3. The planner only acts when there is exactly one later, same-scope, single-line free-function definition and no prior declaration.
4. Qualified member definitions, templates, overload ambiguity, default arguments, unsafe paths, and multi-line ambiguity fail closed.
5. The composed forward effect is a precise `text_replace`, projected to Director `edit_file`; full-file `write_file` is rollback-only.
6. Real success still requires verifier evidence; an applied receipt alone is not authoritative completion.

## Implementation

- Add `deterministic_cpp_use_before_definition_repair` to runtime bindings, catalog, coverage registry, public Plan/Run dispatch, and package exports.
- Add source-path constraints to `cpp.missing_private_members` so `.cpp` free-function diagnostics cannot false-match the header-only rule.
- Insert the exact later definition signature as a forward declaration immediately before the caller.
- Keep aggregate C++ post-execution planning as a consumer of the same runtime-owned builder; do not create an adapter-owned rule.

## Verification

- TDD RED: coverage false-routed and public Plan/Run rejected the unknown source tool.
- Focused regression: 4 passed.
- Repair kernel regression: 193 passed.
- Ruff: clean.
- Targeted mypy: clean.
- Offline exact-r55 replay on a copied source: public Run produced one precise edit receipt and the exact `g++ -fsyntax-only` verifier passed.
- Fresh isolated L3-24 Bench remains the final end-to-end gate.

