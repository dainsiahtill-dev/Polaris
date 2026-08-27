# L3-24 r67 C++ Qualified Definition Context Blueprint

## Problem

Exact run `factory_308086628a50` reached the same-task Director quality-repair
loop with only `src/inkwell/moonlight.cpp` writable.  The final provider request
contained the complete bodies of `Moonlight::days_since_1900` and
`Moonlight::phase_from_date`, but the generated read-only API table
simultaneously labelled both methods `DECLARED BUT NOT DEFINED` and instructed
Director not to call them.  Three physical edits then failed to reduce the exact
`cpp_unit` residual and were correctly rolled back.

## Dynamic evidence

- Final request snapshots: `3b0669cf25511c3525c2231b` and
  `886a4f6fc0a13c8dce1a4679`.
- Offered tool surface was exactly `edit_file`; write authorization remained
  `src/inkwell/moonlight.cpp` only.
- The exact verifier still reports `cpp_unit: pass=29 fail=2`.
- `_cpp_defined_function_names` recognizes free definitions but misses
  class-qualified definitions such as `long Moonlight::days_since_1900(...)`.

## Invariants

1. A function with a concrete in-workspace `.c/.cc/.cpp/.cxx` body must never
   be projected as declaration-only.
2. Matching remains name-based for compatibility with the existing public API
   table; qualification is used only to recognize the definition syntax.
3. Constructors, destructors, control statements and declaration-only aliases
   must not become false definitions.
4. The change affects read-only prompt evidence only.  JobToken scope, write
   authorization, candidate rollback and verifier policy remain unchanged.
5. Generated Bench source remains read-only.

## Design

Extend the C++ definition recognizer so the function declarator accepts zero or
more `Type::` qualifiers before the terminal function name.  Continue returning
the terminal name because header declarations and the current linker guidance
are keyed by that name.

## Verification

- RED/GREEN unit: a header declaring static member functions plus a `.cpp`
  defining `Moonlight::...` must list them under defined functions and omit them
  from `DECLARED BUT NOT DEFINED`.
- Existing declaration-only linker-alias regression remains green.
- Focused Ruff, format, Mypy and pytest pass.
- Fresh isolated same-project repair must no longer contain the contradictory
  declaration-only rows in its final provider request.

