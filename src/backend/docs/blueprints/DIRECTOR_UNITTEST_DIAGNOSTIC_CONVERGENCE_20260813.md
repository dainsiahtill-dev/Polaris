# Director unittest diagnostic convergence

Status: Implementing  
Date: 2026-08-13  
Cells: `director.runtime`, `roles.adapters`, `factory.pipeline`

## Problem

Python `unittest -v` reports many `FAIL`/`ERROR` blocks inside one command
transcript. The repair diagnostic normalizer collapsed that transcript to the
first Python exception. A live L1-03 repair therefore projected an actual
four-to-three failing-test reduction as a one-to-one `equal_count_swap`.
Factory counted the round as stagnant; the following regression became a
second stagnant round and stopped before the configured third repair round.

## Architecture

```text
unittest command transcript
  -> one immutable diagnostic per FAIL/ERROR block
  -> stable test identity + traceback + exception metadata
  -> verifier before/after diagnostic signatures
  -> progress/regression/stagnation classification
  -> same-Director-task bounded correction round
```

The Director repair prompt also requires a generic edit-consistency preflight:
new references, enum members, imports, callables, and mapping keys must already
have an owner definition or be created/updated in the same authorized edit
batch.

## Invariants

1. Progress is measured by causal verifier failures, not command-row count.
2. Each unittest FAIL/ERROR block remains independently traceable.
3. A real diagnostic reduction resets the consecutive-stagnation counter.
4. Two genuinely non-progressing rounds still stop; retries remain bounded.
5. Writer claims and mutation receipts never override verifier authority.
6. Repair stays on the same Director task; PM/CE are not restarted.
7. No target-project business rule is encoded in Polaris.

## Verification

- Unit-normalize a transcript with two ERROR blocks and one FAIL block into
  three diagnostics.
- Replay the real L1-03 verifier evidence and prove counts `4 -> 3 -> 6`.
- Verify the quality-repair final request includes the generic edit-consistency
  preflight.
- Retry only `qa_gate` on the existing isolated L1-03 run and observe the
  third same-task correction round.
