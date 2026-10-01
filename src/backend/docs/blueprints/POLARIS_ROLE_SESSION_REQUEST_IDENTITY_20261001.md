# Public role-session request identity

Status: implementation pending. Approved scope: unattended plan P05 and user full implementation.
Classification: pattern; owner roles.runtime. Transaction identity semantics remain roles.kernel-owned.

## Exact dynamic failure

MiMo generic deep probes32/32 pass. Actual tool probe b05c1476 produces
TransactionIdentityError/transaction_identity_unbound before provider dispatch.
ExecuteRoleSessionCommandV1 and shared _build_session_request do not provide any
first-class execution scope for ordinary user turns. This affects every consumer
of the public session command, not just the internal evaluator.

## Design

```text
public immutable session command owns unique turn_request_id
  shared request preparation preserves existing execution scope or carries that id
  unchanged TransactionKernel validates/binds invocation and attempt
  unchanged TaskRuntime/DEO and tool policy decide mutation permission
```

The nonce is minted once per independent command. Serialized command restoration,
stream/non-stream and retries of the same command retain it; a new command in the
same session is a new logical request. Existing metadata execution_attempt_id,
execution_id, task_runtime_session_id, turn_request_id and nested runtime_execution
take precedence without being rewritten or laundered. Empty/malformed explicit
scope must not be repaired by a random id. Ordinary session labels remain labels.

This is correlation/idempotency identity, not a capability, grant or completion
receipt. Guarded sessions still validate the current typed TaskRuntime authority
before request/kernel access. No new Cell, durable owner, evaluator policy, public
language facade, kernel fallback or write-scope relaxation.

Alternative rejected: allocate uuid each time _build_session_request runs; that
breaks request replay and cannot survive serialization. Benchmark-only metadata
patch rejected: it hides a shared submission-contract gap.

## Proof

- Real kernel identity derivation from public command projection, without provider.
- Same-command stable id, serialization, independent same-session turns, both modes.
- Existing flat/nested execution identity preserved; empty/conflicting identity denied.
- Guarded command without authority cannot reach request/kernel even with nonce.
- Existing owner regression suites, then unchanged real native tool probe.
- Full unattended project/fresh proof remains separate from this narrow contract fix.
