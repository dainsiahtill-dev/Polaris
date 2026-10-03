# Physical HTTP deadline and cancellation

Approved direction: user2026-10-01 explicitly authorized direct implementation
and acceptance under the existing unattended-development plan, without repeated
per-document approval. This is part of P01/P03 foundation work, not completion
of those packages or fresh Bench qualification.

## Evidence and intended outcome

The exact MiMo trace times out logically at660s but finishes physical HTTP at
852.020s. A real localhost provider that trickles bytes reproduces the same
failure: a0.4s configured read timeout returns a valid body after its total
budget. Successful siblings and PM/CE remain preserved; target code is never
edited by the engineering agent.

## Chosen architecture

Reuse aiohttp, already used for provider streaming, for bounded non-stream
HTTP as well. Preserve synchronous Provider InvokeResult and requests.Response
compatibility at the adapter surface. Do not change retries, QA thresholds,
generated code, model bindings or timeout constants.

KernelOne owns an ephemeral invocation budget: monotonic absolute deadline,
irreversible cancellation and local cancellation callbacks. ContextVar passes
this control to worker threads. Nested calls can narrow but never extend the
parent budget. It is neither a capability grant nor a persistent fact source.

```text
KernelOne invocation budget (one deadline/cancel signal)
  -> sync provider worker / existing retry policy
  -> existing strict physical-attempt gate
  -> aiohttp total-deadline transport
  -> existing physical terminal receipt/drain
```

Caller timeout/cancel stops the local transport and prohibits subsequent
worker retries. Physical attempt settlement remains strict; no terminal or
quiescent state is fabricated from cancellation intent alone. Transport kind
is declared truthfully as aiohttp.ClientSession.post in the native projection
and actual wire view; exact route/body/tool checks remain unchanged. Existing
historical contexts are not rewritten or replayed.

## Compatibility and limits

Response status, body bytes, headers, encoding and HTTP-error behavior remain
compatible with current sync consumers. Keep environment proxy selection and
CA bundle behavior explicit; unsupported proxy schemes fail closed rather than
bypass the proxy. Cancellation of a local socket does not prove upstream
compute stopped or billing was cancelled. Legacy health GET is not migrated.
No extra dependencies or second event/receipt ledger.

## Acceptance

RED/GREEN real local trickle, cancellation with worker quiescence, ordinary
response parity, expired budget before network, no retry after caller timeout,
strict route/transport qualification, terminal receipt conservation, scoped
parallel isolation, nested-budget non-extension. Then provider/KernelOne/B3
suites, Ruff/Mypy and existing module cascade. Only then Director-only recovery
or fresh isolated Bench; full L1-L12/N-batch remains the final goal.
