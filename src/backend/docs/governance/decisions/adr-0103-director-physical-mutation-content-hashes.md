# ADR-0103: Director physical mutations expose content hashes

Status: Accepted  
Date: 2026-08-13

## Context

Director tool execution and Factory quality repair used incompatible evidence
contracts. The physical executor returned a successful edit and committed a
durable effect receipt, while Factory correctly refused to call it a mutation
without unequal before/after content hashes. This split produced a false
`workspace_quality_repair_no_mutation` after a real disk change.

## Decision

Every successful Director file mutation result must expose content-level
`before_sha256` and `after_sha256` at the physical effect boundary. Creation
uses `file_absent` as the before token. Consumers may recognize mutation only
when both values are valid and unequal. Generic success, replacement counts,
or dispatch receipts alone are insufficient.

The hashes are evidence inside the existing directed-effect receipt chain;
they do not create a second state owner. Factory remains a strict consumer and
must not infer changes by rescanning arbitrary workspace files.

## Consequences

- Real writes survive projection into stage settlement.
- No-op/rejected writes remain unable to complete a repair task.
- Repair resumes on the same Director task and reruns only failed verifiers.
- Other mutation tools should adopt the same fields when their physical result
  crosses the Factory mutation boundary.

