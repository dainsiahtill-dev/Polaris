# Structural Bug Protocol

## Purpose

Structural bugs are not handled as isolated patches. They are treated as signals
that one or more runtime contracts were implicit, ambiguous, or unverifiable.

The protocol exists to make those contracts explicit before the next change
re-breaks them.

## Trigger Conditions

Use this protocol when a bug matches any of the following:

1. The same failure class appears in multiple paths or adapters.
2. A runtime loop, state machine, transcript path, contract layer, or re-export
   chain failed because data semantics were ambiguous.
3. The fix depends on hidden assumptions such as "this string is already
   sanitized" or "this symbol is always re-exported".
4. The change touches role execution, streaming, tool dispatch, transcript
   persistence, contract re-exports, or other high-risk runtime boundaries.

## Required Artifacts

Before closing a structural bug, ship all of the following:

1. A verification card under
   `docs/governance/templates/verification-cards/`.
2. An ADR under `docs/governance/decisions/`.
3. A debt entry in `docs/governance/debt.register.yaml`.
4. A `generated/verify.pack.json` update for the affected Cell.
5. Automated regression tests for both behavior and governance linkage.

## Execution Sequence

1. Record assumptions.
2. Write the pre-mortem: where the fix is most likely to be wrong.
3. Classify the bug:
   - `one_off`
   - `pattern`
   - `structural`
4. If the bug is `structural`, create or update:
   - verification card
   - ADR
   - debt register entry
   - verify pack
5. Refactor the runtime so the contract is explicit in naming, data shape, or
   typed staging objects.
6. Add or update regression tests.
7. Wire the tests into fitness rules and the governance pipeline when the change
   protects a reusable boundary.

## Minimum Closure Criteria

A structural bug is not considered closed just because the triggering test now
passes. Closure requires:

1. The runtime contract is explicit.
2. The residual risk is documented.
3. The debt is either retired or explicitly tracked.
4. The next engineer can discover the contract from Cell assets and governance
   assets without replaying the incident.
