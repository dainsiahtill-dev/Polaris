# Runtime base-file alias closure

Issue: POL-38. Mode: implementation. Owner: repair-kernel language engineer.
Acceptance: Root independent review, then POL-37 joint QA; no self approval.

## Scope and evidence

Only production file: `src/backend/polaris/cells/director/runtime/internal/repair_kernel/typescript_runtime.py`.
Only migrated generic Plan/Run entrypoints and minimum private helpers change.
Specialized planners, shared path acceptance, DTOs, registry, AST and existing
frontier assertions remain unchanged. All pre-existing dirty work is preserved.

Public commands and runtime dispatcher pass raw `base_files` unchanged. Both
migrated entrypoints currently normalize with a last-wins dictionary before
the syntax builder can prove ownership. Run loses aliases even before Plan.
POL-19's two retained RED tests expose that loss in both insertion orders.

## Architecture and data flow

```text
public Plan / coverage-backed plan probe / public Run
    runtime binding dispatch (raw base_files)
    migrated TypeScript entrypoint: read-only conflict discovery
        differing content at same declared normalized path: no plan, no effects
        equivalent aliases: existing normalization and syntax builder
    existing Composer / Policy / Executor / Receipt / Revalidate
```

The private discovery helper reuses shared permissive path normalization only
for comparison, including declared Cargo manifest basename canonicalization.
It does not widen acceptance or change any normalization helper. TypeScript
and arbitrary filename case remains significant. Only exact string content
equality permits aliases; semantic JSON equality is insufficient.

Conflicting aliases are diagnosed before any last-wins normalization. Original
diagnostics remain attached to rejection. Run must pass the raw mapping to Plan
and return before Policy/Executor/revalidation. Public diagnostic-only coverage
has no base-file input: it still recognizes the diagnostic family; coverage-backed
plan probes must report unplannable rather than claim safe effect plans.

## Assumptions and pre-mortem

- Raw aliases survive public dispatch: source inspected; trace in private reproduction.
- Declared path equivalence comes from existing `path_files` APIs, not OS case rules.
- Do not infer ownership from last insertion, remove an alias, or guess a root config.
- Public Plan currently projects caller diagnostics and generic refusal; retain that
  contract rather than changing DTO/adapters outside authorization.
- Same-content aliases must preserve before/after hashes and stale-content rejection.
- Unit effect receipts prove component behavior only, not production settlement.

## Verification

First run original two RED selectors and new public raw/typed Plan/Run negatives.
Then run new frontier, full TS5110 frontier including four real private compiler
controls, contracts 1-7, existing identity/AST/import/composition frontiers,
path-files and specialized TypeScript planner controls. Record exact selectors,
exit codes and versioned UTF-8 logs. Run scoped Ruff checks/format check and Mypy
using existing Python 3.13 venv without cache or bytecode. No installs, services,
real targets, Bench or provider calls.

Complexity: linear in input paths and content hashing; linear auxiliary storage.
No new Cell, public facade, state owner, scheduler step or effect capability.
