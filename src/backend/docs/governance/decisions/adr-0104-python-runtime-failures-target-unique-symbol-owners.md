# ADR-0104: Python runtime failures target unique symbol owners

Status: Accepted  
Date: 2026-08-13

## Context

Quality repair treated test paths and direct imports as mutation authority.
When several modules were imported, changed-file ordering could select an
unrelated importer and force the Provider to edit only that file. A successful
edit then produced no verifier progress and was reversed on the next turn.

## Decision

For Python unittest/runtime diagnostics, roles.adapters derives candidate
symbols from exception ownership and exact traceback test lines, indexes
actual non-test Python definitions, and promotes only uniquely owned source
files. This authority precedes broad import/test fallbacks. If unique owners do
not cover the observed failed test modules, the existing conservative fallback
is retained. Existing task scope and policy gates remain authoritative.

## Consequences

- Director receives the smallest evidence-backed mutation surface.
- Test files and recently changed importers no longer eclipse actual owners.
- Ambiguous runtime behavior remains fail-closed instead of inventing edits.
- Local verifier retries consume fewer tokens and avoid PM/CE restarts.
