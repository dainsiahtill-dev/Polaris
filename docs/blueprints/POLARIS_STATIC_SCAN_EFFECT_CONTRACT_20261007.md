# Public static artifact scan effect contract

POL-40, stage B1; owner: external Director tools engineer. Classification: pattern.

## Frozen API and data flow

`scan_workspace_artifact_quality_evidence(..., scan_mode="pure_static")`
is the only new static entry. Default `scan_mode="legacy"` retains existing execution.
The errors-only `scan_workspace_artifact_quality` accepts no mode argument.
Unknown mode raises ValueError before scanning. No environment switch selects static mode.

```text
public evidence API
  -> file reads + in-memory syntax/static diagnostics
  -> per-file/package deferred requirements
  -> aggregate diagnostics + coverage + project requirements
  -> ArtifactQualityEvidence.to_dict()
```

`ArtifactQualityDeferredCheck` describes check_id, workspace-relative paths,
reason, optional script_name and project_relative_path (for grouped project
gates). Every intent is required and deferred; it is
neither executable authority nor a Job Token, command, receipt, or pass verdict.
`scan_complete` describes static traversal only. `incomplete_reasons` records
unreadable/unknown source, truncated reads, exceptions, or diagnostic limit.
`checks_complete` requires pure_static mode, complete traversal and zero deferred
checks. Legacy coverage remains uncertified, with checks_complete=false. Neither
this field nor empty errors proves project delivery or compiler success.

## Responsibilities and effects

KernelOne quality remains the public technical capability (capability catalog
entry kernelone.quality); kernelone.core Cell owns context/workflow, not quality.
No new Cell or cross-Cell internal dependency is introduced. Models carry the
contract; _syntax performs in-memory Python compilation, JSON and HTML checks;
_scan_package keeps manifest/shlex checks and defers node-eval parsing; _scan
aggregates and defers JS/TS parsing and TS/Go project checks; _issues preserves
coverage during final projection.

Pure static mode performs no subprocess, tool execution, installation, network,
bytecode generation, or file mutation. JS/TS parser checks remain distinct from
project TypeScript typecheck. Static import checks do not satisfy either gate.
Go compile and test requirements remain deferred. Unsupported source languages
produce incomplete coverage. Early termination preserves discovered requirements.
Pure-static traversal includes every file in requested directories (with the
existing excluded-directory policy), including JSON and unknown extensions.
Unknown extensions prevent a complete coverage claim. Explicit missing or
escaping paths likewise mark incomplete. Legacy file selection is unchanged.

Interface ledger readers receive explicit roots from
`resolve_existing_storage_roots_read_only`, with the canonical runtime read
candidate when none exists, plus the existing workspace-root legacy candidate.
This reuses the KernelOne contract without initializing or write-probing storage.
Directory enumeration propagates read errors. Parser intents enter aggregation
before per-file helpers, so infrastructure exceptions cannot erase them.

Existing syntax_gate, TS/Go scanners, Factory, process containment and UID trust
remain outside this scope. Factory integration and NativeValidationSession/Group
fulfillment require a later bounded assignment. No bwrap provenance or T05
environment/fresh project qualification is claimed here.

## Evidence and verification

Reuse POL-35 versioned RAW by immutable path/hash; reproduce legacy Python
bytecode and JS subprocess effects on private minimal fixtures. Add public API
tests first, observe RED, then implement. Effect traps guard process, bytecode,
filesystem mutation and network during real pure-static scans. Fixtures cover
Python/JS/TS/Go/package eval, static errors, nested/multiple files, serialization,
error-limit and exception preservation, unknowns and legacy compatibility.

Run the new suite, both existing artifact scanner suites and direct consumer
tests without pytest cache/bytecode. Run scoped Ruff and mypy. Do not change old
tests or broaden files to hide failures. Full KernelOne release and fresh Bench
are explicitly unrun under this bounded component assignment.

## Component evidence (2026-10-07)

Pure-static suite: 32 passed. Scoped Ruff/format/mypy: passed. Existing scanner,
package/cross-artifact and direct consumer suites: 286 passed, 2 failed. The
read-only unit scanner tests at test_artifact_quality.py:1493 and :1844 assume
no preceding TS parser invocation and patch-residue being the first diagnostic,
respectively. Dynamic counterexamples and exact commands are in REPORT/RAW.
No old assertion was changed or TS parser bypassed. Board/Root must decide a
separate exact test authorization before that regression gate can close.
Implementation, independent component acceptance and fresh native delivery
remain distinct outcomes.

## Assumptions and pre-mortem

1. Legacy defaults are consumed by existing callers: graph shows QA/Director
   consumers; retain positional behavior and no keyword changes in legacy calls.
2. Per-file/package/final projections otherwise lose coverage: add fields at
   each boundary and assert public serialization.
3. Most likely failure: a retained static helper executes transitively, or
   early returns drop requirements. Trap effects at low-level boundaries and
   record incomplete coverage on all early exits.
4. Quality has no dedicated Cell declaration: capability catalog and existing
   public package govern this bounded extension; graph/fitness updates are
   assessed, not written outside the grant.
