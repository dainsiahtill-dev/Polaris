# Session snapshot admission and empty-input preservation

Issue: POL-100. Mode: implementation. Owner: PM/CE contract and blueprint engineer.
Review owner: Paperclip Root / independent reviewer. Classification: pattern.
This is bounded offline engineering work, not project delivery or native qualification.

## Architecture and responsibility

```text
SessionContinuityRequest (caller-owned config and events)
  -> persisted-projection invariant validation (existing kernelone.core helper)
  -> existing rehydration (legacy transcript index and noise filtering)
  -> canonical ContextOSSnapshot restoration (reference/model validation)
  -> enabled + normalized events: existing StateFirstContextOS projection
     enabled + no normalized events: retain supplied persisted snapshot
     disabled: existing explicit snapshot removal
  -> continuity pack lifecycle
  -> SessionContinuityProjection (changed = actual persisted config difference)
```

Only `session_continuity.py` changes production behavior. KernelOne owns generic
continuity projection; roles.session owns persistence. No cross-Cell internal
dependency, new schema, provider invocation, reset policy or execution authority.
The Cell catalog, cell.yaml and ContextOS public contracts already declare these
capabilities; README.agent.md is stale and does not expand write scope.

## Assumption register and evidence

1. A missing key and explicit `None` denote absence. Existing invariant validator
   returns None; roles.session `_sanitize_context_config` and
   `update_context_os_snapshot` remove a nullable snapshot. Preserve this contract;
   do not reinterpret `False`, `0`, empty text or collections as absence.
2. Any Mapping, including `{}`, enters existing rehydration/model restoration.
   Non-Mapping, non-None values fail with existing
   `ContextOSInvariantViolationError`. Ownership violations also fail at the
   existing validator before rehydration. Nested reference failures propagate
   unchanged from canonical restoration; no catch or replacement snapshot.
3. No normalized events means no new ContextOS projection. With ContextOS enabled,
   retain the original persisted payload exactly, including private archive export,
   metadata and snapshot-only hints. Do not invent a prompt projection or run report.
   This does not promise old WorkingState retention on a message-driven projection.
4. Explicit disable retains existing behavior: valid existing snapshot removed;
   absent/nullable state does not cause a new snapshot. Invalid supplied state still
   fails admission before any successful reset. Incoming-only context keys remain
   prompt-only, and unrelated persisted data stays unchanged.
5. `turn_events` take precedence when nonempty, including when normalization yields
   no events. Do not fall back to messages in that case.
6. `changed` describes persisted changes, not presence of a continuity pack or
   generation of a nonpersisted incremental pack. Existing pack canonicalization,
   removal, a new persisted pack, and snapshot updates still report true.

Root evidence: POL-98 consumer-supplement-v1 reports successful replacement of
`"not-a-snapshot"`, and deletion of a valid snapshot on empty messages. Source
filters non-Mapping to None at admission and deletes restored state whenever no
projection exists. Existing pack early returns also report changed unconditionally.
Prior POL-97 model reference validation is accepted independently and unchanged.

## Pre-mortem and decisions

Admission after filtering would still erase corruption; validation must precede
rehydration. Removing the entire no-projection branch would break explicit disable.
Serializing restored state on a no-op could drop archive exports or snapshot hints;
retain caller payload instead. Comparing against rehydrated/sanitized state would
misstate actual persisted changes; compare final config with original copied config.
Globally forcing changed false would hide pack and reset writes. Do not merge old
WorkingState into new transcript-derived state (POL-98 F03), manufacture missing
archive contents (F04), or repair observer/adaptive/serialization debt (F05-F07).

## Verification and rollback

Before source edit: freeze source and prior evidence; reproduce both failures in
private workspaces; add new consumer tests and retain RED logs. Test non-Mapping
values (including falsy values), absent/null/empty Mapping, enabled no-op/disable,
normalization/turn-event precedence, caller deep immutability, grounded accepted
plan provenance, actual private archive restoration, legacy index rehydration,
continuity pack no-op/canonicalization/rebuild and accurate changed flags.

After edit: new tests, existing session continuity, prior51 preservation and33
reference tests, structured protocol and projection purity. Retain skips and exact
known failure signatures; no broad unrelated rerun. Run focused Ruff, format-check,
strict mypy, diff check and end manifest. Versioned REPORT.md/REPORT.json records
commands, exits, hashes, dynamic counterexamples, residuals and independent handoff.
Rollback is a manual reversal of this scoped diff after Root review; no checkout,
reset, commit, worktree, install, service restart or target/native state edits.

Live Provider/final request, UI/WS, Factory, Run Ledger/QA settlement, release gates
and fresh isolated L1-L12 are not exercised or qualified by this task.

## Observed implementation verification

Initial RED-v1: 43 failures / 10 passes, including two erroneous new fixture
expectations (negative reference size is canonically accepted; low-signal prose
still has a deterministic summary). Corrected RED-v2 before source edit:
41 failures / 12 passes. Final 59-test file against the frozen original consumer:
44 failures / 15 passes; source loaded only in memory, with no working-tree revert.
Final current consumer: 59 passes. Existing model51/reference33: 84 passes;
continuity/protocol/purity: 119 passes / 10 existing optional-evidence skips.

Focused Ruff and format-check pass; strict mypy on new tests passes. Strict mypy
including the consumer remains exit1 with four pre-existing unreachable statements
in defensive collection loops (same statement/function signatures before/after).
This is recorded debt, not a green type gate; no type-ignore or gate relaxation.
Report artifacts provide exact commands, hashes, counterexamples and scope proof.
Independent Root acceptance, combined source qualification and fresh native runs
remain separate steps.
