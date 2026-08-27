# L3-24 r57 Test Harness Forward-Unmask Blueprint

## Exact-run evidence

- Factory run: `factory_e5c6b591e82d`
- Workspace validation: `.polaris/runtime/qa/workspace-validation.json`
- Final Director request snapshot: `a009f1fa02091e3763b33eb0`
- Generated workspace remained read-only during diagnosis.

Dynamic replay proved the product still failed in `src/diary/diary_book.cpp`,
while `tests/test_engine_contracts.py` also failed inside its own error-reporting
path with `NameError: name 'sys' is not defined`.

The repair timeline was physically verified by file-event patches and effect
hashes:

1. `check=True` was replaced by captured compiler output and accepted as
   `progress` (`36bb508a… -> c50b8b4b…`).
2. The next edit added `import sys` (`c50b8b4b… -> e100ebb8…`).
3. The verifier then exposed the same C++ messages already reported by the
   independent compiler verifier, but raw diagnostic count remained eight.
4. Factory classified the edit as `equal_count_swap`, rolled it back, and
   repeated the same edit three times.

Candidate guard snapshots were correct: every rejected round captured
`c50b8b4b…` as its before hash and restored it. The defect was verifier-effect
classification, not rollback corruption or missing physical tool effects.

## Generic invariant

A Python test-harness exception may be considered reduced only when:

- the same unittest/pytest command loses a concrete wrapper-local `NameError`;
- post-edit output exposes compiler errors;
- every exposed compiler error message was already proven before the edit by
  an independent non-test compiler verifier; and
- real verifier success remains authoritative.

Novel compiler messages remain fail-closed. This is a narrow causal
`forward-unmask`/progress rule, not a general permission for equal-count swaps.

## Implementation

- `factory_workspace_quality_impl.py`
  - adds message-normalized compiler evidence comparison;
  - adds `workspace_quality_test_harness_barrier_reduced`.
- `factory_stage_executor/_mixin_02.py`
  - recognizes the proven harness reduction before generic regression/swap
    classification.
- characterization regression contains both the accepted exact shape and a
  negative case for novel compiler errors.

## Verification

- RED: exact characterization returned `equal_count_swap`.
- GREEN: exact characterization returns `progress`.
- Full workspace-quality characterization: `104 passed`.
- Ruff: clean for all three touched files.
- Mypy: clean for both source files.

## Remaining live validation

Run fresh isolated r58. Expected first-order effect: the `import sys` edit is
kept instead of repeated and rolled back. If the next residual remains C++,
dynamic evidence must confirm that same-Director routing moves to the current
production translation unit before any further platform edit.
