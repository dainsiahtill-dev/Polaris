# ADR-0107: No-effect writes are not authoritative mutations

Status: Accepted  
Date: 2026-08-14  
Encoding: UTF-8

## Context

Director tool execution uses two meanings of success: transport/tool handling
may safely accept malformed edits as non-fatal no-ops, while project delivery
requires a physical content mutation. L1-04 showed these meanings were merged:
four `edit_file_empty_search` results preserved disk content but received
authoritative successful DEO receipts and completed the mutation batch.

## Decision

No-effect results are not mutation receipts. The final provider-response guard
must validate arguments from every supported native envelope shape before DEO.
The DEO mutation port must defensively terminalize any physical `no_op=true`
result without committing `receipt_outcome=succeeded`. Transaction convergence
must ignore no-op rows and route known no-effect writes to bounded same-turn
Director re-planning.

Authority remains strict for tool, path, JobToken, lease, and receipt hashes.
Recovery becomes flexible only inside the already-authorized Director task; it
does not widen scope or restart PM/Chief Engineer.

## Consequences

- Disk truth and Run Ledger effect truth cannot diverge on no-op edits.
- QA may remain strict about real failed tests without causing full-chain reset.
- Models may retry corrected edit arguments within a finite same-task loop.
- Low-level editor no-ops stay non-fatal, but cannot satisfy delivery.

