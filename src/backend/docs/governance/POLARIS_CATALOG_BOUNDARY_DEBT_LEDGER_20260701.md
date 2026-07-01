# Polaris Catalog Boundary Debt Ledger

Status: Active
Owner: Polaris backend governance
Created: 2026-07-01
Scope: Cell catalog schema, cross-Cell public boundaries, declared dependencies, and declared side effects.

## Purpose

This ledger tracks the remaining baseline issues reported by
`run_catalog_governance_gate.py --workspace src/backend --mode audit-only`.
It is separate from the legacy/shim convergence ledger: these items are not
old compatibility surfaces, but Cell boundary and manifest contract debt.

The target chain is:

`Cell manifest -> public import boundary -> declared dependency/effect -> catalog gate -> release gate`

## Baseline

After H-37 closed the `runtime_projection` schema blocker, the catalog gate
reported:

| Metric | Count | Notes |
| --- | ---: | --- |
| Total baseline issues | 41 | Existing baseline, `new_issue_count=0`. |
| Blockers | 15 | Cross-Cell internal imports or public-boundary violations. |
| High | 26 | Missing `depends_on` declarations or undeclared side effects. |

## Ledger

| ID | Severity | Gap | Resolution | Status | Verification |
| --- | --- | --- | --- | --- | --- |
| CB-01 | P0 | `roles.adapters` tests imported `director.runtime.internal.repair_kernel` helpers and Rust source-tool constants directly, keeping a cross-Cell internal dependency in test coverage. | `director.runtime.public.repair_kernel_contracts` now exposes only stable read-only constants and pure helper wrappers; `roles.adapters` tests consume that public surface instead of importing repair-kernel internals. | Closed | `ruff`, `py_compile`, `mypy`, targeted pytest slices, and catalog audit-only gate pass; catalog baseline is now 36 issues with 10 blockers and 26 high issues, and `roles.adapters imports director.runtime internal module` is 0. |
| CB-02 | P0 | `roles.adapters.internal.director.adapter` imported `director.tasking.internal` execution profile, execution strategy, and language guidance helpers directly; the public catalog also lacked the declared `roles.adapters -> director.tasking` dependency. | `director.tasking.public.execution_guidance` now owns the stable profile, strategy, override, and language-guidance boundary. The Director adapter consumes that public surface, and both `cell.yaml` and catalog SSoT declare the public module/dependency. | Closed | `ruff`, `py_compile`, `mypy`, targeted adapter/language-guidance pytest slices, and catalog audit-only gate pass; catalog baseline is now 32 issues with 7 blockers and 25 high issues, and both `roles.adapters imports director.tasking internal module` and missing `roles.adapters -> director.tasking` dependency are 0. |
| CB-03 | P0 | `director.execution.public.service` imported `director.tasking.internal` profile and strategy helpers while constructing missing execution-envelope metadata. | `director.execution.public.service` now consumes the `director.tasking.public.execution_guidance` contract introduced by CB-02, preserving execution-envelope behavior without crossing into tasking internals. | Closed | `ruff`, `py_compile`, `mypy`, `director.execution` contract tests, tasking language-guidance tests, and catalog audit-only gate pass; catalog baseline is now 30 issues with 5 blockers and 25 high issues, and `director.execution imports director.tasking internal module` is 0. |
