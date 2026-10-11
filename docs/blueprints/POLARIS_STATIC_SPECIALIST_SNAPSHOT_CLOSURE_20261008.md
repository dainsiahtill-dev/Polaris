# Static specialist snapshot closure

POL-49; mode=implementation; single writer=Director tools engineer; reviewer=Root.
Budget history: original run a027753a-eb4a-4c92-973b-58b1aabaccf4 actually
timed out at 600 seconds despite the planned 2400 cap. Board reports verified
drain/released/matching receipt. One explicitly authorized continuation uses
2400 seconds; original history is retained. Existing dirty remains.

## Scope and evidence

Only production writes: artifact_quality/_scan_python.py, _scan_javascript.py,
_scan.py; revision 2 additionally authorizes only WorkspaceReadSnapshot and its
direct controlled opener in interface_ledger.py. One new frontier test, this blueprint, one verification card, and
the assigned static-specialist-snapshot-closure-v1 report directory are writable.
Other ledger business, AST/import aggregation, runtime repair, targets,
configuration, providers, services and Paperclip APIs remain read-only.

Root input attachments were retrieved using injected credentials and verified:
POL48 RAW 0efe9513489ca7eeb5d382f67f4b00d55804b2bc3d40c69391205a6e22ebdaad;
Root replay 8924280c323e244499e82b0c5d2a57c67d87b98edf6083e27cd7db610de31ccc.
Exact remaining_read_probe.py reproduced three intercepted external opens.
Long private fixture reproduced old TS1109 assertion failure: absolute path
occupied the first 200 characters. Initial fixture-parent setup error is also retained.

## Ownership, reuse and actual data flow

KernelOne quality is a technical capability, not a separately declared Cell;
no cell.yaml/README.agent.md exists for this directory. Graph catalog and
director_pipeline plus FINAL_SPEC confirm KernelOne foundation/public boundaries.
No cross-Cell contract, state owner, runtime effect or graph mutation is needed.
The existing WorkspaceReadSnapshot remains the sole UTF-8 source reader and
cache. Independent reviewer probe hash
6111643c441acbf756e0ec2ed2cdd4785ad2b9b6d1d6a54a4283f0053ebd1453
proved that its absolute root open followed a replaced parent before applying
O_NOFOLLOW to the final root. Post-read drift reporting did not prevent one
actual peer content read. Four invalid package cases were falsely complete.
Final-root symlink control had zero actual peer reads even before this repair.

Root/ancestor identities are now captured through descriptor-relative opens
from the filesystem anchor. Each actual source opener repeats that no-follow
walk and validates device/inode/mode; nested directory witnesses are retained
at first preflight and compared at actual open. Lexical metadata checks reject
pre-read drift and distinguish workspace disappearance from optional file absence.
After opening the source descriptor, confinement and leaf identity are checked
again before returning a stream. Cached readable blockers remain usable while
final drift marks coverage incomplete. No persistent descriptors, second reader,
cross-Cell imports or new public contract are introduced. All owned descriptors
close on normal, filesystem, decode and interrupt exits.

Malformed JSON, non-object package, non-object scripts and non-string scripts
produce specialist incomplete reasons when a reader is supplied. Missing optional
package, absent scripts and valid empty scripts remain complete. Valid script
entries and known blockers remain available even alongside malformed entries.
No-reader consumers retain established best-effort filtering semantics.

```text
public pure_static scan
  safe original inventory + one workspace-bound WorkspaceReadSnapshot
  file diagnostics / static syntax / original symbol index
  same reader + inventory
    Python term-pair text; package scripts; npm alias test text
    JavaScript workspace exports; importer; named/namespace exporter
    catalog completeness (unknown sibling cannot establish absence)
  specialist incomplete reasons + existing errors + final drift witnesses
  bounded syntax summary: replace known absolute source/root paths before clipping
```

interface_ledger imports cross_artifact_interfaces only inside functions;
specialists importing WorkspaceReadSnapshot create no import-time cycle.
_scan depends on specialists, so specialists must not import _scan to traverse.
The orchestrator supplies its existing source inventory. Module resolution uses
that retained inventory for witnessed files; absent candidates are confined and
unknown candidates cannot manufacture missing-module diagnostics. Catalog absence
is diagnostic only when the entire relevant export surface was readable.
Readable named/namespace blockers remain even with unrelated unknown siblings.
No-reader legacy paths retain their existing best-effort behavior.

## Assumptions and pre-mortem

- Metadata preflight cannot authorize later uncontrolled reads: exact RED proves it.
- Cached data survives rewrite/removal/link replacement: retain known blockers,
  but mark final coverage incomplete on drift.
- Missing optional package.json differs from unreadable/disappearing manifest.
- A final-component no-follow flag does not bind absolute ancestors.
- Leaf identity alone is insufficient: a replacement directory can contain a
  hard link to the same file. Actual directory identity must also match.
- Directory timestamps change legitimately; bind device/inode/mode instead.
- Same-size immediate rewrites can have identical metadata on this managed
  filesystem. Preserve the observed failed metadata-drift assertion; revised
  size-changing fixture tests observable drift without pretending stronger detection.
- Exception cleanup must include KeyboardInterrupt; it must propagate after closure.
- Unknown exporters cannot prove missing named/namespace/catalog symbols.
- Explicit entries remain scanned paths; sibling reads do not expand scan scope.
- Absolute-path clipping loses actual diagnostics: normalize known paths first,
  prioritize diagnostic content when even relative paths are long, retain 200 bound.

Highest risks: transitive helper reopen, live resolution erasing retained owners,
false catalog absence, duplicate coverage reasons, and legacy assertion regressions.

## Verification

First exact helper and long calibration RED, then new frontier RED. Verify
importer/exporter/package/namespace/catalog read failures, replacement/removal/
ancestor links/rewrite, Unicode, valid internal imports and known blockers paired
with unknowns. Replay original 19/55/extension/confirmation verbatim (retain old
_load injection failure), 195 components, 288 consumers, long/short old calibrations,
aggregate/syntax/four-effect/three-helper probes. Record argv/exit, source hashes,
fixture mutations and scanner effects separately. Ruff/format/scoped mypy and
normalized strict baseline debt. No full pytest, provider, Bench or service work.

Root independently validates after natural release. Component evidence does not
establish T05/native delivery/fresh Bench/COMPLETED_VERIFIED.

## Continuation checkpoint

Original 57 frontier and both long/short old calibrations passed before new edits.
Exact new reviewer RED: four empty-complete manifests and one actual ancestor
peer read. New RED with identical-inode hardlink controls: 15 FAIL / 15 PASS;
first RED, same-metadata rewrite failure, and first GREEN's incorrect newly added
legacy assertion are retained. Intermediate corrected frontier: 87 PASS. The
original 195-component refusal-identity assertion then exposed a duplicate
specialist reason: two Python/JavaScript cases failed before common source-refusal
projection deduplication. Full error/issue detail remains, and new package/sibling
unknown reasons are retained. Original component tests are unchanged.

Final directory matrix also covers nested ancestors before preflight. A previously
witnessed nested directory deletion initially returned missing (8 PASS / 1 FAIL).
It now returns unknown with directory_identity_drift before any content read.
The final frontier contains 92 cases (original 57 plus 35 continuation cases).
Full evidence and
subsequent regression results are in the assigned RAW_EVIDENCE.v2.json and report.
Historical v1 inputs/reports/tests/probes remain unchanged. Root owns independent
review after real release; this employee does not certify release or delivery.

Original confirmation dependencies were restored byte-for-byte from the input
RAW archive, including extension harness bytes under negative_probe.py. The
first misbound run remains archived (three unexpected read_exception TypeErrors).
The correct original confirmation remains 4 PASS / 1 FAIL: its legacy _load
injection is unused by the controlled snapshot path, so it no longer simulates
disappearance. The real readable expected_public_symbol blocker remains present;
scan_complete describes read coverage, not a quality success verdict. Neither
the old probe nor product API gained fake flags to force a pass.
