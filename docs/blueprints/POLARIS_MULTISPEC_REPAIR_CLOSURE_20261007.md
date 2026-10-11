# Multi-specifier diagnostic and composition closure

Status: bounded follow-up approved by the user's standing direct-execution instruction; implementation pending. Paperclip employees implement, Root independently accepts. Preserve accepted AST scope/statement facts and all unrelated dirty assets. No Paperclip, target project, Composer, policy, receipt, registry or public DTO mutation.

## Actual remaining evidence

POL-23's independent public calls and private TS5.9.3 controls confirm two pre-existing failures, also reproduced with the verified POL-22 preimage:

1. `_repair_diagnostics_from_quality_inputs` merges canonical findings with different `metadata.specifier`. `_repair_diagnostic_structural_key` omits that field; a raw-only key can also match without checking canonical identity. The typed probe loses one path and produces a partial proposal; exact compilation still fails TS2307.
2. `_build_relative_import_plan` emits operations against successive `updated` versions of one importer, while Composer correctly expects the original `base_files` hash. Interleaved a/b/a/b imports produce `before_hash_mismatch`. Do not weaken hash, overlap, policy or current-byte validation.

Root read the actual active functions with CodeGraph. These are upstream identity and downstream same-file aggregation defects, not a reason to reimplement the independently proved AST producer or restart PM/CE.

## Narrow design

### Canonical identity

Own the fix in `director.runtime/public/service/_projections.py`. Retain legacy diagnostic refinement and typed current canonical evidence, but merge only findings whose canonical identity is compatible. Distinct case-sensitive canonical import specifiers remain distinct through normalization, coverage and materialization probe. Raw message equality cannot override an explicit conflicting specifier/path. Missing import identity is conservatively unproved; do not fabricate it from stale raw text.

Reuse existing diagnostic identity contracts. Preserve explicit IDs and unrelated compiler/non-import behavior; no global change to string normalization or path safety. Same canonical finding with duplicate raw/typed evidence still coalesces. Exact public metadata/coverage tests must demonstrate both distinct identities and legitimate deduplication.

Allowed product paths: existing `_projections.py`; new `tests/test_quality_input_identity_frontier.py`. A dedicated verification card may be added at `src/backend/docs/governance/templates/verification-cards/vc-20261007-quality-input-identity.yaml`.

### Original-base same-file composition

Own the fix in the existing `_build_relative_import_plan` in `typescript_syntax/imports_exports.py`. Candidate proof may simulate updates in private memory, but the emitted operation set must bind each importer to its immutable original source. Coalesce the complete proven net rewrite for one importer into one original-base operation; preserve other text, exact hashes, repair mappings and diagnostics. Coalescing is preferred to several independently overlapping first-to-last spans for interleaved specifiers.

Reuse existing operation/composer APIs. Cases, unused imports and unique-export modes share this builder: preserve their intended behavior and prove the relevant callers. No new write path, source tool, whole-project regeneration, full-file formatting or AST producer modification.

Allowed product paths: existing `imports_exports.py`; new `tests/test_multi_specifier_composition_frontier.py`. A dedicated verification card may be added at `src/backend/docs/governance/templates/verification-cards/vc-20261007-multi-specifier-composition.yaml`.

## Required dynamic tests

- Same canonical importer and message, two explicit a/b specifiers: two diagnostics survive raw-only, typed-only and mixed routes. Include reversed order, stale raw path, repeated/same raw text, absent identity and true duplicates; input DTO/hash unchanged.
- One importer with a/b/a/b imports and emoji prefix: public composition succeeds, original-base hash only, no intermediate hash, both real paths fully corrected. Reverse diagnostic order yields the same net source result.
- Two importers, duplicate evidence and same-specifier multiple statements retain current behavior. Unrelated imports, comments/templates and Unicode remain byte-for-byte unchanged.
- Ambiguous/unproved/stale candidates do not acquire authority. A partial plan is not proof of all diagnostics resolved; inspect exact proposal and residual/coverage metadata, not only `planned`.
- Composer still rejects deliberately stale hashes and overlapping inconsistent edits. No direct writer/Run grant or receipt is fabricated by a public planning test.
- Actual private compiler verifies the exact final raw and typed proposals for the POL-23 fixture, with isolated cwd/type roots and unchanged source/config hashes. Do not compile a manually completed reference and call it public output.

## Execution and acceptance

Serialize the two employees in the existing main workspace: canonical identity first, then original-base composition. Root checks each scope/diff/dynamic evidence after physical release; a distinct QA reviews the combined public chain. Exact selected regression includes all contract1–7, existing frontiers/AST/C++ and both new frontiers, with counts, exits, debt and unrun wider gates reported separately. Preserve original assertions and immutable, versioned evidence artifacts; do not overwrite an earlier probe output.

Pre-mortem: adding the field only to the structural key while the raw-key shortcut still collapses findings; treating unknown identity as equality; widening canonical paths from raw strings; grouping operations but keeping intermediate hashes; emitting overlapping spans; accidentally changing shared case/unused behavior; accepting `planned=true` with failed composition; mistaking private compiler or empty query receipts for delivery.

Completion here is only this two-defect planning closure. TS5110, controlled verifier/effect/settlement qualification, global/release gates and fresh isolated Bench remain separate. No new Provider call is needed to reproduce these failures; final-provider evidence is not applicable to the component-only task.
