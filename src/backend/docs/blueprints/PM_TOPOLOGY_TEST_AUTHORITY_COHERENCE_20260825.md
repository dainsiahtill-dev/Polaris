# PM Topology Test Authority Coherence

Status: exact-run root cause proven; implementation pending TDD.

## Problem

Fresh isolated L3-23 run `factory_78b3af283513` failed before Director dispatch
with `chief_engineer.semantic_repair_authority_infeasible`. The immutable PM
contract requires `min_test_files=2`, but Rust deterministic synthesis declares
only `tests/product.rs` and gives every task the same Chief Engineer topology
delegation: `domain_modules` plus `entrypoint`. No task may therefore authorize
a second test path. The Chief Engineer candidate contains two tests, but its
second test is outside PM authority and cannot be frozen.

## Architecture

```text
PM deterministic synthesis
  -> exact task targets and scope
  -> per-task Chief Engineer topology delegation
     -> production task: domain_modules
     -> implementation task: domain_modules + entrypoint
     -> verification task: tests
  -> delivery-depth minimums
  -> Chief Engineer portfolio feasibility
  -> Director handoff
```

## Responsibilities

- `roles.adapters` PM synthesis owns deterministic PM task contracts.
- PM verification task owns test topology delegation when test paths beyond the
  exact declared target are needed to satisfy the delivery-depth contract.
- `chief_engineer.blueprint` remains fail-closed and must not widen PM authority.
- Generated Bench projects remain read-only evidence.

## Invariants

1. A deterministic PM contract cannot require more test files than its exact
   test targets plus delegated test topology can authorize.
2. Test topology is delegated only to the verification task, not globally.
3. Production and entrypoint ownership remains unchanged.
4. No CE feasibility gate is relaxed.
5. Every failure is revalidated through a fresh isolated exact run.

## Verification

- RED: Rust PM synthesis test proves verification task lacks `tests` delegation.
- GREEN: per-task topology metadata matches production, entrypoint, and tests.
- Focused PM synthesis suite, Ruff, Mypy.
- Fresh isolated L3-23 run proves the authority-infeasible signature is gone or
  exposes the next exact residual for dynamic debugging.

