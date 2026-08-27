# L3-24 r58 C++ Include Barrier Blueprint

## Exact-run evidence

- Factory run: `factory_7c2a114d9432`
- Final Director request snapshots:
  - initial materialization: `c8c6532fdbd66cfb5585a8f6`
  - repair rounds: `c8f2c249087e41550d2888cb`, `0d176790b2217f207f47816b`
- Workspace validation: `.polaris/runtime/qa/workspace-validation.json`
- Generated workspace stayed read-only during diagnosis. Replays used copies in
  `/tmp/polaris-l324-r58-replay*` only.

The final request had the correct Director identity, PM contract, CE blueprint,
target files, current UTF-8 source, and scoped tools. The normalized initial
`write_file` invocation already contained malformed C++ text, including
`#include vector`, `#include utility`, and trailing closing-tag fragments. Raw
provider arguments were redacted/content-addressed, so evidence proves the
malformation existed before disk application but does not yet distinguish
provider output from pre-invocation normalization.

Dynamic repair evidence proved a separate convergence defect:

1. Round 1 deleted an include block. Physical effect succeeded, then candidate
   guard rolled it back.
2. Round 2 correctly replaced `#include vector` with `#include <vector>`.
   Before hash `701a4714…`, candidate hash `1d957648…`; receipt committed.
3. The correct edit removed the early preprocessor error but exposed later C++
   diagnostics beyond the verifier's per-translation-unit 1200-character
   excerpt.
4. Factory classified the candidate as non-progress and restored the baseline.
5. Three non-progress rounds exhausted the bounded same-Director repair loop.

## Dynamic replay

The exact verifier command was replayed on two copies:

- Correct candidate: include diagnostics `8 -> 4`, line 17 is
  `#include <vector>`, proof accepted.
- Deletion candidate: include diagnostics also `8 -> 4`, line 17 is no longer
  an include, proof rejected.

Full, untruncated compiler output showed the correct candidate reduced total
errors `410 -> 262`, while deletion expanded them `410 -> 1992`. The bounded
verifier excerpts reported misleading totals (`36 -> 46` correct, `36 -> 34`
deletion), proving raw diagnostic cardinality cannot authorize this repair.

## Generic invariant

A malformed C/C++ include barrier is repaired only when all conditions hold:

- the same verifier command strictly reduces
  `#include expects "FILENAME" or <FILENAME>` occurrences;
- before evidence identifies a simple bare header token and exact file/line;
- the resolved path remains inside the current workspace;
- current candidate disk state contains `#include <token>` at that exact line;
- missing, moved, deleted, ambiguous, non-UTF-8, or out-of-workspace evidence
  fails closed.

This permits a proven preprocessing phase advance while rejecting deletion that
merely hides a diagnostic.

## Implementation

- `factory_workspace_quality_impl.py`
  - adds C/C++ malformed-include diagnostic/source parsing;
  - adds `workspace_quality_cpp_include_barrier_repaired`;
  - passes current workspace into effect classification while candidate state
    still exists on disk.
- `factory_stage_executor/_mixin_02.py`
  - recognizes the verified barrier repair before generic regression/cardinality
    classification.
- characterization regression covers correct replacement, deletion, and
  out-of-workspace rejection.

## Verification

- RED: exact characterization returned `equal_count_swap`.
- GREEN: exact characterization returns `progress`.
- Full workspace-quality characterization: `105 passed`.
- Ruff: clean for all three touched files.
- Mypy: clean for both source files.
- Exact copied-workspace replay: correct candidate accepted; deletion rejected.

## Remaining live validation

Run fresh isolated L3-24 r59. Expected first-order effect: the correct include
repair remains committed and same-Director repair advances to remaining
malformed include/tag diagnostics instead of rolling back. Any new failure must
again be dynamically audited against that exact run before platform edits.
