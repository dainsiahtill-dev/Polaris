# Static scan source snapshot closure

POL-48; mode=implementation; writer=Director tools engineer; reviewer=Root.
Production scope: cross_artifact_interfaces.py, artifact_quality/_scan.py,
artifact_quality/_syntax.py, interface_ledger.py. Existing dirty is preserved.

## Evidence and assumptions

Exact POL-47 RAW SHA-256: eea756c71a05ab059847960134dc01c21bb786ba66fb941db9ef829d05d03b8d.
Exact attachment probe SHA-256: 548af6c9e41a48c81cd5471b6eff653e9a2ff152bdd8aa1b8398843530bb8147.
Before changes: aggregate probe exit 1 / one external read attempt; syntax
probe exit 1 / one each for Python, JSON, HTML. Guards intercept before open.
Only private fixtures mutate. No product runtime or provider is used.

KernelOne quality is a capability, not a separately declared Cell. Existing
public scanner and symbol snapshot contracts are retained. No graph state,
effects, descriptor or cross-Cell ownership change is proposed.

## Dependencies and data flow

interface_ledger imports cross_artifact_interfaces only inside validation.
cross_artifact_interfaces may import the existing reader without an import-time
cycle: interface_ledger's module initialization has no cross_artifact import.
Therefore the conditional source_snapshot.py move is unnecessary and is not
authorized by a demonstrated cycle. There will be no second reader.

```text
public pure_static scan
  workspace-bound WorkspaceReadSnapshot (existing no-follow reader)
    ledger text -> ledger snapshot -> declared contract
    source text -> file checks + Python/JSON/HTML static syntax
    same cached source text -> import/export SymbolIndexSnapshot
      direct + transitive namespace resolution -> cross-artifact validation
      same symbol snapshot -> declared-interface validation
    metadata-only final drift validation -> incomplete coverage
  evidence: retained diagnostics + explicit incomplete reasons + deferred gates
```

One safe source inventory is retained before per-file work; disappearance must
not silently erase an owner. Unknown reads and truncated source are explicitly
incomplete and unknown export surfaces propagate through reexports. Legacy
default APIs keep best-effort reads and normal syntax diagnostics. Static syntax
accepts the original full text, rather than reopening or parsing a truncated
prefix. Snapshot and reader injection enforce workspace binding.

## Pre-mortem and verification plan

Risks: a downstream helper reopens a path; disappearing inventory silently
looks complete; failed owners create spurious missing-symbol diagnoses;
drift loses original known blockers; shared ledger validation rereads sources;
legacy monkeypatches stop reaching new reader. Test all these independently.

Run exact aggregate and attachment RED/GREEN, original 19/55/extension/5 probes,
32+46+79 components, six-file consumer gate and both calibrations. New tests
cover replacement/rewrite/removal/ancestor link and reader failures paired with
known blockers, Unicode, multiple specifiers, scoped/transitive valid imports,
normal Python/JSON/HTML and cross-language diagnostics. Capture argv, exit,
source hashes, full logs and private-fixture mutations separately from scanner
effects. Ruff/format/scoped mypy and strict debt comparison remain mandatory.

Potential independent reads in _scan_javascript.py and _scan_python.py are
outside write scope. Confirm dynamically and report exact missing authorization;
do not replace their diagnostics or monkeypatch global filesystem APIs in product.

T05/Factory/native COMPLETED_VERIFIED/fresh Bench are not approved. Root owns
independent QA and actual namespace drain/release. Engineering reports are not
Run Ledger or platform delivery facts.

## Bounded verification result

Root aggregate and syntax exact replays now exit 0, with no external content
attempts and explicit snapshot drift/incomplete evidence. Original probes:
19/19, 55/55, extension 55/55. Confirmation remains 4 PASS/1 FAIL because old
`_load` injection does not reach the strict reader; both calls still retain
`expected_public_symbol`. No assertion or historical evidence was changed.

The new 38-case frontier plus original 157 components pass (195). Exact
six-file consumers pass (288); both legacy calibrations pass. Ruff, format and
scoped mypy pass. Strict current scope retains three inherited findings;
historical scopes retain four production and 45 test findings, normalized
diagnostics unchanged. An initial duplicate coverage reason and a kwargs mypy
failure were corrected and their logs retained.

Additional RED/GREEN proves that removing an owner after its safe read must
not change namespace resolution; controlled resolution now uses the retained
source inventory. Another RED/GREEN pairs an unreadable owner with a readable
known blocker and an explicit contract: unknown source coverage must not
invent contract-export absence. No parser/AST binding rules changed.

Four effect-trapped interleavings separately record fixture unlink/symlink
operations, before/after content hashes, zero scanner effects, zero processes,
and zero outside content attempts. Existing static probe effect witnesses are
retained; legacy calibration behavior is not labeled a pure-static effect gate.

## Remaining scope blocker

Dynamic `remaining_read_probe.py` confirms three reads intercepted before open:

- `_scan_python.py::_scan_python_acceptance_term_pair_evidence` reopens main.py.
- `_scan_python.py::_scan_python_acceptance_npm_node_alias_evidence` calls
  `_load_package_scripts`, reopening package.json.
- `_scan_javascript.py::_scan_javascript_named_export_evidence` calls
  `_workspace_javascript_named_exports`, reopening main.js.

The named helper entry is patched only to replace its private fixture source
with a sibling link, then calls the real implementation. No content escapes
the guard. These files are outside the approved writer scope. Existing safe
specialist diagnostics remain enabled; detected drift skips their independent
reopen and leaves explicit incomplete evidence. A mutation after that check
still demonstrates the reported specialist race; the check is not claimed as
a complete confinement boundary.

Root owns the next action: independently replay this RAW after actual drain
and release, then authorize one writer for snapshot injection in precisely
`artifact_quality/_scan_python.py` and `artifact_quality/_scan_javascript.py`
plus necessary `_scan.py` plumbing and frontier additions. No API mutation,
handoff upload, delegation or self approval was performed in this run.

Report: `.superpowers/sdd/2026-10-06-paperclip-repair-frontier/static-snapshot-closure-v1/REPORT.json`.
Full workspace tests, platform release gates, Playwright, real provider,
native delivery and fresh Bench were not executed in this bounded run.
