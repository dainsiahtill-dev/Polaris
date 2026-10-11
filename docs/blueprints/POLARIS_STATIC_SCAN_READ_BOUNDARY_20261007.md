# Pure-static read boundary closure

POL-47; mode=implementation; owner=Director tools engineer; review owner=Root.
Scope is exactly four production files, one new frontier test, this blueprint,
the verification card, and the authorized closure report directory. Existing
dirty work belongs to its original owners. No runtime or delivery verdict is
created by this engineering work.

## Evidence and assumptions

The POL-46 RAW SHA-256 is
`606021f2dc37213e4105f756aa2a2c1f37c183b43c7ffa6e330802fff6c834eb`.
Exact restored probes reproduce 19/19, 45/55, and 0/5 before edits; effects=0.
Strict baseline debt is four no-any-return errors and 45 legacy test annotation
errors. These are recorded separately from new defects.

1. Whitespace splitting is not shell command-position discovery: adjacent
   operators merge executable names and ordinary arguments mimic invocations.
   Use bounded lexical tokenization, command positions, and explicit unknown
   reasons. Unsupported shell expansion/control syntax never implies complete.
2. Ledger status and legacy loads are independent reads. The strict read-only
   query must return both validated payload and status from one actual read;
   pure-static contract construction and ledger validation reuse that snapshot.
3. Path resolution after following links is too late. Reject lexical ancestry
   links and workspace escape before content/target metadata reads. Descriptor
   relative opens with no-follow protect the actual read; compare path/fd
   identity and modification witnesses before/after consuming UTF-8 content.
4. TS sibling existence, export reads, and recursive relative references need
   the same controlled reader. Missing/denied/invalid/dynamic export surfaces
   remain incomplete. Normal internal imports retain existing diagnostics.

## Data flow and responsibilities

```text
public pure_static scan
  bounded shell lexer -> command-position Node forms -> required parser intents
  confined workspace reader -> validated ledger snapshot + failure witness
    -> existing declared-interface readers with explicit snapshot
    -> existing declared-interface validator with explicit snapshot
  TS import discovery -> confined candidates -> same per-scan content snapshot
    -> recursive import/export read coverage -> known symbols or unknown reason
  ArtifactQualityEvidence -> diagnostics, incomplete reasons, required intents
```

KernelOne `kernelone.quality` owns this capability, per the current capability
catalog; there is no quality Cell descriptor or independent state owner.
Legacy default readers/writers/storage resolution retain their contracts.
Pure-static never initializes storage or discovers workspace-external caches.

## Pre-mortem and verification

Likely errors: resolving away `..` before checking a linked ancestor, treating
broken links as optional ENOENT, losing empty eval, rereading after status,
silently swallowing unknown export surfaces, or overclaiming tests as delivery.
Run exact original 19/55/confirmation-extension/5 sources without editing them;
old `_load` fault injection may cease to reach the migrated strict reader and
must remain reported as such. Add real strict-reader faults with RED/GREEN and
retain expected_public_symbol control diagnostics.

Run frontier plus original 32/46, exact six-file 288 consumers, two legacy
calibrations, no-effect traps/hash checks, Ruff/format/scoped mypy, and strict
debt deltas. Source, command, exit, interpreter/version, and complete outputs
belong in RAW evidence. No Provider, Bench, service or Paperclip write API.
Independent QA, release, and fresh isolated L1-L12 delivery remain Root-owned.

## Verified bounded result and scope blocker

Final exact probes: original 19/19, first negative 55/55, extension 55/55,
confirmation 4/5. The remaining confirmation assertion injects legacy `_load`;
the strict snapshot no longer calls it. Its original source and failing result
are retained. Both control calls still report expected_public_symbol. Actual
strict-reader disappearance/permission/I/O/UTF-8/drift cases remain unknown;
the new frontier covers those without modifying old probes.

Original 32 plus original 46 plus new 79 components: 157 passed. The exact
six-file consumer gate and two legacy calibration selectors are recorded in
the closure report. Scoped Ruff, formatting and mypy pass; original strict
4/45 findings remain separate debt, with unchanged normalized diagnostics.
Stable external links cause explicit refusal, not artifact validation success.

There is a dynamically proven remaining aggregate race outside production
authorization: `scan_cross_artifact_consistency` constructs another
`build_symbol_index_snapshot`, which calls ordinary Path.read_text after
preflight and controlled TS reads. A private fixture replaces leaf.ts with an
external symlink at that boundary; the external-content guard records the
attempt and its full stack. Initial symlink preflight cannot secure a later
independent read. No actual external bytes are consumed by the guarded probe.

Root must explicitly authorize a unique writer for
`src/backend/polaris/kernelone/quality/cross_artifact_interfaces.py`, specifically
controlled reader/snapshot injection in `build_symbol_index_snapshot` and
`scan_cross_artifact_consistency`, preserving legacy defaults and existing
diagnostics. The assigned scanner and ledger validator can then pass their
snapshot through that public capability. Other aggregate and syntax helpers
also retain independent source reads and require follow-up audit before any
claim of a complete transactional source snapshot. No production monkeypatch,
parser duplication, cache writing, or check bypass is used to evade that scope.

Thus the assigned stable-path defects have component evidence, but POL-47/T05
closure remains blocked on Root scope authorization; independent acceptance,
release and fresh L1-L12 delivery remain unperformed.
