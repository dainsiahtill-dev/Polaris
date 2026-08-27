# L3-24 r62 C++ authored-test compiler owner repair

## Verdict

r62 did not fail because Director lacked tools or refused to edit. It failed because Polaris gave Director the wrong writable file.

Exact compiler evidence named `tests/cpp_unit.cpp:125`. The generated test mutates a value obtained through a `const` reference. Yet the repair selector mixed compiler, runtime-smoke, and delivery-depth candidates, discarded test paths, and forced every Provider repair onto `src/inkwell/cipher.cpp`. Candidate receipts and rollback worked; mutation authority was wrong.

Generated project remained read-only throughout diagnosis and validation.

## Exact-run evidence

- Factory run: `factory_308086628a50`
- Initial verifier: `g++ -std=c++17 -fsyntax-only -I . -I src -I include tests/cpp_unit.cpp`
- Exit: `1`
- Physical compiler owner: `tests/cpp_unit.cpp`
- Final-request snapshots: `81b067f455d004a6da90e209`, `dab71c6bbeef530501d4c732`, `528c83bb3b004d4a0e0e3628`
- Those requests exposed only `src/inkwell/cipher.cpp` to `edit_file`.
- Provider produced real edits; verifier made no causal progress or exposed new declaration errors; candidate guard rolled edits back.

## Root cause

Two classifications were conflated:

1. `workspace_quality_unclaimed_failing_tu_targets()` may fall through to delivery-depth production candidates when compiler parsing yields no remaining marker path.
2. `_workspace_quality_llm_claim_target_files()` formerly treated only non-test sources as compiler authority.

An authored compiler-failing test translation unit is not a runtime observer. Compiler location is physical mutation evidence and must remain authoritative inside the canonical TaskRuntime/JobToken boundary.

## Platform fix

`_workspace_quality_direct_cpp_compiler_target_files()` now reads normalized diagnostics and returns only exact workspace-local C/C++ compiler paths. External standard-library notes are rejected.

Routing invariants:

1. Exact compiler path is selected before mixed runtime/depth causality.
2. Authored test translation units remain valid compiler mutation owners.
3. TaskRuntime and JobToken still authorize scope; path extraction grants no authority by itself.
4. Legacy `FAILING_TUS` discovery is used only when no exact compiler path exists and the marker is explicit.

## Verification

- TDD regressions: `2 passed`.
- Full characterization file: `109 passed`.
- Ruff: pass.
- Mypy: pass.
- Exact r62 dynamic replay after fix:
  - `direct_compiler_targets = ["tests/cpp_unit.cpp"]`
  - `causal_targets = ["tests/cpp_unit.cpp"]`
  - `claim_target_files = ["tests/cpp_unit.cpp"]`

Fresh isolated L3-24 remains required before closing the defect. Next run must prove the final Provider request grants the exact authored test TU, then continue separately to runtime-smoke and depth residuals.

## Same-run stage-local validation (r63 -> r64)

The existing isolated backend for `factory_308086628a50` was reused through
`retry_phase(target_phase="qa_gate")`. PM, Chief Engineer, and the original
Director materialization remained completed; no earlier role was rerun.

r63 proved the compiler-owner fix live:

- final Provider request role: `director`;
- physical tool schema: `edit_file` with forced tool choice;
- authorized target: `tests/cpp_unit.cpp`;
- current target body, the exact compiler diagnostic, and the read-only test
  source context were present;
- file-edit event: `tests/cpp_unit.cpp`, `modify`, three modified lines;
- independent read-only verifier: all seven C/C++ translation units returned
  `g++ -fsyntax-only` exit `0`.

The next quality frontier was runtime behavior, not a recurrence of the
compiler-owner defect. Exact `unittest` replay produced four failures:

- three CLI scenarios exited `3` because decode reported
  `truncated bit cell`;
- `cpp_unit` exited `1` with six residual assertions spanning Cipher/Reveal
  round-trip and Moonlight date calculations.

r63 emitted real edits to `src/inkwell/cli_main.cpp` and
`src/inkwell/moonlight.cpp`, then exhausted that QA repair epoch. Replaying the
terminal r63 diagnostic through the current read-only causal selector produced
`["src/inkwell/cipher.cpp"]`. r64 therefore reopens only `qa_gate` on the same
run so the remaining decode owner can be attempted without repeating PM, CE,
or Director materialization. The generated workspace remains read-only to the
main Agent; all mutations are Polaris Director effects.
