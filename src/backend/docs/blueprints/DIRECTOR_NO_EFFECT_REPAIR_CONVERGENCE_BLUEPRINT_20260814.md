# Director no-effect repair convergence blueprint

Status: Implementation active  
Date: 2026-08-14  
Encoding: UTF-8

## Problem

L1-04 QA-local repair exposed a split truth. A non-stream provider returned
top-level native `edit_file` calls whose `search` was empty. The final-request
guard inspected only nested `function.arguments`, so the malformed calls reached
the physical executor. The executor safely returned
`ok=true, no_op=true, reason=edit_file_empty_search`, but the directed-effect
mutation port committed a durable authoritative `receipt_outcome=succeeded`.
The transaction kernel then counted the batch as a successful mutation although
all file hashes were unchanged.

## Architecture

```text
provider response
  -> final-request tool/argument authority
     -> reject malformed top-level or nested native calls
     -> bounded same-turn Director retry
  -> DEO mutation port
     -> execute physical tool
     -> no_op => no successful effect receipt
     -> real mutation => hash-bound successful effect receipt
  -> transaction batch convergence
     -> no-op receipts never satisfy mutation obligation
     -> re-plan only current Director repair turn
  -> rerun failed verifier only
```

## Invariants

1. Tool authority remains fail-closed; scope and JobToken are never widened.
2. Both nested OpenAI and top-level normalized native tool envelopes receive
   identical argument validation.
3. `ok=true` means transport/tool handling succeeded; it does not prove a file
   mutation. `no_op=true` can never become an authoritative successful effect.
4. Recoverable no-effect writes are terminalized at the consumed DEO operation,
   then retried inside the same Director turn. PM and CE are not restarted.
5. Only unequal before/after content hashes satisfy mutation convergence.

## Verification

- Unit: top-level empty-search call is rejected before physical execution.
- Unit: a physical no-op never commits a successful effect receipt.
- Unit: a no-op batch does not satisfy `_batch_has_authoritative_success` and is
  classified as same-turn replannable.
- Regression: existing genuine writes, pending async effects, policy denials,
  cancellation, and low-level non-fatal no-op behavior remain intact.
- Live: retry only L1-04 `quality_gate`; prove a real edit has unequal hashes and
  only failed Go verifier diagnostics are rerun.

