# ADR-0105: Unittest failures are causal convergence units

Status: Accepted  
Date: 2026-08-13

## Context

Workspace validation stores one command row for a Python unittest run. The
row can contain many independent failing tests. Collapsing it to the first
exception makes diagnostic counts represent commands rather than defects and
can falsely trip the bounded repair-loop stagnation breaker.

## Decision

`director.runtime` normalizes each Python unittest `FAIL`/`ERROR` block into
one repair diagnostic, preserving test identity, traceback, exception, raw
block, and source-transcript hash. Factory continues to use verifier-derived
signatures and the existing two-consecutive-stagnation stop rule. Director's
repair prompt adds a language-neutral consistency preflight for newly
introduced references and their owner definitions.

## Consequences

- Real reductions reset stagnation without weakening QA.
- Regressions remain visible and consume bounded repair budget.
- The configured third round is available after progress followed by one
  regression.
- No infinite retry, PM/CE restart, or target-project patch is introduced.
