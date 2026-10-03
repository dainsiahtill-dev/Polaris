# Build prefixes do not prove arbitrary entrypoint existence

Scope: existing KernelOne package-script quality checker and its tests. Pattern
repair under the unattended master plan; no target edits or new execution owner.

## Evidence and correction

Exact r03 TASK-1 boundary rejected tests/verify.test.mjs while CE declared
tests/verify.test.ts. At21:51:18Z a later stage repair created the mjs path.
The subsequent read-only scanner replay correctly sees that file. Earlier claims
of simultaneous scanner/boundary disagreement were premature: different epochs
were compared. Preserve this distinction in the audit record.

Separately the production checker has an unconditional exemption whenever a build
command occurs before an interpreter. This can suppress a missing source/test
entrypoint even outside known build-output roots. Reproduce on a test-owned
temporary workspace with the same command shape, not by deleting a real target.

## Existing-owner design and implementation

PackageScriptsCheckResult and typed ArtifactQualityIssue stay unchanged. Reuse
the existing build-output classification and lifecycle logic. A compile/build
prefix may defer validation of a known build output (dist/build/etc.), but must
not excuse absent source/test paths. No command execution or environment install
is introduced into this static checker. Real verifier receipts still determine
whether output was actually built and passed.

1. RED direct package checker: build-prefix + missing source/test rejects with
   typed npm_script_missing_local_entrypoint and original command/path preserved.
2. RED artifact evidence consumer: same missing target retained, not swallowed.
3. Restrict the exemption; preserve existing dist and separate-build tests.
4. Run related quality and Director scope suites; Ruff/Mypy; independent review.
5. Continue exact-run stage authority/repair-owner tracing. This detector change
   alone is not fresh project completion or the full recovery fix.

## Pre-mortem and invariants

- Do not claim that all generated assets must already exist before a build.
- Do not infer unknown build destinations as authorized files.
- Do not delete/alter the real generated target to reproduce a historical epoch.
- Do not mint CE identity or write scope from proposed repair paths.
- Keep temporal scan evidence separate from canonical same-epoch verification.
