# Cross-Artifact Interface Contract Plane

Status: active architecture
Last updated: 2026-06-28

## Purpose

The cross-artifact interface contract plane prevents multi-file agents from
solving symbol drift with guessed imports or empty stubs. It is not an import
repair heuristic. It is a shared contract layer across Chief Engineer,
Director, artifact quality gates, repair planning, and task-market requeue
semantics.

## Authority Model

Validated PM contracts still own task scope, target files, and acceptance
criteria. Chief Engineer owns interface intent inside that authorized scope.
Director owns implementation changes, but only inside the execution envelope and
inside the interface contract it receives.

The authority split is:

- Chief Engineer declares provider and consumer intent through
  `public_symbols` and `consumes_symbols`.
- `interface_ledger` persists CE-declared public code symbols and legacy
  interface identifiers across fissioned tasks.
- `cross_artifact_interfaces` scans the workspace and records physical import,
  export, re-export, and signature facts.
- Artifact quality compares declared intent with actual code facts.
- Director repair may repair only plans with
  `authority=director_repair_within_contract`.
- Contract gaps produce `cross_artifact.contract_amendment_request.v1` and are
  requeued to Chief Engineer through `pending_design`.

## Invariants

1. Consumers must not import symbols that the provider step did not declare in
   `public_symbols`.
2. `consumes_symbols` must reference a provider target and exact symbol names
   declared by that provider.
3. `interface_names` may contain UI ids, asset keys, selectors, or other domain
   identifiers. They are not automatically code exports.
4. `public_symbols` are the code-facing interface contract. Gate and repair
   evidence must prefer them over legacy `interface_names`.
5. Empty placeholders such as `class X: pass`, TODO, NotImplemented, or
   placeholder-only exports are not valid contract repairs.
6. If a missing symbol has no declared interface and no safe existing export,
   Director must not invent a new public API. The task returns to CE for
   amendment.
7. If CE declared the symbol and the owner file does not export it, Director may
   repair the implementation while preserving the contract.
8. If the declared signature is wrong for the desired design, Director must
   request a contract amendment instead of silently mutating CE intent.

## State Flow

```text
PM_CONTRACTED
  -> CE_BLUEPRINT_FISSION
  -> CE_DECLARED_INTERFACES_RECORDED
  -> DIRECTOR_EXECUTING
  -> ARTIFACT_QUALITY_EVIDENCE
  -> one of:
       DIRECTOR_REPAIR_WITHIN_CONTRACT -> pending_exec
       CE_CONTRACT_AMENDMENT_REQUIRED  -> pending_design
       QA_PENDING
```

## Runtime Evidence Objects

`SymbolIndexSnapshot`

- Built by `build_symbol_index_snapshot`.
- Records physical exports, namespace exports, imports, unknown export paths,
  and signature digests for supported languages.
- Currently covers Python, TypeScript, JavaScript, and Go with conservative
  fail-open behavior for ambiguous dynamic exports.

`ArtifactQualityEvidence`

- Built by `scan_workspace_artifact_quality_evidence`.
- Preserves the legacy string error list while also exposing structured
  `cross_artifact_issues`, `cross_artifact_repair_plans`, and optional
  `contract_amendment_request`.

`CrossArtifactRepairPlan`

- `rename_consumer_to_existing_interface`: safe consumer rename to an existing
  export.
- `add_real_interface_to_owner`: CE declared the export, owner implementation is
  missing it, Director may implement it.
- `align_owner_signature_to_contract`: owner implementation signature must align
  to CE-declared signature.
- `contract_amendment_required`: the contract is missing or ambiguous; return
  to CE.

## Critical Files

- `src/backend/polaris/cells/chief_engineer/blueprint/internal/step_contract.py`
  normalizes and validates `public_symbols` and `consumes_symbols`.
- `src/backend/polaris/cells/chief_engineer/blueprint/internal/ce_consumer.py`
  records declared interfaces before publishing Director work.
- `src/backend/polaris/kernelone/quality/interface_ledger.py` persists
  CE-declared interface facts.
- `src/backend/polaris/kernelone/quality/cross_artifact_interfaces.py` builds
  code-fact snapshots and typed repair/amendment plans.
- `src/backend/polaris/kernelone/quality/artifact_quality.py` exposes legacy
  errors and structured artifact-quality evidence.
- `src/backend/polaris/cells/director/runtime/internal/repair_kernel/diagnostics.py`
  normalizes cross-artifact unresolved symbols into typed repair diagnostics.
- `src/backend/polaris/cells/director/runtime/internal/repair_kernel/registry.py`
  routes cross-artifact diagnostics to language-specific repair rules.
- `src/backend/polaris/cells/director/task_consumer/internal/director_consumer.py`
  routes within-contract repair back to `pending_exec` and contract amendments
  back to `pending_design`.

## Negative Test Matrix

| Case | Expected Result |
| --- | --- |
| Consumer imports `WeatherReprot`, provider exports `WeatherReport` | Director repair plan renames consumer to existing export |
| Consumer imports `WeatherReport`, no provider export, no CE declaration | `contract_amendment_required`, requeue to CE |
| CE declares `WeatherReport`, owner exports `WeatherSnapshot` | `director_repair_within_contract`, requeue to Director with repair plan |
| CE declares selector `#game` in `interface_names` for JS file | Not treated as a code export contract |
| CE declares `public_symbols=["WeatherReport"]` | Treated as code interface contract |
| Repair would create `class WeatherReport: pass` | Rejected; placeholder stubs are not valid |

## Extension Rules

New language support must extend the scanner first, then the gate, then repair.
Do not add a language-specific import fix that bypasses this plane.

For future game, app, plugin, and multi-service projects:

- Use domain identifiers in `interface_names`.
- Use exported code/API names in `public_symbols`.
- Use explicit provider-target maps in `consumes_symbols`.
- Add scanner support only where static evidence is reliable.
- Fail open for dynamic constructs that cannot be proven.
- Fail closed for execution authority: Director must never expand scope or
  mutate the CE contract itself.
