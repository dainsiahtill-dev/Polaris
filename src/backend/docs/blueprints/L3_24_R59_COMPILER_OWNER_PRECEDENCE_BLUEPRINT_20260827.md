# L3-24 r59 Compiler Owner Precedence Blueprint

## Exact-run evidence

- Factory run: `factory_fd2dbd023e42`
- Workspace: `/tmp/factory-bench-l3-24-r59/workspaces/9e5cb5d86055-4dbac1f2fedb6eae/L3-24-023d7314133040b6/1873d28e53dfc867caf94bf7`
- Final Director request snapshots:
  - initial materialization: `00194f83b0b165b29b8c6cf6`, `2308359e759b7c26297ee8ec`
  - repair rounds: `bd8cee107374a6ba5ad75bc2`, `1df6218c6d7ca268953538f1`, `acbd6fabb2f3e0460ad229e1`, `3e2c53e0fd341255db9a2be6`
- Generated workspace remained read-only. Dynamic replay called read-only Polaris
  routing helpers against exact persisted QA evidence.

r59 proved the r58 barrier fix generalized: C++ failing translation units fell
from four to one, eight source files materialized, and the chain reached QA.
The remaining primary compiler failure was:

```text
src/core/cipher.cpp:97:22: error: no matching function for call to
InvisibleCipher::to_hex(const char*, unsigned long) const
```

The declaration accepted one `std::string_view` argument. The generated target
project defect was therefore locally repairable by Director.

## Dynamic root cause

All four repair requests had correct Director identity, PM contract, CE
blueprint, current compiler feedback, and an `edit_file` tool. The platform
nevertheless forced every mutable target to `tests/test_acceptance.py`.

Physical evidence:

- Director issued two real `edit_file` calls against the Python observer test.
- Both candidates were receipted and then rolled back as equal-count swaps.
- `src/core/cipher.cpp` remained read-only in every repair request.
- Repair stopped at `three_nonprogress_repairs_without_verified_progress`.

Persisted round evidence contained a contradiction:

- `explicit_quality_target_files[0] == src/core/cipher.cpp`
- `repair_target_files == [tests/test_acceptance.py]`
- `rotated_repair_targets == true`

The contradiction originated in Factory before adapter rotation. Mixed
TaskRuntime owner evidence began with the unittest observer, and
`_workspace_quality_llm_claim_target_files` chose that first intersecting owner.
The existing `FAILING_TUS` compiler frontier was calculated but not supplied to
the claim selector. Adapter then correctly treated the Factory-selected test as
the forced mutation target, overriding its own production-first candidates.

## Generic invariant

When exact verifier evidence contains a compiler translation-unit frontier:

1. restrict candidates to non-test paths in that frontier;
2. preserve current normalized diagnostic causal order inside that set;
3. select the first path also authorized by the immutable TaskRuntime owner;
4. do not let a derived unittest observer outrank the physical compiler owner;
5. if the frontier contains only test targets, preserve test ownership.

This is target-language-neutral routing policy. It does not repair target code,
invent a diagnostic rule, or broaden JobToken scope.

## Implementation

- `factory_workspace_quality_impl.py`
  - `_workspace_quality_llm_claim_target_files` accepts the compiler diagnostic
    frontier and intersects it with current diagnostic order and owner scope.
  - `_apply_workspace_quality_llm_repairs` passes the already-computed frontier.
- `test_characterization_workspace_quality_checks.py`
  - pure owner-selection regression for the exact mixed test/C++ case;
  - Factory-wrapper regression proving the frontier reaches the Director repair
    request.

## Verification

- TDD RED: selector rejected the new compiler-frontier contract.
- Targeted regressions: `2 passed`.
- Full workspace-quality characterization: `107 passed`.
- Ruff: clean.
- Mypy: clean.
- Exact r59 read-only replay:
  - old claim: `tests/test_acceptance.py`
  - new claim: `src/core/cipher.cpp`

## Next live gate

Run fresh isolated L3-24 r60. The first LLM repair for this residual must expose
`src/core/cipher.cpp` as its only mutable target. If r60 fails, capture the new
exact final request, tool/effect receipts, verifier result, TaskRuntime, Run
Ledger/QA, and settlement before any further edit.
