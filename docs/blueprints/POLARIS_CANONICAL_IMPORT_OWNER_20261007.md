# Canonical import owner boundary (POL-33)

Mode: implementation. Owner: director.runtime planner; external employee report only.
Independent acceptance belongs to Root and a distinct reviewer. No project delivery claim.

## Evidence and design

Exact QA fixture replay `canonical-import-owner-gap-v1/qa-red-v1.txt` exits 1:
typed `unresolved_relative_import`, canonical path `checks/current.ts`, absent
specifier, stale raw path `checks/stale.ts` produces a stale-file operation.
The upstream structured normalizer correctly retains unknown identity. The parser
guards canonical fields only when the specifier key exists, so absence acquires
authority from raw provenance. Invalid present values already fail closed.

Data flow:

```text
generic public Plan / PlanProbe / materialization probe
  quality input normalization and identity (unchanged)
  existing runtime executable dispatcher
  relative-import parser: canonical code requires canonical specifier
  existing AST candidate proof and original-base aggregation (unchanged)
  existing Composer and public projection (unchanged)
```

Use the existing diagnostic code as the boundary. All
`unresolved_relative_import` diagnostics use their canonical path/specifier;
missing specifier yields no parsed import. This requires no trusted hint or
upstream change: original legacy unresolved text already normalizes into this
code with a valid canonical specifier. Other legacy text continues using the
existing raw fallback. Bare TS2307 is already unplannable for this tool; the
legal legacy positive control supplies the original unresolved scanner row
alongside TS2307. The negative control preserves bare TS2307 rejection rather
than adding new compiler parsing outside this task.

Only `_parse_unresolved_relative_import_errors` and its local comment change.
No new Cell, public contract, source_tool, state owner or effects. KernelOne AST,
coverage, registry, Composer, policy, receipts and scheduler are reused unchanged.
Graph/schema/descriptor generation is not needed for this private branch fix;
their files remain read-only. No targets or runtime are edited.

## Assumptions and falsification

1. Canonical code alone identifies canonical authority. Exact absent typed
   current/stale and current/current inputs must produce no patches in all three
   public interfaces; complete proposals and trace, not status alone, prove it.
2. Raw-only normalization supplies canonical identity. Original unresolved text
   and TS2307 positive controls must still produce exact current-file patches.
3. Valid canonical fields outrank conflicting raw evidence. Explicit current
   specifier with contradictory raw owner/specifier must repair only current.
4. Input and source hashes remain unchanged. All queries compare deep copies and
   UTF-8 hashes; separate process audit guards import and call effects.

Pre-mortem: rejecting legacy normalized inputs; accepting missing identity from
raw or message; hiding bad operations behind a status change; breaking the shared
case/unused/unique callers; modifying prior dirty implementation or assertions;
calling no-plan a compiler success. Positive, negative and mixed controls test
these risks. Missing identity remains uncovered for planning and unresolved for
the verifier; it does not imply compilation or project completion.

## Verification

Preserve original QA RED, run the new public frontier RED before production edit,
then GREEN and exact fixture GREEN. Execute the original 847 selectors plus new
frontier with actual collection count, and the separate 28 shared callers.
Compile exact public proposal and original source in private TS5.9.3 cwd using
strict/noEmit, types=[], empty typeRoots and unchanged hash sets. Absent has no
plan and remains TS2307; explicit exact output compiles 0. Audit-hook query
process must observe zero write/spawn/network attempts. Scoped mypy/Ruff/format
and diff checks compare existing debt without reformatting production.

Evidence directory:
`.superpowers/sdd/2026-10-06-paperclip-repair-frontier/canonical-import-owner-gap-v1/`.
Provider/Bench/services/effects/settlement and fresh isolated L1-L12 are not
authorized by this bounded corrective task. Root must arrange independent new
revision QA before TS5110 continuation.
