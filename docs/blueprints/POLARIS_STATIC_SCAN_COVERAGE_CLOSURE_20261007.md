# Static scan coverage closure

POL-45; mode implementation; owner external Director tools engineer.
Classification: pattern. Independent reviewer and final acceptance owner: Root.

## Scope and architecture

Only `_scan_package.py`, `_scan.py`, and `interface_ledger.py` production code
may change. Existing models, exports, callers and tests remain read-only.
KernelOne capability catalog declares `kernelone.quality` with public entry
`polaris.kernelone.quality`; `kernelone.core` owns context/workflow, not quality.
No cross-Cell internal dependency or new state owner is needed.

```text
public pure_static scan
  package invocation discovery -> required deferred parser intent
  read-only ledger status -> optional missing / readable / unknown
  legacy ledger helpers -> existing diagnostics, with exception coverage
  workspace traversal -> regular files / unvisited symlink coverage
  existing ArtifactQualityEvidence -> errors + intents + incomplete reasons
```

Invocation presence is independent of JavaScript source truthiness. Empty and
whitespace eval still require the real parser; this scanner never fulfills it.
Recognize documented eval/print forms and bounded Node options. Ambiguous shell
or unknown Node option syntax remains explicitly incomplete, without executing
or pretending to parse general shell.

A minimal public read-only ledger status method distinguishes optional ENOENT
from denied/I/O/invalid UTF-8/malformed JSON or structure. It requires an explicit
storage root, avoiding write-probing CLI storage initialization. Legacy `_load`
and existing consumers keep best-effort behavior. Both pure-static helper layers
retain exception evidence instead of swallowing it. Keep the existing two root
candidates, exact task/scope semantics, and readable public-symbol diagnostics.

Traversal never follows symlinks. Unvisited links in traversed directories or
explicit scopes produce incomplete coverage, including links in path ancestors,
cycles and broken links. Existing excluded-directory policy remains unchanged.
No directory link is read, including links outside workspace.

## Assumptions and pre-mortem

- Original immutable QA probe is replayable from RAW `private_probe_source`.
  RED replay: 19 cases, 15 PASS / 4 FAIL; zero scan effects.
- Empty eval disappears twice: iterator filters source and caller uses `any`.
  Both must change; nonempty legacy diagnostics remain.
- `_load` and two aggregate catch blocks erase ledger uncertainty. Separate
  read-only status preserves legacy callers and prevents pure-static certification.
- `os.walk` lists directory links in `dirs` but does not visit them. Explicit
  scopes currently resolve links before checking; check lexical ancestors first.
- Most likely regressions: loss of prior intents on exception, global strict
  ledger behavior, scope broadening, or external reads. Trap effects and external
  reads; preserve original 32 + exact 288 selectors and all assertions.

## Verification plan

Unmodified QA 19-case RED/GREEN, new public frontier tests for empty eval, ledger
unknown/readable/absent, helper exceptions, full/explicit symlink scope and normal
directories. Public scans guard subprocess, bytecode, writes and fixture hashes.
Run author static 32 and POL-41 six-file 288 selectors, plus exact two calibrations.
Run scoped Ruff check/format check and mypy with baseline/current differences.
No full release, Provider, Bench, services, installation or target mutation.
Component verification is not independent acceptance or fresh project delivery.

## Complexity and residual boundary

Traversal is linear in visited entries plus requested path components; link
checks use lstat and do not recurse. Ledger status reads are linear in ledger
size, bounded to existing candidates; no caching, copying or secondary truth.
Deferred requirements remain non-authoritative. Read status is an observation,
not a transactional lock against a concurrent external ledger replacement.

## Verified component result

Unmodified QA probe: RED 15/19, GREEN 19/19. New frontier: 46 passed;
author suite: 32 passed; exact POL-41 six-file selector: 288 passed;
two original calibration selectors: 2 passed. Pure scan effects: zero;
fixture hashes unchanged. Full logs and immutable input/source hashes are in
`.superpowers/sdd/2026-10-06-paperclip-repair-frontier/static-scan-coverage-closure-v1/`.
Ruff, format check and scoped mypy pass. Strict mypy retains four pre-existing
`no-any-return` findings, with zero added/removed after line normalization.

Supplemental RED probes prevented lexical normalization from hiding a linked
ancestor and rejected unsupported ledger schema. Node oracle confirmed empty
separate `-e` source and print forms; `-e=` / empty `--eval=` are invalid in the
installed Node and remain conservatively deferred plus incomplete. One new test
incorrectly expected a contract amendment for a missing export; corrected to
assert the existing cross-artifact issue task binding. Original tests and QA
probe were not edited. No independent acceptance, real release, T05 environment,
JobToken/ProjectReceipt or fresh isolated L1-L12 completion is claimed.
