# L3-24 r46 Compiler Diagnostic Multiplicity Blueprint

## Exact-run problem

Fresh isolated run `factory_30c20410aeac` reached Director quality repair.
Two forced `edit_file` turns changed `src/invisible_ink.cpp` from hash prefix
`2e9be` to `371499`, replacing one unqualified `Mode` occurrence.  The post-edit
compiler frontier moved from line 95 to line 101, proving a real causal effect.
Factory nevertheless classified both candidates as
`workspace_quality_repair_equal_count_swap` and restored the original bytes.

Dynamic evidence in `.polaris/runtime/workspace-validation.json` and
TaskRuntime shows `status=restored`, the exact before/after hashes, and the
same rollback reason.  This excludes LLM context, tool normalization, no-op,
authorization, and missing-effect explanations.

## Root cause

`factory_workspace_quality_impl.py` canonicalizes compiler diagnostics as a
set of `path|message` identities.  Line numbers are correctly excluded for
stability, but set semantics also discard occurrence multiplicity.  Removing
one of several same-path/same-message occurrences therefore appears unchanged,
even though the compiler frontier strictly shrank.

## Invariants

1. Diagnostic identity remains stable across line movement: normalized path
   plus normalized compiler message, excluding line and column.
2. Multiplicity remains authoritative: each occurrence contributes one count.
3. Progress requires a component-wise non-increase and a strict total decrease.
4. A new identity, or an increased count for any identity, remains fail-closed.
5. Tool policy, candidate transaction, CAS rollback, TaskRuntime settlement,
   and target-project bytes are unchanged.
6. Generated Bench project files are evidence only and must never be edited.

## Data flow

`verifier result -> compiler line parser -> Counter[path|message] ->
same-command multiset comparison -> candidate effect classification ->
retain or rollback -> revalidate`

## Pre-mortem

- If location is included in identity, harmless line movement looks like a
  swap.  Keep location excluded.
- If only total count is compared, replacing one error with another can look
  better.  Require component-wise non-increase.
- If output is opaque or truncated, no progress is provable.  Keep fail-closed.
- If the target project is patched, the platform defect is hidden.  Forbid it.

## Acceptance

- Regression reproduces r46: identical top-level gate count, repeated C++
  diagnostic multiplicity decreases, expected classification `progress`.
- Existing compiler-swap and regression tests remain red-safe.
- Focused pytest, Ruff, Mypy, and exact r46 evidence replay pass.
- A subsequent fresh isolated L3-24 run retains the causal edit instead of
  restoring it.  Project completion still requires authoritative
  `ProjectOutcome`, successful `quality_gate`, and no failed command receipt.

## Local closure evidence

- TDD RED: expected `progress`, observed `equal_count_swap`.
- TDD GREEN: repeated-occurrence reduction, strict subset, and swap rejection
  tests all pass.
- Regression: 99 workspace-quality checks and 38 candidate-guard checks pass.
- Static gates: Ruff and Mypy pass on the changed implementation/test surface.
- Exact r46 replay: compiler occurrences `15 -> 13`, no identity count
  increased, and the new frontier classifier returns `true` where the recorded
  run returned `equal_count_swap`.
