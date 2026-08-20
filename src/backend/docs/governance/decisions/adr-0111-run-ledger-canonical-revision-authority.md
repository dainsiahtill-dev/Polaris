# ADR-0111: Run Ledger owns canonical gate revision authority

Status: Accepted  
Date: 2026-08-21  
Owner: `control_plane.run_ledger`

## Context

Factory previously allocated `gate_revision` by reading its local RunLedger
NDJSON file. Runtime-root migration or process restart can recreate that file
with only a suffix of canonical history. Live run `factory_ec5697b14a71`
therefore produced a second revision chain `1..4` while FactStream already
contained `1..7`. The target project and final QA commands passed, but canonical
projection correctly failed closed on the fork.

## Decision

1. Gate producers provide identity and physical evidence, not revision numbers.
2. `control_plane.run_ledger` allocates revision metadata from canonical
   `execution.control_plane` facts before durable append.
3. Multiple branch heads remain an integrity failure until a new event:
   - continues one structurally valid head; and
   - lists every other branch head in
     `resolves_gate_revision_branch_heads`.
4. Resolved branches remain immutable history but lose current outcome
   authority. Partial, unknown, ambiguous, or implicit resolution fails closed.
5. Projection exposes resolved and unresolved revision evidence for automatic
   causal diagnosis.

## Consequences

- Restarted local projections cannot reset canonical gate revision numbering.
- Existing forked runs can recover without deleting facts or restarting the
  full PM/CE/Director chain.
- Genuine concurrent forks still block until an explicit, auditable resolver
  revision is appended.
- Revision allocation now follows Single State Owner: Factory consumes the Run
  Ledger public append contract instead of reading compatibility state.

## Verification

- Graph-based revision-chain tests, including negative unresolved fork tests.
- Factory canonical-allocation regression with stale local ledger state.
- Exact-run QA-only revalidation of `factory_ec5697b14a71`.

