# ADR-0106: Cancelled Stage ACK Recovery Does Not Roll Back Runtime State

Status: Accepted  
Date: 2026-08-14

## Context

Factory stage persistence has immutable event/checkpoint evidence and a mutable
run snapshot. A process can be cancelled after `stage_completed` is durable but
before its commit marker is appended. Later lifecycle actions may validly
advance retry epoch and workspace fencing authority before restart recovery
closes that missing marker.

Restoring the old checkpoint into the mutable run snapshot repairs one missing
ACK by destroying newer authority. L1-04 proved the resulting split: durable
workspace lease token 106 versus regressed run token 104.

## Decision

Cancelled-stage recovery is an append-only ACK repair:

- validate exact event, intent, immutable checkpoint, and last-stage pointer;
- append only the missing `factory_stage_persistence_committed` marker;
- hash the immutable checkpoint snapshot named by the original transaction;
- never save the checkpoint over the current mutable run;
- validate that the current run still points at the same last-stage commit;
- clear only the exact cancellation quarantine through event reduction.

## Consequences

Later retry/lease/lifecycle facts survive restart. Corrupt or unrelated cuts
remain quarantined. Current runs already corrupted by the superseded behavior
need explicit event/lease reconciliation or a fresh isolated acceptance run;
the new rule prevents recurrence but does not fabricate missing mutable facts.

