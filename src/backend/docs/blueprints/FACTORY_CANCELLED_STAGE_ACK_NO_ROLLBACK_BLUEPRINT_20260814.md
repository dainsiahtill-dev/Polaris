# Factory Cancelled-Stage ACK Recovery Without Runtime Rollback

Status: Implementation active  
Date: 2026-08-14  
Owner Cell: `factory.pipeline`

## Problem

L1-04 exposed a crash window absent from L1-01 through L1-03: a
`stage_completed` event and immutable checkpoint were durable, cancellation
occurred before `factory_stage_persistence_committed`, and a later QA retry
legitimately reopened physical epoch 41 with workspace fencing token 106.
Startup recovery then copied the older stage checkpoint back into the mutable
run snapshot. The admission lease stayed at token 106 while the run snapshot
regressed to token 104, so subsequent same-stage retry correctly failed closed
with `Factory workspace lease owner has been fenced`.

## Architecture

```text
immutable stage event + immutable checkpoint
                 |
                 v
validate exact event / intent / checkpoint / last-stage pointer
                 |
                 v
append missing factory_stage_persistence_committed marker
                 |
                 +---- never overwrite current mutable FactoryRun
                 |
                 v
reduce event stream; clear only exact cancellation quarantine
```

## Invariants

1. Recovery may only close `cancelled_before_commit_ack` for its exact pending
   `stage_completed` event.
2. Immutable checkpoint proves the original stage transaction and supplies the
   marker's canonical run/checkpoint hashes.
3. Current mutable run state is newer authority. Recovery must validate its
   last-stage pointer but must not replace status, retry epoch, lease token,
   lifecycle claim, or later metadata.
4. Any pointer, run-id, hash, or reducer mismatch remains fail-closed.
5. Startup driver must not resume a run when recovery returns terminal state.

## Verification

- Regression: later mutable retry evidence survives cancelled-stage ACK repair.
- Existing exact cancellation cut still converges and can re-enter same stage.
- Mismatched checkpoint/pointer remains quarantined.
- Factory run driver does not submit terminal recovery output.
- Ruff, format, mypy, focused pytest, then same-run live QA retry.

