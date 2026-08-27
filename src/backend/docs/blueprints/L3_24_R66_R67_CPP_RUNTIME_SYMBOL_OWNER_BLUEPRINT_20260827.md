# L3-24 r66-r67 C++ runtime symbol owner closure

## Verdict

Closed the exact-run causal-owner defect. A C++ test call-site must not compete
with the corresponding production definition when Factory selects a runtime
behavior repair owner.

## Dynamic evidence ladder

1. Exact residual: `factory_308086628a50` had two Moonlight assertions and no
   Cipher assertion.
2. Exact resolver call: the pre-fix public resolver fell back from
   `tests/cpp_unit.cpp` to unrelated `src/inkwell/cipher.cpp`.
3. Helper drill-down: both `tests/cpp_unit.cpp` and
   `src/inkwell/moonlight.cpp` contained `Moonlight::days_since_1900`; the
   textual uniqueness guard treated call-site and definition as peers.
4. Source fix: product-owner scanning now excludes test-like C/C++ translation
   units. Compiler/test-source errors continue to use the independent compiler
   owner route.
5. r67 final request: snapshots `3b0669cf25511c3525c2231b` and
   `886a4f6fc0a13c8dce1a4679` authorize only
   `src/inkwell/moonlight.cpp` and include its real UTF-8 body.
6. r67 effects: three `edit_file` effects touched only that owner. Each failed
   the same verifier, was restored by candidate guard, and the existing
   three-nonprogress fuse stopped the run.

## Invariants

- Runtime assertion observers are not product-definition owners.
- Test files remain eligible when the compiler itself names the test source.
- Final provider scope, tool path authority, effect receipt, verifier result,
  rollback receipt, and terminal TaskRuntime state must agree.
- A correct owner does not imply a correct semantic patch; candidate guard must
  remain fail-closed.
- Generated Bench artifacts remain read-only evidence.

## Verification

- Director quality routing: `64 passed`.
- Factory candidate transactions: `110 passed`.
- Production-source Ruff: pass.
- Production-source Mypy: pass.
- Exact same-run r67: owner scope changed from `cipher.cpp` to
  `moonlight.cpp`; no owner drift; no false acceptance.

## Residual, not part of this closure

The bound model produced three wrong date/month-phase edits. The current
verifier-test repair branch intentionally exposes only `edit_file`; therefore
the model cannot run a bounded diagnostic probe before mutation. This is a
separate architecture decision. Do not loosen the tool surface until a test
proves all of the following: probe is read-only, mutation remains mandatory,
candidate budget is bounded, exact verifier evidence is fed into the edit turn,
and read-only loops still terminate.
